"""Wrapper Agent smoke test — tool call 동작 확인.

4/6 오전 첫 작업: wrapper_agent.py SYSTEM_PROMPT 강화가 실제로
Agent를 run_refund_workflow tool 호출로 유도하는지 확인.

케이스:
- T1 (해지 방법, 결제 이력 없음)
- T2→T3 멀티턴 (견적 안내 → 확정)

실행:
  python -m scripts.smoke_wrapper_agent
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.wrapper_agent import (
    clear_all_sessions,
    get_agent_for_session,
)


def build_user_message(case: dict) -> str:
    """골든셋 케이스를 agent 입력 메시지로 변환.

    admin_data 를 `<admin_data>...</admin_data>` 블록에 embed → SYSTEM_PROMPT가
    이 블록을 tool 인자로 그대로 전달하도록 지시했음.
    """
    user_text = "\n".join(case.get("user_messages") or [])
    admin_data = case.get("admin_data") or {}
    admin_json = json.dumps(admin_data, ensure_ascii=False)
    conv_time = case.get("conversation_time", "")
    return (
        f"{user_text}\n\n"
        f"<admin_data>{admin_json}</admin_data>\n"
        f"<conversation_time>{conv_time}</conversation_time>"
    )


def load_case(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def count_tool_calls(agent, result) -> tuple[int, list[str]]:
    """Agent 실행 결과에서 tool call 횟수 + tool 이름 추출.

    Strands Agent는 `agent.messages` (full conversation history) 에 tool_use
    블록을 누적. result 객체 대신 agent.messages 를 탐색.
    """
    names: list[str] = []
    try:
        messages = getattr(agent, "messages", None) or []
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                # Strands 포맷: {"toolUse": {"name": ..., "input": ...}}
                if "toolUse" in block and isinstance(block["toolUse"], dict):
                    names.append(block["toolUse"].get("name", "?"))
                # 대체 포맷: {"type": "tool_use", "name": ...}
                elif block.get("type") == "tool_use":
                    names.append(block.get("name", "?"))
    except Exception as e:
        print(f"[count_tool_calls] 탐색 실패: {e}")
    return len(names), names


def run_case(case_path: Path) -> dict:
    case = load_case(case_path)
    session_id = case.get("source_chat_id") or case_path.stem
    agent = get_agent_for_session(session_id)
    message = build_user_message(case)

    print(f"\n{'='*70}")
    print(f"📋 {case_path.name}")
    print(f"   시나리오: {case.get('scenario','')}")
    print(f"   expected template: {case.get('expected',{}).get('template_id','?')}")
    print(f"{'='*70}")

    try:
        result = agent(message)
    except Exception as e:
        return {"case": case_path.name, "ok": False, "error": f"invoke failed: {e}"}

    n_calls, names = count_tool_calls(agent, result)
    text_out = str(result)

    print(f"\n🔧 tool calls: {n_calls} — {names}")
    print(f"\n💬 agent answer (앞 500자):\n{text_out[:500]}")

    tool_called = "run_refund_workflow" in names
    return {
        "case": case_path.name,
        "tool_called": tool_called,
        "tool_calls": names,
        "answer_preview": text_out[:200],
    }


def main():
    clear_all_sessions()
    golden_dir = ROOT / "data/mock_scenarios/golden/v2"
    cases = sorted(golden_dir.glob("*.json"))
    results = [run_case(p) for p in cases if p.exists()]

    print(f"\n\n{'='*70}")
    print("📊 SMOKE TEST 요약")
    print(f"{'='*70}")
    passed = sum(1 for r in results if r.get("tool_called"))
    for r in results:
        mark = "✅" if r.get("tool_called") else "❌"
        print(f"{mark} {r['case']}: calls={r.get('tool_calls', [])}")
    print(f"\n통과: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
