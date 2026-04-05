"""ActionHarness — tool pre/post validation 헬퍼.

Gayoon 이전 프로젝트의 "Trust boundary를 코드로" 원칙 차용:
- LLM에 규칙 프롬프트로 주지 않음
- Tool 호출 전/후 코드로 검증
- 차단된 경우 명확한 에러/대체 반환

사용 패턴:
    harness = ActionHarness()

    @tool
    def compose_template(template_id: str, slots: dict) -> str:
        # Pre: 필수 slot 체크
        harness.check_required_slots(template_id, slots)
        # 실제 렌더링
        answer = render_template(...)
        # Post: 민감정보 노출 차단
        answer = harness.mask_sensitive(answer)
        return answer
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from .loader import DomainLoader, get_loader


class HarnessError(Exception):
    """Harness validation 실패 (tool이 호출자에게 명확한 에러 전달용)"""
    pass


@dataclass
class HarnessCheck:
    """Harness 체크 결과"""
    ok: bool
    reason: str = ""
    details: dict = None


# 카드번호 패턴 — raw 노출 차단용
CARD_NUMBER_PATTERNS = [
    re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),  # 16자리
    re.compile(r"\b\d{6}[-\s]?\d{7}\b"),  # 13자리 (JCB 등)
]


class ActionHarness:
    """Tool pre/post validation 헬퍼."""

    def __init__(self, loader: DomainLoader | None = None):
        self.loader = loader or get_loader()

    # ── Pre-tool checks ──

    def check_required_slots(self, template_id: str, slots: dict) -> HarnessCheck:
        """템플릿이 요구하는 필수 slot이 모두 있는지 확인."""
        template = self.loader.get_template(template_id)
        if template is None:
            return HarnessCheck(ok=False, reason=f"Unknown template: {template_id}")

        required = template.get("required_slots", [])
        missing = [s for s in required if not slots.get(s)]
        if missing:
            return HarnessCheck(
                ok=False,
                reason=f"Missing required slots: {missing}",
                details={"missing": missing, "template_id": template_id},
            )
        return HarnessCheck(ok=True)

    def check_user_identified(self, context: dict) -> HarnessCheck:
        """본인확인 여부 (us_user_id 유무)"""
        uid = context.get("us_user_id") or (context.get("user") or {}).get("us_user_id")
        if not uid:
            return HarnessCheck(ok=False, reason="User not identified")
        return HarnessCheck(ok=True)

    # ── Post-tool checks / 정제 ──

    def mask_card_numbers(self, text: str) -> str:
        """답변 내 카드번호 raw 노출 마스킹.

        ⚠️ admin API는 카드번호를 raw로 내려주므로 답변에 들어가면 안 됨.
        """
        if not text:
            return text
        for pattern in CARD_NUMBER_PATTERNS:
            text = pattern.sub(
                lambda m: m.group(0)[:6] + "*" * 6 + m.group(0)[-4:]
                if len(m.group(0).replace("-", "").replace(" ", "")) >= 13
                else "****",
                text,
            )
        return text

    def validate_refund_amount(self, amount: int, total: int) -> HarnessCheck:
        """환불 금액이 총액을 초과하지 않는지"""
        if amount < 0:
            return HarnessCheck(ok=False, reason=f"Negative refund amount: {amount}")
        if amount > total:
            return HarnessCheck(
                ok=False,
                reason=f"Refund ({amount}) exceeds total ({total})",
            )
        return HarnessCheck(ok=True)

    def validate_no_policy_violation(self, answer: str, forbidden_terms: list[str] | None = None) -> HarnessCheck:
        """답변에 금지 용어 포함 여부 (예: 확정 약속, 법적 표현)"""
        forbidden = forbidden_terms or [
            "100% 환불 보장",
            "즉시 처리",  # 실제론 은행 절차 2~3일 필요
        ]
        for term in forbidden:
            if term in (answer or ""):
                return HarnessCheck(
                    ok=False,
                    reason=f"Forbidden term in answer: {term}",
                )
        return HarnessCheck(ok=True)
