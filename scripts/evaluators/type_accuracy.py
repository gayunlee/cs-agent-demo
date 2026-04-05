"""Template ID 일치 검증."""
from __future__ import annotations

import json

from strands_evals.evaluators import Evaluator
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput


class TypeAccuracyEvaluator(Evaluator):
    """Agent가 고른 template_id가 expected와 일치하는가.

    actual_output은 run_agent_task가 반환한 JSON string:
        {"template_id": "...", "refund_amount": 30015}
    expected_template_id는 case.metadata에 주입.
    """

    def evaluate(self, case: EvaluationData) -> list[EvaluationOutput]:
        expected = (case.metadata or {}).get("expected_template_id", "")
        actual_template_id = _parse_template_id(case.actual_output)
        ok = bool(expected) and actual_template_id == expected
        return [EvaluationOutput(
            score=1.0 if ok else 0.0,
            test_pass=ok,
            reason=f"expected={expected}, actual={actual_template_id}",
        )]


def _parse_template_id(actual_output) -> str:
    if actual_output is None:
        return ""
    if isinstance(actual_output, dict):
        return actual_output.get("template_id", "")
    if isinstance(actual_output, str):
        try:
            d = json.loads(actual_output)
            return d.get("template_id", "")
        except (json.JSONDecodeError, ValueError):
            return actual_output  # 평탄 string이면 그대로
    return ""
