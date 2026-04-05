"""Shadow pipeline — 비영업시간 (금/토/일) 대화 → wrapper agent → snapshot + prediction 저장.

Gayoon 설계 (2026-04-06):
- 비영업시간 window = 금요일 + 토요일 + 일요일 (한국시간 기준)
- 각 대화에 대해 wrapper agent (Bedrock Haiku 4.5) 돌림
- **admin API 조회 결과 (admin_data)** 를 스냅샷으로 저장 → 다음 영업일 매니저 답변과 비교 시 "에이전트가 본 입력" 복원용
- **에이전트 답변** 도 함께 저장 → 매니저 실답변 나오면 direct 비교

MVP 데이터 소스:
- 현재는 `data/test_cases/refund_test_cases_enriched.json` (389건, admin_data + conversation_turns 이미 포함) 재활용
- 실 BQ pull + admin API 호출은 Phase 2 (shadow_collector.py)

출력:
- data/shadow/snapshots/{chat_id}.json   — 입력 스냅샷 (admin_data, first_user_turn, ts)
- data/shadow/predictions/{chat_id}.json — 에이전트 예측 (draft, intent, template_id, should_respond, 스냅샷 ref)
- data/shadow/run_log_{timestamp}.json    — 실행 요약

실행:
    .venv311/bin/python -m scripts.shadow_run
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401

from src.agents.wrapper_agent import (
    clear_all_sessions,
    clear_session,
    get_agent_for_session,
)

KST = timezone(timedelta(hours=9))

DATA_SRC = ROOT / "data/test_cases/refund_test_cases_enriched.json"
SNAPSHOT_DIR = ROOT / "data/shadow/snapshots"
PREDICTION_DIR = ROOT / "data/shadow/predictions"
RUN_LOG_DIR = ROOT / "data/shadow"


# ─────────────────────────────────────────────────────────────
# 비영업시간 필터
# ─────────────────────────────────────────────────────────────


def is_off_hours_weekend(ts_ms: int) -> bool:
    """금(4)/토(5)/일(6) 한국시간 기준 전체를 off-hours 로 간주."""
    if not ts_ms:
        return False
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=KST)
    return dt.weekday() in (4, 5, 6)


def get_first_user_turn(case: dict) -> tuple[str, int]:
    """case 의 conversation_turns 에서 첫 유저 턴의 (text, ts) 반환."""
    turns = case.get("conversation_turns") or []
    for t in turns:
        if t.get("role") == "user" and t.get("text"):
            return t["text"].strip(), int(t.get("ts") or 0)
    return "", 0


# ─────────────────────────────────────────────────────────────
# Tool result 파싱
# ─────────────────────────────────────────────────────────────


def extract_latest_tool_result(agent) -> dict:
    latest: dict = {}
    for m in agent.messages:
        content = m.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or "toolResult" not in b:
                continue
            for c in b["toolResult"].get("content", []):
                if isinstance(c, dict) and "text" in c:
                    try:
                        latest = json.loads(c["text"])
                    except Exception:
                        pass
    return latest


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────


def main():
    if not DATA_SRC.exists():
        print(f"❌ 데이터 파일 없음: {DATA_SRC}")
        return 1

    with open(DATA_SRC, encoding="utf-8") as f:
        cases = json.load(f)
    print(f"📦 로드: {len(cases)}건 (refund_test_cases_enriched.json)")

    # 비영업시간 window 필터
    windowed = []
    for case in cases:
        _, ts = get_first_user_turn(case)
        if is_off_hours_weekend(ts):
            windowed.append(case)
    print(f"📅 주말 (금/토/일) window 필터 통과: {len(windowed)}건")

    if not windowed:
        print("⚠️  주말 케이스 없음. 종료.")
        return 0

    # 디렉토리 준비
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    PREDICTION_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    clear_all_sessions()

    run_summary = {
        "started_at": datetime.now(KST).isoformat(),
        "data_source": str(DATA_SRC.relative_to(ROOT)),
        "window": "금/토/일 한국시간 (weekday 4,5,6)",
        "wrapper_model": "us.anthropic.claude-haiku-4-5-20251001-v1:0 (Bedrock)",
        "total_input": len(windowed),
        "processed": 0,
        "domain_pass": 0,
        "domain_skip": 0,
        "errors": [],
        "results": [],
    }

    for i, case in enumerate(windowed, 1):
        chat_id = case.get("chat_id", f"unknown_{i}")
        user_text, ts = get_first_user_turn(case)
        if not user_text:
            continue

        admin_data = case.get("admin_data", {})
        ts_kst = datetime.fromtimestamp(ts / 1000, tz=KST).isoformat() if ts else None
        weekday = datetime.fromtimestamp(ts / 1000, tz=KST).strftime("%a") if ts else None

        print(f"\n[{i}/{len(windowed)}] {chat_id[:12]} · {weekday} {ts_kst} · {user_text[:40]!r}")

        # 1. 입력 스냅샷 저장 (admin_data + user first turn + ts)
        snapshot = {
            "chat_id": chat_id,
            "captured_at": datetime.now(KST).isoformat(),
            "user_first_text": user_text,
            "user_first_ts": ts,
            "user_first_kst": ts_kst,
            "conversation_time": case.get("conversation_time", ""),
            "admin_data": admin_data,
            "full_conversation_turns": case.get("conversation_turns", []),  # 매니저 실답변 포함 — 비교용
        }
        snap_path = SNAPSHOT_DIR / f"{chat_id}.json"
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        # 2. wrapper agent 실행
        session_id = f"shadow_{chat_id}"
        clear_session(session_id)
        agent = get_agent_for_session(session_id)

        msg = (
            f"{user_text}\n\n"
            f"<admin_data>{json.dumps(admin_data, ensure_ascii=False)}</admin_data>\n"
            f"<conversation_time>{case.get('conversation_time', '')}</conversation_time>"
        )

        try:
            draft_text = agent.handle_turn(msg)
            tool_result = extract_latest_tool_result(agent)
            should_respond = agent.should_respond()
            intent = getattr(agent, "last_intent", "") or tool_result.get("intent", "")
            template_id = tool_result.get("template_id", "")
            refund_amount = tool_result.get("refund_amount")
            error = None
        except Exception as e:
            draft_text = ""
            tool_result = {}
            should_respond = False
            intent = ""
            template_id = ""
            refund_amount = None
            error = f"{type(e).__name__}: {e}"
            run_summary["errors"].append({"chat_id": chat_id, "error": error})
            print(f"    ❌ error: {error}")

        # 3. 예측 저장
        prediction = {
            "chat_id": chat_id,
            "snapshot_ref": str(snap_path.relative_to(ROOT)),
            "wrapper_model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "intent": intent,
            "is_refund_domain": should_respond,
            "template_id": template_id,
            "refund_amount": refund_amount,
            "draft_answer": draft_text,
            "reasoning_path": tool_result.get("reasoning_path", ""),
            "error": error,
            "generated_at": datetime.now(KST).isoformat(),
        }
        pred_path = PREDICTION_DIR / f"{chat_id}.json"
        with open(pred_path, "w", encoding="utf-8") as f:
            json.dump(prediction, f, ensure_ascii=False, indent=2)

        run_summary["processed"] += 1
        if should_respond:
            run_summary["domain_pass"] += 1
            marker = "✅"
        else:
            run_summary["domain_skip"] += 1
            marker = "🚫"

        print(f"    {marker} intent={intent!r} template={template_id!r} "
              f"refund={refund_amount} respond={should_respond}")

        run_summary["results"].append({
            "chat_id": chat_id,
            "intent": intent,
            "template_id": template_id,
            "should_respond": should_respond,
            "refund_amount": refund_amount,
            "weekday": weekday,
        })

    run_summary["finished_at"] = datetime.now(KST).isoformat()

    # 로그 저장
    log_name = datetime.now(KST).strftime("run_log_%Y%m%d_%H%M%S.json")
    log_path = RUN_LOG_DIR / log_name
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(run_summary, f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 60)
    print("📊 Shadow run 요약")
    print("=" * 60)
    print(f"  total input:    {run_summary['total_input']}")
    print(f"  processed:      {run_summary['processed']}")
    print(f"  domain pass:    {run_summary['domain_pass']}")
    print(f"  domain skip:    {run_summary['domain_skip']}")
    print(f"  errors:         {len(run_summary['errors'])}")
    print(f"\n  snapshots:      {SNAPSHOT_DIR.relative_to(ROOT)}/ ({run_summary['processed']} files)")
    print(f"  predictions:    {PREDICTION_DIR.relative_to(ROOT)}/ ({run_summary['processed']} files)")
    print(f"  run log:        {log_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
