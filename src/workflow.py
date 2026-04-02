"""환불/해지 워크플로우 그래프

정책 기반 분기 — 데이터 조건만으로 템플릿 결정.
환불 계산은 RefundEngine 사용.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class WorkflowContext:
    """워크플로우 실행 중 누적되는 컨텍스트"""
    user_messages: list[str] = field(default_factory=list)
    phone: str = ""
    conversation_turns: list[dict] = field(default_factory=list)
    us_user_id: str = ""
    user_name: str = ""
    signup_method: str = ""
    products: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    memberships: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    has_accessed: bool = False
    active_products: list[dict] = field(default_factory=list)
    success_txs: list[dict] = field(default_factory=list)
    refund_txs: list[dict] = field(default_factory=list)
    latest_tx: dict = field(default_factory=dict)
    latest_refunded: bool = False
    all_refunded: bool = False
    intent: str = ""
    prev_had_t2: bool = False
    prev_had_t4: bool = False
    prev_manager_count: int = 0
    refund_amount: int = 0
    deduction: int = 0
    fee: int = 0
    refund_explanation: str = ""
    refundable: bool = False
    path: list[str] = field(default_factory=list)
    template_id: str = ""
    template_variables: dict = field(default_factory=dict)


def run_workflow(ctx: WorkflowContext) -> str:
    """정책 기반 워크플로우 — 데이터 조건만으로 분기

    확정 정책:
      유저 식별 불가 → T6
      카드 문의 → T8
      결제 없음 → T1
      전부 환불됨 → T3
      미환불 있음 → T2 (RefundEngine으로 전액/부분 계산)
      이전턴 T2 → T3
    """

    # ── Node 0: 카드 문의 (유저 식별/데이터 무관) ──
    user_text = " ".join(ctx.user_messages).lower()
    if any(kw in user_text for kw in ["카드 변경", "카드변경", "카드 분실", "카드 만료", "카드 재발급"]):
        ctx.path.append("카드_문의 → T8")
        return "T8_카드변경_안내"

    # ── Node 1: 이전 대화 맥락 ──
    _analyze_prev_turns(ctx)

    if ctx.prev_had_t2 and ctx.prev_manager_count >= 1:
        ctx.path.append("이전턴_T2 → T3")
        return "T3_환불_접수_완료"

    # ── Node 2: 유저 식별 ──
    if not ctx.us_user_id:
        ctx.path.append("유저_식별_불가")
        return "T6_본인확인_요청"

    # ── Node 3: 데이터 계산 ──
    _compute_derived(ctx)

    # ── Node 4: 결제 이력 없음 → T1 ──
    if not ctx.success_txs:
        ctx.path.append("결제없음 → T1")
        return "T1_구독해지_방법_앱"

    # ── Node 5: 전부 환불됨 → T3 ──
    if ctx.all_refunded:
        ctx.path.append("전부_환불됨 → T3")
        return "T3_환불_접수_완료"

    # ── Node 6: 미환불 결제 있음 → T2 (RefundEngine) ──
    _calculate_refund(ctx)
    return "T2_환불_규정_금액"


def _analyze_prev_turns(ctx: WorkflowContext):
    """이전 대화 턴 분석"""
    if not ctx.conversation_turns:
        return

    last_user_ts = 0
    for t in reversed(ctx.conversation_turns):
        if t.get("role") == "user":
            text = (t.get("text") or "").strip()
            if text and not text.startswith("👆") and not text.startswith("💬") and not text.startswith("✅"):
                last_user_ts = t.get("ts", 0)
                break

    prev_mgr = [
        t["text"].lower() for t in ctx.conversation_turns
        if t.get("role") == "manager" and t.get("ts", 0) < last_user_ts
    ]

    ctx.prev_manager_count = len(prev_mgr)
    ctx.prev_had_t2 = any(
        "환불 규정" in m or "7일 이내 구독권" in m or "환불금" in m or "환불 금액" in m
        for m in prev_mgr
    )
    ctx.prev_had_t4 = any(
        "구독형 스터디" in m or "정기적으로 제공되는" in m
        for m in prev_mgr
    )


def _compute_derived(ctx: WorkflowContext):
    """조회 결과에서 파생 데이터 계산"""
    ctx.active_products = [p for p in ctx.products if p.get("status") == "active"]
    ctx.success_txs = [t for t in ctx.transactions if t.get("state") == "purchased_success"]
    ctx.refund_txs = [t for t in ctx.transactions if t.get("state") == "purchased_refund"]

    if ctx.success_txs:
        ctx.latest_tx = ctx.success_txs[-1]
        latest_round = ctx.latest_tx.get("round", 0)
        latest_amount = ctx.latest_tx.get("amount", 0)
        ctx.latest_refunded = any(
            (t.get("round") == latest_round or t.get("amount") == latest_amount)
            for t in ctx.refund_txs
        )
        ctx.all_refunded = len(ctx.refund_txs) >= len(ctx.success_txs) and len(ctx.refund_txs) > 0


def _calculate_refund(ctx: WorkflowContext):
    """RefundEngine으로 환불 금액 계산"""
    from datetime import date, datetime
    from src.refund_engine import RefundEngine, RefundInput

    latest = ctx.latest_tx
    tx_amount = latest.get("amount", 0)
    if isinstance(tx_amount, str):
        try:
            tx_amount = int(tx_amount)
        except ValueError:
            tx_amount = 0
    tx_date = (latest.get("date") or latest.get("created_at") or "")[:10]

    # 1개월 정가 추정
    payment_cycle = 1
    if ctx.memberships:
        cycle = ctx.memberships[0].get("paymentCycle", 1)
        if isinstance(cycle, int) and cycle > 0:
            payment_cycle = cycle
    elif ctx.products:
        name = (ctx.products[0].get("name") or "").lower()
        if "6개월" in name:
            payment_cycle = 6
        elif "3개월" in name:
            payment_cycle = 3
    monthly_price = tx_amount // payment_cycle if payment_cycle > 1 else tx_amount

    # 결제일 파싱
    pay_date = None
    if tx_date:
        try:
            pay_date = datetime.strptime(tx_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    if pay_date:
        engine = RefundEngine()
        inp = RefundInput(
            total_paid=tx_amount,
            monthly_price=monthly_price,
            payment_date=pay_date,
            payment_cycle_days=30,
            content_accessed=ctx.has_accessed,
        )
        calc = engine.calculate(inp)

        ctx.refundable = calc.refundable
        ctx.refund_amount = calc.refund_amount
        ctx.deduction = calc.deduction
        ctx.fee = calc.fee
        ctx.refund_explanation = calc.explanation

        if calc.refundable and calc.deduction == 0:
            ctx.template_variables["환불유형"] = "full"
        else:
            ctx.template_variables["환불유형"] = "partial"

        ctx.template_variables["결제금액"] = f"{tx_amount:,}"
        ctx.template_variables["환불금액"] = f"{calc.refund_amount:,}"
        ctx.template_variables["차감금"] = f"{calc.deduction:,}"
        ctx.template_variables["수수료"] = f"{calc.fee:,}"

        days = inp.days_elapsed
        accessed = "열람" if ctx.has_accessed else "미열람"
        within7 = "7일이내" if days <= 7 else "7일경과"
        refund_type = "전액" if ctx.template_variables["환불유형"] == "full" else "부분"
        ctx.path.append(f"미환불_{len(ctx.success_txs)}건 → {within7}+{accessed} → T2_{refund_type}")
    else:
        ctx.path.append(f"미환불_{len(ctx.success_txs)}건 → 결제일불명 → T2")


def _set_t4_variables(ctx: WorkflowContext):
    """T4 템플릿 변수 설정 (폐기됐지만 하위 호환)"""
    pass


def _format_month(ym: str) -> str:
    if "-" in ym:
        parts = ym.split("-")
        if len(parts) >= 2:
            return f"{int(parts[1])}월"
    return ym
