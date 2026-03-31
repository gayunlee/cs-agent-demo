"""관리자센터 API 클라이언트 — 유저 정보 조회"""
from __future__ import annotations
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ADMIN_BASE_URL = os.getenv("ADMIN_API_BASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN", "")


@dataclass
class UserInfo:
    user_id: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    signup_method: str = ""
    signup_date: str = ""
    memo: str = ""


@dataclass
class ProductInfo:
    my_product_id: str = ""
    master_name: str = ""
    product_name: str = ""
    grade: str = ""
    status: str = ""  # 활성/해지예정/만료
    platform: str = ""  # 어스캠퍼스/어스플러스
    start_date: str = ""
    next_payment_date: str = ""


@dataclass
class PaymentInfo:
    payment_id: str = ""
    product_name: str = ""
    amount: int = 0
    monthly_price: int = 0  # 1개월 정가 (환불 계산용)
    card_name: str = ""
    card_last4: str = ""
    payment_date: str = ""
    payment_cycle_days: int = 30
    is_promotion: bool = False
    promotion_price: int = 0


@dataclass
class UsageInfo:
    has_accessed: bool = False
    last_access_date: str = ""
    content_viewed_count: int = 0


@dataclass
class LookupResult:
    user: UserInfo | None = None
    products: list[ProductInfo] = field(default_factory=list)
    payments: list[PaymentInfo] = field(default_factory=list)
    usage: UsageInfo | None = None
    raw_responses: dict = field(default_factory=dict)

    def to_display(self) -> str:
        lines = ["📋 **조회 결과**", ""]
        if self.user:
            lines.append("**[회원 정보]**")
            lines.append(f"  이름: {self.user.name}")
            lines.append(f"  연락처: {self.user.phone}")
            lines.append(f"  가입방법: {self.user.signup_method}")
            lines.append(f"  가입일: {self.user.signup_date}")
            lines.append("")
        if self.products:
            lines.append("**[보유 상품]**")
            for p in self.products:
                lines.append(f"  - {p.master_name} / {p.product_name} ({p.grade}) — {p.status}")
                lines.append(f"    플랫폼: {p.platform}, 다음결제: {p.next_payment_date}")
            lines.append("")
        if self.payments:
            lines.append("**[결제 내역]**")
            for pay in self.payments:
                lines.append(f"  - {pay.product_name}: {pay.amount:,}원 ({pay.payment_date})")
                lines.append(f"    카드: {pay.card_name} ({pay.card_last4})")
                if pay.is_promotion:
                    lines.append(f"    ⚠️ 프로모션 가격 적용 중 ({pay.promotion_price:,}원)")
            lines.append("")
        if self.usage:
            lines.append("**[이용 현황]**")
            lines.append(f"  콘텐츠 열람: {'있음' if self.usage.has_accessed else '없음'}")
            if self.usage.has_accessed:
                lines.append(f"  최근 접속: {self.usage.last_access_date}")
                lines.append(f"  열람 콘텐츠: {self.usage.content_viewed_count}건")
            lines.append("")
        return "\n".join(lines)


class AdminAPIClient:
    """관리자센터 API 클라이언트.
    데모: ADMIN_API_TOKEN을 브라우저에서 복사해 .env에 설정.
    프로덕션: 서비스 계정 토큰으로 교체."""

    def __init__(self, base_url: str = "", token: str = ""):
        self.base_url = (base_url or ADMIN_BASE_URL).rstrip("/")
        self.token = token or ADMIN_TOKEN
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=10.0,
        )

    def _get(self, path: str, params: dict = None) -> dict:
        try:
            resp = self.client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Admin API {e.response.status_code}: {path}")
            return {}
        except Exception as e:
            logger.error(f"Admin API error: {path} — {e}")
            return {}

    # ── 유저 검색 ──

    def search_user_by_phone(self, phone: str) -> str | None:
        """전화번호로 유저 검색 → userId 반환"""
        data = self._get("/v3/users", params={"phoneNumber": phone})
        users = data.get("data", data.get("items", []))
        if isinstance(users, list) and users:
            return str(users[0].get("id", ""))
        return None

    # ── 개별 조회 ──

    def get_user(self, user_id: str) -> UserInfo:
        """GET /v1/users/{id} — 기본정보"""
        data = self._get(f"/v1/users/{user_id}")
        if not data:
            return UserInfo(user_id=user_id)
        d = data.get("data", data)
        return UserInfo(
            user_id=user_id,
            name=d.get("name", d.get("nickname", "")),
            phone=d.get("phoneNumber", d.get("phone", "")),
            email=d.get("email", ""),
            signup_method=d.get("signupMethod", d.get("provider", "")),
            signup_date=d.get("createdAt", d.get("signupDate", "")),
            memo=d.get("memo", ""),
        )

    def get_products(self, user_id: str) -> list[ProductInfo]:
        """GET /users/{id}/my-products — 보유 상품"""
        data = self._get(f"/users/{user_id}/my-products")
        items = data.get("data", data.get("items", []))
        if not isinstance(items, list):
            items = []
        products = []
        for item in items:
            products.append(ProductInfo(
                my_product_id=str(item.get("id", item.get("myProductId", ""))),
                master_name=item.get("masterName", item.get("master", {}).get("name", "")),
                product_name=item.get("productName", item.get("product", {}).get("name", "")),
                grade=item.get("grade", item.get("tier", "")),
                status=item.get("status", ""),
                platform=item.get("platform", ""),
                start_date=item.get("startDate", item.get("createdAt", "")),
                next_payment_date=item.get("nextPaymentDate", ""),
            ))
        return products

    def get_payments(self, user_id: str) -> list[PaymentInfo]:
        """GET /cs/refund-user/{userId}/products — 멤버십 결제 정보"""
        data = self._get(f"/cs/refund-user/{user_id}/products")
        items = data.get("data", data.get("items", []))
        if not isinstance(items, list):
            items = []
        payments = []
        for item in items:
            payments.append(PaymentInfo(
                payment_id=str(item.get("paymentId", item.get("id", ""))),
                product_name=item.get("productName", item.get("product", {}).get("name", "")),
                amount=item.get("amount", item.get("price", 0)),
                monthly_price=item.get("monthlyPrice", item.get("originalMonthlyPrice", 0)),
                card_name=item.get("cardName", item.get("card", {}).get("name", "")),
                card_last4=item.get("cardLast4", item.get("card", {}).get("last4", "")),
                payment_date=item.get("paymentDate", item.get("paidAt", "")),
                payment_cycle_days=item.get("paymentCycleDays", item.get("cycleDays", 30)),
                is_promotion=item.get("isPromotion", False),
                promotion_price=item.get("promotionPrice", 0),
            ))
        return payments

    def get_shop_payments(self, user_id: str) -> list[PaymentInfo]:
        """GET /cs/refund-user/{userId}/shop-product — 샵 결제 내역"""
        data = self._get(f"/cs/refund-user/{user_id}/shop-product")
        items = data.get("data", data.get("items", []))
        if not isinstance(items, list):
            items = []
        payments = []
        for item in items:
            payments.append(PaymentInfo(
                payment_id=str(item.get("paymentId", item.get("id", ""))),
                product_name=item.get("productName", ""),
                amount=item.get("amount", item.get("price", 0)),
                payment_date=item.get("paymentDate", item.get("paidAt", "")),
                card_name=item.get("cardName", ""),
                card_last4=item.get("cardLast4", ""),
            ))
        return payments

    def get_usage(self, user_id: str) -> UsageInfo:
        """GET /users/{id}/contents — 콘텐츠 열람 이력"""
        data = self._get(f"/users/{user_id}/contents")
        items = data.get("data", data.get("items", []))
        if not isinstance(items, list):
            items = []
        if not items:
            return UsageInfo(has_accessed=False)
        return UsageInfo(
            has_accessed=True,
            last_access_date=items[0].get("accessedAt", items[0].get("lastViewedAt", "")),
            content_viewed_count=len(items),
        )

    # ── 통합 조회 ──

    def lookup_all(self, user_id: str) -> LookupResult:
        """유저 ID로 전체 정보 조회"""
        user = self.get_user(user_id)
        products = self.get_products(user_id)
        payments = self.get_payments(user_id)
        shop_payments = self.get_shop_payments(user_id)
        usage = self.get_usage(user_id)
        return LookupResult(
            user=user,
            products=products,
            payments=payments + shop_payments,
            usage=usage,
        )

    def lookup_by_phone(self, phone: str) -> LookupResult | None:
        """전화번호로 유저 검색 → 전체 정보 조회"""
        user_id = self.search_user_by_phone(phone)
        if not user_id:
            logger.warning(f"유저 없음: {phone}")
            return None
        return self.lookup_all(user_id)
