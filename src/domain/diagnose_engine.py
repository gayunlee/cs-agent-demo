"""DiagnoseEngine — 조건 체인 순회 + first_failure 반환.

Gayoon 이전 프로젝트의 visibility_chain 엔진 패턴 차용:
- YAML chain의 requires 배열을 순서대로 평가
- 첫 실패 rule에서 멈추고 fail_message + 상세 정보 반환
- 모두 통과하면 success + compute 결과
- 진단, Action Harness, Knowledge handler가 모두 같은 chain 참조 (SSoT)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from .dsl import DSLEvaluator, DSLError
from .loader import DomainLoader, get_loader


@dataclass
class DiagnoseResult:
    """진단 결과.

    Attributes:
        chain_id: 평가한 체인 ID
        passed: 모든 rule 통과 여부
        failed_rule_id: 실패 시 rule ID (통과 시 None)
        fail_message: 실패 시 포맷된 메시지 (통과 시 "")
        fail_template: 실패 시 사용할 답변 템플릿 ID (있을 경우)
        evaluated: 평가한 rule ID 리스트 (순서대로, 실패 지점까지)
    """
    chain_id: str
    passed: bool
    failed_rule_id: str | None = None
    fail_message: str = ""
    fail_template: str | None = None
    evaluated: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


class DiagnoseEngine:
    """체인 순회 엔진.

    Usage:
        engine = DiagnoseEngine(functions={"has_keyword": ...})
        result = engine.evaluate_chain("can_process_refund", context)
        if result.passed:
            # 다음 단계 진행
        else:
            # result.fail_template로 답변 생성
    """

    def __init__(
        self,
        loader: DomainLoader | None = None,
        functions: dict[str, Callable] | None = None,
    ):
        self.loader = loader or get_loader()
        self.evaluator = DSLEvaluator(functions=functions)

    def evaluate_chain(self, chain_id: str, context: dict) -> DiagnoseResult:
        """체인을 순회하며 각 rule의 check를 평가.

        Args:
            chain_id: refund_chains.yaml의 체인 ID
            context: DSL 평가에 사용할 컨텍스트 dict

        Returns:
            DiagnoseResult: 통과/실패 + 상세 정보
        """
        chain = self.loader.get_chain(chain_id)
        if chain is None:
            raise ValueError(f"Chain not found: {chain_id}")

        result = DiagnoseResult(chain_id=chain_id, passed=True)

        for rule in chain.get("requires", []):
            rule_id = rule.get("id", "?")
            result.evaluated.append(rule_id)

            check_expr = rule.get("check", "")
            try:
                ok = self.evaluator.eval(check_expr, context)
            except DSLError as e:
                result.passed = False
                result.failed_rule_id = rule_id
                result.fail_message = f"DSL 평가 실패 ({rule_id}): {e}"
                result.trace.append({"rule": rule_id, "error": str(e)})
                return result

            result.trace.append({
                "rule": rule_id,
                "check": check_expr,
                "result": bool(ok),
            })

            if not ok:
                result.passed = False
                result.failed_rule_id = rule_id
                # fail_message 템플릿 치환
                raw_msg = rule.get("fail_message", f"조건 실패: {rule_id}")
                result.fail_message = self.evaluator.format_message(raw_msg, context)
                result.fail_template = rule.get("fail_template")
                return result

        # 모든 rule 통과
        return result

    def evaluate_chains(self, chain_ids: list[str], context: dict) -> list[DiagnoseResult]:
        """여러 체인을 순차 평가. 각각 독립."""
        return [self.evaluate_chain(cid, context) for cid in chain_ids]

    def first_passing_chain(self, chain_ids: list[str], context: dict) -> DiagnoseResult | None:
        """여러 체인 중 첫 번째 통과 체인 반환. 없으면 None."""
        for cid in chain_ids:
            r = self.evaluate_chain(cid, context)
            if r.passed:
                return r
        return None
