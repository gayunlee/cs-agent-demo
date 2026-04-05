"""Wrapper Strands Agent — 검증된 legacy workflow를 tool로 감싸서 노출.

설계 (2026-04-05 Gayoon 확정, 2026-04-06 multi-turn 보강):
- 단일 Strands Agent + 평탄한 tools + SlidingWindow 멀티턴
- `src/agents/consultant.py`는 Phase 3 그대로 + 검증 안 됨 → 사용 안 함
- 이 wrapper는 **검증된 legacy `RefundAgentV2.process()`** 를 `@tool` 로 감싸고,
  Strands Agent가 top 레이어로 앉아 멀티턴/Memory/AgentCore 훅을 제공.

Multi-turn 맥락 전달 (2026-04-06 수정):
- Session별 Agent 인스턴스마다 `turn_log` closure 보관
- @tool 호출 시 closure에서 snapshot → legacy의 `conversation_turns` 로 주입
- 이렇게 하면 legacy intent classifier가 "네 진행해주세요" 같은 후속 턴에서
  이전 맥락을 보고 올바른 템플릿(T3)으로 라우팅 가능
- caller(smoke script / dashboard)는 `agent.log_user_turn()` / `agent.log_assistant_turn()`
  으로 session turn_log 를 업데이트해야 함

장점:
- 기존 workflow.py / RefundAgentV2 / refund_engine.py / templates.py = 0 변경 (회귀 0)
- Strands Agent tool-loop 실제 돌아감 (해커톤 쇼케이스)
- SlidingWindowConversationManager 로 LLM-level memory 자동
- turn_log closure 로 legacy-level 맥락도 전달됨
- AgentCore Guardrail / Evaluation 훅 지점 확보
"""
from __future__ import annotations

import json
from typing import Callable

from strands import Agent, tool
from strands.models import BedrockModel
from strands.agent.conversation_manager import SlidingWindowConversationManager

from src.refund_agent_v2 import RefundAgentV2


MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-west-2"


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
        }

    return run_refund_workflow


def create_wrapper_agent(
    session_id: str = "default",
    model_id: str = MODEL_ID,
    region: str = REGION,
    max_window: int = 20,
) -> Agent:
    """Wrapper Strands Agent 생성 (session 별 turn_log closure 포함).

    Returns:
        Agent: legacy workflow을 tool로 감싸고 SlidingWindow로 LLM-level 멀티턴 관리.
               `agent.log_user_turn(text)` / `agent.log_assistant_turn(text)` 로
               legacy-level 맥락 전달용 turn_log 업데이트.
    """
    turn_log: list[dict] = []
    admin_cache: dict = {}
    refund_tool = _make_refund_tool(turn_log, admin_cache, session_id)

    model = BedrockModel(model_id=model_id, region_name=region)
    agent = Agent(
        model=model,
        tools=[refund_tool, handoff_to_human],
        system_prompt=SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(window_size=max_window),
    )

    # closure 로깅 훅 노출 — caller 가 agent(msg) 호출 전후로 사용
    # ts 필드 필수: workflow.py 가 last_user_ts 기준으로 prev_mgr 필터링함
    # (증가 시퀀스로 할당해 turn 순서 보존)
    def _log_user(text: str) -> None:
        turn_log.append({"role": "user", "text": text, "ts": len(turn_log) + 1})

    def _log_assistant(text: str) -> None:
        turn_log.append({"role": "manager", "text": text, "ts": len(turn_log) + 1})

    agent.log_user_turn = _log_user  # type: ignore[attr-defined]
    agent.log_assistant_turn = _log_assistant  # type: ignore[attr-defined]
    agent.turn_log = turn_log  # type: ignore[attr-defined]
    agent.admin_cache = admin_cache  # type: ignore[attr-defined]
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
