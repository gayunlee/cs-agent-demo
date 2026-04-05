"""strands-agents-evals 첫 슬라이스 — v2 골든셋 + type/amount evaluator.

사용:
    .venv311/bin/python scripts/run_evals_strands.py

동작:
1. data/mock_scenarios/golden/v2/*.json 전부 로드
2. 각 case에 대해 RefundAgentV2.process() 실행 (실제 LLM 분류)
3. type_accuracy + amount_accuracy 평가자로 채점
4. 결과 출력 (케이스별 pass/fail + overall score)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# .venv311에서 실행되어야 함 — strands_evals 의존성
sys.path.insert(0, str(Path(__file__).parent.parent))

from strands_evals import Case, Experiment

from src.refund_agent_v2 import RefundAgentV2
from scripts.evaluators.type_accuracy import TypeAccuracyEvaluator
from scripts.evaluators.amount_accuracy import AmountAccuracyEvaluator

GOLDEN_V2 = Path(__file__).parent.parent / "data/mock_scenarios/golden/v2"


def load_v2_cases() -> list[Case]:
    """v2 골든셋 json → Case 리스트.

    expected_refund_amount 우선순위:
    1. refund_amount_policy (정책 공식 기준, 있으면 우선)
    2. refund_amount_manager (매니저 실답변 기준)
    """
    cases: list[Case] = []
    for p in sorted(GOLDEN_V2.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
        exp = s.get("expected") or {}
        expected_amt = exp.get("refund_amount_policy") or exp.get("refund_amount_manager")
        cases.append(Case(
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
        ))
    return cases


def run_agent_task(case: Case) -> dict:
    """Case 실행 → {'template_id': ..., 'refund_amount': ...} dict 반환.

    evaluator들이 actual_output에서 꺼내 쓴다.
    """
    s = case.input  # 시나리오 dict
    use_real_llm = os.environ.get("CS_EVAL_MOCK", "0") != "1"
    agent = RefundAgentV2(mock=not use_real_llm)

    result = agent.process(
        user_messages=s["user_messages"],
        chat_id=s.get("source_chat_id") or case.name,
        admin_data=s["admin_data"],
        conversation_time=s.get("conversation_time", ""),
        conversation_turns=s.get("conversation_turns") or [],
    )

    # 환불금액 추출 — [final] step의 template variables에서
    refund_amount = None
    for step in (result.steps or []):
        if step.step == "final":
            v = (step.detail or {}).get("variables") or {}
            amt_str = v.get("환불금액")
            if amt_str:
                try:
                    refund_amount = int(str(amt_str).replace(",", ""))
                except (ValueError, TypeError):
                    pass
            break

    # strands-evals 규약: {"output": OutputT, "trajectory": ...}
    # OutputT는 actual_output에 저장돼서 evaluator가 접근 가능.
    return {
        "output": {
            "template_id": result.template_id or "",
            "refund_amount": refund_amount,
        },
        "trajectory": [step.step for step in (result.steps or [])],
    }


def main() -> int:
    cases = load_v2_cases()
    print(f"📦 로드된 v2 골든셋: {len(cases)}건")
    for c in cases:
        md = c.metadata
        amt = md.get("expected_refund_amount")
        amt_txt = f"{amt:,}원" if amt else "(N/A)"
        print(f"  - {c.name} | exp={md['expected_template_id']} | {amt_txt}")

    mode = "LLM" if os.environ.get("CS_EVAL_MOCK", "0") != "1" else "mock"
    print(f"\n⚙️  실행 모드: {mode} (CS_EVAL_MOCK=1 로 설정하면 mock 키워드 분류)")

    evaluators = [
        TypeAccuracyEvaluator(),
        AmountAccuracyEvaluator(),
    ]
    experiment = Experiment(cases=cases, evaluators=evaluators)

    print(f"\n🚀 평가 시작 ({len(cases)}건 × {len(evaluators)}개 평가자)...")
    reports = experiment.run_evaluations(run_agent_task)

    print("\n" + "=" * 70)
    print("📊 결과")
    print("=" * 70)

    for r in reports:
        name = r.evaluator_name if hasattr(r, "evaluator_name") else type(r).__name__
        overall = getattr(r, "overall_score", None)
        cases_list = getattr(r, "cases", []) or []
        scores = getattr(r, "scores", []) or []
        passes = getattr(r, "test_passes", []) or []
        reasons = getattr(r, "reasons", []) or []

        pass_count = sum(1 for p in passes if p)
        print(f"\n[{name}] {pass_count}/{len(cases_list)} pass · overall={overall:.2f}" if overall is not None else f"\n[{name}]")
        for case_data, sc, ps, rs in zip(cases_list, scores, passes, reasons):
            # case_data는 EvaluationData dict — 이름만 뽑기
            if isinstance(case_data, dict):
                cn = case_data.get("name", "?")
            else:
                cn = getattr(case_data, "name", str(case_data))
            mark = "✅" if ps else "❌"
            print(f"  {mark} {cn:<42} score={sc:.2f} — {rs}")

    # 저장
    out_dir = Path("data/eval_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "strands_evals_v2.json"
    save_data = {
        "cases_count": len(cases),
        "mode": mode,
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
