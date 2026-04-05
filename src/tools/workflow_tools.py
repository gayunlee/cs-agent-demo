"""정책/워크플로우 + Harness 기반 tool들.

핵심 tool:
- diagnose_refund_case: YAML chain 순회 → template_id 반환
- calculate_refund_amount: RefundEngine 호출 (결정론적 계산)
- compose_template_answer: YAML 템플릿 렌더링 + post harness

⚠️ 모든 tool은 "상담 Agent의 context"를 직접 받지 않음.
   Strands Agent는 tool을 LLM이 선택한 인자로 호출하는데,
   우리 case에선 context를 module-level 변수에 주입해두고 tool이 참조.
   (Strands의 agent context sharing 패턴 — agent closure 또는 global)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from strands import tool

from src.domain.diagnose_engine import DiagnoseEngine
from src.domain.loader import get_loader
from src.domain.functions import DEFAULT_FUNCTIONS
from src.domain.action_harness import ActionHarness

# 전역 상태 (Phase 3에서 Agent 래퍼가 턴 시작마다 주입)
_current_context: dict = {}


def set_context(ctx: dict):
    """Agent 턴 시작 시 호출. tool들이 참조할 context 세팅."""
    global _current_context
    _current_context = ctx


def get_context() -> dict:
    return _current_context


# ─────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────

_engine: DiagnoseEngine | None = None
_harness: ActionHarness | None = None


def _get_engine() -> DiagnoseEngine:
    global _engine
    if _engine is None:
        _engine = DiagnoseEngine(functions=DEFAULT_FUNCTIONS)
    return _engine


def _get_harness() -> ActionHarness:
    global _harness
    if _harness is None:
        _harness = ActionHarness()
    return _harness


@tool
def diagnose_refund_case() -> dict:
    """현재 유저 컨텍스트를 보고 어느 답변 템플릿을 써야 할지 진단합니다.

    YAML chain(domain/refund_chains.yaml)을 순회하며 routing_order 순서대로 평가합니다.
    첫 매칭되는 체인의 on_pass_template을 반환합니다.

    Returns:
        dict: {
            "template_id": str,   # 사용할 템플릿 ID (예: "T2_환불_규정_금액")
            "matched_chain": str, # 매칭된 체인 ID
            "trace": list         # 평가 경로 (디버깅용)
        }
    """
    engine = _get_engine()
    loader = get_loader()
    chains_file = loader.load("refund_chains.yaml")
    routing_order = chains_file.get("routing_order", [])
    chains = chains_file.get("chains", {})

    ctx = _current_context
    trace = []

    for chain_id in routing_order:
        r = engine.evaluate_chain(chain_id, ctx)
        trace.append({
            "chain": chain_id,
            "passed": r.passed,
            "failed_rule": r.failed_rule_id,
        })
        if r.passed:
            chain = chains.get(chain_id, {})
            template_id = chain.get("on_pass_template")
            if template_id:
                return {
                    "template_id": template_id,
                    "matched_chain": chain_id,
                    "trace": trace,
                }
            # on_pass_template 없는 체인 (예: needs_user_identification)은 계속 진행
            # 대신 special handling 필요

    # 매칭 없음 → T6 본인확인 요청 (안전한 fallback)
    return {
        "template_id": "T6_본인확인_요청",
        "matched_chain": "<no_match>",
        "trace": trace,
    }


@tool
def calculate_refund_amount() -> dict:
    """현재 context의 결제 내역을 기반으로 환불 금액을 계산합니다.

    기존 RefundEngine을 재사용해 결정론적으로 계산.
    주기 정보는 상품명 파싱(_infer_cycle_from_products).

    Returns:
        dict: {
            "refundable": bool,
            "refund_type": "full" | "partial",
            "결제금액": str, "환불금액": str, "차감금": str, "수수료": str,
            "explanation": str
        }
    """
    from datetime import datetime
    from src.refund_engine import RefundEngine, RefundInput
    from src.workflow import _infer_cycle_from_products

    ctx = _current_context
    success_txs = ctx.get("ctx", {}).get("success_txs", []) or ctx.get("success_txs", [])
    products = ctx.get("ctx", {}).get("products", []) or ctx.get("products", [])
    conv_time = ctx.get("ctx", {}).get("conversation_time", "") or ctx.get("conversation_time", "")
    has_accessed = ctx.get("ctx", {}).get("has_accessed", False) or ctx.get("has_accessed", False)

    if not success_txs:
        return {"refundable": False, "reason": "결제 내역 없음"}

    latest = success_txs[-1]
    tx_amount = latest.get("amount", 0)
    if isinstance(tx_amount, str):
        try:
            tx_amount = int(tx_amount)
        except ValueError:
            tx_amount = 0

    tx_date = (latest.get("created_at") or latest.get("date") or "")[:10]
    pay_date = None
    if tx_date:
        try:
            pay_date = datetime.strptime(tx_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    cycle_months = _infer_cycle_from_products(products)
    monthly_price = tx_amount // cycle_months if cycle_months > 1 else tx_amount

    calc_today = None
    if conv_time:
        try:
            calc_today = datetime.strptime(conv_time[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    if not pay_date:
        return {"refundable": False, "reason": "결제일 불명"}

    engine = RefundEngine()
    inp = RefundInput(
        total_paid=tx_amount,
        monthly_price=monthly_price,
        payment_date=pay_date,
        payment_cycle_days=30,
        content_accessed=has_accessed,
        today=calc_today,
    )
    calc = engine.calculate(inp)

    # Post harness: 환불 금액 검증
    harness = _get_harness()
    check = harness.validate_refund_amount(calc.refund_amount, tx_amount)
    if not check.ok:
        return {"refundable": False, "reason": check.reason}

    refund_type = "full" if calc.refundable and calc.deduction == 0 else "partial"

    return {
        "refundable": calc.refundable,
        "refund_type": refund_type,
        "결제금액": f"{tx_amount:,}",
        "환불금액": f"{calc.refund_amount:,}",
        "차감금": f"{calc.deduction:,}",
        "수수료": f"{calc.fee:,}",
        "explanation": calc.explanation,
    }


@tool
def compose_template_answer(template_id: str, slots_json: str = "{}") -> str:
    """템플릿 ID와 slots(JSON 문자열)을 받아 최종 답변 텍스트를 생성합니다.

    Args:
        template_id: templates.yaml의 템플릿 ID
        slots_json: 치환할 slot 값들의 JSON 문자열. 예: '{"환불금액": "50,000"}'

    Returns:
        str: 완성된 답변 텍스트 (카드번호 마스킹 등 post harness 적용)
    """
    import json

    loader = get_loader()
    template = loader.get_template(template_id)
    if template is None:
        return f"⚠️ 템플릿을 찾을 수 없습니다: {template_id}"

    try:
        slots = json.loads(slots_json) if slots_json else {}
    except json.JSONDecodeError:
        slots = {}

    # Pre harness: 필수 slot 체크
    harness = _get_harness()
    check = harness.check_required_slots(template_id, slots)
    if not check.ok:
        return f"⚠️ 필수 정보 부족: {check.details.get('missing', [])}"

    # 템플릿 치환
    text = template.get("text", "")
    for key, value in slots.items():
        text = text.replace("{" + key + "}", str(value))

    # Post harness: 카드번호 마스킹 + 정책 위반 체크
    text = harness.mask_card_numbers(text)
    policy_check = harness.validate_no_policy_violation(text)
    if not policy_check.ok:
        return f"⚠️ 정책 위반: {policy_check.reason}"

    return text
