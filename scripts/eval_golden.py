"""골든셋 smoke test

`data/mock_scenarios/golden/*.json`의 각 시나리오에 대해:
1. RefundAgentV2.process 실행
2. template_id가 expected.template_id와 일치하는지
3. wf_ctx.path에 expected.path_contains가 포함되는지
4. final_answer가 비어 있지 않은지
5. LLM fallback 케이스는 실제 Bedrock 호출 (기본은 mock)

Usage:
    python3 scripts/eval_golden.py              # mock 모드
    python3 scripts/eval_golden.py --real-llm   # LLM fallback 실제 호출
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.refund_agent_v2 import RefundAgentV2

GOLDEN_DIR = Path("data/mock_scenarios/golden")
RESULT_PATH = Path("data/eval_results/golden_replay.json")


def load_scenarios() -> list[dict]:
    scenarios = []
    for p in sorted(GOLDEN_DIR.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
            s["_file"] = p.name
            scenarios.append(s)
    return scenarios


def run_scenario(agent: RefundAgentV2, scenario: dict) -> dict:
    result = agent.process(
        user_messages=scenario["user_messages"],
        chat_id=scenario.get("scenario", "golden"),
        admin_data=scenario["admin_data"],
        conversation_time=scenario.get("conversation_time", ""),
        conversation_turns=scenario.get("conversation_turns") or [],
    )

    expected = scenario.get("expected", {})
    exp_template = expected.get("template_id", "")
    exp_path = expected.get("path_contains", "")

    # workflow path 추출 (step details의 path)
    wf_path = []
    for s in result.steps:
        if s.step == "classify" and s.detail.get("path"):
            wf_path = s.detail["path"]
            break

    path_str = " → ".join(wf_path)
    template_ok = result.template_id == exp_template
    path_ok = (exp_path in path_str) if exp_path else True
    answer_ok = bool(result.final_answer and len(result.final_answer.strip()) > 10)

    passed = template_ok and path_ok and answer_ok

    return {
        "file": scenario["_file"],
        "scenario": scenario["scenario"],
        "expected_template": exp_template,
        "actual_template": result.template_id,
        "template_ok": template_ok,
        "expected_path_contains": exp_path,
        "actual_path": path_str,
        "path_ok": path_ok,
        "answer_ok": answer_ok,
        "answer_preview": (result.final_answer or "")[:200],
        "answer_length": len(result.final_answer or ""),
        "passed": passed,
        "intent": result.intent,
    }


def main():
    real_llm = "--real-llm" in sys.argv
    mode = "real-llm" if real_llm else "mock"
    print(f"골든셋 smoke test (mode: {mode})\n")

    scenarios = load_scenarios()
    print(f"시나리오 {len(scenarios)}건 로드\n")

    agent = RefundAgentV2(mock=not real_llm)

    results = []
    passed_count = 0
    for i, sc in enumerate(scenarios, 1):
        print(f"[{i}/{len(scenarios)}] {sc['scenario']}")
        try:
            r = run_scenario(agent, sc)
        except Exception as e:
            r = {
                "file": sc["_file"],
                "scenario": sc["scenario"],
                "error": str(e),
                "passed": False,
            }
        results.append(r)

        if r.get("passed"):
            passed_count += 1
            print(f"   ✅ {r['actual_template']}")
        else:
            print(f"   ❌ {r.get('actual_template', '(에러)')}")
            if "error" in r:
                print(f"      ERROR: {r['error']}")
            else:
                if not r["template_ok"]:
                    print(f"      template: expected {r['expected_template']}, got {r['actual_template']}")
                if not r["path_ok"]:
                    print(f"      path '{r['expected_path_contains']}' not in '{r['actual_path']}'")
                if not r["answer_ok"]:
                    print(f"      answer too short: {r['answer_length']} chars")
        if r.get("answer_preview"):
            print(f"      📄 {r['answer_preview'][:100]}...")
        print()

    print("=" * 60)
    print(f"  결과: {passed_count}/{len(scenarios)} 통과 ({passed_count/len(scenarios)*100:.0f}%)")
    print("=" * 60)

    # 저장
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_PATH, "w") as f:
        json.dump({
            "mode": mode,
            "total": len(scenarios),
            "passed": passed_count,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {RESULT_PATH}")

    return 0 if passed_count == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(main())
