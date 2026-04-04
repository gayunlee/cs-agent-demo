"""Eval Step 1: 템플릿 매칭 정확도 테스트

389건 테스트 케이스 × 실제 매니저 응답을 기준으로:
- agent가 선택한 템플릿 vs 실제 매니저가 사용한 템플릿
- 매칭 정확도 산출
- 오분류 패턴 분석
"""
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.refund_agent_v2 import RefundAgentV2
from src.templates import TEMPLATES


def classify_manager_response(mgr_text: str) -> str:
    """실제 매니저 응답이 어떤 템플릿에 해당하는지 분류 (ground truth)"""
    mgr = mgr_text.lower() if mgr_text else ""

    if '구독해지방법' in mgr or '구독해지 방법' in mgr or '정기결제 구독해지' in mgr:
        return 'T1_구독해지_방법_앱'
    if '7일 이내 구독권 미개시' in mgr or '환불 규정에 따른 금액' in mgr:
        return 'T2_환불_규정_금액'
    if '환불금' in mgr and '원' in mgr and ('차감' in mgr or '수수료' in mgr):
        return 'T2_환불_규정_금액'
    if '6개월마다 정기결제' in mgr and '환불' in mgr:
        return 'T2_환불_규정_금액'
    if '환불 접수 완료' in mgr or '환불접수 완료' in mgr:
        return 'T3_환불_접수_완료'
    if '정기적으로 제공되는 콘텐츠' in mgr or '구독형 스터디' in mgr:
        return 'T4_자동결제_설명'
    if '성함' in mgr and ('휴대전화' in mgr or '번호' in mgr) and len(mgr) < 100:
        return 'T6_본인확인_요청'
    if '구독해지 = 자동결제' in mgr or ('해지 잘 되어' in mgr):
        return 'T7_해지_확인_완료'
    if '카드 변경' in mgr or '결제카드' in mgr or '카드변경' in mgr:
        return 'T8_카드변경_안내'
    return 'T99_기타'


def run_eval(data_path: str = "data/test_cases/refund_test_cases.json", use_llm: bool = False):
    with open(data_path) as f:
        cases = json.load(f)

    # 매니저 응답 있는 케이스만
    cases = [c for c in cases if c.get('manager_responses') and len(c['manager_responses'][0]) > 20]
    print(f"테스트 케이스: {len(cases)}건\n")

    agent = RefundAgentV2(region="us-west-2", mock=not use_llm)

    correct = 0
    total = 0
    mismatches = []
    intent_counts = Counter()
    template_counts = Counter()
    gt_counts = Counter()

    for i, case in enumerate(cases):
        user_msgs = case["user_messages"]
        mgr_resp = case["manager_responses"][0]
        user_id = case.get("user_id", "")

        # Ground truth: 매니저 응답 기반 템플릿
        gt_template = classify_manager_response(mgr_resp)
        gt_counts[gt_template] += 1

        # Agent 분류 (Step 1만)
        result = agent.process(user_msgs, user_id=user_id)
        pred_template = result.template_id
        intent = result.intent

        intent_counts[intent] += 1
        template_counts[pred_template] += 1

        match = pred_template == gt_template
        if match:
            correct += 1
        else:
            mismatches.append({
                "idx": i,
                "user": user_msgs[0][:60],
                "intent": intent,
                "predicted": pred_template,
                "actual": gt_template,
                "manager": mgr_resp[:100],
            })
        total += 1

    accuracy = correct / total * 100 if total else 0
    print(f"{'='*60}")
    print(f"템플릿 매칭 정확도: {correct}/{total} ({accuracy:.1f}%)")
    print(f"{'='*60}")

    print(f"\n--- Ground Truth 분포 ---")
    for t, cnt in gt_counts.most_common():
        print(f"  {t}: {cnt}건 ({cnt/total*100:.1f}%)")

    print(f"\n--- Agent 예측 분포 ---")
    for t, cnt in template_counts.most_common():
        print(f"  {t}: {cnt}건")

    print(f"\n--- 의도 분류 분포 ---")
    for intent, cnt in intent_counts.most_common():
        print(f"  {intent}: {cnt}건")

    if mismatches:
        print(f"\n--- 오분류 ({len(mismatches)}건) ---")
        # 오분류 패턴 요약
        mismatch_patterns = Counter()
        for m in mismatches:
            key = f"{m['predicted']} → 실제: {m['actual']}"
            mismatch_patterns[key] += 1

        print(f"\n  오분류 패턴 top:")
        for pattern, cnt in mismatch_patterns.most_common(10):
            print(f"    {pattern}: {cnt}건")

        print(f"\n  오분류 예시 (최대 10건):")
        for m in mismatches[:10]:
            print(f"\n  [{m['idx']}] 유저: {m['user']}")
            print(f"    의도: {m['intent']} → 예측: {m['predicted']}")
            print(f"    실제: {m['actual']}")
            print(f"    매니저: {m['manager']}")

    # 결과 저장
    output = {
        "accuracy": accuracy,
        "total": total,
        "correct": correct,
        "gt_distribution": dict(gt_counts),
        "pred_distribution": dict(template_counts),
        "mismatches": mismatches,
    }
    with open("data/eval_results/eval_template_matching.json", "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: data/eval_results/eval_template_matching.json")


if __name__ == "__main__":
    use_llm = "--llm" in sys.argv
    data_path = "data/test_cases/refund_test_cases.json"
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            data_path = arg
    run_eval(data_path, use_llm=use_llm)
