"""환불/해지 워크플로우 그래프

정책 기반 분기 — 데이터 조건만으로 템플릿 결정.
환불 계산은 RefundEngine 사용.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from src.admin_api import MembershipItem, RefundHistoryItem


@dataclass
class WorkflowContext:
    """워크플로우 실행 중 누적되는 컨텍스트

    memberships, refunds는 dict로 받아도 자동으로 dataclass로 변환됨 (__post_init__).
    이는 테스트/enriched JSON과의 호환을 유지하면서 workflow 내부에선 타입 안전성 확보.
    """
    user_messages: list[str] = field(default_factory=list)
    phone: str = ""
    conversation_turns: list[dict] = field(default_factory=list)
    conversation_time: str = ""  # 대화 시점 (ISO) — RefundEngine 시점 복원용
    us_user_id: str = ""
    user_name: str = ""
    signup_method: str = ""
    products: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    memberships: list = field(default_factory=list)  # list[MembershipItem]
    refunds: list = field(default_factory=list)  # list[RefundHistoryItem]
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
    prev_had_t6: bool = False
    prev_manager_count: int = 0
    refund_amount: int = 0
    deduction: int = 0
    fee: int = 0
    new_product_price: int = 0  # 상품변경 시 신규 상품 가격 (메시지 파싱 또는 외부 주입)
    refund_explanation: str = ""
    refundable: bool = False
    path: list[str] = field(default_factory=list)
    template_id: str = ""
    template_variables: dict = field(default_factory=dict)

    def __post_init__(self):
        # dict → dataclass 자동 변환 (enriched JSON / test dict 호환)
        self.memberships = [
            MembershipItem.from_api(m) if isinstance(m, dict) else m
            for m in self.memberships
        ]
        self.refunds = [
            RefundHistoryItem.from_api(r) if isinstance(r, dict) else r
            for r in self.refunds
        ]


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
        # Branch D: 이전에 T6 안내했는데도 여전히 식별 실패 → 재질문
        if ctx.prev_had_t6:
            ctx.path.append("T6_재질문")
            return "T6b_본인확인_재질문"
        ctx.path.append("유저_식별_불가")
        return "T6_본인확인_요청"

    # Branch D: 본인 아님 시그널 (가족/타인 번호 언급)
    if any(kw in user_text for kw in [
        "가족 번호", "가족번호", "엄마 번호", "아빠 번호", "남편 번호", "아내 번호",
        "제 번호 아니", "제 명의 아니", "다른 사람 번호", "본인 번호 아니"
    ]):
        ctx.path.append("타인번호_시그널 → T6b")
        return "T6b_본인확인_재질문"

    # ── Branch C: 환불 지연/미처리 재촉 ──
    # 진행 중 환불 건이 있고 + 유저가 재촉/상태확인 의도
    if ctx.refunds and _is_urging_refund(user_text):
        pending = _find_pending_refund(ctx.refunds)
        if pending:
            ctx.path.append("환불지연_재촉 → T12")
            ctx.template_variables["환불접수일"] = _format_date(pending.created_at)
            ctx.template_variables["환불예정금액"] = f"{pending.refund_history.refund_amount:,}"
            ctx.template_variables["상품명"] = pending.product_name or "구독 상품"
            return "T12_환불진행_상태안내"

    # ── Node 3: 데이터 계산 ──
    _compute_derived(ctx)

    # ── Node 4: 결제 이력 없음 → T1 ──
    if not ctx.success_txs:
        ctx.path.append("결제없음 → T1")
        return "T1_구독해지_방법_앱"

    # ── Node 5: 전부 환불됨 → T3 ──
    if ctx.all_refunded:
        # Fallback 분기: 전부 환불된 상태에서 유저가 새 질문(재결제/재가입/예외) 제기
        if _is_post_refund_question(user_text):
            ctx.path.append("전부환불후_추가질문 → LLM_FALLBACK")
            return "T_LLM_FALLBACK"
        ctx.path.append("전부_환불됨 → T3")
        return "T3_환불_접수_완료"

    # ── Fallback 분기: 환불 철회/예외 요청 등 ──
    if _is_refund_withdrawal_intent(user_text) or _is_exception_request(user_text):
        ctx.path.append("환불철회/예외 → LLM_FALLBACK")
        return "T_LLM_FALLBACK"

    # ── Branch A: 상품 변경 + 차액 환불 ──
    if _is_product_change_intent(user_text):
        _calculate_exchange(ctx, user_text)
        ctx.path.append("상품변경 → T10")
        return "T10_상품변경_차액환불"

    # ── Branch B: 중복/이중 결제 환불 선택 ──
    # 미환불 결제가 2건 이상이고, 유저 메시지에 중복 의도가 있거나 금액 불일치 언급
    unrefunded = [t for t in ctx.success_txs if not any(
        (r.get("round") == t.get("round") or r.get("amount") == t.get("amount"))
        for r in ctx.refund_txs
    )]
    if len(unrefunded) >= 2 and _is_duplicate_payment_intent(user_text):
        ctx.template_variables["미환불건수"] = str(len(unrefunded))
        ctx.template_variables["결제목록"] = _format_payment_list(unrefunded)
        ctx.path.append(f"중복결제_{len(unrefunded)}건 → T11")
        return "T11_중복결제_환불선택"

    # ── Node 6: 미환불 결제 있음 → T2 (RefundEngine) ──
    _calculate_refund(ctx)
    # Fallback: 환불 규정에 매칭 안 되거나 계산 실패(차감금 > 총액) → LLM
    if not ctx.refundable and ctx.success_txs:
        ctx.path.append("T2_계산실패 → LLM_FALLBACK")
        return "T_LLM_FALLBACK"
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
    ctx.prev_had_t6 = any(
        "성함" in m and ("휴대전화" in m or "번호" in m)
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
    # 필드명 통일: API 스펙은 created_at. enriched의 date는 refund_agent_v2._use_enriched_data에서 정규화됨.
    # 테스트에서는 date로 들어올 수 있어 fallback 유지.
    tx_date = (latest.get("created_at") or latest.get("date") or "")[:10]

    # 1개월 정가 추정
    payment_cycle = 1
    if ctx.memberships:
        # memberships는 list[MembershipItem] (__post_init__에서 변환됨)
        cycle = ctx.memberships[0].payment_cycle
        if cycle and cycle > 0:
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
        # 시점 복원: conv_time이 있으면 그 날짜를 '오늘'로 써서 과거 상태 정확히 계산
        calc_today = None
        if ctx.conversation_time:
            try:
                calc_today = datetime.strptime(ctx.conversation_time[:10], "%Y-%m-%d").date()
            except ValueError:
                calc_today = None
        inp = RefundInput(
            total_paid=tx_amount,
            monthly_price=monthly_price,
            payment_date=pay_date,
            payment_cycle_days=30,
            content_accessed=ctx.has_accessed,
            today=calc_today,
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


def _is_refund_withdrawal_intent(text: str) -> bool:
    """환불 철회 — '환불 안 할래', '환불 취소', '다시 이용할게' """
    kws = ["환불 취소", "환불취소", "환불 안 할", "환불 안할", "환불 안 받",
           "다시 이용", "계속 이용", "환불 철회", "철회할게", "철회하고"]
    return any(kw in text for kw in kws)


def _is_exception_request(text: str) -> bool:
    """예외 전액환불 요청 — '전액', '예외', '특별' 등"""
    kws = ["전액 환불", "전액환불", "특별 환불", "예외 환불", "100% 환불",
           "다 돌려", "전부 돌려", "규정 예외"]
    return any(kw in text for kw in kws)


def _is_post_refund_question(text: str) -> bool:
    """전부 환불된 상태에서 새 질문 — 재결제/재가입/추가 환불/기타 문의"""
    kws = ["재결제", "다시 결제", "재가입", "다시 가입", "새 카드",
           "추가 환불", "또 환불", "다른 건", "보안카드"]
    return any(kw in text for kw in kws)


def _is_duplicate_payment_intent(text: str) -> bool:
    """중복/이중 결제 의도 감지"""
    kws = ["중복", "이중", "두 번", "두번", "2번 결제", "두 개", "두개",
           "여러 번", "여러번", "또 결제", "또 빠져", "또 빠졌"]
    return any(kw in text for kw in kws)


def _format_payment_list(txs: list[dict]) -> str:
    """결제 내역을 유저에게 보여줄 목록 형식으로"""
    lines = []
    for i, t in enumerate(txs, 1):
        amount = t.get("amount", 0)
        if isinstance(amount, str):
            try:
                amount = int(amount)
            except ValueError:
                amount = 0
        tx_date = (t.get("date") or t.get("created_at") or "")[:10]
        round_no = t.get("round", "?")
        lines.append(f"  {i}. {tx_date} / {round_no}회차 / {amount:,}원")
    return "\n".join(lines)


def _is_product_change_intent(text: str) -> bool:
    """상품 변경 의도 감지"""
    # "변경" 단독은 너무 광범위 — 맥락 키워드 결합
    strong = ["상품 변경", "상품변경", "다른 상품", "다른상품", "상품 바꿔",
              "차액", "바꿔서", "바꾸고 싶", "으로 변경", "로 변경"]
    return any(kw in text for kw in strong)


def _extract_price_hint(text: str) -> int | None:
    """유저 메시지에서 '10만원', '100,000원', '10만' 같은 금액 추출"""
    import re
    # 만원 단위: "10만원", "10만"
    m = re.search(r"(\d+)\s*만\s*원?", text)
    if m:
        return int(m.group(1)) * 10000
    # 원 단위: "100,000원", "100000원"
    m = re.search(r"([\d,]+)\s*원", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _calculate_exchange(ctx: WorkflowContext, user_text: str):
    """상품 변경 차액 계산 — Branch A"""
    from datetime import datetime
    from src.refund_engine import RefundEngine, RefundInput

    # 신규 상품 가격: 외부 주입이 우선, 없으면 메시지에서 파싱
    new_price = ctx.new_product_price or _extract_price_hint(user_text) or 0

    latest = ctx.latest_tx or (ctx.success_txs[-1] if ctx.success_txs else {})
    tx_amount = latest.get("amount", 0)
    if isinstance(tx_amount, str):
        try:
            tx_amount = int(tx_amount)
        except ValueError:
            tx_amount = 0
    # 필드명 통일: created_at 우선, 테스트 호환 위해 date도 fallback
    tx_date = (latest.get("created_at") or latest.get("date") or "")[:10]

    pay_date = None
    if tx_date:
        try:
            pay_date = datetime.strptime(tx_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    ctx.template_variables["기존결제금액"] = f"{tx_amount:,}"
    ctx.template_variables["신규상품가격"] = f"{new_price:,}" if new_price else "(확인 필요)"

    if pay_date and tx_amount and new_price:
        engine = RefundEngine()
        inp = RefundInput(
            total_paid=tx_amount,
            monthly_price=tx_amount,  # 정가 추정 — 단건 기준
            payment_date=pay_date,
            payment_cycle_days=30,
            content_accessed=ctx.has_accessed,
        )
        result = engine.calculate_exchange(inp, new_price)
        ctx.template_variables["기존환불가능액"] = f"{(result.refund_amount + new_price if result.refundable else 0):,}"
        ctx.template_variables["차액환불금액"] = f"{result.refund_amount:,}"
        ctx.refund_amount = result.refund_amount
        ctx.refund_explanation = result.explanation
    else:
        # 신규 가격 파싱 실패 — 템플릿 변수만 placeholder로 두고 상담사/LLM에 맡김
        ctx.template_variables["기존환불가능액"] = "(계산 필요)"
        ctx.template_variables["차액환불금액"] = "(계산 필요)"


def _is_urging_refund(text: str) -> bool:
    """재촉/상태확인 의도 감지"""
    urging_kws = [
        "왜 아직", "언제 환불", "언제쯤", "처리 안", "처리 안돼", "처리안",
        "아직도", "아직 환불", "환불 언제", "환불언제",
        "며칠 됐", "몇일 됐", "1달", "한달", "한 달", "일주일 지",
        "소식이 없", "답변이 없", "연락이 없",
    ]
    return any(kw in text for kw in urging_kws)


def _find_pending_refund(refunds: list) -> "RefundHistoryItem | None":
    """진행 중(refundAt 없음)인 환불 건 찾기.

    refunds는 list[RefundHistoryItem] (WorkflowContext.__post_init__에서 변환됨).
    """
    for r in refunds:
        if r.is_pending:
            return r
    # 진행 중 없으면 가장 최근 건 반환 (상태 재확인 용도)
    return refunds[0] if refunds else None


def _format_date(iso: str) -> str:
    """ISO 날짜 → '1월 15일' 형식"""
    if not iso or len(iso) < 10:
        return iso
    try:
        parts = iso[:10].split("-")
        return f"{int(parts[1])}월 {int(parts[2])}일"
    except (ValueError, IndexError):
        return iso[:10]


def _format_month(ym: str) -> str:
    if "-" in ym:
        parts = ym.split("-")
        if len(parts) >= 2:
            return f"{int(parts[1])}월"
    return ym
