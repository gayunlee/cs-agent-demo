"""Eval Loop: 실행 → 분석 → 개선 제안 → re-eval

Usage:
  python scripts/eval_loop.py                    # eval 1회 + 분석
  python scripts/eval_loop.py --suggest          # 개선 제안 포함
  python scripts/eval_loop.py --history          # 이전 결과와 비교
"""
import json
import sys
import os
from pathlib import Path
from collections import Counter
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.refund_agent_v2 import RefundAgentV2

ENRICHED_PATH = "data/refund_test_cases_enriched.json"
HISTORY_PATH = "data/eval_history.json"


def classify_manager_response(mgr_text: str) -> str:
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


def run_eval(cases, agent):
    """eval 실행 → 결과 반환"""
    results = []
    for case in cases:
        mgr = case['manager_responses'][0]
        gt = classify_manager_response(mgr)

        conv_time = ""
        if case.get("user_timestamps"):
            ts = case["user_timestamps"][0]
            if isinstance(ts, (int, float)):
                from datetime import timezone
                conv_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        r = agent.process(
            user_messages=case["user_messages"],
            chat_id=case.get("chat_id", ""),
            admin_data=case.get("admin_data"),
            conversation_time=conv_time,
            conversation_turns=case.get("conversation_turns"),
        )

        results.append({
            "chat_id": case.get("chat_id", ""),
            "user": case["user_messages"][0][:60],
            "intent": r.intent,
            "predicted": r.template_id,
            "actual": gt,
            "correct": r.template_id == gt,
            "manager": mgr[:120],
            "steps": [s.content for s in r.steps if s.step == "classify"],
            "n_products": len(case.get("admin_data", {}).get("products", [])),
            "n_transactions": len(case.get("admin_data", {}).get("transactions", [])),
        })
    return results


def analyze(results):
    """결과 분석"""
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    accuracy = correct / total * 100 if total else 0

    # T99 제외
    non_t99 = [r for r in results if r["actual"] != "T99"]
    non_t99_correct = sum(1 for r in non_t99 if r["correct"])
    non_t99_acc = non_t99_correct / len(non_t99) * 100 if non_t99 else 0

    # confusion matrix
    confusion = Counter()
    for r in results:
        confusion[(r["predicted"], r["actual"])] += 1

    # GT 분포
    gt_dist = Counter(r["actual"] for r in results)
    pred_dist = Counter(r["predicted"] for r in results)

    # 오분류 패턴 (건수 순)
    mismatches = [(k, v) for k, v in confusion.most_common() if k[0] != k[1]]

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 1),
        "non_t99_total": len(non_t99),
        "non_t99_correct": non_t99_correct,
        "non_t99_accuracy": round(non_t99_acc, 1),
        "gt_distribution": dict(gt_dist.most_common()),
        "pred_distribution": dict(pred_dist.most_common()),
        "top_mismatches": [{"pattern": f"{p}→{g}", "count": c} for (p, g), c in mismatches[:10]],
        "confusion": {f"{p}→{g}": c for (p, g), c in confusion.most_common()},
    }


def suggest_improvements(results, analysis):
    """오분류 패턴 분석 → 개선 제안 생성"""
    suggestions = []
    mismatches = [r for r in results if not r["correct"]]

    # 패턴별 그룹핑
    patterns = {}
    for m in mismatches:
        key = (m["predicted"], m["actual"])
        patterns.setdefault(key, [])
        patterns[key].append(m)

    for (pred, actual), cases in sorted(patterns.items(), key=lambda x: -len(x[1])):
        count = len(cases)
        if count < 3:
            continue

        # 패턴 분석
        intents = Counter(c["intent"] for c in cases)
        top_intent = intents.most_common(1)[0][0]

        suggestion = {
            "pattern": f"{pred} → 실제: {actual}",
            "count": count,
            "dominant_intent": top_intent,
            "examples": [{"user": c["user"][:50], "manager": c["manager"][:80]} for c in cases[:3]],
        }

        # 구체적 제안
        if pred.startswith("T2") and actual == "T99":
            suggestion["fix"] = (
                "T99(비정형) 케이스는 이전 대화 맥락에 의존하는 경우가 많음. "
                "이전 상담 이력 조회가 없으면 구분 불가 — T99는 평가에서 제외하거나, "
                "이전 대화 참조 기능 추가 필요."
            )
        elif pred.startswith("T2") and actual.startswith("T1"):
            suggestion["fix"] = (
                f"해지 의도({top_intent})인데 T2(환불규정)로 감. "
                "_select_template에서 해지_방법 의도일 때 결제이력 있어도 T1 우선 조건 추가. "
                "예: 유저가 '환불'을 직접 언급하지 않으면 T1."
            )
        elif pred.startswith("T2") and actual.startswith("T3"):
            suggestion["fix"] = (
                "이미 환불 처리 완료된 건인데 T2로 감. "
                "환불 이력(refund_txs) 체크를 대화 시점 기준으로 강화. "
                "최근 환불 이력이 있으면 T3 우선."
            )
        elif pred.startswith("T2") and actual.startswith("T4"):
            suggestion["fix"] = (
                "자동결제 직후 문의인데 T2로 감. "
                "의도 분류에서 '자동결제_불만' 감지를 강화하거나, "
                "최근 결제가 2일 이내면 T4 우선."
            )
        elif pred.startswith("T4") and actual.startswith("T1"):
            suggestion["fix"] = (
                "자동결제 불만이 아닌데 T4로 감. "
                "의도 분류에서 '다음 정기결제 해지 방법' 같은 패턴은 자동결제_불만이 아닌 해지_방법으로 분류."
            )
        else:
            suggestion["fix"] = f"패턴 분석 필요: {pred} → {actual}, 주 의도: {top_intent}"

        suggestions.append(suggestion)

    return suggestions


def save_history(analysis):
    """결과를 히스토리에 추가"""
    history = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            history = json.load(f)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "accuracy": analysis["accuracy"],
        "non_t99_accuracy": analysis["non_t99_accuracy"],
        "total": analysis["total"],
        "correct": analysis["correct"],
    }
    history.append(entry)

    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def print_history():
    """히스토리 출력"""
    if not os.path.exists(HISTORY_PATH):
        print("히스토리 없음")
        return

    with open(HISTORY_PATH) as f:
        history = json.load(f)

    print(f"\n{'='*60}")
    print("  Eval 히스토리")
    print(f"{'='*60}")
    print(f"  {'#':>3}  {'시간':20}  {'전체':>8}  {'T99제외':>8}  {'맞음':>5}/{' 전체':>5}")
    for i, h in enumerate(history):
        ts = h["timestamp"][:16]
        print(f"  {i+1:>3}  {ts:20}  {h['accuracy']:>7.1f}%  {h['non_t99_accuracy']:>7.1f}%  {h['correct']:>5}/{h['total']:>5}")

    if len(history) >= 2:
        prev = history[-2]
        curr = history[-1]
        diff = curr["accuracy"] - prev["accuracy"]
        diff99 = curr["non_t99_accuracy"] - prev["non_t99_accuracy"]
        print(f"\n  변화: 전체 {diff:+.1f}%, T99제외 {diff99:+.1f}%")


def main():
    show_suggest = "--suggest" in sys.argv
    show_history = "--history" in sys.argv

    if show_history:
        print_history()
        return

    # 데이터 로드
    with open(ENRICHED_PATH) as f:
        cases = json.load(f)

    valid = [c for c in cases
             if c.get('admin_data') and not c['admin_data'].get('error')
             and c.get('manager_responses') and len(c['manager_responses'][0]) > 20]

    print(f"테스트 케이스: {len(valid)}건\n")

    # Eval 실행
    agent = RefundAgentV2(mock=True)
    results = run_eval(valid, agent)

    # 분석
    analysis = analyze(results)

    print(f"{'='*60}")
    print(f"  전체 정확도: {analysis['correct']}/{analysis['total']} ({analysis['accuracy']}%)")
    print(f"  T99 제외:   {analysis['non_t99_correct']}/{analysis['non_t99_total']} ({analysis['non_t99_accuracy']}%)")
    print(f"{'='*60}")

    print(f"\n--- 주요 오분류 ---")
    for m in analysis["top_mismatches"][:7]:
        print(f"  {m['pattern']}: {m['count']}건")

    # 히스토리 저장
    save_history(analysis)
    print_history()

    # 개선 제안
    if show_suggest:
        suggestions = suggest_improvements(results, analysis)
        print(f"\n{'='*60}")
        print(f"  개선 제안 ({len(suggestions)}건)")
        print(f"{'='*60}")
        for i, s in enumerate(suggestions, 1):
            print(f"\n  [{i}] {s['pattern']} ({s['count']}건)")
            print(f"      주 의도: {s['dominant_intent']}")
            print(f"      제안: {s['fix']}")
            for ex in s["examples"][:2]:
                print(f"      예시: {ex['user']}")

    # 상세 결과 저장
    with open("data/eval_loop_latest.json", "w") as f:
        json.dump({
            "analysis": analysis,
            "suggestions": suggest_improvements(results, analysis) if show_suggest else [],
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n상세 결과: data/eval_loop_latest.json")


if __name__ == "__main__":
    main()
