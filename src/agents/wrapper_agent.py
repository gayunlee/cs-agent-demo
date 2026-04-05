"""Wrapper Strands Agent — 검증된 legacy workflow를 tool로 감싸서 노출.

설계 (2026-04-05 ~ 2026-04-06):
- 단일 Strands Agent + 평탄한 tools + AgentCore Memory 멀티턴
- `src/agents/consultant.py`는 Phase 3 그대로 + 검증 안 됨 → 사용 안 함
- 이 wrapper는 **검증된 legacy `RefundAgentV2.process()`** 를 `@tool` 로 감싸고,
  Strands Agent가 top 레이어로 앉아 멀티턴/Memory/AgentCore 훅을 제공.

Memory 2-layer (2026-04-06):
1. **AgentCore Memory** (persistence):
   - `src/memory.py` 를 통해 `MemorySessionManager` 사용
   - 매 턴마다 `save_turn` 으로 이벤트 저장
   - Agent 호출 시작 시 `get_context_for_prompt` 로 short-term + long-term context 를
     system_prompt 에 주입 → 세션/프로세스 넘어서 맥락 유지
   - us-product-agent 패턴 그대로 (SlidingWindow 안 씀)

2. **turn_log closure** (legacy 주입):
   - legacy `RefundAgentV2.process()` 의 `conversation_turns` 인자용
   - workflow.py 의 `prev_had_t2` / `last_user_ts` 필터링을 위해 ts 필드 포함
   - AgentCore Memory 와 별개 역할 (legacy 가 자체 context 형식을 요구함)

장점:
- 기존 workflow.py / RefundAgentV2 / refund_engine.py / templates.py = 0 변경 (회귀 0)
- Strands Agent tool-loop 실제 동작
- AgentCore Memory 가 실제 persistence 제공 (해커톤 쇼케이스 가치)
- Guardrail / Evaluation 훅 지점 확보
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel

from src.refund_agent_v2 import RefundAgentV2
from src.memory import AgentMemory, create_memory_session, get_context_for_prompt, save_turn

logger = logging.getLogger(__name__)


MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-west-2"
GUARDRAIL_ID_PATH = Path(__file__).resolve().parents[2] / "guardrail_id.json"

# 환불 도메인 gate — legacy intent classifier 결과 재사용 (중복 LLM 호출 없음).
# `result.intent` 는 RefundAgentV2 자체 classifier 의 **한국어 7-enum** 결과를 담음
# (`src/refund_agent_v2.py:28-43` CLASSIFY_PROMPT). workflow.py 가 쓰는 영어 intent
# (`src/intent_classifier.py`)와는 별개. 여기 whitelist 는 한국어 enum 기준.
# 장기적으로 모든 CS 유형 대응하면 이 whitelist 는 제거 예정.
REFUND_DOMAIN_INTENTS: set[str] = {
    "환불_요청",
    "해지_방법",
    "해지_확인",
    "자동결제_불만",
    "환불_규정_문의",
    "카드변경",
}
# RefundAgentV2 한국어 classifier 에는 "기타" 만 비도메인. 경계 intent 없음.
ALLOWED_INTENTS: set[str] = REFUND_DOMAIN_INTENTS


def _load_guardrail_kwargs() -> dict:
    """guardrail_id.json 이 있으면 BedrockModel 용 kwargs 반환, 없으면 빈 dict."""
    if not GUARDRAIL_ID_PATH.exists():
        return {}
    try:
        with open(GUARDRAIL_ID_PATH) as f:
            gd = json.load(f)
        return {
            "guardrail_id": gd["guardrailId"],
            "guardrail_version": str(gd["version"]),
        }
    except Exception as e:
        logger.warning(f"guardrail_id.json 로드 실패 (guardrail 없이 동작): {e}")
        return {}


SYSTEM_PROMPT = """당신은 어스플러스(한국 교육 SaaS)의 CS 상담 에이전트입니다.

# 핵심 규칙 (절대 어기지 말 것)

1. **환불/해지/취소/결제/구독/정기결제/카드/중복결제** 관련 메시지가 들어오면
   **반드시 `run_refund_workflow` tool을 호출**해야 합니다.
   - 첫 턴이든 후속 턴이든 상관없음. **매 턴마다 tool 재호출**.
   - 절대 먼저 질문하지 마세요. 절대 이전 턴의 기억으로 답을 지어내지 마세요.
   - Tool 결과만이 정확한 답입니다. 이전 tool 결과를 기억으로 합성하지 마세요.

2. **후속 확정/진행/철회 턴도 tool 호출 대상**입니다.
   예: "네 진행해주세요", "환불 확정합니다", "취소할게요", "다시 이용할래요"
   → 모두 `run_refund_workflow` 재호출. Legacy workflow가 이전 턴 맥락을 참고해
     올바른 템플릿(T2 견적 → T3 접수완료 등)으로 라우팅합니다.

3. **admin_data_json 전달**:
   유저 메시지에 `<admin_data>...</admin_data>` 블록이 있으면, 그 안의 JSON 문자열을
   **그대로** `admin_data_json` 인자로 전달. 블록이 없으면 (후속 턴 등) 빈 문자열 `""`.

4. **chat_id 전달**:
   가능하면 유저 메시지의 `<chat_id>...</chat_id>` 값을 `chat_id` 인자로 전달.
   없으면 빈 문자열.

5. **tool 결과 처리**:
   tool이 반환하는 `draft_answer` 를 **그대로** 답변으로 사용. 임의 수정 금지.
   절대 카드번호, 환불금액, 영업일 같은 세부사항을 지어내지 마세요.

6. **handoff 조건**:
   환불 범위를 명백히 벗어난 케이스(시스템 오류, 앱 로그인 불가 등)만 `handoff_to_human`.
   애매하면 `run_refund_workflow` 먼저 시도.

# 예시

## 첫 턴
유저: "구독 해지 부탁드려요 <admin_data>{...}</admin_data>"
✅ run_refund_workflow(user_message="구독 해지 부탁드려요", admin_data_json='{...}') → draft_answer 출력

## 후속 턴
유저: "네 진행해주세요"
✅ run_refund_workflow(user_message="네 진행해주세요", admin_data_json="") → draft_answer 출력
   (legacy workflow가 이전 턴 T2 견적 맥락을 보고 T3 접수완료로 라우팅)
❌ "감사합니다! 환불 확정을 받았습니다. 카드 끝자리 5886..." (창작 금지)
"""


@tool
def handoff_to_human(reason: str) -> dict:
    """환불/해지 범위를 벗어난 문의를 상담사에게 인계합니다.

    시스템 오류, 여러 도메인이 엮인 복합 이슈, 또는 AI가 확신할 수 없는
    케이스에 사용하세요.

    Args:
        reason: 인계 사유 (예: "앱 로그인 오류로 기술지원 필요")
    """
    return {
        "action": "handoff",
        "reason": reason,
        "message": f"해당 문의는 상담사가 직접 확인 후 답변 드리도록 전달드렸습니다. 사유: {reason}",
    }


def _make_refund_tool(turn_log: list[dict], admin_cache: dict, session_id: str):
    """Session 별 closure — turn_log + admin_data 를 capture 해서 legacy에 주입.

    - `turn_log`: 이전 턴 목록 → legacy `conversation_turns` 로 전달
    - `admin_cache`: 첫 턴 admin_data snapshot → 후속 턴에서 재사용
      (LLM이 후속 턴에 admin_data_json="" 넘겨도 세션 캐시로 복구)
    """

    @tool
    def run_refund_workflow(
        user_message: str,
        admin_data_json: str = "",
        conversation_time: str = "",
        chat_id: str = "",
    ) -> dict:
        """검증된 환불/해지 워크플로우를 실행합니다.

        유저 메시지와 admin API 조회 결과를 받아, 환불 정책에 따라 적절한
        답변 템플릿과 변수를 결정합니다. 템플릿 16종 + 환불 금액 계산 포함.
        이전 턴 맥락은 자동으로 주입됩니다 (wrapper session turn_log).

        Args:
            user_message: 이번 턴 유저 메시지
            admin_data_json: admin API 조회 결과 JSON string (후속 턴에서는 빈 문자열 OK)
            conversation_time: 대화 시점 ISO 8601 (선택)
            chat_id: 대화방 ID (로그용)

        Returns:
            dict: template_id, draft_answer, reasoning_path, refund_amount
        """
        try:
            parsed = json.loads(admin_data_json) if admin_data_json else {}
        except (json.JSONDecodeError, ValueError):
            parsed = {}

        # admin_data 세션 캐시: 새로 받은 게 있으면 캐시에 덮어쓰고, 없으면 캐시 사용
        if parsed:
            admin_cache.clear()
            admin_cache.update(parsed)
        admin_data = dict(admin_cache)

        # turn_log snapshot — 이전 턴 맥락을 legacy에 넘김
        prior_turns = list(turn_log)

        v2_agent = RefundAgentV2(mock=False)
        result = v2_agent.process(
            user_messages=[user_message],
            chat_id=chat_id or session_id,
            admin_data=admin_data,
            conversation_time=conversation_time,
            conversation_turns=prior_turns,
        )

        # 환불 금액 추출
        refund_amount = None
        for step in (result.steps or []):
            if step.step == "final":
                vars_ = (step.detail or {}).get("variables") or {}
                amt_str = vars_.get("환불금액")
                if amt_str:
                    try:
                        refund_amount = int(str(amt_str).replace(",", ""))
                    except (ValueError, TypeError):
                        pass
                break

        # 판단 경로 요약
        path_parts: list[str] = []
        for step in (result.steps or []):
            if step.step == "classify":
                p = (step.detail or {}).get("path") or []
                if p:
                    path_parts.extend(p if isinstance(p, list) else [str(p)])

        return {
            "template_id": result.template_id or "",
            "draft_answer": result.final_answer or "",
            "reasoning_path": " → ".join(path_parts) if path_parts else "",
            "refund_amount": refund_amount,
            "intent": result.intent or "",
            "is_refund_domain": (result.intent or "") in ALLOWED_INTENTS,
        }

    return run_refund_workflow


def create_wrapper_agent(
    session_id: str = "default",
    actor_id: str = "cs_agent_default",
    model_id: str = MODEL_ID,
    region: str = REGION,
) -> Agent:
    """Wrapper Strands Agent 생성 + AgentCore Memory 연결 + legacy turn_log closure.

    Returns:
        Agent: legacy workflow 을 tool 로 감싸고, AgentCore Memory 로 세션 맥락 유지.
               `agent.handle_turn(user_text)` 로 한 턴 처리 (Memory save/retrieve 자동).
               또는 low-level: `log_user_turn` / `log_assistant_turn` + 직접 호출.
    """
    # legacy 주입용 turn_log + admin_data 세션 캐시
    turn_log: list[dict] = []
    admin_cache: dict = {}
    refund_tool = _make_refund_tool(turn_log, admin_cache, session_id)

    # AgentCore Memory 세션 생성 (실패 시 None → memory 없이 동작)
    mem = create_memory_session(session_id=session_id, actor_id=actor_id)

    guardrail_kwargs = _load_guardrail_kwargs()
    model = BedrockModel(model_id=model_id, region_name=region, **guardrail_kwargs)
    agent = Agent(
        model=model,
        tools=[refund_tool, handoff_to_human],
        system_prompt=SYSTEM_PROMPT,
        # conversation_manager 미지정 — Strands 기본 (in-process messages).
        # 세션 간 persistence 는 AgentCore Memory 가 담당.
    )

    # legacy turn_log 로깅 훅 — ts 필드 증가 시퀀스로 할당
    def _log_user(text: str) -> None:
        turn_log.append({"role": "user", "text": text, "ts": len(turn_log) + 1})

    def _log_assistant(text: str) -> None:
        turn_log.append({"role": "manager", "text": text, "ts": len(turn_log) + 1})

    def _handle_turn(user_text: str) -> str:
        """한 턴을 완전히 처리 — Memory save/retrieve + legacy log + Agent 호출.

        1. user_text 를 AgentCore Memory + legacy turn_log 에 저장
        2. Memory 에서 context 꺼내 system_prompt 에 주입
        3. Agent 호출 (내부적으로 run_refund_workflow tool 호출)
        4. tool result 에서 intent 추출 → agent.last_intent 저장 (caller 가 gate 판단용)
        5. assistant 답변을 Memory + legacy turn_log 에 저장
        """
        save_turn(mem, "user", user_text)
        _log_user(user_text)

        base_prompt = SYSTEM_PROMPT
        memory_context = get_context_for_prompt(mem, user_text)
        if memory_context:
            agent.system_prompt = (
                base_prompt
                + "\n\n# 이전 대화 맥락 (AgentCore Memory)\n"
                + memory_context
            )
        else:
            agent.system_prompt = base_prompt

        result = agent(user_text)
        answer = str(result)

        # tool result 에서 가장 최근 intent 추출 (legacy 재사용 = 중복 LLM 호출 없음)
        last_intent = ""
        is_refund_domain = False
        for m in agent.messages:
            content = m.get("content", [])
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or "toolResult" not in b:
                    continue
                for c in b["toolResult"].get("content", []):
                    if isinstance(c, dict) and "text" in c:
                        try:
                            j = json.loads(c["text"])
                        except Exception:
                            continue
                        if j.get("intent"):
                            last_intent = j["intent"]
                        if "is_refund_domain" in j:
                            is_refund_domain = bool(j["is_refund_domain"])
        agent.last_intent = last_intent  # type: ignore[attr-defined]
        agent.last_is_refund_domain = is_refund_domain  # type: ignore[attr-defined]

        save_turn(mem, "assistant", answer[:2000])
        _log_assistant(answer)
        return answer

    def _should_respond() -> bool:
        """마지막 handle_turn 결과가 환불 도메인인지 — caller 의 draft skip 판단용.

        True 면 draft 저장/답장, False 면 skip (무응답).
        """
        return bool(getattr(agent, "last_is_refund_domain", False))

    agent.log_user_turn = _log_user  # type: ignore[attr-defined]
    agent.log_assistant_turn = _log_assistant  # type: ignore[attr-defined]
    agent.handle_turn = _handle_turn  # type: ignore[attr-defined]
    agent.should_respond = _should_respond  # type: ignore[attr-defined]
    agent.turn_log = turn_log  # type: ignore[attr-defined]
    agent.admin_cache = admin_cache  # type: ignore[attr-defined]
    agent.memory = mem  # type: ignore[attr-defined]
    agent.last_intent = ""  # type: ignore[attr-defined]
    agent.last_is_refund_domain = False  # type: ignore[attr-defined]
    return agent


# ─────────────────────────────────────────────────────────────
# Session 관리
# ─────────────────────────────────────────────────────────────

_SESSION_AGENTS: dict[str, Agent] = {}


def get_agent_for_session(session_id: str) -> Agent:
    """Session별 wrapper agent 인스턴스 반환.

    같은 session_id → 같은 agent → 같은 turn_log closure → legacy에 이전 턴 주입.
    """
    if session_id not in _SESSION_AGENTS:
        _SESSION_AGENTS[session_id] = create_wrapper_agent(session_id=session_id)
    return _SESSION_AGENTS[session_id]


def clear_session(session_id: str) -> None:
    _SESSION_AGENTS.pop(session_id, None)


def clear_all_sessions() -> None:
    _SESSION_AGENTS.clear()
