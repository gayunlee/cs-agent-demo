"""실 채널톡 대화 데이터에서 **모호한 첫 메시지** 케이스 추출 + 분류 + 저장.

입력:
- data/test_cases/refund_convos_jan.json       (100건, turns: [{role, text}])
- data/test_cases/refund_test_cases_enriched.json (389건, conversation_turns: [{role, text, ts}])

출력:
- data/ambiguous_catalog.json — 분류된 케이스 리스트 (케이스 1건당 user_first + manager_first + pattern + keywords)
- 표준출력 — 패턴별 집계

모호 판정 기준 (Gayoon 2026-04-05 정의):
- **첫 유저 메시지** 가 "무엇을 도와드릴까요?" 수준의 재질문이 필요할 정도로 정보 부족
- 짧거나 (≤25자 or ≤6단어) 또는 단독 키워드
- 매니저의 첫 응답이 재질문 성격 (성함/번호/상품/증상/무엇을 등)

분류 패턴:
- A. 짧은_환불해지    — "환불해주세요", "해지", "환불 가능?" (환불/해지 단독)
- B. 자동결제_맥락    — "자동결제 됐는데...", "연장되어 결제" (정기결제 맥락)
- C. 상품_특정_부족   — "에셋 환불", "A과정 해지" (상품 언급은 있으나 추가 확인 필요)
- D. 환불외_오픈     — "문의드립니다", "궁금해요" (환불 키워드 없음)
- E. 기타_모호

실행:
    .venv311/bin/python -m scripts.build_ambiguous_catalog
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCES = [
    ROOT / "data/test_cases/refund_convos_jan.json",
    ROOT / "data/test_cases/refund_test_cases_enriched.json",
]
OUT_PATH = ROOT / "data/ambiguous_catalog.json"


# 매니저 응답에서 "재질문" 성격을 감지하는 키워드
CLARIFICATION_KEYWORDS = [
    # 본인확인
    "성함", "이름", "전화번호", "휴대전화", "번호 알려", "번호 남겨", "연락처",
    # 상품특정
    "어떤 상품", "어떤 과정", "상품명", "상품 알려", "어느 상품", "어떤 멤버십",
    # 증상구체화
    "어떤 화면", "어떤 증상", "어느 기기", "언제부터", "어떤 부분",
    # 일반확인
    "확인 가능한", "확인 가능하신", "확인 도와", "확인 후 안내", "확인이 필요",
    # 오픈 재질문
    "무엇을 도와", "어떻게 도와", "자세히", "궁금하신", "남겨주시면",
]

# 환불/해지 맥락 키워드
REFUND_CANCEL_KW = ["환불", "해지", "취소", "구독취소"]
# 자동결제 키워드
AUTO_PAY_KW = ["자동", "연장", "재구매", "정기결제"]
# 오픈 질문 키워드
OPEN_INQUIRY_KW = ["문의", "궁금", "안녕", "질문", "도와"]


def is_short_or_fragment(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    if len(t) <= 25:
        return True
    words = re.split(r"\s+", t)
    return len(words) <= 6


def find_clarification_keywords(manager_text: str) -> list[str]:
    if not manager_text:
        return []
    hits = [k for k in CLARIFICATION_KEYWORDS if k in manager_text]
    return hits


def classify_pattern(user_text: str, manager_text: str) -> str:
    u = (user_text or "").strip()
    has_refund = any(k in u for k in REFUND_CANCEL_KW)
    has_auto = any(k in u for k in AUTO_PAY_KW)
    has_open = any(k in u for k in OPEN_INQUIRY_KW)

    if has_auto:
        return "B_자동결제_맥락"
    if has_refund:
        # 상품 언급 포함 (상품명 키워드) — 현재는 엄밀 매칭 어려우니 길이 기준으로만
        if len(u) > 10 and not has_open:
            return "C_상품_특정_부족"
        return "A_짧은_환불해지"
    if has_open:
        return "D_환불외_오픈"
    return "E_기타_모호"


def extract_first_user_and_manager(turns: list[dict]) -> tuple[str | None, str | None]:
    """turns 리스트에서 (유저 첫 발화, 그 이후 매니저 첫 발화) 반환."""
    first_user: str | None = None
    first_manager: str | None = None
    user_seen = False
    for t in turns:
        role = t.get("role", "")
        text = (t.get("text") or "").strip()
        if not text:
            continue
        # bot 자동 응답은 skip (첫 인사 등)
        if role == "bot":
            continue
        if role == "user":
            if not user_seen:
                first_user = text
                user_seen = True
            continue
        if role == "manager":
            if user_seen and first_manager is None:
                first_manager = text
                break
    return first_user, first_manager


def process_file(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []

    cases: list[dict] = []
    for conv in data:
        chat_id = conv.get("chat_id", "")
        turns = conv.get("turns") or conv.get("conversation_turns") or []
        if not turns:
            continue

        user_first, manager_first = extract_first_user_and_manager(turns)
        if not user_first or not manager_first:
            continue

        # 1차 필터: 유저 메시지가 짧거나 단독 키워드성이면 후보
        if not is_short_or_fragment(user_first):
            continue

        # 2차 필터: 매니저 응답에 재질문 키워드가 있는지
        kw = find_clarification_keywords(manager_first)
        if not kw:
            continue

        pattern = classify_pattern(user_first, manager_first)
        cases.append({
            "chat_id": chat_id,
            "source_file": path.name,
            "user_first": user_first,
            "manager_first_excerpt": manager_first[:300],
            "pattern": pattern,
            "clarification_keywords_matched": kw,
        })
    return cases


def main():
    all_cases: list[dict] = []
    for src in SOURCES:
        if not src.exists():
            print(f"⚠️  skip (not found): {src}")
            continue
        cases = process_file(src)
        print(f"📂 {src.name}: {len(cases)}건 추출")
        all_cases.extend(cases)

    # 중복 제거 (같은 chat_id)
    seen = set()
    dedup: list[dict] = []
    for c in all_cases:
        cid = c["chat_id"]
        if cid in seen:
            continue
        seen.add(cid)
        dedup.append(c)

    # 패턴 집계
    counts = Counter(c["pattern"] for c in dedup)
    total = len(dedup)

    print("\n" + "=" * 60)
    print(f"📊 모호 첫 메시지 카탈로그 — 총 {total}건 (중복 제거 후)")
    print("=" * 60)
    for pat, cnt in counts.most_common():
        pct = 100 * cnt / total if total else 0
        print(f"  {pat:<22} {cnt:>4}건 ({pct:.1f}%)")

    # 저장
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_from": [str(s.relative_to(ROOT)) for s in SOURCES if s.exists()],
        "total": total,
        "pattern_counts": dict(counts),
        "definition": {
            "ambiguous_criteria": "첫 유저 메시지가 25자 이하 또는 6단어 이하 + 매니저 첫 응답에 재질문 키워드 포함",
            "patterns": {
                "A_짧은_환불해지": "환불/해지 키워드 단독 or 짧은 요청",
                "B_자동결제_맥락": "자동/연장/재구매 키워드 포함",
                "C_상품_특정_부족": "환불/해지 + 상품 언급 있으나 모호",
                "D_환불외_오픈": "환불 키워드 없는 오픈 질문 (문의/궁금 등)",
                "E_기타_모호": "위 분류 어디에도 안 맞음",
            },
            "clarification_keyword_list": CLARIFICATION_KEYWORDS,
        },
        "cases": dedup,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n💾 저장: {OUT_PATH.relative_to(ROOT)} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
