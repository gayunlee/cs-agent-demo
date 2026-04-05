"""대화 관리 tool들 — Rasa CALM의 conversation patterns 영감.

각 tool은 LLM이 현재 상황을 보고 선택 호출. 결정론적 템플릿이 맞지 않는 경우에만.
"""
from __future__ import annotations
from strands import tool


@tool
def ask_clarification(missing_info: str, context_hint: str = "") -> str:
    """정보가 부족할 때 유저에게 자연어로 재질문합니다.

    Args:
        missing_info: 필요한 정보 (예: "본인 성함과 연락처", "환불 요청 상품명")
        context_hint: 이전 대화 맥락 요약 (자연스러운 연결을 위해)

    Returns:
        str: 유저에게 보낼 재질문 문구
    """
    # 간단한 템플릿. LLM이 호출할 때 missing_info를 구체적으로 전달하면 자연스러움.
    base = (
        "회원님, 정확히 확인드리기 위해 추가 정보가 필요합니다.\n"
        f"{missing_info}을(를) 말씀해 주시면 바로 확인 도와드리겠습니다."
    )
    return base


@tool
def handle_off_topic(topic_summary: str = "") -> str:
    """유저 메시지가 환불/해지 상담 범위를 벗어날 때 정중히 안내합니다.

    Args:
        topic_summary: 벗어난 주제 요약 (예: "투자 조언 요청", "일반 문의")

    Returns:
        str: 범위 밖임을 안내하는 답변
    """
    return (
        "안녕하세요 회원님, 문의 주셔서 감사합니다.\n"
        "현재 채널에서는 환불/해지/결제 관련 상담을 도와드리고 있습니다.\n"
        "다른 문의사항은 어스플러스 고객센터(1:1 문의)로 연락 주시면 정확히 확인해 드리겠습니다."
    )


@tool
def handle_emotional_distress(emotion_type: str = "frustration") -> str:
    """유저가 불만/좌절/분노를 표현할 때 공감 표현을 먼저 합니다.

    Args:
        emotion_type: 'frustration' | 'disappointment' | 'anger' | 'confusion'

    Returns:
        str: 공감 표현 (이후 정책 안내가 이어져야 함 — 이 tool은 opener만 생성)
    """
    openers = {
        "frustration": "회원님, 답답한 상황에 기다리게 해드려 정말 죄송합니다.",
        "disappointment": "회원님, 기대에 미치지 못해 실망감을 드린 점 진심으로 사과드립니다.",
        "anger": "회원님, 불편을 드려 대단히 죄송합니다. 차분히 확인해 드리겠습니다.",
        "confusion": "회원님, 혼란스러우실 텐데 하나씩 차근히 안내드리겠습니다.",
    }
    return openers.get(emotion_type, openers["frustration"])


@tool
def handle_cancellation_of_flow(reason: str = "") -> str:
    """유저가 진행 중이던 요청(예: 환불)을 철회한다고 할 때.

    Args:
        reason: 철회 사유 (유저 메시지에서 추출, 선택)

    Returns:
        str: 철회 확인 답변
    """
    return (
        "네 회원님, 요청 철회 확인했습니다.\n"
        "추가로 필요하신 사항 있으시면 언제든 말씀해 주세요."
    )
