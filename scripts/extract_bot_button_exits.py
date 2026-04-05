"""BQ 조사 — 봇 버튼 클릭 후 유저 이탈했는데 매니저가 추가 대응한 케이스.

Gayoon 질문 (2026-04-06):
"봇 버튼 클릭 + 이탈한 경우에도 매니저가 추가 대응하는지?"

봇 버튼 label 추정 기준:
- user_first 에 "/" 포함 (예: "구독 상품변경/결제정보 확인")
- 또는 줄바꿈 포함 or 긴 구조화된 문장

이탈 기준:
- user_turn_count == 1 (유저가 첫 메시지 후 추가 발화 없음)

매니저 대응 여부:
- 매니저 메시지 존재?
- 매니저 응답 내용 — 재질문인가, 정보 제공인가, 아무 것도 안 하는가

출력: data/ambiguous/bot_button_exits.json
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

OUT_PATH = ROOT / "data/ambiguous/bot_button_exits.json"


def build_query(start_ms: int, end_ms: int) -> str:
    return f"""
    WITH convos AS (
      SELECT
        chatId,
        ARRAY_AGG(
          STRUCT(personType, plainText, createdAt)
          ORDER BY createdAt
        ) AS turns
      FROM `us-service-data.channel_io.messages`
      WHERE createdAt >= {start_ms}
        AND createdAt < {end_ms}
        AND plainText IS NOT NULL
        AND plainText != ''
      GROUP BY chatId
    )
    SELECT
      chatId,
      ARRAY(
        SELECT plainText FROM UNNEST(turns)
        WHERE personType = 'user' ORDER BY createdAt
      ) AS user_msgs,
      ARRAY(
        SELECT plainText FROM UNNEST(turns)
        WHERE personType = 'manager' ORDER BY createdAt
      ) AS manager_msgs,
      (SELECT COUNT(*) FROM UNNEST(turns) WHERE personType = 'user') AS user_turn_count,
      (SELECT COUNT(*) FROM UNNEST(turns) WHERE personType = 'manager') AS mgr_turn_count
    FROM convos
    WHERE EXISTS (SELECT 1 FROM UNNEST(turns) WHERE personType = 'user')
    """


def looks_like_bot_button(text: str) -> bool:
    """봇 버튼 label 추정 — 슬래시 포함 or 긴 구조화 명사 조합."""
    if not text:
        return False
    t = text.strip()
    # 구체적 label 특징
    if "/" in t:
        return True
    if "\n" in t and len(t) < 30:
        return True
    # 알려진 label 리스트 (앞 조사에서 발견)
    known_labels = [
        "구독 상품변경",
        "결제정보 확인",
        "사이트 및 동영상 오류",
        "환불해지",
        "라이브 오류",
    ]
    for lbl in known_labels:
        if lbl in t:
            return True
    return False


def main():
    now = datetime.now(timezone.utc)
    end = now
    start = now - timedelta(days=30)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    print("=" * 60)
    print(f"BQ — 봇 버튼 클릭 후 이탈 + 매니저 대응 조사 (1달)")
    print(f"  기간: {start.date()} ~ {end.date()}")
    print("=" * 60)

    client = BigQueryClient()
    rows = client.execute_query(build_query(start_ms, end_ms))
    print(f"\n📦 전체 대화: {len(rows)}건")

    # 분류
    bot_button_exits_with_mgr: list[dict] = []  # 봇 버튼 + 이탈(user_turn=1) + 매니저 대응 있음
    bot_button_exits_no_mgr: list[dict] = []    # 봇 버튼 + 이탈 + 매니저 대응 없음
    bot_button_followup: list[dict] = []        # 봇 버튼 + 유저 추가 발화 있음 (이탈 아님)
    total_bot_button = 0

    for r in rows:
        user_msgs = r.get("user_msgs") or []
        manager_msgs = r.get("manager_msgs") or []
        user_turn_count = r.get("user_turn_count", 0)
        if not user_msgs:
            continue
        first_user = user_msgs[0].strip()
        if not looks_like_bot_button(first_user):
            continue

        total_bot_button += 1

        case = {
            "chat_id": r["chatId"],
            "user_first": first_user,
            "user_turn_count": user_turn_count,
            "mgr_turn_count": r.get("mgr_turn_count", 0),
            "user_msgs": user_msgs,
            "first_manager_text": manager_msgs[0][:400] if manager_msgs else None,
        }

        if user_turn_count == 1:
            if manager_msgs:
                bot_button_exits_with_mgr.append(case)
            else:
                bot_button_exits_no_mgr.append(case)
        else:
            bot_button_followup.append(case)

    print(f"\n📊 봇 버튼 클릭 케이스 총 {total_bot_button}건")
    print(f"  • 이탈 + 매니저 대응 있음:   {len(bot_button_exits_with_mgr)}건")
    print(f"  • 이탈 + 매니저 대응 없음:   {len(bot_button_exits_no_mgr)}건")
    print(f"  • 유저 추가 발화 있음:       {len(bot_button_followup)}건")

    # 매니저 응답 샘플 (이탈 + 대응 케이스)
    if bot_button_exits_with_mgr:
        print("\n--- 봇 버튼 이탈 + 매니저 대응 샘플 (최대 10건) ---")
        for c in bot_button_exits_with_mgr[:10]:
            mgr = (c["first_manager_text"] or "").replace("\n", " ")[:150]
            print(f"  [user] {c['user_first'][:50]}")
            print(f"    → [mgr] {mgr}")
            print()

    # 저장
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "window_days": 30,
        "total_conversations": len(rows),
        "bot_button_detection_rules": {
            "slash_in_first_user": True,
            "newline_in_short_first_user": True,
            "known_labels": [
                "구독 상품변경", "결제정보 확인", "사이트 및 동영상 오류", "환불해지", "라이브 오류"
            ],
        },
        "counts": {
            "total_bot_button": total_bot_button,
            "exit_with_manager_response": len(bot_button_exits_with_mgr),
            "exit_without_manager_response": len(bot_button_exits_no_mgr),
            "followup_messages_exist": len(bot_button_followup),
        },
        "exit_with_manager_samples": bot_button_exits_with_mgr[:30],
        "exit_without_manager_samples": bot_button_exits_no_mgr[:10],
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n💾 저장: {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
