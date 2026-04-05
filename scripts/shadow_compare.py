"""Shadow compare — snapshot/prediction vs 매니저 실답변 비교.

shadow_run.py 결과 + snapshot 의 full_conversation_turns 에 들어있는
매니저 실답변을 꺼내 에이전트 답변과 비교.

비교 항목:
1. template_match — 매니저 답변에서 추론한 template_id 와 에이전트 template 일치?
2. refund_amount_match — 매니저 답변에 금액 언급 있으면 에이전트 금액과 ±1원 일치?
3. is_refund_domain 판정 일치 — 에이전트가 환불 도메인이라고 했는데 매니저도 환불 관련 답변?

매니저 template 추론: 각 템플릿의 시그니처 문구 매칭 (키워드 기반).

출력:
- data/shadow/compare_report_{timestamp}.json — 전체 결과
- data/shadow/compare_report_{timestamp}.csv  — CSV 요약

실행:
    .venv311/bin/python -m scripts.shadow_compare
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "data/shadow/snapshots"
PREDICTION_DIR = ROOT / "data/shadow/predictions"
OUT_DIR = ROOT / "data/shadow"

KST = timezone(timedelta(hours=9))

# 매니저 답변 → template_id 추론용 시그니처 문구
TEMPLATE_SIGNATURES: list[tuple[str, list[str]]] = [
    ("T1_구독해지_방법_앱", ["정기결제 구독해지 방법", "구독해지 방법 안내", "멤버십 관리 클릭"]),
    ("T2_환불_규정_금액", ["7일 이내 구독권 미개시", "환불 규정에 따른 금액", "환불 금액:", "환불금액"]),
    ("T3_환불_접수_완료", ["환불 접수 완료", "환불 처리 완료", "환불이 완료", "환불 최종 확정"]),
    ("T4_자동결제_설명", ["정기적으로 제공되는 콘텐츠", "구독형 상품", "자동결제되는 상품"]),
    ("T6_본인확인_요청", ["성함", "휴대전화", "전화번호", "본인 확인"]),
    ("T7_해지_확인_완료", ["해지 처리 완료", "해지처리 완료", "해지가 완료"]),
    ("T8_카드변경_안내", ["결제카드 변경", "카드 변경", "결제 변경"]),
    ("T10_상품변경_안내", ["상품 변경", "다른 상품", "변경 안내"]),
    ("T11_중복결제_환불", ["중복 결제", "이중 결제", "중복결제 환불"]),
    ("T12_환불진행_상태안내", ["환불 진행 중", "환불 처리 중", "카드사 사정"]),
]

_AMOUNT_RE = re.compile(r"(\d[\d,]{2,})\s*원")


def infer_manager_template(manager_text: str) -> tuple[str, list[str]]:
    """매니저 답변 텍스트에서 template_id 추론 (시그니처 키워드 매칭)."""
    if not manager_text:
        return "", []
    scores: list[tuple[str, int, list[str]]] = []
    for tid, signatures in TEMPLATE_SIGNATURES:
        hits = [s for s in signatures if s in manager_text]
        if hits:
            scores.append((tid, len(hits), hits))
    if not scores:
        return "UNKNOWN", []
    scores.sort(key=lambda x: -x[1])
    return scores[0][0], scores[0][2]


def extract_amount(text: str) -> int | None:
    if not text:
        return None
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def get_first_manager_text(snapshot: dict) -> str:
    turns = snapshot.get("full_conversation_turns") or []
    for t in turns:
        if t.get("role") == "manager" and t.get("text"):
            return t["text"]
    return ""


def main():
    if not SNAPSHOT_DIR.exists() or not PREDICTION_DIR.exists():
        print("❌ snapshot or prediction 디렉토리 없음. shadow_run.py 먼저 실행.")
        return 1

    pred_files = sorted(PREDICTION_DIR.glob("*.json"))
    if not pred_files:
        print("❌ prediction 없음.")
        return 1

    print(f"📦 prediction: {len(pred_files)}건")

    rows: list[dict] = []
    stats = {
        "total": 0,
        "has_manager_response": 0,
        "agent_should_respond": 0,
        "template_match": 0,
        "template_mismatch": 0,
        "template_unknown_mgr": 0,
        "amount_agent_present": 0,
        "amount_mgr_present": 0,
        "amount_exact_match": 0,
        "amount_close_match_100": 0,
    }

    for pred_path in pred_files:
        pred = json.loads(pred_path.read_text(encoding="utf-8"))
        chat_id = pred["chat_id"]
        snap_path = SNAPSHOT_DIR / f"{chat_id}.json"
        if not snap_path.exists():
            continue
        snap = json.loads(snap_path.read_text(encoding="utf-8"))

        mgr_text = get_first_manager_text(snap)
        mgr_template, mgr_hits = infer_manager_template(mgr_text)
        mgr_amount = extract_amount(mgr_text)

        agent_template = pred.get("template_id", "")
        agent_amount = pred.get("refund_amount")
        agent_should_respond = pred.get("is_refund_domain", False)

        template_match = bool(agent_template and mgr_template and agent_template == mgr_template)
        amount_diff = None
        amount_exact = False
        amount_close = False
        if agent_amount is not None and mgr_amount is not None:
            amount_diff = agent_amount - mgr_amount
            amount_exact = abs(amount_diff) <= 1
            amount_close = abs(amount_diff) <= 100

        stats["total"] += 1
        if mgr_text:
            stats["has_manager_response"] += 1
        if agent_should_respond:
            stats["agent_should_respond"] += 1
        if template_match:
            stats["template_match"] += 1
        elif mgr_template == "UNKNOWN":
            stats["template_unknown_mgr"] += 1
        else:
            stats["template_mismatch"] += 1
        if agent_amount is not None:
            stats["amount_agent_present"] += 1
        if mgr_amount is not None:
            stats["amount_mgr_present"] += 1
        if amount_exact:
            stats["amount_exact_match"] += 1
        if amount_close:
            stats["amount_close_match_100"] += 1

        rows.append({
            "chat_id": chat_id,
            "intent": pred.get("intent", ""),
            "agent_should_respond": agent_should_respond,
            "agent_template": agent_template,
            "mgr_template_inferred": mgr_template,
            "template_match": template_match,
            "mgr_sig_hits": ", ".join(mgr_hits),
            "agent_amount": agent_amount,
            "mgr_amount": mgr_amount,
            "amount_diff": amount_diff,
            "amount_exact_match": amount_exact,
            "amount_close_match_100": amount_close,
            "mgr_first_text_excerpt": (mgr_text[:200].replace("\n", " ") if mgr_text else ""),
            "agent_draft_excerpt": (pred.get("draft_answer") or "")[:200].replace("\n", " "),
        })

    # 요약 출력
    total = stats["total"]
    print("\n" + "=" * 60)
    print("📊 Shadow compare 결과")
    print("=" * 60)
    print(f"  total                      {total}")
    print(f"  has manager response       {stats['has_manager_response']}")
    print(f"  agent should_respond       {stats['agent_should_respond']}")
    print(f"  template match             {stats['template_match']}/{total} "
          f"({100*stats['template_match']/total:.1f}%)" if total else "")
    print(f"  template mismatch          {stats['template_mismatch']}")
    print(f"  mgr template unknown       {stats['template_unknown_mgr']}")
    print(f"  amount both present        "
          f"{min(stats['amount_agent_present'], stats['amount_mgr_present'])}")
    print(f"  amount exact match (±1)    {stats['amount_exact_match']}")
    print(f"  amount close match (±100)  {stats['amount_close_match_100']}")

    # 저장
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"compare_report_{ts}.json"
    csv_path = OUT_DIR / f"compare_report_{ts}.csv"

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(KST).isoformat(),
            "stats": stats,
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)

    if rows:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print(f"\n💾 저장:")
    print(f"  {report_path.relative_to(ROOT)}")
    print(f"  {csv_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
