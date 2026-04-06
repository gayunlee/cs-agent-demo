"""CS Agent — Strands Agent + 6 tools + AgentCore Memory.

리팩토링 완료 (2026-04-06):
- WrapperAgent 껍데기 제거. 순수 Strands Agent + tools.
- Agent 가 lookup_admin_data 로 직접 admin API 호출 → <admin_data> 태그 불필요.
- YAML DiagnoseEngine 으로 라우팅 → tools_required 따라 tool 순차 호출.
- Memory 는 AgentCore Memory (세션 간 persistence).

Tools (6개):
1. lookup_admin_data      — admin API 조회 (유저 정보/결제/상품)
2. diagnose_refund_case   — YAML chain → template_id + tools_required
3. calculate_refund_amount — refund_engine 환불금 계산
4. compose_template_answer — templates 답변 완성
5. ask_clarification      — 모호 재질문
6. handoff_to_human       — 상담사 인계
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

REFUND_TEMPLATE_PREFIX = "T"


def _load_guardrail_kwargs() -> dict:
    if not GUARDRAIL_ID_PATH.exists():
        return {}
    try:
        with open(GUARDRAIL_ID_PATH) as f:
            gd = json.load(f)
        return {"guardrail_id": gd["guardrailId"], "guardrail_version": str(gd["version"])}
    except Exception as e:
        logger.warning(f"guardrail 로드 실패: {e}")
        return {}


SYSTEM_PROMPT = """\
당신은 어스플러스(한국 교육 SaaS)의 CS 상담 에이전트입니다.

# 절대 규칙

1. **환불/해지/취소/결제/구독/카드/자동결제/중복결제/상품변경** 관련 메시지:
   - 유저 정보가 없으면 먼저 `lookup_admin_data` 로 조회하세요.
     (채널톡에서 전화번호가 오면 phone, user_id 가 오면 user_id 로)
   - 그 다음 `diagnose_refund_case` 호출. intent 인자에 아래 enum 중 하나:
     환불_요청, 해지_방법, 해지_확인, 자동결제_불만, 환불_규정_문의,
     카드변경, 상품변경, 중복결제, 환불지연, 환불철회, 예외환불,
     감정폭발, 시스템오류, 복합이슈, 기타
   - diagnose 결과의 `tools_required` 에 있는 tool 순서대로 호출.
   - `compose_template_answer` 결과를 **그대로** 출력. 임의 수정 금지.

2. **모호한 첫 메시지** ("안녕하세요 문의드려요", "문의드립니다"):
   tool 호출 없이 "안녕하세요 회원님, 어떤 부분을 도와드릴까요?" 직접 답변.

3. **이미 유저 정보가 이전 턴에서 조회됨**:
   lookup_admin_data 재호출 불필요. diagnose 부터 시작.

⚠️ "해지+환불" = 환불_요청 (복합이슈 아님). 환불 과정에서 해지 언급은 자연스러운 요청.
⚠️ 후속 턴 "네 진행해주세요" = 환불_요청 (YAML 이 T3 접수완료로 라우팅).
⚠️ 복합이슈 = 환불+기술오류, 환불+배송 같이 **다른 도메인** 엮인 경우만.
"""


# ─────────────────────────────────────────────────────────────
# Context builder — admin_data → DiagnoseEngine context
# ─────────────────────────────────────────────────────────────

def build_engine_context(user_text: str, admin_data: dict, turn_log: list[dict], conversation_time: str = "") -> dict:
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

    prev_mgr_texts = [(t.get("text") or "").lower() for t in turn_log if t.get("role") == "manager"]
    prev_had_t2 = any(
        kw in m for m in prev_mgr_texts
        for kw in ["환불 규정", "7일 이내 구독권", "환불금", "환불 금액"]
    )

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
            "conversation_time": conversation_time,
            "prev_had_t2": prev_had_t2,
        },
        "success_txs": success_txs,
        "products": products,
        "conversation_time": conversation_time,
        "has_accessed": usage.get("accessed", False),
    }


# ─────────────────────────────────────────────────────────────
# Tool: lookup_admin_data (신규 — agent 가 직접 admin API 호출)
# ─────────────────────────────────────────────────────────────

@tool
def lookup_admin_data(phone: str = "", user_id: str = "") -> dict:
    """유저의 관리자센터 데이터를 조회합니다.

    채널톡에서 전화번호(phone) 또는 user_id 를 받아 관리자센터 API 로
    상품/결제/열람/멤버십/환불 정보를 가져옵니다.

    Args:
        phone: 유저 전화번호 (01012345678 형태)
        user_id: 관리자센터 us_user_id (24자 hex)

    Returns:
        dict: {us_user_id, ch_name, phone, products, transactions, usage, memberships, refunds}
    """
    from src.admin_api import AdminAPIClient

    client = AdminAPIClient()
    resolved_id = user_id

    if not resolved_id and phone:
        resolved_id = client.search_user_by_phone(phone)

    if not resolved_id:
        return {"error": "유저 식별 불가. 성함/휴대전화 번호를 확인해주세요."}

    try:
        result = client.lookup_all(resolved_id)
        admin_data = {
            "us_user_id": resolved_id,
            "ch_name": getattr(result, "name", ""),
            "phone": phone,
            "products": getattr(result, "products", []),
            "transactions": getattr(result, "transactions", []),
            "usage": getattr(result, "usage", {}),
            "memberships": getattr(result, "memberships", []),
            "refunds": getattr(result, "refunds", []),
        }
        # diagnose/calculate tool 이 참조할 context 자동 업데이트
        from src.tools.workflow_tools import set_context as _set_ctx
        _set_ctx(build_engine_context(user_text="", admin_data=admin_data, turn_log=[]))
        return admin_data
    except Exception as e:
        return {"error": f"admin API 조회 실패: {e}"}


# ─────────────────────────────────────────────────────────────
# Tools: ask_clarification + handoff_to_human
# ─────────────────────────────────────────────────────────────

@tool
def ask_clarification(reason: str = "") -> dict:
    """유저 첫 메시지가 모호할 때 정중한 오픈 재질문을 생성합니다."""
    return {
        "draft_answer": "안녕하세요 회원님, 어스플러스입니다. 문의 주셔서 감사합니다.\n어떤 부분을 도와드릴까요? 편하게 말씀 주시면 안내 도와드리겠습니다.",
        "is_clarification": True,
        "reason": reason,
    }


@tool
def handoff_to_human(reason: str) -> dict:
    """환불/해지 범위를 벗어난 문의를 상담사에게 인계합니다."""
    return {
        "action": "handoff",
        "reason": reason,
        "message": f"해당 문의는 상담사가 직접 확인 후 답변 드리겠습니다. 사유: {reason}",
    }


# ─────────────────────────────────────────────────────────────
# Agent 생성 + 세션 관리
# ─────────────────────────────────────────────────────────────

# workflow_tools 의 기존 3 tool import
from src.tools.workflow_tools import (
    set_context as _set_tool_context,
    diagnose_refund_case,
    calculate_refund_amount,
    compose_template_answer,
)

ALL_TOOLS = [
    lookup_admin_data,
    diagnose_refund_case,
    calculate_refund_amount,
    compose_template_answer,
    ask_clarification,
    handoff_to_human,
]


def create_agent(session_id: str, actor_id: str = "cs_agent_default") -> Agent:
    """Strands Agent 생성 + AgentCore Memory 연결."""
    guardrail_kwargs = _load_guardrail_kwargs()
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION, **guardrail_kwargs)

    mem = create_memory_session(session_id=session_id, actor_id=actor_id)

    agent = Agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)

    # 세션 상태 (Agent 인스턴스에 attach)
    agent.memory = mem  # type: ignore[attr-defined]
    agent.turn_log = []  # type: ignore[attr-defined]
    agent.last_template_id = ""  # type: ignore[attr-defined]
    agent.last_intent = ""  # type: ignore[attr-defined]
    agent.last_is_refund_domain = False  # type: ignore[attr-defined]
    agent.session_id = session_id  # type: ignore[attr-defined]

    # 기존 caller 호환 — agent.handle_turn(msg), agent.should_respond()
    agent.handle_turn = lambda msg: handle_turn(agent, msg)  # type: ignore[attr-defined]
    agent.should_respond = lambda: should_respond(agent)  # type: ignore[attr-defined]

    return agent


def handle_turn(agent: Agent, user_text: str) -> str:
    """한 턴 처리 — Memory save + context setup + Agent 호출 + 결과 추출.

    WrapperAgent 클래스 대신 순수 함수. Agent 인스턴스에 attach 된 상태만 사용.
    """
    mem = getattr(agent, "memory", None)
    turn_log = getattr(agent, "turn_log", [])

    # 1. user turn 저장
    save_turn(mem, "user", user_text)
    turn_log.append({"role": "user", "text": user_text, "ts": len(turn_log) + 1})

    # 2. context for workflow_tools (diagnose/calculate 용)
    # admin_data 는 agent 가 lookup tool 로 조회 → tool 내부에서 _current_context 에 세팅
    # 여기서는 빈 context 세팅 (lookup 전이므로)
    _set_tool_context({
        "user_text": user_text,
        "ctx": {"us_user_id": "", "products": [], "success_txs": [], "refund_txs": [],
                "active_products": [], "latest_refunded": False, "has_accessed": False,
                "memberships": [], "refunds": [], "conversation_time": "", "prev_had_t2": False},
        "success_txs": [], "products": [], "conversation_time": "", "has_accessed": False,
    })

    # 3. Memory context → system_prompt 보강
    memory_ctx = get_context_for_prompt(mem, user_text)
    if memory_ctx:
        agent.system_prompt = SYSTEM_PROMPT + "\n\n# 이전 대화 맥락\n" + memory_ctx
    else:
        agent.system_prompt = SYSTEM_PROMPT

    # 4. Agent 호출 (tool-loop: lookup → diagnose → calculate → compose)
    result = agent(user_text)
    answer = str(result)

    # 5. 결과 추출
    _extract_results(agent)

    # 6. assistant turn 저장
    save_turn(mem, "assistant", answer[:2000])
    turn_log.append({"role": "manager", "text": answer, "ts": len(turn_log) + 1})

    return answer


def should_respond(agent: Agent) -> bool:
    """draft 전달 여부."""
    if getattr(agent, "last_is_refund_domain", False):
        return True
    # tool 미호출 = clarification
    has_tool = any(
        isinstance(b, dict) and "toolUse" in b
        for m in agent.messages for b in (m.get("content") or [])
        if isinstance(b, dict)
    )
    return not has_tool


def _extract_results(agent: Agent) -> None:
    """agent.messages 에서 마지막 tool result 파싱."""
    agent.last_template_id = ""  # type: ignore
    agent.last_intent = ""  # type: ignore
    agent.last_is_refund_domain = False  # type: ignore

    for m in agent.messages:
        for b in (m.get("content") or []):
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
                        agent.last_template_id = tid  # type: ignore
                        agent.last_is_refund_domain = tid.startswith(REFUND_TEMPLATE_PREFIX)  # type: ignore
                    if j.get("matched_chain"):
                        agent.last_intent = j["matched_chain"]  # type: ignore


def update_tool_context(agent: Agent, admin_data: dict, user_text: str = "") -> None:
    """lookup_admin_data 결과로 tool context 업데이트. 외부 caller 용."""
    turn_log = getattr(agent, "turn_log", [])
    ctx = build_engine_context(user_text, admin_data, turn_log)
    _set_tool_context(ctx)


# ─────────────────────────────────────────────────────────────
# 세션 관리 — Agent 인스턴스 캐시
# ─────────────────────────────────────────────────────────────

_SESSIONS: dict[str, Agent] = {}


def get_agent_for_session(session_id: str) -> Agent:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = create_agent(session_id)
    return _SESSIONS[session_id]


def clear_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def clear_all_sessions() -> None:
    _SESSIONS.clear()
