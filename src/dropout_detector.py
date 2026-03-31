"""워크플로우 봇 이탈 패턴 감지

데이터 탐색 결과 (10,396건 분석):
- Type A: 봇 메뉴만 클릭, 텍스트 없음 (1,389건, 13.4%) — 50.4%가 환불/해지 의도
- Type B: 10자 이내 trivial 텍스트 (142건, 1.4%)
- Type C: 환불 버튼 + 상세 미입력 (355건, 3.4%)
- Type D: 봇만 응대, 매니저 개입 없음 (90건, 0.9%) — 방치 케이스
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.webhook_handler import ParsedEvent


class DropoutType(Enum):
    MENU_ONLY = "menu_only"          # Type A: 봇 메뉴만 클릭
    TRIVIAL_TEXT = "trivial_text"     # Type B: 이름/인사만 입력
    REFUND_NO_DETAIL = "refund_no_detail"  # Type C: 환불 버튼 + 상세 미입력
    BOT_ABANDONED = "bot_abandoned"   # Type D: 봇만 응대, 방치


REFUND_KEYWORDS = ["환불", "취소", "결제취소", "돈", "카드취소"]
TRIVIAL_PATTERNS = ["안녕", "네", "ㅇㅇ", "ㅎㅇ", ".", ".."]

# 유형별 추가 질문
FOLLOWUP_MESSAGES = {
    DropoutType.MENU_ONLY: (
        "안녕하세요, 어스입니다. "
        "메뉴를 선택해 주셨는데, 어떤 도움이 필요하신가요? "
        "아래 중 해당하시는 내용을 말씀해 주시면 바로 안내드리겠습니다.\n\n"
        "1️⃣ 환불/결제 문의\n"
        "2️⃣ 구독 해지/변경\n"
        "3️⃣ 수강/강의 관련\n"
        "4️⃣ 로그인/접속 오류\n"
        "5️⃣ 기타 문의"
    ),
    DropoutType.TRIVIAL_TEXT: (
        "안녕하세요, 어스입니다. "
        "어떤 내용으로 문의하셨는지 말씀해 주시면 바로 안내드리겠습니다."
    ),
    DropoutType.REFUND_NO_DETAIL: (
        "안녕하세요, 어스입니다. "
        "환불 관련 문의로 확인됩니다. "
        "정확한 안내를 위해 아래 내용을 알려주시면 빠르게 도움드리겠습니다.\n\n"
        "• 환불 희망하시는 상품명 (마스터명)\n"
        "• 환불 사유\n\n"
        "또는 가입하신 연락처를 알려주시면 바로 확인해드리겠습니다."
    ),
    DropoutType.BOT_ABANDONED: (
        "안녕하세요, 어스입니다. "
        "앞서 문의해 주신 내용 확인 중입니다. "
        "혹시 추가로 필요하신 사항이 있으시면 말씀해 주세요."
    ),
}


@dataclass
class DropoutResult:
    type: DropoutType
    followup_message: str
    confidence: float  # 0.0 ~ 1.0


class DropoutDetector:
    """워크플로우 봇 이탈 패턴 감지기"""

    def detect(self, event: "ParsedEvent") -> DropoutResult | None:
        """이벤트를 분석해 이탈 패턴이면 DropoutResult 반환, 정상 문의면 None"""

        # Type C: 환불 버튼 클릭 + 상세 미입력
        if self._is_refund_button_only(event):
            return DropoutResult(
                type=DropoutType.REFUND_NO_DETAIL,
                followup_message=FOLLOWUP_MESSAGES[DropoutType.REFUND_NO_DETAIL],
                confidence=0.9,
            )

        # Type A: 봇 메뉴만 클릭, 텍스트 없음
        if event.buttons_clicked and not event.has_meaningful_message:
            return DropoutResult(
                type=DropoutType.MENU_ONLY,
                followup_message=FOLLOWUP_MESSAGES[DropoutType.MENU_ONLY],
                confidence=0.85,
            )

        # Type B: trivial 텍스트 (이름, 인사만)
        if self._is_trivial(event.message):
            return DropoutResult(
                type=DropoutType.TRIVIAL_TEXT,
                followup_message=FOLLOWUP_MESSAGES[DropoutType.TRIVIAL_TEXT],
                confidence=0.8,
            )

        # 정상 문의
        return None

    def _is_refund_button_only(self, event: "ParsedEvent") -> bool:
        """환불 관련 버튼을 눌렀지만 상세 내용이 없는 경우"""
        if not event.buttons_clicked:
            return False
        has_refund_button = any(
            any(kw in btn for kw in REFUND_KEYWORDS)
            for btn in event.buttons_clicked
        )
        return has_refund_button and not event.has_meaningful_message

    def _is_trivial(self, message: str) -> bool:
        """의미 없는 짧은 텍스트인지"""
        if not message:
            return False
        stripped = message.strip()
        if len(stripped) > 10:
            return False
        # 이름만 입력한 경우 (2~4글자 한글)
        if 2 <= len(stripped) <= 4 and all('\uac00' <= c <= '\ud7a3' for c in stripped):
            return True
        # 패턴 매칭
        return stripped.lower() in TRIVIAL_PATTERNS
