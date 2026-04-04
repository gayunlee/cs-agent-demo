"""관리자센터 API 클라이언트 — 유저 정보 조회

API 스키마 (Apidog 기준):
- GET /v3/users → {users: UserDto[]}
- GET /v1/users/{id} → {profile: UserProfileDto, memo, masters, blockedUsers}
- GET /users/{id}/my-products → {myProducts: MyProductDto[], pagination}
  - 필수 params: limit (string), status ("active"|"inactive")
- GET /cs/refund-user/{userId}/products → {_id, owner, product, transactions: TransactionHistoryResponse[]}
- GET /users/{id}/contents → {} (스키마 비어있음, 실제 응답 확인 필요)
"""
from __future__ import annotations
import os
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

ADMIN_BASE_URL = os.getenv("ADMIN_API_BASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN", "")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN", "")


@dataclass
class UserInfo:
    user_id: str = ""
    name: str = ""          # nickName
    phone: str = ""         # phoneNumber
    email: str = ""
    signup_method: str = "" # direct/google/apple/naver/kakao
    signup_state: str = ""  # ACTIVE/DORMANT/SUSPENDED/SUBSCRIBE
    signup_date: str = ""   # createdAt
    last_accessed: str = "" # lastAccessedAt
    content_view: int = 0   # contentView


@dataclass
class ProductInfo:
    my_product_id: str = ""
    master_name: str = ""
    product_name: str = ""
    product_type: str = ""   # SUBSCRIPTION / ONE_TIME_PURCHASE / INTEGRATION
    status: str = ""         # active / inactive
    price: int = 0           # 구매 당시 가격
    purchased_count: int = 0 # 결제 성공 횟수
    activated_at: str = ""
    expired_at: str = ""


@dataclass
class TransactionInfo:
    transaction_id: str = ""
    round: int = 0           # 결제 회차
    provider: str = ""       # toss/hecto/google/apple 등
    state: str = ""          # purchased_success/purchased_refund 등
    method: str = ""         # 결제 방법
    method_info: str = ""    # 카드사 등
    amount: int = 0          # 금액
    created_at: str = ""


@dataclass
class UsageInfo:
    has_accessed: bool = False
    content_view_count: int = 0
    last_access_date: str = ""


# ── Membership history dataclasses (GET /v1/users/{id}/membership-history) ──
# 스펙: openspec/api-interfaces.md L148~168


@dataclass
class MembershipTransaction:
    """membership-history 내 개별 거래 이력"""
    created_at: str = ""
    state: str = ""
    method: str = ""
    purchased_amount: str = ""
    purchased_method: str = ""
    card_number: str = ""
    expired_at: str = ""
    changed_display_name: str = ""
    easy_pay_code: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "MembershipTransaction":
        return cls(
            created_at=data.get("createdAt", ""),
            state=data.get("state", ""),
            method=data.get("method", ""),
            purchased_amount=data.get("purchasedAmount", "") or "",
            purchased_method=data.get("purchasedMethod", "") or "",
            card_number=data.get("cardNumber", "") or "",
            expired_at=data.get("expiredAt", "") or "",
            changed_display_name=data.get("changedDisplayName", "") or "",
            easy_pay_code=data.get("easyPayCode", "") or "",
        )


@dataclass
class MembershipItem:
    """membership-history 내 개별 멤버십"""
    product_name: str = ""
    payment_cycle: int = 1         # 결제 주기 (개월)
    expiration: bool = False
    membership_type: str = ""      # card/CA/VA/corp/PZ/unknown
    transaction_histories: list[MembershipTransaction] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "MembershipItem":
        cycle_raw = data.get("paymentCycle", 1)
        # 스펙은 number지만 실제 응답이 string일 수 있음 — 방어적 변환
        try:
            cycle = int(cycle_raw) if cycle_raw else 1
        except (ValueError, TypeError):
            cycle = 1
        txs = [
            MembershipTransaction.from_api(t)
            for t in (data.get("transactionHistories") or [])
            if isinstance(t, dict)
        ]
        # API 스펙: memberShipType (capital S) / enriched 데이터 호환: membershipType
        mtype = data.get("memberShipType") or data.get("membershipType") or ""
        return cls(
            product_name=data.get("productName", ""),
            payment_cycle=cycle,
            expiration=bool(data.get("expiration", False)),
            membership_type=mtype,
            transaction_histories=txs,
        )


# ── Refund history dataclasses (GET /v1/users/{id}/membership-refund-history) ──
# 스펙: openspec/api-interfaces.md L176~195


@dataclass
class PaymentHistoryDetail:
    """환불 이력 내 원결제 정보"""
    amount: int = 0
    card_type: str = ""
    card_no: str = ""
    key: str = ""
    created_at: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "PaymentHistoryDetail":
        amount_raw = data.get("amount", 0)
        try:
            amount = int(amount_raw) if amount_raw else 0
        except (ValueError, TypeError):
            amount = 0
        return cls(
            amount=amount,
            card_type=data.get("cardType", "") or "",
            card_no=data.get("cardNo", "") or "",
            key=data.get("key", "") or "",
            created_at=data.get("createdAt", "") or "",
        )


@dataclass
class RefundHistoryDetail:
    """환불 이력 내 환불 상세. refund_at="" 이면 진행중."""
    refund_amount: int = 0
    refund_at: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "RefundHistoryDetail":
        amount_raw = data.get("refundAmount", 0)
        try:
            amount = int(amount_raw) if amount_raw else 0
        except (ValueError, TypeError):
            amount = 0
        # refundAt이 None일 수 있음 → ""로 통일
        return cls(
            refund_amount=amount,
            refund_at=(data.get("refundAt") or ""),
        )

    @property
    def is_pending(self) -> bool:
        """환불 진행 중(아직 refundAt 없음)"""
        return not self.refund_at


@dataclass
class RefundHistoryItem:
    """membership-refund-history 단일 아이템"""
    product_name: str = ""
    created_at: str = ""
    payment_history: PaymentHistoryDetail = field(default_factory=PaymentHistoryDetail)
    refund_history: RefundHistoryDetail = field(default_factory=RefundHistoryDetail)

    @classmethod
    def from_api(cls, data: dict) -> "RefundHistoryItem":
        return cls(
            product_name=data.get("productName", ""),
            created_at=data.get("createdAt", ""),
            payment_history=PaymentHistoryDetail.from_api(data.get("paymentHistory") or {}),
            refund_history=RefundHistoryDetail.from_api(data.get("refundHistory") or {}),
        )

    @property
    def is_pending(self) -> bool:
        return self.refund_history.is_pending


@dataclass
class LookupResult:
    user: UserInfo | None = None
    products: list[ProductInfo] = field(default_factory=list)
    transactions: list[TransactionInfo] = field(default_factory=list)
    usage: UsageInfo | None = None

    def to_display(self) -> str:
        lines = ["📋 **조회 결과**", ""]
        if self.user:
            lines.append("**[회원 정보]**")
            lines.append(f"  닉네임: {self.user.name}")
            lines.append(f"  연락처: {self.user.phone}")
            lines.append(f"  가입방법: {self.user.signup_method}")
            lines.append(f"  상태: {self.user.signup_state}")
            lines.append(f"  가입일: {self.user.signup_date}")
            lines.append(f"  최근접속: {self.user.last_accessed}")
            lines.append(f"  콘텐츠열람: {self.user.content_view}건")
            lines.append("")
        if self.products:
            lines.append("**[보유 상품]**")
            for p in self.products:
                lines.append(f"  - {p.master_name} / {p.product_name}")
                lines.append(f"    유형: {p.product_type}, 상태: {p.status}, 가격: {p.price:,}원")
                lines.append(f"    결제횟수: {p.purchased_count}회, 만료: {p.expired_at}")
            lines.append("")
        if self.transactions:
            lines.append("**[거래 내역]**")
            for t in self.transactions:
                lines.append(f"  - [{t.round}회차] {t.state} / {t.amount:,}원 ({t.created_at})")
                lines.append(f"    결제: {t.method} {t.method_info} ({t.provider})")
            lines.append("")
        if self.usage:
            lines.append("**[이용 현황]**")
            lines.append(f"  콘텐츠 열람: {'있음' if self.usage.has_accessed else '없음'} ({self.usage.content_view_count}건)")
            if self.usage.last_access_date:
                lines.append(f"  최근 접속: {self.usage.last_access_date}")
            lines.append("")
        return "\n".join(lines)


class AdminAPIClient:
    """관리자센터 API 클라이언트. 401 시 refresh token으로 자동 갱신."""

    def __init__(self, base_url: str = "", token: str = "", refresh_token: str = ""):
        self.base_url = (base_url or ADMIN_BASE_URL).rstrip("/")
        self.token = token or ADMIN_TOKEN
        self.refresh_token = refresh_token or REFRESH_TOKEN
        self.client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def _get(self, path: str, params: dict = None) -> dict | list:
        try:
            resp = self.client.get(path, params=params, headers=self._headers())
            if resp.status_code == 401 and self.refresh_token:
                logger.info("401 → refresh token으로 갱신 시도")
                if self._refresh():
                    resp = self.client.get(path, params=params, headers=self._headers())
            logger.info(f"Admin API {resp.status_code}: {path}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Admin API {e.response.status_code}: {path} — {e.response.text[:300]}")
            return {}
        except Exception as e:
            logger.error(f"Admin API error: {path} — {e}")
            return {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _refresh(self) -> bool:
        """POST /v2/auth/refresh → 새 accessToken 획득"""
        try:
            resp = self.client.post(
                "/v2/auth/refresh",
                headers={"Authorization": f"Bearer {self.refresh_token}"},
            )
            if resp.status_code == 200 or resp.status_code == 201:
                data = resp.json()
                new_token = data.get("accessToken", "")
                new_refresh = data.get("refreshToken", "")
                if new_token:
                    self.token = new_token
                    logger.info("토큰 갱신 성공")
                if new_refresh:
                    self.refresh_token = new_refresh
                return bool(new_token)
            else:
                logger.error(f"토큰 갱신 실패: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"토큰 갱신 오류: {e}")
            return False

    # ── 유저 검색 ──

    def search_user_by_phone(self, phone: str) -> str | None:
        """GET /v3/users — 전화번호로 유저 검색 → userId 반환"""
        data = self._get("/v3/users", params={
            "phoneNumber": phone,
            "offset": 0,
            "limit": 10,
        })
        users = data.get("users", [])
        if isinstance(users, list) and users:
            active = [u for u in users if not u.get("deleted", False)]
            target = active[0] if active else users[0]
            return str(target.get("id", ""))
        return None

    # ── 개별 조회 ──

    def get_user(self, user_id: str) -> UserInfo:
        """GET /v1/users/{id} → {profile: UserProfileDto, memo, masters}"""
        data = self._get(f"/v1/users/{user_id}")
        if not data:
            return UserInfo(user_id=user_id)

        profile = data.get("profile", {})
        return UserInfo(
            user_id=user_id,
            name=profile.get("nickName", ""),
            phone=profile.get("phoneNumber", ""),
            email=profile.get("email", ""),
            signup_method=profile.get("signUpMethod", ""),
            signup_state=profile.get("signUpState", ""),
            signup_date=profile.get("createdAt", ""),
            last_accessed=profile.get("lastAccessedAt", ""),
            content_view=profile.get("contentView", 0),
        )

    def get_products(self, user_id: str) -> list[ProductInfo]:
        """GET /users/{id}/my-products → {myProducts: MyProductDto[]}
        필수 params: limit (string), status"""
        products = []
        for status in ("active", "inactive"):
            data = self._get(f"/users/{user_id}/my-products", params={
                "limit": "50",
                "status": status,
            })
            items = data.get("myProducts", [])
            if not isinstance(items, list):
                continue
            for item in items:
                product = item.get("product", {})
                products.append(ProductInfo(
                    my_product_id=str(item.get("id", "")),
                    master_name=product.get("masterName", ""),
                    product_name=product.get("name", ""),
                    product_type=item.get("type", ""),
                    status=item.get("status", status),
                    price=item.get("price", 0),
                    purchased_count=item.get("purchasedSuccessCount", 0),
                    activated_at=item.get("activatedAt", ""),
                    expired_at=item.get("expiredAt", ""),
                ))
        return products

    def get_refund_info(self, user_id: str) -> tuple[list[ProductInfo], list[TransactionInfo]]:
        """GET /cs/refund-user/{userId}/products → 멤버십 + 거래내역"""
        data = self._get(f"/cs/refund-user/{user_id}/products")
        if not data or isinstance(data, list):
            # 응답이 배열일 수 있음
            items = data if isinstance(data, list) else []
        else:
            items = [data] if data.get("_id") else []

        products = []
        transactions = []

        for item in items:
            product_data = item.get("product", {})
            products.append(ProductInfo(
                my_product_id=str(item.get("_id", "")),
                product_name=product_data.get("name", "") if isinstance(product_data, dict) else "",
                status=item.get("status", ""),
                activated_at=item.get("createdAt", ""),
                expired_at=item.get("expiredAt", ""),
            ))

            for tx in item.get("transactions", []):
                amount_raw = tx.get("amount", "0")
                try:
                    amount = int(amount_raw)
                except (ValueError, TypeError):
                    amount = 0
                transactions.append(TransactionInfo(
                    transaction_id=str(tx.get("_id", "")),
                    round=tx.get("round", 0),
                    provider=tx.get("provider", ""),
                    state=tx.get("state", ""),
                    method=tx.get("method", ""),
                    method_info=tx.get("methodInfo", ""),
                    amount=amount,
                    created_at=tx.get("createdAt", ""),
                ))

        return products, transactions

    def get_membership_history(self, user_id: str) -> tuple[UsageInfo, list[MembershipItem]]:
        """GET /v1/users/{id}/membership-history — 멤버십 이용 이력

        스펙: openspec/api-interfaces.md L148
        Response: { memberships: MembershipItem[] }
        """
        data = self._get(f"/v1/users/{user_id}/membership-history")
        if not data:
            return UsageInfo(has_accessed=False), []

        raw_memberships = data.get("memberships", [])
        if not isinstance(raw_memberships, list):
            raw_memberships = []

        memberships = [
            MembershipItem.from_api(m) for m in raw_memberships if isinstance(m, dict)
        ]

        # 거래 이력이 있으면 이용한 것으로 판단
        total_tx = sum(len(m.transaction_histories) for m in memberships)
        latest_date = ""
        for m in memberships:
            for tx in m.transaction_histories:
                if tx.created_at and tx.created_at > latest_date:
                    latest_date = tx.created_at

        return UsageInfo(
            has_accessed=total_tx > 0,
            content_view_count=total_tx,
            last_access_date=latest_date,
        ), memberships

    def get_refund_history(self, user_id: str) -> list[RefundHistoryItem]:
        """GET /v1/users/{id}/membership-refund-history — 기존 환불 이력

        스펙: openspec/api-interfaces.md L176
        Response: { refunds: RefundHistoryItem[] }
        """
        data = self._get(f"/v1/users/{user_id}/membership-refund-history",
                         params={"offset": 0, "limit": 20})
        if not data:
            return []
        raw_refunds = data.get("refunds", []) if isinstance(data, dict) else []
        if not isinstance(raw_refunds, list):
            return []
        return [RefundHistoryItem.from_api(r) for r in raw_refunds if isinstance(r, dict)]

    # ── 통합 조회 ──

    def lookup_all(self, user_id: str) -> LookupResult:
        """유저 ID로 전체 정보 조회"""
        user = self.get_user(user_id)
        products = self.get_products(user_id)
        refund_products, transactions = self.get_refund_info(user_id)
        usage, _ = self.get_membership_history(user_id)

        # profile의 contentView로 usage 보충
        if user.content_view > 0 and not usage.has_accessed:
            usage = UsageInfo(
                has_accessed=True,
                content_view_count=user.content_view,
                last_access_date=user.last_accessed,
            )

        # refund_products에서 products 보충 (중복 제거)
        existing_ids = {p.my_product_id for p in products}
        for rp in refund_products:
            if rp.my_product_id not in existing_ids:
                products.append(rp)

        return LookupResult(
            user=user,
            products=products,
            transactions=transactions,
            usage=usage,
        )

    def lookup_by_phone(self, phone: str) -> LookupResult | None:
        """전화번호로 유저 검색 → 전체 정보 조회"""
        user_id = self.search_user_by_phone(phone)
        if not user_id:
            logger.warning(f"유저 없음: {phone}")
            return None
        return self.lookup_all(user_id)
