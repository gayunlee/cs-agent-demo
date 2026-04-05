"""DSL 표현식에 주입할 커스텀 함수 registry.

YAML chain의 `check:` 필드에서 호출 가능한 헬퍼들.
전부 순수 함수 — 사이드이펙트 없음.
"""
from __future__ import annotations
from typing import Any

from .loader import get_loader


def has_keyword(text: str, group_name: str) -> bool:
    """유저 메시지에 키워드 그룹 중 하나라도 포함되는지.

    Args:
        text: 검사할 텍스트 (user_text)
        group_name: refund_chains.yaml의 keyword_groups 하위 키
    """
    if not text or not group_name:
        return False
    loader = get_loader()
    chains_file = loader.load("refund_chains.yaml")
    groups = chains_file.get("keyword_groups", {}) or {}
    keywords = groups.get(group_name, []) or []
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def unrefunded_count(success_txs: list | None, refund_txs: list | None) -> int:
    """미환불 결제 건수.

    round 또는 amount가 일치하는 환불 건이 있으면 환불된 것으로 간주.
    """
    if not success_txs:
        return 0
    refund_txs = refund_txs or []
    unrefunded = [
        t for t in success_txs
        if not any(
            (r.get("round") == t.get("round") or r.get("amount") == t.get("amount"))
            for r in refund_txs
        )
    ]
    return len(unrefunded)


def has_pending_refund(refunds: list | None) -> bool:
    """진행 중(refundAt 없음)인 환불 건 존재 여부."""
    if not refunds:
        return False
    for r in refunds:
        # RefundHistoryItem dataclass or dict
        if hasattr(r, "is_pending"):
            if r.is_pending:
                return True
        elif isinstance(r, dict):
            rh = r.get("refundHistory") or r.get("refund_history") or {}
            if not (rh.get("refundAt") or rh.get("refund_at")):
                return True
    return False


def len_of_list(value: Any) -> int:
    """리스트 길이 (None 안전)"""
    if value is None:
        return 0
    try:
        return len(value)
    except TypeError:
        return 0


# DSL에 주입할 기본 함수 레지스트리
DEFAULT_FUNCTIONS = {
    "has_keyword": has_keyword,
    "unrefunded_count": unrefunded_count,
    "has_pending_refund": has_pending_refund,
    "len_of": len_of_list,
}
