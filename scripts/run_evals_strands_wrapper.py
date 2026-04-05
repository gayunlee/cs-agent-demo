"""strands-agents-evals — wrapper 경로 버전.

`scripts/run_evals_strands.py` 는 legacy `RefundAgentV2.process()` 를 직접 호출.
이 스크립트는 같은 v2 골든셋을 **wrapper agent 경로** 로 돌려서 동일한
evaluator (TypeAccuracy, AmountAccuracy) 로 채점. wrapper 층 회귀를 숫자로 잡는다.

차이점:
- RefundAgentV2.process() → agent.handle_turn(msg) (Strands Agent + AgentCore Memory + Guardrail)
- user_message 에 admin_data 를 `<admin_data>` 블록으로 embed
- template_id / refund_amount 는 `agent.messages` 의 toolResult 에서 추출

사용:
    .venv311/bin/python -m scripts.run_evals_strands_wrapper
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401

from strands_evals import Case, Experiment

from src.agents.wrapper_agent import clear_all_sessions, get_agent_for_session
from scripts.evaluators.type_accuracy import TypeAccuracyEvaluator
from scripts.evaluators.amount_accuracy import AmountAccuracyEvaluator

GOLDEN_V2 = ROOT / "data/mock_scenarios/golden/v2"


def load_v2_cases() -> list[Case]:
    cases: list[Case] = []
    for p in sorted(GOLDEN_V2.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
        exp = s.get("expected") or {}
        expected_amt = exp.get("refund_amount_policy") or exp.get("refund_amount_manager")
        cases.append(
            Case(
                name=p.stem,
                input=s,
                expected_output=exp.get("template_id", ""),
                metadata={
                    "source_chat_id": s.get("source_chat_id", ""),
                    "expected_template_id": exp.get("template_id", ""),
                    "expected_refund_amount": expected_amt,
                    "applied_rule": exp.get("applied_rule", ""),
                    "scenario": s.get("scenario", ""),
                },
            )
        )
    return cases


def _extract_tool_output(agent) -> tuple[str, int | None]:
    """agent.messages 에서 run_refund_workflow 의 가장 최근 toolResult 파싱."""
    template_id = ""
    refund_amount: int | None = None
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
                        j = json.loads(c["text"])
                    except Exception:
                        continue
                    tid = j.get("template_id")
                    if tid:
                        template_id = tid
                    if j.get("refund_amount") is not None:
                        refund_amount = j["refund_amount"]
    return template_id, refund_amount


def run_wrapper_task(case: Case) -> dict:
    """Case 실행 — wrapper.handle_turn 경로."""
    s = case.input
    session_id = f"eval_{case.name}"

    # 같은 이름의 이전 실행 제거 (멱등성)
    from src.agents.wrapper_agent import clear_session

    clear_session(session_id)
    agent = get_agent_for_session(session_id)

    user_text = "\n".join(s.get("user_messages") or [])
    admin_json = json.dumps(s.get("admin_data") or {}, ensure_ascii=False)
    msg = (
        f"{user_text}\n\n"
        f"<admin_data>{admin_json}</admin_data>\n"
        f"<conversation_time>{s.get('conversation_time','')}</conversation_time>"
    )

    try:
        agent.handle_turn(msg)
    except Exception as e:
        print(f"⚠️  {case.name} 실행 실패: {e}")
        return {
            "output": {"template_id": "", "refund_amount": None},
            "trajectory": ["wrapper_error"],
        }

    template_id, refund_amount = _extract_tool_output(agent)
    return {
        "output": {"template_id": template_id, "refund_amount": refund_amount},
        "trajectory": ["wrapper", "run_refund_workflow"],
    }


def main() -> int:
    clear_all_sessions()

    cases = load_v2_cases()
    print(f"📦 로드된 v2 골든셋: {len(cases)}건 (wrapper 경로)")
    for c in cases:
        md = c.metadata
        amt = md.get("expected_refund_amount")
        amt_txt = f"{amt:,}원" if amt else "(N/A)"
        print(f"  - {c.name} | exp={md['expected_template_id']} | {amt_txt}")

    evaluators = [TypeAccuracyEvaluator(), AmountAccuracyEvaluator()]
    experiment = Experiment(cases=cases, evaluators=evaluators)

    print(f"\n🚀 wrapper 경로 평가 시작 ({len(cases)}건 × {len(evaluators)})...")
    reports = experiment.run_evaluations(run_wrapper_task)

    print("\n" + "=" * 70)
    print("📊 결과 (wrapper 경로)")
    print("=" * 70)

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
        else:
            print(f"\n[{name}]")
        for case_data, sc, ps, rs in zip(cases_list, scores, passes, reasons):
            cn = case_data.get("name", "?") if isinstance(case_data, dict) else getattr(case_data, "name", "?")
            mark = "✅" if ps else "❌"
            print(f"  {mark} {cn:<42} score={sc:.2f} — {rs}")

    # 저장
    out_dir = ROOT / "data/eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "strands_evals_v2_wrapper.json"
    save_data = {
        "cases_count": len(cases),
        "path": "wrapper",
        "reports": [
            {
                "evaluator": type(r).__name__,
                "overall_score": getattr(r, "overall_score", None),
                "cases": getattr(r, "cases", None),
                "scores": getattr(r, "scores", None),
                "test_passes": getattr(r, "test_passes", None),
                "reasons": getattr(r, "reasons", None),
            }
            for r in reports
        ],
    }
    with open(out_path, "w") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
