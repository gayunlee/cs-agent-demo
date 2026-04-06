"""Wrapper Agent v2 — YAML DiagnoseEngine + 투명 tool 5개.

리팩토링 (2026-04-06):
- RefundAgentV2 블랙박스 → 5개 투명 tool 조합
- workflow.py if/else → YAML 21 체인 (domain/refund_chains.yaml)
- closure monkey-patch → WrapperAgent 클래스 (typed)

3-layer 분리:
- YAML (선언적): 세부 유형 라우팅 + 어떤 tool 필요한지 (tools_required)
- Agent (LLM): YAML diagnose 결과 따라 tool 순서대로 호출
- Code (@tool): API 응답값 기반 세부 계산/분기 (refund_engine, templates)

Tools:
1. diagnose_refund_case  → YAML chain → template_id + tools_required
2. calculate_refund      → refund_engine → 금액 계산
3. compose_template_answer → templates → 답변 텍스트
4. ask_clarification     → 모호 재질문
5. handoff_to_human      → 상담사 인계
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel

from src.memory import AgentMemory, create_memory_session, get_context_for_prompt, save_turn

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-west-2"
GUARDRAIL_ID_PATH = Path(__file__).resolve().parents[2] / "guardrail_id.json"

# 환불 도메인 whitelist — YAML chain 의 on_pass_template 이 T 로 시작하면 도메인 통과
# 나중에 도메인 확장 시 여기만 수정
REFUND_TEMPLATE_PREFIX = "T"


def _load_guardrail_kwargs() -> dict:
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
        logger.warning(f"guardrail 로드 실패: {e}")
        return {}


SYSTEM_PROMPT = """\
당신은 어스플러스(한국 교육 SaaS)의 CS 상담 에이전트입니다.

# 절대 규칙 (반드시 지킬 것)

1. **환불/해지/취소/결제/구독/카드/자동결제/중복결제/상품변경** 키워드가 하나라도 있으면
   **반드시 `diagnose_refund_case` tool 을 첫 번째로 호출**하세요.
   - 절대 먼저 질문하지 마세요.
   - 절대 직접 답변하지 마세요.
   - 절대 "확인해드리겠습니다" 같은 공허한 답만 하지 마세요.

2. **diagnose_refund_case 호출 시 intent 인자**를 반드시 채우세요.
   유저 메시지를 읽고 아래 중 하나를 골라 intent 에 넣으세요:
   - "환불_요청": 환불해주세요, 돈 돌려주세요, 환불 부탁
   - "해지_방법": 해지하고 싶어요, 구독 취소, 구독해지
   - "해지_확인": 해지 처리 됐나요?, 해지 확인
   - "자동결제_불만": 자동결제 됐는데, 왜 결제됐나요
   - "환불_규정_문의": 환불 가능한가요?, 환불 규정이 뭔가요
   - "카드변경": 카드 변경, 결제 수단 변경
   - "상품변경": 다른 상품으로 바꾸고 싶어요
   - "중복결제": 두 번 결제됐어요, 중복 결제
   - "환불지연": 환불 언제 돼요?, 아직 환불 안 됐어요
   - "환불철회": 환불 취소할게요, 다시 이용할래요
   - "예외환불": 건강 사유, 특별한 사정으로 예외 환불
   - "감정폭발": 화나요, 사기, 소비자원 신고
   - "시스템오류": 앱 오류, 로그인 안 돼요
   - "복합이슈": 환불+기술오류, 환불+배송 같이 **다른 도메인**이 섞인 경우만
   - "기타": 위 어느 것에도 해당 안 됨

   ⚠️ **주의 — 복합이슈 오분류 방지**:
   "해지 + 환불", "구독취소 + 환불", "해지하고 환불해주세요" → **환불_요청** (복합이슈 아님!)
   환불 과정에서 해지 언급은 자연스러운 요청. 복합이슈는 환불+기술오류, 환불+배송처럼 **다른 도메인**이 엮인 경우만.

   ⚠️ **후속 턴 (이전에 환불 견적 안내했고 유저가 확정)**:
   "네 진행해주세요", "환불 확정", "진행 부탁" → **환불_요청**
   이전 턴에서 T2 견적을 안내했으면 YAML 이 자동으로 T3(접수완료)로 라우팅합니다.

3. **diagnose_refund_case 결과 처리**:
   - `tools_required` 목록에 있는 tool 을 순서대로 호출하세요.
   - `compose_template_answer` 가 있으면 template_id 와 필요한 variables 를 넘겨 답변 완성.
   - `calculate_refund_amount` 가 있으면 먼저 금액 계산 후 결과를 variables 에 포함.
   - 최종 `compose_template_answer` 결과를 **그대로** 출력. 임의 수정 금지.

4. **모호한 첫 메시지** (도메인 불문):
   "안녕하세요 문의드려요", "문의드립니다" 같이 의도 불분명하면
   tool 호출 없이 **"안녕하세요 회원님, 어스플러스입니다. 어떤 부분을 도와드릴까요?"** 로 직접 답변.

# 예시

## 환불 요청
유저: "환불해주세요 <admin_data>{...}</admin_data>"
✅ diagnose_refund_case(intent="환불_요청") → template_id="T2_환불_규정_금액", tools_required=["calculate_refund_amount","compose_template_answer"]
   → calculate_refund_amount() → 금액 결과
   → compose_template_answer(template_id="T2_환불_규정_금액", slots_json='{"환불금액":"30,000"}')
   → 출력

## 해지 방법 (결제 없음)
유저: "해지부탁드려요"
✅ diagnose_refund_case(intent="해지_방법") → template_id="T1_구독해지_방법_앱", tools_required=["compose_template_answer"]
   → compose_template_answer(template_id="T1_구독해지_방법_앱") → 출력

## 모호
유저: "안녕하세요 문의드려요"
✅ "안녕하세요 회원님, 어스플러스입니다. 어떤 부분을 도와드릴까요?" (tool 호출 없이)
"""


# ─────────────────────────────────────────────────────────────
# Context builder — admin_data → DiagnoseEngine 이 기대하는 dict
# ─────────────────────────────────────────────────────────────


def build_engine_context(
    user_text: str,
    admin_data: dict,
    conversation_turns: list[dict] | None = None,
    conversation_time: str = "",
) -> dict:
    """admin_data 를 DiagnoseEngine/tool 이 기대하는 context dict 로 변환."""
    products = admin_data.get("products") or []
    transactions = admin_data.get("transactions") or []
    usage = admin_data.get("usage") or {}
    memberships = admin_data.get("memberships") or []
    refunds = admin_data.get("refunds") or []

    active_products = [p for p in products if p.get("status") == "active"]
    success_txs = [t for t in transactions if t.get("state") == "purchased_success"]
    refund_txs = [t for t in transactions if t.get("state") == "purchased_refund"]

    latest_refunded = False
    if success_txs:
        latest_round = success_txs[-1].get("round", 0)
        latest_amount = success_txs[-1].get("amount", 0)
        latest_refunded = any(
            (t.get("round") == latest_round or t.get("amount") == latest_amount)
            for t in refund_txs
        )

    turns = conversation_turns or []
    # prev_had_t2: 이전 매니저 턴에 환불 규정/금액 키워드가 있었는지
    prev_mgr_texts = [
        (t.get("text") or "").lower()
        for t in turns
        if t.get("role") == "manager"
    ]
    prev_had_t2 = any(
        kw in m for m in prev_mgr_texts
        for kw in ["환불 규정", "7일 이내 구독권", "환불금", "환불 금액"]
    )

    # DSL 표현식이 `ctx.field` 형태로 접근하므로 "ctx" sub-dict 필수.
    # `user_text` 는 최상위 (has_keyword(user_text, ...) 패턴).
    return {
        "user_text": user_text,
        "ctx": {
            "products": products,
            "active_products": active_products,
            "transactions": transactions,
            "success_txs": success_txs,
            "refund_txs": refund_txs,
            "latest_refunded": latest_refunded,
            "has_accessed": usage.get("accessed", False),
            "us_user_id": admin_data.get("us_user_id", ""),
            "memberships": memberships,
            "refunds": refunds,
            "conversation_turns": turns,
            "conversation_time": conversation_time,
            "prev_had_t2": prev_had_t2,
        },
        # calculate_refund_amount tool 도 ctx 에서 직접 읽음
        "success_txs": success_txs,
        "products": products,
        "conversation_time": conversation_time,
        "has_accessed": usage.get("accessed", False),
    }


# ─────────────────────────────────────────────────────────────
# WrapperAgent 클래스
# ─────────────────────────────────────────────────────────────


class WrapperAgent:
    """Strands Agent wrapper — YAML 라우팅 + 투명 tool 5개 + AgentCore Memory."""

    def __init__(
        self,
        session_id: str,
        actor_id: str = "cs_agent_default",
        model_id: str = MODEL_ID,
        region: str = REGION,
    ):
        self.session_id = session_id
        self.turn_log: list[dict] = []
        self.admin_cache: dict = {}
        self.last_intent: str = ""
        self.last_template_id: str = ""
        self.last_is_refund_domain: bool = False

        # AgentCore Memory 세션
        self.memory: AgentMemory | None = create_memory_session(
            session_id=session_id, actor_id=actor_id
        )

        # Strands Agent + tools
        guardrail_kwargs = _load_guardrail_kwargs()
        model = BedrockModel(model_id=model_id, region_name=region, **guardrail_kwargs)
        tools = self._make_tools()
        self._agent = Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)

    def _make_tools(self) -> list:
        """Session 별 tool 생성 (self 캡처 closure)."""
        # 기존 workflow_tools 의 3 tool 을 import + context 연동
        from src.tools.workflow_tools import (
            set_context,
            diagnose_refund_case,
            calculate_refund_amount,
            compose_template_answer,
        )

        # context setter 를 handle_turn 에서 호출하므로 tool 자체는 그대로 사용
        self._set_tool_context = set_context

        @tool
        def ask_clarification(reason: str = "") -> dict:
            """유저 첫 메시지가 모호할 때 정중한 오픈 재질문을 생성합니다.

            Args:
                reason: 재질문 사유 (예: "의도 불분명", "상품 특정 필요")
            """
            return {
                "draft_answer": (
                    "안녕하세요 회원님, 어스플러스입니다. 문의 주셔서 감사합니다.\n"
                    "어떤 부분을 도와드릴까요? 편하게 말씀 주시면 안내 도와드리겠습니다."
                ),
                "is_clarification": True,
                "reason": reason,
            }

        @tool
        def handoff_to_human(reason: str) -> dict:
            """환불/해지 범위를 벗어난 문의를 상담사에게 인계합니다.

            Args:
                reason: 인계 사유
            """
            return {
                "action": "handoff",
                "reason": reason,
                "message": f"해당 문의는 상담사가 직접 확인 후 답변 드리겠습니다. 사유: {reason}",
            }

        return [
            diagnose_refund_case,
            calculate_refund_amount,
            compose_template_answer,
            ask_clarification,
            handoff_to_human,
        ]

    # ─── Public API ─────────────────────────────────

    def handle_turn(self, user_text: str) -> str:
        """한 턴을 완전히 처리.

        1. Memory + turn_log 에 user 저장
        2. admin_data 파싱 + cache 업데이트
        3. tool context 세팅 (DiagnoseEngine 용)
        4. Memory context → system_prompt 보강
        5. Agent 호출 (tool-loop)
        6. 결과 추출 (template_id, intent, should_respond)
        7. Memory + turn_log 에 assistant 저장
        """
        # 1. user turn 기록
        save_turn(self.memory, "user", user_text)
        self._log_turn("user", user_text)

        # 2. admin_data 파싱
        admin_json = self._extract_tag(user_text, "admin_data")
        conv_time = self._extract_tag(user_text, "conversation_time")
        if admin_json:
            try:
                parsed = json.loads(admin_json)
                self.admin_cache.clear()
                self.admin_cache.update(parsed)
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. tool context 세팅
        plain_text = self._strip_tags(user_text)
        ctx = build_engine_context(
            user_text=plain_text,
            admin_data=self.admin_cache,
            conversation_turns=self.turn_log,
            conversation_time=conv_time,
        )
        self._set_tool_context(ctx)

        # 4. Memory context → system_prompt 보강
        memory_ctx = get_context_for_prompt(self.memory, plain_text)
        if memory_ctx:
            self._agent.system_prompt = (
                SYSTEM_PROMPT + "\n\n# 이전 대화 맥락\n" + memory_ctx
            )
        else:
            self._agent.system_prompt = SYSTEM_PROMPT

        # 5. Agent 호출
        result = self._agent(user_text)
        answer = str(result)

        # 6. 결과 추출
        self._extract_agent_results()

        # 7. assistant turn 기록
        save_turn(self.memory, "assistant", answer[:2000])
        self._log_turn("manager", answer)
        return answer

    def should_respond(self) -> bool:
        """draft 전달 여부 판단.

        True:
        - 환불 도메인 (template 이 T 로 시작)
        - 모호 재질문 (tool 미호출, agent 직접 답변)
        False:
        - handoff 또는 비도메인 skip
        """
        if self.last_is_refund_domain:
            return True
        # tool 미호출 = clarification
        has_tool_call = any(
            isinstance(b, dict) and "toolUse" in b
            for m in self._agent.messages
            for b in (m.get("content") or [])
            if isinstance(b, dict)
        )
        if not has_tool_call:
            return True
        return False

    @property
    def messages(self):
        """Strands agent.messages 접근 (테스트/스모크용)."""
        return self._agent.messages

    # ─── Internal helpers ───────────────────────────

    def _log_turn(self, role: str, text: str) -> None:
        self.turn_log.append({
            "role": role,
            "text": text,
            "ts": len(self.turn_log) + 1,
        })

    def _extract_tag(self, text: str, tag: str) -> str:
        """<tag>...</tag> 에서 내용 추출."""
        start = f"<{tag}>"
        end = f"</{tag}>"
        i = text.find(start)
        if i < 0:
            return ""
        j = text.find(end, i + len(start))
        if j < 0:
            return ""
        return text[i + len(start):j].strip()

    def _strip_tags(self, text: str) -> str:
        """<admin_data>...</admin_data> 등 태그 블록 제거 → 순수 유저 텍스트."""
        import re
        return re.sub(r"<\w+>.*?</\w+>", "", text, flags=re.DOTALL).strip()

    def _extract_agent_results(self) -> None:
        """agent.messages 에서 마지막 tool result 파싱 → self 속성 업데이트."""
        self.last_template_id = ""
        self.last_intent = ""
        self.last_is_refund_domain = False

        for m in self._agent.messages:
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
                        tid = j.get("template_id", "")
                        if tid:
                            self.last_template_id = tid
                            self.last_is_refund_domain = tid.startswith(
                                REFUND_TEMPLATE_PREFIX
                            )
                        if j.get("matched_chain"):
                            self.last_intent = j["matched_chain"]


# ─────────────────────────────────────────────────────────────
# Session 관리
# ─────────────────────────────────────────────────────────────

_SESSIONS: dict[str, WrapperAgent] = {}


def get_agent_for_session(session_id: str) -> WrapperAgent:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = WrapperAgent(session_id=session_id)
    return _SESSIONS[session_id]


def clear_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def clear_all_sessions() -> None:
    _SESSIONS.clear()
