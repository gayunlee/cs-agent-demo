"""strands-evals — 멀티턴 케이스 평가 (wrapper 경로).

T2→T3 같은 멀티턴 시나리오를 2턴 handle_turn 으로 실행 후
최종 턴의 template_id 로 TypeAccuracy 채점.

smoke_wrapper_multiturn.py 의 상위 호환 — 점수 + 이유 제공.

실행:
    .venv311/bin/python -m scripts.run_evals_strands_multiturn
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401
from strands_evals import Case, Experiment
from src.agents.wrapper_agent import clear_all_sessions, clear_session, get_agent_for_session
from scripts.evaluators.type_accuracy import TypeAccuracyEvaluator

GOLDEN_V2 = ROOT / "data/mock_scenarios/golden/v2"


def load_multiturn_cases() -> list[Case]:
    """conversation_turns 에 user 턴이 2개 이상인 케이스만."""
    cases: list[Case] = []
    for p in sorted(GOLDEN_V2.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
        turns = s.get("conversation_turns") or []
        user_turns = [t for t in turns if t.get("role") == "user" and t.get("text")]
        if len(user_turns) < 2:
            continue
        exp = s.get("expected") or {}
        cases.append(Case(
            name=p.stem,
            input=s,
            expected_output=exp.get("template_id", ""),
            metadata={
                "expected_template_id": exp.get("template_id", ""),
                "user_turn_count": len(user_turns),
                "scenario": s.get("scenario", ""),
            },
        ))
    return cases


def run_multiturn_task(case: Case) -> dict:
    """멀티턴 실행 — 모든 user 턴을 순차 handle_turn."""
    s = case.input
    session_id = f"eval_mt_{case.name}"
    clear_session(session_id)
    agent = get_agent_for_session(session_id)

    turns = s.get("conversation_turns") or []
    user_turns = [t.get("text", "") for t in turns if t.get("role") == "user" and t.get("text")]
    admin_json = json.dumps(s.get("admin_data") or {}, ensure_ascii=False)

    for i, user_text in enumerate(user_turns):
        if i == 0:
            msg = (
                f"{user_text}\n\n"
                f"<admin_data>{admin_json}</admin_data>\n"
                f"<conversation_time>{s.get('conversation_time', '')}</conversation_time>"
            )
        else:
            msg = user_text

        try:
            agent.handle_turn(msg)
        except Exception as e:
            return {
                "output": {"template_id": f"ERROR:{e}"},
                "trajectory": ["error"],
            }

    return {
        "output": {"template_id": agent.last_template_id or ""},
        "trajectory": [f"turn_{i}" for i in range(len(user_turns))],
    }


def main() -> int:
    clear_all_sessions()
    cases = load_multiturn_cases()
    if not cases:
        print("⚠️  멀티턴 케이스 없음")
        return 0

    print(f"📦 멀티턴 케이스: {len(cases)}건")
    for c in cases:
        md = c.metadata
        print(f"  - {c.name} | turns={md['user_turn_count']} | exp={md['expected_template_id']}")

    evaluators = [TypeAccuracyEvaluator()]
    experiment = Experiment(cases=cases, evaluators=evaluators)

    print(f"\n🚀 멀티턴 평가 시작...")
    reports = experiment.run_evaluations(run_multiturn_task)

    print("\n" + "=" * 60)
    print("📊 결과 (멀티턴)")
    print("=" * 60)
    for r in reports:
        name = r.evaluator_name if hasattr(r, "evaluator_name") else type(r).__name__
        overall = getattr(r, "overall_score", None)
        cases_list = getattr(r, "cases", []) or []
        scores = getattr(r, "scores", []) or []
        passes = getattr(r, "test_passes", []) or []
        reasons = getattr(r, "reasons", []) or []
        pass_count = sum(1 for p in passes if p)
        if overall is not None:
            print(f"\n[{name}] {pass_count}/{len(cases_list)} pass · overall={overall:.2f}")
        for cd, sc, ps, rs in zip(cases_list, scores, passes, reasons):
            cn = cd.get("name", "?") if isinstance(cd, dict) else getattr(cd, "name", "?")
            mark = "✅" if ps else "❌"
            print(f"  {mark} {cn:<42} score={sc:.2f} — {rs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
