"""에이전트 도구 — 고객 정보 조회 (시뮬레이션)
실제 관리자센터 API 대신, 대화 맥락에서 추론한 mock 데이터를 반환.
프로덕션에서는 실제 API로 교체."""
from __future__ import annotations
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class AccountInfo:
    name: str = ""
    phone: str = ""
    email: str = ""
    signup_method: str = ""  # 휴대폰/네이버/구글/카카오
    signup_date: str = ""
    account_id: str = ""


@dataclass
class SubscriptionInfo:
    master_name: str = ""
    product_name: str = ""
    grade: str = ""  # 베이직/프리미엄/VIP
    status: str = ""  # 활성/해지예정/만료
    start_date: str = ""
    next_payment_date: str = ""
    platform: str = ""  # 어스캠퍼스/어스플러스


@dataclass
class PaymentInfo:
    last_payment_date: str = ""
    amount: int = 0
    card_name: str = ""
    card_last4: str = ""
    is_promotion: bool = False
    promotion_price: int = 0
    next_payment_date: str = ""


@dataclass
class UsageInfo:
    has_accessed: bool = False
    last_access_date: str = ""
    content_viewed_count: int = 0
    days_since_payment: int = 0


@dataclass
class LookupResult:
    """도구 조회 결과 통합"""
    account: AccountInfo | None = None
    subscription: SubscriptionInfo | None = None
    payment: PaymentInfo | None = None
    usage: UsageInfo | None = None
    tools_called: list = field(default_factory=list)

    def to_display(self) -> str:
        """CS팀에게 보여줄 조회 결과 포맷"""
        lines = ["📋 **조회 결과**", ""]
        if self.account:
            lines.append("**[회원 정보]**")
            lines.append(f"  이름: {self.account.name}")
            lines.append(f"  연락처: {self.account.phone}")
            lines.append(f"  가입방법: {self.account.signup_method}")
            lines.append(f"  가입일: {self.account.signup_date}")
            lines.append("")
        if self.subscription:
            lines.append("**[구독 정보]**")
            lines.append(f"  마스터: {self.subscription.master_name}")
            lines.append(f"  상품: {self.subscription.product_name} ({self.subscription.grade})")
            lines.append(f"  상태: {self.subscription.status}")
            lines.append(f"  플랫폼: {self.subscription.platform}")
            lines.append(f"  다음 결제: {self.subscription.next_payment_date}")
            lines.append("")
        if self.payment:
            lines.append("**[결제 내역]**")
            lines.append(f"  최근 결제: {self.payment.last_payment_date}")
            lines.append(f"  금액: {self.payment.amount:,}원")
            lines.append(f"  카드: {self.payment.card_name} ({self.payment.card_last4})")
            if self.payment.is_promotion:
                lines.append(f"  ⚠️ 프로모션 가격 적용 중 ({self.payment.promotion_price:,}원)")
            lines.append("")
        if self.usage:
            lines.append("**[이용 현황]**")
            lines.append(f"  콘텐츠 열람: {'있음' if self.usage.has_accessed else '없음'}")
            if self.usage.has_accessed:
                lines.append(f"  최근 접속: {self.usage.last_access_date}")
                lines.append(f"  열람 콘텐츠: {self.usage.content_viewed_count}건")
            lines.append(f"  결제 후 경과: {self.usage.days_since_payment}일")
            refund_eligible = self.usage.days_since_payment <= 7
            lines.append(f"  환불 규정: {'7일 이내 ✅' if refund_eligible else '7일 경과 ⚠️'}")
            lines.append("")

        lines.append(f"🔧 호출된 도구: {', '.join(self.tools_called)}")
        return "\n".join(lines)


# 마스터 목록 (실제 데이터 기반)
MASTERS = [
    {"name": "박두환", "product": "투자동행학교", "platform": "어스캠퍼스", "prices": [55000, 110000, 330000]},
    {"name": "김영익", "product": "김영익의 경제스쿨", "platform": "어스플러스", "prices": [49000, 99000]},
    {"name": "이항영", "product": "주식투자 마스터클래스", "platform": "어스캠퍼스", "prices": [77000, 154000]},
    {"name": "강환국", "product": "퀀트투자 아카데미", "platform": "어스플러스", "prices": [66000, 132000]},
    {"name": "체밀턴", "product": "1등매니저 따라하기", "platform": "어스캠퍼스", "prices": [55000, 550000]},
]

SIGNUP_METHODS = ["휴대폰", "네이버", "구글", "카카오"]
CARDS = ["신한카드", "삼성카드", "KB국민카드", "현대카드", "롯데카드", "하나카드"]
GRADES = ["베이직", "프리미엄", "VIP"]


def determine_needed_tools(text: str, category: str) -> list[str]:
    """대화 내용과 분류 결과로 필요한 조회 도구 결정"""
    tools = ["lookup_account"]  # 기본: 항상 회원정보 조회

    text_lower = text.lower()

    if category in ("결제·환불",) or any(kw in text_lower for kw in ["환불", "결제", "카드", "금액", "돈"]):
        tools.extend(["lookup_payment", "lookup_usage"])
    if category in ("구독·멤버십",) or any(kw in text_lower for kw in ["해지", "구독", "가입", "변경", "업그레이드"]):
        tools.append("lookup_subscription")
    if category in ("콘텐츠·수강",) or any(kw in text_lower for kw in ["강의", "수업", "녹화", "플랫폼", "어디서"]):
        tools.append("lookup_subscription")
    if any(kw in text_lower for kw in ["로그인", "비밀번호", "접속"]):
        pass  # account만으로 충분

    return list(dict.fromkeys(tools))  # 중복 제거


def simulate_lookup(text: str, category: str) -> LookupResult:
    """대화 맥락에서 mock 조회 데이터 생성.
    실제 프로덕션에서는 관리자센터 API 호출로 교체."""
    tools_needed = determine_needed_tools(text, category)

    now = datetime.now()
    result = LookupResult(tools_called=tools_needed)

    # 대화에서 마스터명 추출
    detected_master = None
    for m in MASTERS:
        if m["name"] in text or m["product"] in text:
            detected_master = m
            break
    if not detected_master:
        detected_master = random.choice(MASTERS)

    # 회원정보
    if "lookup_account" in tools_needed:
        result.account = AccountInfo(
            name="고객님",  # 실제로는 연락처로 조회
            phone="010-****-" + str(random.randint(1000, 9999)),
            email="user@example.com",
            signup_method=random.choice(SIGNUP_METHODS),
            signup_date=(now - timedelta(days=random.randint(30, 365))).strftime("%Y-%m-%d"),
            account_id=f"ACC-{random.randint(100000, 999999)}",
        )

    # 구독정보
    if "lookup_subscription" in tools_needed:
        status = random.choice(["활성", "활성", "활성", "해지예정"])
        result.subscription = SubscriptionInfo(
            master_name=detected_master["name"],
            product_name=detected_master["product"],
            grade=random.choice(GRADES),
            status=status,
            start_date=(now - timedelta(days=random.randint(7, 180))).strftime("%Y-%m-%d"),
            next_payment_date=(now + timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d"),
            platform=detected_master["platform"],
        )

    # 결제내역
    if "lookup_payment" in tools_needed:
        days_ago = random.choice([2, 5, 8, 15, 30])  # 결제일로부터 경과일
        amount = random.choice(detected_master["prices"])
        is_promo = random.random() < 0.3
        result.payment = PaymentInfo(
            last_payment_date=(now - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            amount=amount,
            card_name=random.choice(CARDS),
            card_last4=str(random.randint(1000, 9999)),
            is_promotion=is_promo,
            promotion_price=int(amount * 0.7) if is_promo else 0,
            next_payment_date=(now - timedelta(days=days_ago) + timedelta(days=30)).strftime("%Y-%m-%d"),
        )

    # 이용현황
    if "lookup_usage" in tools_needed:
        days_since = result.payment.last_payment_date if result.payment else (now - timedelta(days=5)).strftime("%Y-%m-%d")
        payment_date = datetime.strptime(days_since if isinstance(days_since, str) else days_since, "%Y-%m-%d")
        days_elapsed = (now - payment_date).days
        has_accessed = random.random() < 0.6
        result.usage = UsageInfo(
            has_accessed=has_accessed,
            last_access_date=(now - timedelta(days=random.randint(0, 3))).strftime("%Y-%m-%d") if has_accessed else "",
            content_viewed_count=random.randint(1, 15) if has_accessed else 0,
            days_since_payment=days_elapsed,
        )

    return result
