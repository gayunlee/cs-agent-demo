"""BQ 에서 1달치 채널톡 대화 추출 + 모호 후보 필터.

Gayoon 타깃 (2026-04-06 정정):
- **자유 텍스트로 매우 짧게 쓰고 나가버린** 케이스 ("환불신청", "가입변경", "해지")
- **봇 버튼 클릭 케이스는 제외** — 유저가 봇 메뉴로 해소하고 나간 걸 수도 있음
- 도메인 불문 (환불 키워드 있든 없든)

쿼리 전략:
- 1달 (현재 시점 기준 30일)
- chatId 단위 그룹
- 유저 메시지 전부 aggregate + 매니저 첫 응답

필터 기준:
- 유저 첫 메시지 ≤ 10자 (매우 짧은 자유 텍스트만)
- 유저 전체 누계 텍스트 ≤ 20자 (추가 설명 없음)
- "/" 포함 제외 (봇 메뉴 label 특징)
- 줄바꿈/여러 문장 구조 제외
- 매니저 첫 응답에 재질문 키워드

출력:
- data/ambiguous/raw_1month_candidates.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bigquery.client import BigQueryClient

CLARIFICATION_KW = [
    "성함", "이름", "전화번호", "휴대전화", "연락처", "남겨",
    "어떤", "무엇을", "자세히", "구체적", "궁금하신",
    "어느 기기", "언제부터",
]

OUT_PATH = ROOT / "data/ambiguous/raw_1month_candidates.json"


def build_query(start_ts_ms: int, end_ts_ms: int) -> str:
    return f"""
    WITH convos AS (
      SELECT
        chatId,
        ARRAY_AGG(
          STRUCT(personType, plainText, createdAt)
          ORDER BY createdAt
        ) AS turns
      FROM `us-service-data.channel_io.messages`
      WHERE createdAt >= {start_ts_ms}
        AND createdAt < {end_ts_ms}
        AND plainText IS NOT NULL
        AND plainText != ''
      GROUP BY chatId
    )
    SELECT
      chatId,
      ARRAY(
        SELECT plainText
        FROM UNNEST(turns)
        WHERE personType = 'user'
        ORDER BY createdAt
      ) AS user_msgs,
      (
        SELECT plainText
        FROM UNNEST(turns)
        WHERE personType = 'manager'
        ORDER BY createdAt
        LIMIT 1
      ) AS first_mgr_text,
      (
        SELECT COUNT(*)
        FROM UNNEST(turns)
        WHERE personType = 'user'
      ) AS user_turn_count
    FROM convos
    WHERE ARRAY_LENGTH(
      ARRAY(SELECT 1 FROM UNNEST(turns) WHERE personType = 'user')
    ) >= 1
    AND EXISTS (SELECT 1 FROM UNNEST(turns) WHERE personType = 'manager')
    """


def main():
    now = datetime.now(timezone.utc)
    end = now
    start = now - timedelta(days=30)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    print("=" * 60)
    print(f"BQ 추출 — 1달 (채널톡 전체 CS 도메인)")
    print(f"  기간: {start.date()} ~ {end.date()}")
    print("=" * 60)

    client = BigQueryClient()
    query = build_query(start_ms, end_ms)
    rows = client.execute_query(query)
    print(f"\n📦 전체 대화: {len(rows)}건")

    # 필터: 모호 후보
    candidates = []
    for r in rows:
        user_msgs = r.get("user_msgs") or []
        first_mgr = r.get("first_mgr_text") or ""
        if not user_msgs or not first_mgr:
            continue

        user_all = " ".join(user_msgs).strip()
        user_first = user_msgs[0].strip()

        # 매우 짧은 자유 텍스트만 (봇 label 아님)
        if len(user_first) > 15:
            continue
        if len(user_all) > 30:
            continue
        # 봇 메뉴 label 배제 (슬래시, 줄바꿈)
        if "/" in user_first or "\n" in user_first:
            continue
        # 최소 길이
        if len(user_first.strip()) < 2:
            continue

        # 매니저 재질문 여부는 **태그만** 하고 필터에선 제외
        kw_hits = [k for k in CLARIFICATION_KW if k in first_mgr]

        candidates.append({
            "chat_id": r["chatId"],
            "user_msgs": user_msgs,
            "user_first": user_first,
            "user_all_text": user_all,
            "user_turn_count": r.get("user_turn_count"),
            "first_mgr_text": first_mgr[:400],
            "clarification_keywords_matched": kw_hits,
        })

    print(f"📊 모호 후보 (환불 외 + 짧음 + 매니저 재질문): {len(candidates)}건")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": 30,
        },
        "total_conversations_scanned": len(rows),
        "candidates_count": len(candidates),
        "filter_rules": {
            "user_first_max_len": 10,
            "user_all_text_max_len": 20,
            "exclude_bot_label_chars": ["/", "\n"],
            "manager_clarification_keywords": CLARIFICATION_KW,
        },
        "candidates": candidates,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"💾 저장: {OUT_PATH.relative_to(ROOT)} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
