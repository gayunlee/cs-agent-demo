"""동적 환불 계산 엔진

검증된 핵심 공식 (데이터 탐색에서 매니저 안내 원문 + 실제 금액 역산 일치):
  차감금 = 1개월_정가 × (경과_비율)
  잔여금 = 결제총액 - 차감금
  수수료 = 잔여금 × 10%
  환불액 = 잔여금 - 수수료

검증 예: 27만원 결제, 정가 10만원/월 → 차감 33,333 → 잔여 236,667 → 수수료 23,667 → 환불 213,000원 ✓
"""
from __future__ import annotations
import json
import math
import logging
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).parent.parent / "config" / "refund_rules.json"


@dataclass
class RefundInput:
    """환불 계산에 필요한 입력값"""
    total_paid: int                # 결제 총액
    monthly_price: int             # 1개월 정가
    payment_date: date             # 결제일
    payment_cycle_days: int = 30   # 결제 주기 (일)
    content_accessed: bool = False # 콘텐츠 열람 여부
    today: date | None = None      # 계산 기준일 (테스트용)

    @property
    def days_elapsed(self) -> int:
        ref = self.today or date.today()
        return (ref - self.payment_date).days

    @property
    def months_elapsed(self) -> int:
        return self.days_elapsed // 30

    @property
    def period_fraction(self) -> float:
        if self.payment_cycle_days <= 0:
            return 0.0
        return self.days_elapsed / self.payment_cycle_days


@dataclass
class RefundResult:
    """환불 계산 결과"""
    rule_id: str
    label: str
    refundable: bool
    refund_amount: int = 0
    deduction: int = 0
    fee: int = 0
    explanation: str = ""
    confidence: str = "high"

    def to_display(self) -> str:
        if not self.refundable:
            return f"❌ 환불 불가 ({self.label})\n{self.explanation}"
        lines = [
            f"✅ {self.label}",
            f"  환불 금액: {self.refund_amount:,}원",
        ]
        if self.deduction:
            lines.append(f"  차감금: {self.deduction:,}원")
        if self.fee:
            lines.append(f"  수수료: {self.fee:,}원")
        if self.confidence != "high":
            lines.append(f"  ⚠️ 신뢰도: {self.confidence} (CS팀 확인 권장)")
        if self.explanation:
            lines.append(f"  근거: {self.explanation}")
        return "\n".join(lines)


class RefundEngine:
    """동적 환불 규정 테이블 기반 계산 엔진"""

    def __init__(self, rules_path: str | Path = RULES_PATH):
        self.rules_path = Path(rules_path)
        self.rules = self._load_rules()

    def _load_rules(self) -> list[dict]:
        try:
            with open(self.rules_path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("rules", [])
        except FileNotFoundError:
            logger.error(f"규정 테이블 없음: {self.rules_path}")
            return []

    def reload(self):
        """규정 테이블 리로드 (정책 변경 시)"""
        self.rules = self._load_rules()

    def calculate(self, inp: RefundInput) -> RefundResult:
        """환불 금액 계산"""
        matched_rule = self._match_rule(inp)
        if not matched_rule:
            return RefundResult(
                rule_id="NONE",
                label="규정 매칭 실패",
                refundable=False,
                explanation="적용 가능한 환불 규정을 찾지 못했습니다. CS팀에 직접 문의해 주세요.",
            )

        method = matched_rule.get("refund", {}).get("method", "none")
        confidence = matched_rule.get("confidence", "high")
        rule_id = matched_rule.get("id", "UNKNOWN")
        label = matched_rule.get("label", "")

        if method == "none":
            return RefundResult(
                rule_id=rule_id,
                label=label,
                refundable=False,
                confidence=confidence,
                explanation=f"결제일로부터 {inp.days_elapsed}일 경과, 이용기간 {inp.period_fraction:.0%} 경과",
            )

        if method == "full":
            return RefundResult(
                rule_id=rule_id,
                label=label,
                refundable=True,
                refund_amount=inp.total_paid,
                confidence=confidence,
                explanation=f"결제일로부터 {inp.days_elapsed}일, 콘텐츠 열람 없음 → 전액 환불",
            )

        if method == "fraction_deduct":
            return self._calc_fraction_deduct(inp, matched_rule)

        if method == "months_deduct":
            return self._calc_months_deduct(inp, matched_rule)

        return RefundResult(
            rule_id=rule_id,
            label=label,
            refundable=False,
            explanation=f"알 수 없는 계산 방식: {method}",
        )

    def _match_rule(self, inp: RefundInput) -> dict | None:
        """입력값에 맞는 규칙 찾기 (순서대로, 첫 매칭)"""
        for rule in self.rules:
            conditions = rule.get("conditions", {})
            if self._check_conditions(inp, conditions):
                return rule
        return None

    def _check_conditions(self, inp: RefundInput, conditions: dict) -> bool:
        """조건 평가"""
        # days_from_payment
        days_cond = conditions.get("days_from_payment")
        if days_cond:
            if "max" in days_cond and inp.days_elapsed > days_cond["max"]:
                return False
            if "min" in days_cond and inp.days_elapsed < days_cond["min"]:
                return False

        # content_accessed
        if "content_accessed" in conditions:
            if conditions["content_accessed"] != inp.content_accessed:
                return False

        # period_fraction
        frac_cond = conditions.get("period_fraction")
        if frac_cond:
            if "max" in frac_cond and inp.period_fraction > frac_cond["max"]:
                return False
            if "min" in frac_cond and inp.period_fraction < frac_cond["min"]:
                return False

        # months_elapsed
        months_cond = conditions.get("months_elapsed")
        if months_cond:
            if "min" in months_cond and inp.months_elapsed < months_cond["min"]:
                return False
            if "max" in months_cond and inp.months_elapsed > months_cond["max"]:
                return False

        return True

    def _calc_fraction_deduct(self, inp: RefundInput, rule: dict) -> RefundResult:
        """비율 차감 계산: 차감금 = 정가 × fraction, 수수료 = 잔여 × fee_rate"""
        refund_config = rule.get("refund", {})
        deduct_fraction = refund_config.get("deduct_fraction", 0.333)
        fee_rate = refund_config.get("fee_rate", 0.10)

        deduction = math.floor(inp.monthly_price * deduct_fraction)
        remaining = inp.total_paid - deduction
        fee = math.floor(remaining * fee_rate)
        refund_amount = remaining - fee

        return RefundResult(
            rule_id=rule.get("id", ""),
            label=rule.get("label", ""),
            refundable=True,
            refund_amount=max(0, refund_amount),
            deduction=deduction,
            fee=fee,
            confidence=rule.get("confidence", "high"),
            explanation=(
                f"결제총액 {inp.total_paid:,}원 - "
                f"차감금 {deduction:,}원 (정가 {inp.monthly_price:,}원 × {deduct_fraction:.0%}) - "
                f"수수료 {fee:,}원 ({fee_rate:.0%}) = "
                f"환불액 {max(0, refund_amount):,}원"
            ),
        )

    def _calc_months_deduct(self, inp: RefundInput, rule: dict) -> RefundResult:
        """N개월 차감 계산: 차감금 = 정가 × N개월, 수수료 = 잔여 × fee_rate"""
        refund_config = rule.get("refund", {})
        fee_rate = refund_config.get("fee_rate", 0.10)
        months = inp.months_elapsed

        deduction = inp.monthly_price * months
        remaining = inp.total_paid - deduction
        if remaining <= 0:
            return RefundResult(
                rule_id=rule.get("id", ""),
                label="환불 불가 (이용 기간 초과)",
                refundable=False,
                confidence=rule.get("confidence", "high"),
                explanation=f"{months}개월 경과, 차감금({deduction:,}원)이 결제총액({inp.total_paid:,}원) 이상",
            )

        fee = math.floor(remaining * fee_rate)
        refund_amount = remaining - fee

        return RefundResult(
            rule_id=rule.get("id", ""),
            label=rule.get("label", ""),
            refundable=True,
            refund_amount=max(0, refund_amount),
            deduction=deduction,
            fee=fee,
            confidence=rule.get("confidence", "high"),
            explanation=(
                f"결제총액 {inp.total_paid:,}원 - "
                f"차감금 {deduction:,}원 (정가 {inp.monthly_price:,}원 × {months}개월) - "
                f"수수료 {fee:,}원 ({fee_rate:.0%}) = "
                f"환불액 {max(0, refund_amount):,}원"
            ),
        )
