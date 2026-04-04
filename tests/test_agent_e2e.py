"""Agent e2e 테스트 — 유저 메시지 × mock API 응답

모든 조합에서 데이터 상태가 답변을 결정하는지 검증.
예외: 카드 키워드 → T8
"""
import json
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.workflow import WorkflowContext, run_workflow


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def load_test_data():
    with open("data/test_cases/test_messages.json") as f:
        messages = json.load(f)
    with open("data/mock_scenarios/mock_api_responses.json") as f:
        mocks = json.load(f)
    return messages, mocks


def resolve_dates(mock):
    """RECENT_*DAYS 플레이스홀더를 실제 날짜로 치환"""
    raw = json.dumps(mock)
    raw = raw.replace('"RECENT_3DAYS"', f'"{days_ago(3)}"')
    raw = raw.replace('"RECENT_5DAYS"', f'"{days_ago(5)}"')
    raw = raw.replace('"RECENT_20DAYS"', f'"{days_ago(20)}"')
    raw = raw.replace('"RECENT_3DAYS_PLUS_6M"', f'"{days_ago(-180)}"')
    raw = raw.replace('"RECENT_5DAYS_PLUS_6M"', f'"{days_ago(-175)}"')
    raw = raw.replace('"RECENT_5DAYS_PLUS_1M"', f'"{days_ago(-25)}"')
    raw = raw.replace('"RECENT_20DAYS_PLUS_1M"', f'"{days_ago(-10)}"')
    return json.loads(raw)


def run_e2e():
    messages, mocks = load_test_data()

    passed = 0
    failed = 0
    failures = []

    # 카드 메시지 패턴 (이것만 T8)
    card_keywords = ["카드 변경", "카드변경", "카드 분실", "카드 만료", "카드 재발급"]

    for mock_name, mock_data in mocks.items():
        mock_resolved = resolve_dates(mock_data)
        expected = mock_resolved["expected_template"]

        for pattern, msgs in messages.items():
            # 카드 mock은 카드 메시지에만 적용
            if mock_name == "mock_card_issue" and pattern != "card_change":
                continue
            # 카드 메시지는 카드 mock에서만 테스트 (다른 mock에서는 스킵)
            if pattern == "card_change" and mock_name != "mock_card_issue":
                continue

            for msg in msgs:
                is_card = any(kw in msg.lower() for kw in card_keywords)
                if is_card:
                    test_expected = "T8_카드변경_안내"
                else:
                    test_expected = expected

                wf_ctx = WorkflowContext(
                    user_messages=[msg],
                    us_user_id=mock_resolved.get("us_user_id", ""),
                    products=mock_resolved.get("products", []),
                    transactions=mock_resolved.get("transactions", []),
                    has_accessed=mock_resolved.get("usage", {}).get("accessed", False),
                    memberships=mock_resolved.get("memberships", []),
                    refunds=mock_resolved.get("refunds", []),
                )
                result = run_workflow(wf_ctx)

                if result == test_expected:
                    passed += 1
                else:
                    failed += 1
                    failures.append({
                        "mock": mock_name,
                        "pattern": pattern,
                        "msg": msg[:50],
                        "expected": test_expected,
                        "actual": result,
                        "path": wf_ctx.path,
                    })

    total = passed + failed
    print(f"{'='*60}")
    print(f"  e2e 테스트: {passed}/{total} ({passed/total*100:.1f}%)")
    print(f"  (메시지 {sum(len(v) for v in messages.values())}개 × mock {len(mocks)}개)")
    print(f"{'='*60}")

    if failures:
        print(f"\n  실패 {len(failures)}건:")
        # 패턴별로 그룹핑
        from collections import Counter
        fail_patterns = Counter()
        for f in failures:
            fail_patterns[f"{f['actual']} (실제) ← {f['expected']} (기대) | mock={f['mock']}"] += 1

        for pat, cnt in fail_patterns.most_common(10):
            print(f"    {pat}: {cnt}건")

        print(f"\n  실패 예시:")
        for f in failures[:5]:
            print(f"    mock={f['mock']} | \"{f['msg']}\"")
            print(f"      기대: {f['expected']} → 실제: {f['actual']}")
            print(f"      경로: {f['path']}")
    else:
        print(f"\n  전부 통과!")

    # 환불 금액 검증 (T2 케이스)
    print(f"\n{'='*60}")
    print(f"  환불 금액 검증 (T2 케이스)")
    print(f"{'='*60}")

    for mock_name in ["mock_full_refund", "mock_partial_accessed", "mock_partial_expired", "mock_multi_payment"]:
        mock_resolved = resolve_dates(mocks[mock_name])
        wf_ctx = WorkflowContext(
            user_messages=["환불해주세요"],
            us_user_id=mock_resolved.get("us_user_id", ""),
            products=mock_resolved.get("products", []),
            transactions=mock_resolved.get("transactions", []),
            has_accessed=mock_resolved.get("usage", {}).get("accessed", False),
        )
        run_workflow(wf_ctx)
        refund_type = wf_ctx.template_variables.get("환불유형", "?")
        amount = wf_ctx.template_variables.get("환불금액", "?")
        print(f"  {mock_name}: {refund_type} | 환불 {amount}원 | {wf_ctx.path[-1] if wf_ctx.path else ''}")

    return failed == 0


if __name__ == "__main__":
    success = run_e2e()
    sys.exit(0 if success else 1)
