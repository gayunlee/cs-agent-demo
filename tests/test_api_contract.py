"""API 컨트랙트 검증 테스트

enriched 389건의 admin_data 필드 shape을 dataclass로 파싱하면서
- 파싱 성공률
- 필드 타입 불일치
- 누락 필드
를 전수 측정한다.

실패 케이스는 "API 스펙 확인 필요" 리스트로 모아 개발자에게 요청할 수 있게 함.
"""
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.admin_api import (
    MembershipItem,
    MembershipTransaction,
    RefundHistoryItem,
    PaymentHistoryDetail,
    RefundHistoryDetail,
)

ENRICHED_PATH = "data/test_cases/refund_test_cases_enriched.json"


def test_enriched_contract():
    """enriched 389건의 admin_data.memberships/refunds를 dataclass로 파싱"""
    with open(ENRICHED_PATH) as f:
        cases = json.load(f)

    print(f"대상: {len(cases)}건 enriched\n")

    # 집계 카운터
    stats = {
        "total": len(cases),
        "with_admin": 0,
        "with_memberships": 0,
        "with_refunds": 0,
        "memberships_parsed_ok": 0,
        "memberships_parse_error": 0,
        "refunds_parsed_ok": 0,
        "refunds_parse_error": 0,
    }

    # 필드 존재 여부
    membership_field_presence = Counter()
    refund_field_presence = Counter()
    parse_errors = []

    # 샘플 저장
    membership_samples = []
    refund_samples = []

    for case in cases:
        admin = case.get("admin_data") or {}
        if not admin or admin.get("error"):
            continue
        stats["with_admin"] += 1

        # ── memberships ──
        memberships = admin.get("memberships") or []
        if memberships:
            stats["with_memberships"] += 1
            for m in memberships:
                if not isinstance(m, dict):
                    continue
                # 필드 존재 카운트
                for k in ("productName", "paymentCycle", "expiration",
                          "memberShipType", "membershipType", "transactionHistories"):
                    if k in m:
                        membership_field_presence[k] += 1
                # 파싱 시도
                try:
                    item = MembershipItem.from_api(m)
                    stats["memberships_parsed_ok"] += 1
                    if len(membership_samples) < 3:
                        membership_samples.append({
                            "chat_id": case.get("chat_id", "")[:12],
                            "product_name": item.product_name,
                            "payment_round": item.payment_round,
                            "tx_count": len(item.transaction_histories),
                            "membership_type": item.membership_type,
                        })
                except Exception as e:
                    stats["memberships_parse_error"] += 1
                    parse_errors.append({
                        "type": "membership",
                        "chat_id": case.get("chat_id", "")[:12],
                        "error": str(e),
                        "raw": {k: v for k, v in m.items() if k != "transactionHistories"},
                    })

        # ── refunds ──
        refunds = admin.get("refunds") or []
        if refunds:
            stats["with_refunds"] += 1
            for r in refunds:
                if not isinstance(r, dict):
                    continue
                for k in ("productName", "createdAt", "paymentHistory", "refundHistory"):
                    if k in r:
                        refund_field_presence[k] += 1
                try:
                    item = RefundHistoryItem.from_api(r)
                    stats["refunds_parsed_ok"] += 1
                    if len(refund_samples) < 3:
                        refund_samples.append({
                            "chat_id": case.get("chat_id", "")[:12],
                            "product_name": item.product_name,
                            "created_at": item.created_at,
                            "refund_amount": item.refund_history.refund_amount,
                            "refund_at": item.refund_history.refund_at,
                            "is_pending": item.is_pending,
                            "payment_amount": item.payment_history.amount,
                        })
                except Exception as e:
                    stats["refunds_parse_error"] += 1
                    parse_errors.append({
                        "type": "refund",
                        "chat_id": case.get("chat_id", "")[:12],
                        "error": str(e),
                        "raw": r,
                    })

    # ── 결과 리포트 ──
    print("=" * 60)
    print("  통계")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("  Membership 필드 존재율")
    print("=" * 60)
    total_m = stats["memberships_parsed_ok"] + stats["memberships_parse_error"]
    for k, c in membership_field_presence.most_common():
        pct = c / total_m * 100 if total_m else 0
        print(f"  {k}: {c}/{total_m} ({pct:.1f}%)")

    print("\n" + "=" * 60)
    print("  Refund 필드 존재율")
    print("=" * 60)
    total_r = stats["refunds_parsed_ok"] + stats["refunds_parse_error"]
    for k, c in refund_field_presence.most_common():
        pct = c / total_r * 100 if total_r else 0
        print(f"  {k}: {c}/{total_r} ({pct:.1f}%)")

    print("\n" + "=" * 60)
    print("  샘플 — Membership (파싱 후)")
    print("=" * 60)
    for s in membership_samples:
        print(f"  {s}")

    print("\n" + "=" * 60)
    print("  샘플 — Refund (파싱 후)")
    print("=" * 60)
    for s in refund_samples:
        print(f"  {s}")

    if parse_errors:
        print("\n" + "=" * 60)
        print(f"  파싱 에러 ({len(parse_errors)}건)")
        print("=" * 60)
        for e in parse_errors[:10]:
            print(f"  [{e['type']}] {e['chat_id']}: {e['error']}")

    # 최종 판정
    m_ok = stats["memberships_parse_error"] == 0
    r_ok = stats["refunds_parse_error"] == 0
    print("\n" + "=" * 60)
    print(f"  Membership 컨트랙트: {'✅ PASS' if m_ok else '❌ FAIL'}")
    print(f"  Refund 컨트랙트: {'✅ PASS' if r_ok else '❌ FAIL'}")
    print("=" * 60)

    return m_ok and r_ok


if __name__ == "__main__":
    ok = test_enriched_contract()
    sys.exit(0 if ok else 1)
