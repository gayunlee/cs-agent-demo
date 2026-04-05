"""환불 금액 ±1원 이내 일치 검증."""
from __future__ import annotations

import json

from strands_evals.evaluators import Evaluator
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput

TOLERANCE_WON = 1


class AmountAccuracyEvaluator(Evaluator):
    """T2 케이스 환불 금액 ±1원 이내 일치.

    expected_refund_amount가 None → 금액 체크 불요 → PASS.
    actual_output JSON에서 refund_amount 추출.
    """

    def evaluate(self, case: EvaluationData) -> list[EvaluationOutput]:
        md = case.metadata or {}
        expected = md.get("expected_refund_amount")
        actual = _parse_refund_amount(case.actual_output)

        if expected is None:
            return [EvaluationOutput(
                score=1.0,
                test_pass=True,
                reason="금액 체크 불요 (비-T2 유형)",
            )]

        if actual is None:
            return [EvaluationOutput(
                score=0.0,
                test_pass=False,
                reason=f"agent 금액 없음 (expected={expected})",
            )]

        diff = abs(int(actual) - int(expected))
        ok = diff <= TOLERANCE_WON
        return [EvaluationOutput(
            score=1.0 if ok else 0.0,
            test_pass=ok,
            reason=f"expected={expected}, actual={actual}, diff={diff}",
        )]


def _parse_refund_amount(actual_output):
    if actual_output is None:
        return None
    if isinstance(actual_output, dict):
        return actual_output.get("refund_amount")
    if isinstance(actual_output, str):
        try:
            d = json.loads(actual_output)
            return d.get("refund_amount")
        except (json.JSONDecodeError, ValueError):
            return None
    return None
