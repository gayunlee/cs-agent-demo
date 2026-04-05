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


def run_workflow(ctx: WorkflowContext, use_llm_intent: bool = True) -> str:
    """Hybrid 워크플로우 — LLM 의도 분류 + 코드 데이터 분기

    트러스트 경계:
    - **LLM**: 유저 의도 분류 (refund_request / cancel_method / card_change / …)
    - **코드**: 이전턴 맥락, 유저 식별, 데이터 조건(미환불/전부환불/중복), 금액 계산

    Args:
        use_llm_intent: True(기본)면 Bedrock LLM 호출, False면 mock 키워드 분류 (회귀 테스트용)
    """
    from src.intent_classifier import IntentClassifier

    user_text = " ".join(ctx.user_messages).lower()  # 일부 하위 코드에서 참조

    # ── Node 0: Intent 분류 (LLM or mock) ──
    classifier = IntentClassifier(mock=not use_llm_intent)
    intent_result = classifier.classify(
        user_messages=ctx.user_messages,
        conversation_turns=ctx.conversation_turns,
    )
    mode_tag = "LLM" if use_llm_intent else "mock"
    ctx.path.append(f"intent[{mode_tag}]: {intent_result.intent} ({intent_result.confidence})")

    intent = intent_result.intent
    ctx.intent = intent  # ctx에도 저장 (디버그/노출용)

    # ── Node 1: 이전 대화 맥락 (코드 판단 — LLM 무관) ──
    _analyze_prev_turns(ctx)

    # T2 후속 → T3 전환 (이전 턴이 T2 견적 제공 + 유저가 refund_request / 단순 동의)
    if ctx.prev_had_t2 and ctx.prev_manager_count >= 1 and intent in ("refund_request", "other"):
        ctx.path.append("이전턴_T2 → T3")
        return "T3_환불_접수_완료"

    # ── Node 2: 유저 식별 (데이터 판단) ──
    if not ctx.us_user_id:
        if ctx.prev_had_t6:
            ctx.path.append("T6_재질문")
            return "T6b_본인확인_재질문"
        ctx.path.append("유저_식별_불가")
        return "T6_본인확인_요청"

    # 가족/타인 번호 시그널 (여전히 코드 — 개인정보 관련이라 결정론 유지)
    if any(kw in user_text for kw in [
        "가족 번호", "가족번호", "엄마 번호", "아빠 번호", "남편 번호", "아내 번호",
        "제 번호 아니", "제 명의 아니", "다른 사람 번호", "본인 번호 아니"
    ]):
        ctx.path.append("타인번호_시그널 → T6b")
        return "T6b_본인확인_재질문"

    # ── Node 3: 카드 변경 (데이터 무관) ──
    if intent == "card_change":
        ctx.path.append("카드_문의 → T8")
        return "T8_카드변경_안내"

    # ── Node 4: 데이터 계산 ──
    _compute_derived(ctx)

    # ── Node 5: 환불 지연 재촉 ──
    if intent == "refund_delay" and ctx.refunds:
        pending = _find_pending_refund(ctx.refunds)
        if pending:
            ctx.path.append("환불지연_재촉 → T12")
            ctx.template_variables["환불접수일"] = _format_date(pending.created_at)
            ctx.template_variables["환불예정금액"] = f"{pending.refund_history.refund_amount:,}"
            ctx.template_variables["상품명"] = pending.product_name or "구독 상품"
            return "T12_환불진행_상태안내"

    # ── Node 6: 해지 신청 처리 확인 ──
    if intent == "cancel_check":
        cancelled = _is_membership_cancelled(ctx)
        ctx.template_variables["구독상태"] = "해지됨" if cancelled else "활성"
        if not cancelled:
            ctx.template_variables["_t7_variant"] = "not_cancelled"
        ctx.path.append(f"해지확인 → T7_{'완료' if cancelled else '미완료'}")
        return "T7_해지_확인_완료"

    # ── Node 7: 결제 이력 없음 ──
    if not ctx.success_txs:
        ctx.path.append("결제없음 → T1")
        return "T1_구독해지_방법_앱"

    # ── Node 8: 전부 환불됨 ──
    if ctx.all_refunded:
        # 전부환불 후 추가 질문(재가입/재결제) — refund_request 아닌 intent만 LLM fallback
        if intent not in ("refund_request",) or _is_post_refund_question(user_text):
            ctx.path.append("전부환불후_추가질문 → LLM_FALLBACK")
            return "T_LLM_FALLBACK"
        ctx.path.append("전부_환불됨 → T3")
        return "T3_환불_접수_완료"

    # ── Node 9: edge intents → LLM fallback ──
    if intent in ("refund_withdrawal", "exception_refund", "emotional_escalation",
                  "system_error", "compound_issue", "other"):
        ctx.path.append(f"환불철회/예외 → LLM_FALLBACK")
        return "T_LLM_FALLBACK"

    # ── Node 10: 자동결제 불만 → T4 (리텐션) ──
    if intent == "auto_payment_complaint" and not ctx.prev_had_t2:
        _set_t4_variables_real(ctx)
        ctx.path.append("자동결제_불만 → T4")
        return "T4_자동결제_설명"

    # ── Node 11: 상품 변경 + 차액 환불 ──
    if intent == "product_change":
        _calculate_exchange(ctx, user_text)
        ctx.path.append("상품변경 → T10")
        return "T10_상품변경_차액환불"

    # ── Node 12: 중복 결제 ──
    unrefunded = [t for t in ctx.success_txs if not any(
        (r.get("round") == t.get("round") or r.get("amount") == t.get("amount"))
        for r in ctx.refund_txs
    )]
    if intent == "duplicate_payment" and len(unrefunded) >= 2:
        ctx.template_variables["미환불건수"] = str(len(unrefunded))
        ctx.template_variables["결제목록"] = _format_payment_list(unrefunded)
        ctx.path.append(f"중복결제_{len(unrefunded)}건 → T11")
        return "T11_중복결제_환불선택"

    # ── Node 13: 해지 방법 단순 문의 ──
    if intent == "cancel_method_inquiry":
        ctx.path.append("cancel_method → T1")
        return "T1_구독해지_방법_앱"

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
    """RefundEngine으로 환불 금액 계산

    ⚠️ 주기(개월) 정보 소스 주의:
    - membership.payment_round (API 필드명 paymentCycle)는 "결제 회차 카운트"이지 주기가 아님.
      증거: us-admin MembershipHistoryAccordion:10-37 getPaymentCycleLabel.
      → 여기서 절대 사용 금지.
    - 실제 주기(개월) 필드는 ProductListData.paymentPeriod (상품 상세 API)에 있지만
      membership → product join 경로가 불명확하므로 현재는 상품명 파싱이 유일한 수단.
    - 1순위: _infer_cycle_from_products — 상품명에서 "6개월"/"1년" 추출
    - 폴백: 매칭 실패 시 1개월로 간주 (_infer_cycle_from_products 내부)
    """
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

    # 주기(개월) 추정 — 위 docstring 참조.
    payment_cycle_months = _infer_cycle_from_products(ctx.products)
    monthly_price = tx_amount // payment_cycle_months if payment_cycle_months > 1 else tx_amount

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
    """T4 템플릿 변수 설정 (하위 호환 stub)"""
    pass


def _set_t4_variables_real(ctx: WorkflowContext):
    """T4 자동결제 템플릿 변수 채우기.

    - 마스터명: products[0].master (또는 product_name에서 추출)
    - 이전결제월 / 현재결제월: transactions success 건들의 날짜에서 월 추출 (최근 2건)
    """
    # 마스터명
    master = ""
    if ctx.products:
        p = ctx.products[0]
        master = p.get("master") or p.get("master_name") or ""
        if not master:
            # product name에서 첫 단어 추출 fallback
            name = p.get("name", "")
            master = name.split()[0] if name else "선생님"
    ctx.template_variables["마스터명"] = master or "선생님"

    # 결제 월 추출 — success_txs는 이미 _compute_derived에서 세팅됨
    success_dates = []
    for t in (ctx.success_txs or []):
        d = (t.get("date") or t.get("created_at") or "")[:10]
        if d:
            success_dates.append(d)
    success_dates.sort()

    def month_of(date_str: str) -> str:
        if len(date_str) >= 7:
            try:
                return f"{int(date_str[5:7])}월"
            except ValueError:
                pass
        return ""

    if len(success_dates) >= 2:
        ctx.template_variables["이전결제월"] = month_of(success_dates[-2])
        ctx.template_variables["현재결제월"] = month_of(success_dates[-1])
    elif len(success_dates) == 1:
        ctx.template_variables["현재결제월"] = month_of(success_dates[0])
        ctx.template_variables["이전결제월"] = ""
    else:
        ctx.template_variables["이전결제월"] = ""
        ctx.template_variables["현재결제월"] = ""


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
    # 카드 관련 문맥이면 상품 변경 아님 — T8 카드변경으로 가야 함
    if "카드" in text:
        return False
    # "변경" 단독은 너무 광범위 — 맥락 키워드 결합
    strong = ["상품 변경", "상품변경", "다른 상품", "다른상품", "상품 바꿔",
              "차액", "바꿔서", "바꾸고 싶", "으로 변경", "로 변경"]
    return any(kw in text for kw in strong)


def _is_auto_payment_complaint(text: str) -> bool:
    """자동결제 불만/설명 요청 감지 — T4 리텐션 트리거"""
    kws = [
        "자동으로 결제", "자동결제", "자동결재",
        "자동으로 구독", "연장되어", "연장됐", "연장되었",
        "왜 결제", "결제된 줄 몰", "모르게 결제",
        "나도 모르게", "허락 없이", "취소 안 했는데",
    ]
    return any(kw in text for kw in kws)


def _is_cancel_check_intent(text: str) -> bool:
    """해지 신청 처리 확인 의도 감지 — T7 트리거.

    유저가 이전에 해지를 신청했고 "처리 됐나?"를 확인하는 문의.
    "해지" 맥락이 반드시 포함돼야 환불 철회 같은 다른 의도와 구분됨.
    """
    # 환불 철회("환불 취소/안 받을게") 의도는 제외
    if "환불 취소" in text or "환불취소" in text or "환불 안 할" in text or "환불 안할" in text:
        return False

    # "해지" 단어가 반드시 들어가야 T7 (환불 문맥에서 "처리됐나"는 T12 환불지연)
    if "해지" not in text:
        return False

    # 해지 + 확인/처리 의도 키워드
    kws = [
        "처리 되었", "처리됐", "처리 됐",
        "확인 차", "확인차", "확인 부탁", "확인 해주",
        "됐나", "됐는지", "되었는지", "되었나",
        "신청했는데", "신청해놨", "신청은 했",
        "잘 되었", "잘됐", "완료 됐", "완료됐",
    ]
    return any(kw in text for kw in kws)


def _is_membership_cancelled(ctx: WorkflowContext) -> bool:
    """멤버십이 해지된 상태인지 판단.

    판단 기준 (우선순위):
    1. memberships[].expiration == True → 해지
    2. memberships[].transaction_histories에 'cancel' state 존재
    3. products[].status == 'inactive'

    ctx.memberships는 MembershipItem dataclass (admin_api.py) — attribute 접근 사용.
    ctx.products는 dict list.
    """
    for mb in (ctx.memberships or []):
        # MembershipItem dataclass or dict 둘 다 지원
        expiration = getattr(mb, "expiration", None)
        if expiration is None and isinstance(mb, dict):
            expiration = mb.get("expiration")
        if expiration is True:
            return True

        histories = getattr(mb, "transaction_histories", None)
        if histories is None and isinstance(mb, dict):
            histories = mb.get("transactionHistories")
        for th in (histories or []):
            state = getattr(th, "state", None)
            if state is None and isinstance(th, dict):
                state = th.get("state")
            if state and "cancel" in str(state).lower():
                return True

    for p in (ctx.products or []):
        if isinstance(p, dict) and p.get("status") == "inactive":
            return True
    return False


def _extract_price_hints(text: str) -> list[int]:
    """유저 메시지에서 금액 표현 전부 추출 (출현 순서대로).

    예: "50만원 취소하고 10만원으로 변경" → [500000, 100000]
    """
    import re
    hints: list[int] = []
    # 모든 '\d+만(원)?' 매칭 — 만원 단위
    for m in re.finditer(r"(\d+)\s*만\s*원?", text):
        hints.append(int(m.group(1)) * 10000)
    # 모든 '[\d,]+원' 매칭 — 원 단위 (만 단위와 중복 가능하지만 보강용)
    for m in re.finditer(r"([\d,]{4,})\s*원", text):
        try:
            val = int(m.group(1).replace(",", ""))
            if val not in hints:
                hints.append(val)
        except ValueError:
            pass
    return hints


def _pick_new_price(hints: list[int], existing_amount: int) -> int:
    """여러 금액 후보 중 '신규 상품 가격'으로 쓸 값 선택.

    휴리스틱: 기존 결제금액과 다른 것 중 가장 작은 것.
    (유저가 "50만원 → 10만원" 말하면 10만이 신규 가격)
    """
    candidates = [h for h in hints if h > 0 and h != existing_amount]
    if not candidates:
        return 0
    return min(candidates)


def _calculate_exchange(ctx: WorkflowContext, user_text: str):
    """상품 변경 차액 계산 — Branch A"""
    from datetime import datetime
    from src.refund_engine import RefundEngine, RefundInput

    latest = ctx.latest_tx or (ctx.success_txs[-1] if ctx.success_txs else {})
    tx_amount = latest.get("amount", 0)
    if isinstance(tx_amount, str):
        try:
            tx_amount = int(tx_amount)
        except ValueError:
            tx_amount = 0
    # 필드명 통일: created_at 우선, 테스트 호환 위해 date도 fallback
    tx_date = (latest.get("created_at") or latest.get("date") or "")[:10]

    # 신규 상품 가격: 외부 주입이 우선, 없으면 메시지에서 파싱
    # 메시지에 "50만원 취소하고 10만원으로 변경" 같이 여러 금액이 나올 수 있으니,
    # 기존 결제금액과 다른 것 중 작은 값을 선택 (downgrade 가정).
    if ctx.new_product_price:
        new_price = ctx.new_product_price
    else:
        hints = _extract_price_hints(user_text)
        new_price = _pick_new_price(hints, tx_amount)

    pay_date = None
    if tx_date:
        try:
            pay_date = datetime.strptime(tx_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    ctx.template_variables["기존결제금액"] = f"{tx_amount:,}"
    ctx.template_variables["신규상품가격"] = f"{new_price:,}" if new_price else "(확인 필요)"

    if pay_date and tx_amount and new_price:
        # 상품명에서 주기 추정 (6개월 상품이면 monthly = total/6)
        cycle_months = _infer_cycle_from_products(ctx.products)
        monthly_price = tx_amount // cycle_months if cycle_months > 1 else tx_amount

        # 시점 복원: conv_time 있으면 그 날을 '오늘'로
        calc_today = None
        if ctx.conversation_time:
            try:
                calc_today = datetime.strptime(ctx.conversation_time[:10], "%Y-%m-%d").date()
            except ValueError:
                calc_today = None

        engine = RefundEngine()
        inp = RefundInput(
            total_paid=tx_amount,
            monthly_price=monthly_price,
            payment_date=pay_date,
            payment_cycle_days=30,
            content_accessed=ctx.has_accessed,
            today=calc_today,
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


def _infer_cycle_from_products(products: list[dict]) -> int:
    """상품명에서 결제 주기(개월) 추출. 없으면 1 (단건/1개월 기본).

    예: "박두환 Official club 투자동행반(6개월)" → 6
        "서재형 투자학교 (3개월)" → 3
        "1년 멤버십" → 12
    """
    if not products:
        return 1
    for p in products:
        name = (p.get("name") or p.get("product_name") or "").lower()
        if not name:
            continue
        if "12개월" in name or "1년" in name:
            return 12
        if "6개월" in name:
            return 6
        if "3개월" in name:
            return 3
        if "1개월" in name:
            return 1
    return 1


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
