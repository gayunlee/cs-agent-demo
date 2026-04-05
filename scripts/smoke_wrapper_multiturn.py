"""Multi-turn memory smoke test — T2→T3 시나리오.

Turn 1: "구독해지, 환불 요청합니다." → T2 견적 기대
Turn 2: "네 진행해주세요." → T3 접수완료 기대 (이전 턴 맥락 필요)

목적: SlidingWindow Memory가 wrapper level 에 쌓이는지, 그리고
turn 2에서 legacy workflow가 올바른 경로를 타는지 확인.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.wrapper_agent import clear_all_sessions, get_agent_for_session


def main():
    clear_all_sessions()
    case_path = ROOT / "data/mock_scenarios/golden/v2/T2_T3_68c0de43_multiturn.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    admin_json = json.dumps(case["admin_data"], ensure_ascii=False)
    conv_time = case["conversation_time"]
    session_id = case["source_chat_id"]
    agent = get_agent_for_session(session_id)

    turn1_msg = (
        "구독해지, 환불 요청합니다.\n\n"
        f"<admin_data>{admin_json}</admin_data>\n"
        f"<conversation_time>{conv_time}</conversation_time>"
    )
    turn2_msg = "네 진행해주세요. 환불 확정합니다."

    print("=" * 70)
    print("TURN 1 — 환불 요청")
    print("=" * 70)
    agent.log_user_turn(turn1_msg)
    r1 = agent(turn1_msg)
    agent.log_assistant_turn(str(r1))
    print(str(r1)[:600])

    print("\n" + "=" * 70)
    print("TURN 2 — 확정 (이전 턴 맥락 필요)")
    print("=" * 70)
    agent.log_user_turn(turn2_msg)
    r2 = agent(turn2_msg)
    agent.log_assistant_turn(str(r2))
    print(str(r2)[:600])

    print(f"\n[turn_log 크기] {len(agent.turn_log)} 턴 누적 (legacy 주입용)")

    # tool result 안의 template_id 추출 (text 블록 안에 JSON 문자열 임베드)
    print("\n" + "=" * 70)
    print("Tool return 값 (template_id 확인)")
    print("=" * 70)
    template_ids: list[str] = []
    for i, m in enumerate(agent.messages):
        content = m.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and "toolResult" in b:
                tr = b["toolResult"]
                for c in tr.get("content", []):
                    if isinstance(c, dict) and "text" in c:
                        try:
                            j = json.loads(c["text"])
                        except Exception:
                            continue
                        tid = j.get("template_id", "?")
                        template_ids.append(tid)
                        print(f"  msg[{i}] template_id={tid!r}  "
                              f"refund={j.get('refund_amount')}  "
                              f"path={j.get('reasoning_path','')!r}")

    # 검증
    print("\n" + "=" * 70)
    if len(template_ids) >= 2:
        t1_ok = "T2" in template_ids[0]
        t2_ok = "T3" in template_ids[1]
        print(f"Turn 1 template: {template_ids[0]}  {'✅ T2 경로' if t1_ok else '❌'}")
        print(f"Turn 2 template: {template_ids[1]}  {'✅ T3 경로' if t2_ok else '❌'}")
        print(f"\n{'🎉 멀티턴 T2→T3 통과!' if (t1_ok and t2_ok) else '❌ 멀티턴 실패'}")
    print("=" * 70)

    # messages 전체 확인 — memory에 turn1 content 남아있는지
    print("\n" + "=" * 70)
    print(f"agent.messages 총 {len(agent.messages)}개")
    print("=" * 70)
    for i, m in enumerate(agent.messages):
        role = m.get("role", "?")
        content = m.get("content", [])
        if isinstance(content, list):
            preview_parts = []
            for b in content:
                if isinstance(b, dict):
                    if "text" in b:
                        preview_parts.append(f"text:{b['text'][:80]}")
                    elif "toolUse" in b:
                        preview_parts.append(f"toolUse:{b['toolUse'].get('name')}")
                    elif "toolResult" in b:
                        preview_parts.append("toolResult:<...>")
            print(f"  [{i}] {role}: {' | '.join(preview_parts)}")

    # turn 2 결과에 T3 키워드 ("접수 완료") 있는지
    r2_text = str(r2)
    has_t3 = any(kw in r2_text for kw in ["접수 완료", "접수완료", "환불 접수"])
    has_t2 = any(kw in r2_text for kw in ["환불 금액", "견적"])
    print("\n" + "=" * 70)
    print(f"Turn 2 결과에 T3 키워드: {has_t3}")
    print(f"Turn 2 결과에 T2 키워드: {has_t2}")
    print("=" * 70)


if __name__ == "__main__":
    main()
