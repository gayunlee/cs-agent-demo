"""Wrapper Strands Agent — 검증된 legacy workflow를 tool로 감싸서 노출.

설계 (2026-04-05 Gayoon 확정):
- 최종 아키텍처 = 단일 Strands Agent + 평탄한 tools + SlidingWindow 멀티턴
- 기존 `src/agents/consultant.py`는 Phase 3 그대로 + 검증 안 됨 → 사용 안 함
- 이 wrapper는 **검증된 legacy `RefundAgentV2.process()`** 를 `@tool` 로 감싸고,
  Strands Agent가 top 레이어로 앉아 멀티턴/Memory/AgentCore 훅을 제공.

장점:
- 기존 workflow.py / RefundAgentV2 / refund_engine.py / templates.py = 0 변경 (회귀 0)
- Strands Agent tool-loop 실제 돌아감 (해커톤 쇼케이스)
- SlidingWindowConversationManager 로 멀티턴 state 자동
- AgentCore Guardrail / Evaluation 훅 지점 확보

Session 관리:
- `get_agent_for_session(session_id)` — chat_id 기반 agent 인스턴스 캐시
- 같은 session이면 같은 agent → SlidingWindow 가 이전 턴 자동 기억
"""
from __future__ import annotations

import json
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel
from strands.agent.conversation_manager import SlidingWindowConversationManager

from src.refund_agent_v2 import RefundAgentV2


MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-west-2"


SYSTEM_PROMPT = """당신은 어스플러스(한국 교육 SaaS)의 CS 상담 에이전트입니다.

환불/해지 문의가 들어오면 `run_refund_workflow` tool을 호출해 처리하세요.
- tool이 template_id, draft_answer, reasoning_path를 반환하면 그대로 답변으로 사용.
- 멀티턴 대화에서는 이전 턴을 참고해 자연스럽게 이어가세요.
- 환불 범위를 벗어난 케이스(시스템 오류, 복합 이슈 등)는 `handoff_to_human`.

톤:
- "안녕하세요 회원님"으로 시작, 정중한 경어체.
- 답변 초안은 tool이 생성한 것을 그대로 사용 (임의 수정 금지).
"""


@tool
def run_refund_workflow(
    user_message: str,
    admin_data_json: str,
    conversation_time: str = "",
    chat_id: str = "",
) -> dict:
    """검증된 환불/해지 워크플로우를 실행합니다.

    유저 메시지와 admin API 조회 결과를 받아서, 환불 정책에 따라
    적절한 답변 템플릿과 변수를 결정합니다. 템플릿 16종 + 환불 금액 계산 포함.

    Args:
        user_message: 유저의 환불/해지 문의 메시지
        admin_data_json: admin API 조회 결과 (products, transactions, usage, refunds 등)의 JSON string
        conversation_time: 대화 시점 ISO 8601 (시점 복원용, 선택)
        chat_id: 대화방 ID (로그용, 선택)

    Returns:
        dict: {
            "template_id": 선택된 답변 템플릿 ID,
            "draft_answer": 완성된 답변 초안 텍스트,
            "reasoning_path": 판단 경로 요약,
            "refund_amount": 계산된 환불 금액 (T2인 경우)
        }
    """
    try:
        admin_data = json.loads(admin_data_json) if admin_data_json else {}
    except (json.JSONDecodeError, ValueError):
        admin_data = {}

    agent = RefundAgentV2(mock=False)
    result = agent.process(
        user_messages=[user_message],
        chat_id=chat_id or "wrapper",
        admin_data=admin_data,
        conversation_time=conversation_time,
        conversation_turns=[],
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


@tool
def handoff_to_human(reason: str) -> dict:
    """환불/해지 범위를 벗어난 문의를 상담사에게 인계합니다.

    시스템 오류, 여러 도메인이 엮인 복합 이슈, 또는 AI가 확신할 수 없는
    케이스에 사용하세요.

    Args:
        reason: 인계 사유 (예: "앱 로그인 오류로 기술지원 필요")

    Returns:
        dict: {"action": "handoff", "reason": reason}
    """
    return {
        "action": "handoff",
        "reason": reason,
        "message": f"해당 문의는 상담사가 직접 확인 후 답변 드리도록 전달드렸습니다. 사유: {reason}",
    }


def create_wrapper_agent(
    model_id: str = MODEL_ID,
    region: str = REGION,
    max_window: int = 20,
) -> Agent:
    """Wrapper Strands Agent 생성.

    Returns:
        Agent: legacy workflow을 tool로 감싸고 SlidingWindow로 멀티턴 관리
    """
    model = BedrockModel(
        model_id=model_id,
        region_name=region,
    )
    return Agent(
        model=model,
        tools=[run_refund_workflow, handoff_to_human],
        system_prompt=SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(window_size=max_window),
    )


# ─────────────────────────────────────────────────────────────
# Session 관리 — 멀티턴 대화 맥락 유지
# 같은 session_id로 호출하면 같은 agent 인스턴스 반환 → SlidingWindow가 이전 턴 자동 기억
# ─────────────────────────────────────────────────────────────

_SESSION_AGENTS: dict[str, Agent] = {}


def get_agent_for_session(session_id: str) -> Agent:
    """Session별 wrapper agent 인스턴스 반환.

    멀티턴 대화에서 같은 session_id로 호출하면 동일 agent → 이전 턴 자동 기억.
    새 session이면 새 agent 생성.
    """
    if session_id not in _SESSION_AGENTS:
        _SESSION_AGENTS[session_id] = create_wrapper_agent()
    return _SESSION_AGENTS[session_id]


def clear_session(session_id: str) -> None:
    """Session 초기화 (테스트용)."""
    _SESSION_AGENTS.pop(session_id, None)


def clear_all_sessions() -> None:
    """전체 session 초기화 (테스트용)."""
    _SESSION_AGENTS.clear()
