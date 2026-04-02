"""Eval v2: enriched 데이터 기반 — 조회 결과 기반 템플릿 매칭 정확도

1. enriched 데이터 로드 (실제 유저 정보 포함)
2. agent v2 실행 (admin_data 전달 → API 호출 없이 판단)
3. 매니저 실제 응답 기반 ground truth와 비교
4. 결과 분석
"""
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.refund_agent_v2 import RefundAgentV2


def classify_manager_response(mgr_text: str) -> str:
    """매니저 응답 → ground truth 템플릿"""
    m = (mgr_text or "").lower()
    if '구독해지방법' in m or '구독해지 방법' in m or '정기결제 구독해지' in m:
        return 'T1_구독해지_방법_앱'
    if '7일 이내 구독권 미개시' in m or '환불 규정에 따른' in m:
        return 'T2_환불_규정_금액'
    if '환불금' in m and '원' in m and ('차감' in m or '수수료' in m):
        return 'T2_환불_규정_금액'
    if '6개월마다 정기결제' in m and '환불' in m:
        return 'T2_환불_규정_금액'
    if '환불 접수 완료' in m or '환불접수 완료' in m:
        return 'T3_환불_접수_완료'
    if '정기적으로 제공되는 콘텐츠' in m or '구독형 스터디' in m:
        return 'T4_자동결제_설명'
    if '성함' in m and ('휴대전화' in m or '번호' in m) and len(m) < 150:
        return 'T6_본인확인_요청'
    if '카드 변경' in m or '결제카드' in m or '카드변경' in m:
        return 'T8_카드변경_안내'
    if '해지처리 도와드' in m or '해지 처리 도와드' in m:
        return 'T9_해지처리_완료'
    return 'T99_기타'


def run_eval(data_path: str = "data/refund_test_cases_enriched.json"):
    with open(data_path) as f:
        cases = json.load(f)

    # admin_data + 매니저 응답 있는 케이스만
    valid = [c for c in cases
             if c.get('admin_data') and not c['admin_data'].get('error')
             and c.get('manager_responses') and len(c['manager_responses'][0]) > 20]
    print(f"테스트 케이스: {len(valid)}건 (enriched + 매니저 응답 있음)\n")

    agent = RefundAgentV2(mock=True)  # LLM 없이 mock 분류기만

    correct = 0
    total = 0
    mismatches = []
    pred_counts = Counter()
    gt_counts = Counter()
    confusion = Counter()  # (predicted, actual) pairs

    for case in valid:
        mgr = case['manager_responses'][0]
        gt = classify_manager_response(mgr)
        gt_counts[gt] += 1

        # 대화 시점 추출 (첫 유저 메시지 타임스탬프)
        conv_time = ""
        if case.get("user_timestamps"):
            ts = case["user_timestamps"][0]
            if isinstance(ts, (int, float)):
                from datetime import datetime, timezone
                conv_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        result = agent.process(
            user_messages=case["user_messages"],
            chat_id=case.get("chat_id", ""),
            admin_data=case.get("admin_data"),
            conversation_time=conv_time,
        )

        pred = result.template_id
        pred_counts[pred] += 1
        confusion[(pred, gt)] += 1

        if pred == gt:
            correct += 1
        else:
            mismatches.append({
                "chat_id": case.get("chat_id", "")[:12],
                "user": case["user_messages"][0][:60],
                "intent": result.intent,
                "predicted": pred,
                "actual": gt,
                "manager": mgr[:120],
                "products": len(case["admin_data"].get("products", [])),
                "transactions": len(case["admin_data"].get("transactions", [])),
                "steps": [s.content for s in result.steps if s.step == "classify"],
            })
        total += 1

    accuracy = correct / total * 100 if total else 0

    print(f"{'='*60}")
    print(f"  템플릿 매칭 정확도: {correct}/{total} ({accuracy:.1f}%)")
    print(f"{'='*60}")

    # T99 제외 정확도
    non_t99 = sum(1 for (p, g), c in confusion.items() if g != 'T99_기타' and p == g)
    non_t99_total = sum(c for (p, g), c in confusion.items() if g != 'T99_기타')
    if non_t99_total:
        print(f"  T99 제외 정확도: {non_t99}/{non_t99_total} ({non_t99/non_t99_total*100:.1f}%)")

    print(f"\n--- Ground Truth 분포 ---")
    for t, cnt in gt_counts.most_common():
        print(f"  {t}: {cnt}건 ({cnt/total*100:.1f}%)")

    print(f"\n--- Agent 예측 분포 ---")
    for t, cnt in pred_counts.most_common():
        print(f"  {t}: {cnt}건")

    # 오분류 패턴
    if mismatches:
        mismatch_patterns = Counter()
        for m in mismatches:
            key = f"{m['predicted']} → 실제: {m['actual']}"
            mismatch_patterns[key] += 1

        print(f"\n--- 오분류 패턴 ({len(mismatches)}건) ---")
        for pattern, cnt in mismatch_patterns.most_common(10):
            print(f"  {pattern}: {cnt}건")

        print(f"\n--- 오분류 예시 (최대 10건) ---")
        for m in mismatches[:10]:
            print(f"\n  [{m['chat_id']}] 유저: {m['user']}")
            print(f"    의도: {m['intent']} → 예측: {m['predicted']}")
            print(f"    실제: {m['actual']}")
            print(f"    판단: {m['steps']}")
            print(f"    매니저: {m['manager']}")
            print(f"    데이터: 상품 {m['products']}개, 거래 {m['transactions']}건")

    # 결과 저장
    output = {
        "accuracy": accuracy,
        "accuracy_excl_t99": non_t99 / non_t99_total * 100 if non_t99_total else 0,
        "total": total,
        "correct": correct,
        "gt_distribution": dict(gt_counts),
        "pred_distribution": dict(pred_counts),
        "confusion": {f"{p}→{g}": c for (p, g), c in confusion.most_common()},
        "mismatches_count": len(mismatches),
    }
    with open("data/eval_v2_results.json", "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: data/eval_v2_results.json")


if __name__ == "__main__":
    data_path = sys.argv[1] if len(sys.argv) > 1 else "data/refund_test_cases_enriched.json"
    run_eval(data_path)
