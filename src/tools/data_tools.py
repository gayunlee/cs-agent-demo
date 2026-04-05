"""데이터 조회 tool들 — src/admin_api.py의 AdminAPIClient를 @tool로 wrap.

⚠️ 실제 API 호출 vs Mock 전환:
- 환경변수 ADMIN_API_BASE_URL + ADMIN_API_TOKEN 있으면 실제 호출
- 없거나 테스트 모드면 전달된 context(enriched/mock data) 반환

이 tool들은 상담 Agent가 유저 식별 + 관련 데이터 수집에 사용.
기존 RefundAgentV2._use_enriched_data / _call_all_tools 로직을 이식.
"""
from __future__ import annotations
import os
from typing import Any

from strands import tool

from src.tools.workflow_tools import get_context


# ─────────────────────────────────────────────────────────
# Mock/Real 판단
# ─────────────────────────────────────────────────────────

def _is_mock_mode() -> bool:
    """실제 admin API 토큰이 없으면 mock 모드"""
    return not (os.getenv("ADMIN_API_BASE_URL") and os.getenv("ADMIN_API_TOKEN"))


def _get_client():
    """실제 API 모드일 때만 AdminAPIClient 반환"""
    from src.admin_api import AdminAPIClient
    return AdminAPIClient()


# ─────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────


@tool
def search_user_by_phone(phone: str) -> dict:
    """전화번호로 유저 검색 → userId 반환.

    Args:
        phone: 전화번호 (하이픈 포함/제외 모두)

    Returns:
        dict: {"us_user_id": str, "found": bool, "source": "mock"|"api"}
    """
    ctx = get_context()

    # Mock 모드: context에 이미 주입된 us_user_id 반환
    if _is_mock_mode():
        uid = ctx.get("ctx", {}).get("us_user_id") or ctx.get("us_user_id", "")
        return {
            "us_user_id": uid,
            "found": bool(uid),
            "source": "mock",
        }

    # Real 모드
    try:
        client = _get_client()
        uid = client.search_user_by_phone(phone) or ""
        return {
            "us_user_id": uid,
            "found": bool(uid),
            "source": "api",
        }
    except Exception as e:
        return {"us_user_id": "", "found": False, "source": "api", "error": str(e)}


@tool
def get_user_profile(us_user_id: str) -> dict:
    """유저 프로필 조회 (GET /v1/users/{id}).

    Args:
        us_user_id: admin API userId

    Returns:
        dict: 프로필 정보 (name, phone, signup_method 등)
    """
    ctx = get_context()

    if _is_mock_mode():
        return {
            "us_user_id": us_user_id,
            "name": ctx.get("ctx", {}).get("user_name", ""),
            "signup_method": ctx.get("ctx", {}).get("signup_method", ""),
            "source": "mock",
        }

    try:
        client = _get_client()
        user = client.get_user(us_user_id)
        return {
            "us_user_id": user.user_id,
            "name": user.name,
            "phone": user.phone,
            "signup_method": user.signup_method,
            "signup_state": user.signup_state,
            "source": "api",
        }
    except Exception as e:
        return {"error": str(e), "source": "api"}


@tool
def get_membership_history_summary() -> dict:
    """현재 유저의 멤버십 구매 이력 요약.

    ⚠️ paymentCycle은 회차(카운트)이지 주기 아님 — payment_round로 이름 통일.
    주기 정보 필요하면 compose/calculate에서 상품명 파싱.

    Returns:
        dict: {
            "memberships": [...],   # 상품명, 회차, 거래 이력 요약
            "has_accessed": bool,    # 콘텐츠 열람 여부
            "source": "mock"|"api"
        }
    """
    ctx = get_context()
    inner = ctx.get("ctx", {}) or ctx

    if _is_mock_mode():
        memberships = inner.get("memberships", []) or []
        # dataclass → dict 변환 (LLM에 전달 위해)
        simplified = []
        for m in memberships:
            if hasattr(m, "product_name"):
                simplified.append({
                    "product_name": m.product_name,
                    "payment_round": m.payment_round,
                    "is_onetime": getattr(m, "is_onetime", False),
                    "tx_count": len(m.transaction_histories),
                })
            elif isinstance(m, dict):
                simplified.append({
                    "product_name": m.get("productName") or m.get("product_name", ""),
                    "payment_round": m.get("paymentCycle") or m.get("payment_round", 0),
                })
        return {
            "memberships": simplified,
            "has_accessed": inner.get("has_accessed", False),
            "source": "mock",
        }

    # Real 모드
    uid = inner.get("us_user_id", "")
    if not uid:
        return {"memberships": [], "has_accessed": False, "source": "api", "error": "no user_id"}
    try:
        client = _get_client()
        usage, memberships = client.get_membership_history(uid)
        simplified = [
            {
                "product_name": m.product_name,
                "payment_round": m.payment_round,
                "is_onetime": m.is_onetime,
                "tx_count": len(m.transaction_histories),
            }
            for m in memberships
        ]
        return {
            "memberships": simplified,
            "has_accessed": usage.has_accessed,
            "source": "api",
        }
    except Exception as e:
        return {"memberships": [], "source": "api", "error": str(e)}


@tool
def get_refund_history_summary() -> dict:
    """현재 유저의 환불 이력 요약.

    Returns:
        dict: {
            "refunds": [...],       # productName, refund_amount, is_pending
            "has_pending": bool,     # 진행 중 환불 있는지
            "source": "mock"|"api"
        }
    """
    ctx = get_context()
    inner = ctx.get("ctx", {}) or ctx

    if _is_mock_mode():
        refunds = inner.get("refunds", []) or []
        simplified = []
        has_pending = False
        for r in refunds:
            if hasattr(r, "product_name"):
                simplified.append({
                    "product_name": r.product_name,
                    "created_at": r.created_at,
                    "refund_amount": r.refund_history.refund_amount,
                    "is_pending": r.is_pending,
                })
                if r.is_pending:
                    has_pending = True
            elif isinstance(r, dict):
                rh = r.get("refundHistory") or r.get("refund_history") or {}
                pending = not (rh.get("refundAt") or rh.get("refund_at"))
                simplified.append({
                    "product_name": r.get("productName") or r.get("product_name", ""),
                    "refund_amount": rh.get("refundAmount") or rh.get("refund_amount", 0),
                    "is_pending": pending,
                })
                if pending:
                    has_pending = True
        return {
            "refunds": simplified,
            "has_pending": has_pending,
            "source": "mock",
        }

    # Real 모드
    uid = inner.get("us_user_id", "")
    if not uid:
        return {"refunds": [], "has_pending": False, "source": "api", "error": "no user_id"}
    try:
        client = _get_client()
        refunds = client.get_refund_history(uid)
        simplified = [
            {
                "product_name": r.product_name,
                "created_at": r.created_at,
                "refund_amount": r.refund_history.refund_amount,
                "is_pending": r.is_pending,
            }
            for r in refunds
        ]
        return {
            "refunds": simplified,
            "has_pending": any(r["is_pending"] for r in simplified),
            "source": "api",
        }
    except Exception as e:
        return {"refunds": [], "source": "api", "error": str(e)}


@tool
def get_transaction_list() -> dict:
    """현재 유저의 결제 내역 요약 (성공 + 환불 건 구분).

    Returns:
        dict: {
            "success_txs": [...],   # 결제 성공
            "refund_txs": [...],    # 환불 완료
            "unrefunded_count": int,
            "source": "mock"|"api"
        }
    """
    from src.domain.functions import unrefunded_count

    ctx = get_context()
    inner = ctx.get("ctx", {}) or ctx

    # Mock/Real 공통: context에 이미 transactions 들어있음 (Agent 턴 시작 시 주입)
    success_txs = inner.get("success_txs", []) or []
    refund_txs = inner.get("refund_txs", []) or []

    return {
        "success_txs": success_txs,
        "refund_txs": refund_txs,
        "unrefunded_count": unrefunded_count(success_txs, refund_txs),
        "source": "mock" if _is_mock_mode() else "api",
    }
