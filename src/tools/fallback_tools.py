"""폴백 tool들 — LLM 자유 응답 + 상담사 인계.

- llm_freeform_answer: 템플릿이 매칭되지 않는 edge case용 LLM 자유 생성.
  기존 src/llm_fallback.py에서 이식하되, 정책 텍스트는 프롬프트에서 제거.
- handoff_to_human: 에이전트가 대응할 수 없다고 판단 시 상담사 인계.
"""
from __future__ import annotations
from strands import tool

from src.tools.workflow_tools import get_context


@tool
def llm_freeform_answer(situation_summary: str) -> str:
    """템플릿으로 답변 불가한 edge case에 대해 자유형 답변 초안을 생성합니다.

    ⚠️ 정책 텍스트는 프롬프트에 없음. LLM은 "상황에 맞는 안내"만 생성하고,
    구체 정책은 현재 context에서 참조되는 tool 결과를 통해 전달됨.

    Args:
        situation_summary: 현재 상황 요약 (LLM이 작성, 예: "환불 철회 요청 후 재가입 문의")

    Returns:
        str: 자유형 답변 초안 + 확신도 태그
    """
    # 현재는 간단 버전. 실제 LLM 호출은 상위 Strands Agent의 model에서 이미 일어남.
    # 이 tool은 "현재 context + 상황 요약을 LLM에게 한 번 더 자유 생성 요청"
    # Phase 3에서 상담 Agent system prompt에 "이 tool 호출 시 공감 + 다음 단계 안내" 가이드 포함.

    prefix = "⚠️ 정책 밖 패턴 — 상담사 확인 필요\n\n"
    body = (
        f"회원님, 문의 주신 내용 ({situation_summary}) 확인했습니다. "
        "담당자가 상세 확인 후 답변 드리도록 하겠습니다. "
        "잠시만 기다려 주시면 감사하겠습니다."
    )
    return prefix + body


@tool
def handoff_to_human(reason: str) -> str:
    """에이전트가 처리할 수 없는 문의를 상담사에게 인계합니다.

    Args:
        reason: 인계 사유 (예: "환불 규정 예외 요청", "법적 분쟁 문의")

    Returns:
        str: 인계 알림 메시지
    """
    return (
        "회원님, 문의 주신 내용은 담당 매니저가 직접 확인이 필요한 사항입니다.\n"
        f"(사유: {reason})\n"
        "담당자가 곧 안내 드릴 예정이니 조금만 기다려 주시면 감사하겠습니다."
    )
