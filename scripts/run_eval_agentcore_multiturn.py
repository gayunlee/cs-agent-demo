"""AgentCore Evaluation — 멀티턴 T2→T3 세션.

2턴 세션을 하나의 OTel trace 로 수집 → ADOT 변환 → Builtin.ToolSelectionAccuracy.
"Turn 1 에서 diagnose → T2, Turn 2 에서 diagnose → T3" 전체를 한 번에 평가.

smoke_wrapper_multiturn.py 의 상위 호환 (binary pass/fail 대신 점수 + explanation).

실행:
    .venv311/bin/python -m scripts.run_eval_agentcore_multiturn
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


def _fix_tuples(obj):
    if isinstance(obj, dict):
        return {k: _fix_tuples(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_fix_tuples(i) for i in obj]
    return obj


def run_eval() -> int:
    print("=" * 60)
    print("AgentCore Evaluation — 멀티턴 T2→T3")
    print("=" * 60)

    # 1. telemetry
    from strands_evals.telemetry import StrandsEvalsTelemetry
    telemetry = StrandsEvalsTelemetry().setup_in_memory_exporter()
    print("✅ InMemory span exporter")

    # 2. 2턴 세션 실행
    from src.agents.wrapper_agent import clear_all_sessions, get_agent_for_session

    clear_all_sessions()
    case_path = ROOT / "data/mock_scenarios/golden/v2/T2_T3_68c0de43_multiturn.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    admin_json = json.dumps(case["admin_data"], ensure_ascii=False)
    session_id = "eval_multiturn"
    agent = get_agent_for_session(session_id)

    # Turn 1 — 환불 요청
    turn1_msg = (
        f"{case['user_messages'][0]}\n\n"
        f"<admin_data>{admin_json}</admin_data>\n"
        f"<conversation_time>{case.get('conversation_time', '')}</conversation_time>"
    )
    print(f"\n🧪 Turn 1: {case['user_messages'][0]}")
    try:
        r1 = agent.handle_turn(turn1_msg)
        t1_template = agent.last_template_id
        print(f"   → template: {t1_template}")
    except Exception as e:
        print(f"   ❌ Turn 1 실패: {e}")
        return 1

    # Turn 2 — 확정
    turn2_msg = "네 진행해주세요. 환불 확정합니다."
    print(f"\n🧪 Turn 2: {turn2_msg}")
    try:
        r2 = agent.handle_turn(turn2_msg)
        t2_template = agent.last_template_id
        print(f"   → template: {t2_template}")
    except Exception as e:
        print(f"   ❌ Turn 2 실패: {e}")
        return 1

    # 3. span 수집
    raw_spans = list(telemetry.in_memory_exporter.get_finished_spans())
    print(f"\n📦 수집된 span: {len(raw_spans)}개")
    if not raw_spans:
        print("❌ span 0개")
        return 1

    scope_names = {s.instrumentation_scope.name for s in raw_spans if s.instrumentation_scope}
    if "strands.telemetry.tracer" not in scope_names:
        print(f"❌ scope 불일치: {scope_names}")
        return 1
    print(f"✅ scope whitelist 일치")

    total_events = sum(len(s.events) for s in raw_spans)
    print(f"   총 event: {total_events}개")

    # 4. ADOT 변환
    from bedrock_agentcore.evaluation.span_to_adot_serializer import convert_strands_to_adot
    try:
        adot_docs = convert_strands_to_adot(raw_spans)
        adot_docs = _fix_tuples(adot_docs)
    except Exception as e:
        print(f"❌ ADOT 변환 실패: {e}")
        return 1
    print(f"✅ ADOT 변환: {len(adot_docs)}개 doc")

    # 5. evaluate
    import boto3
    region = os.environ.get("EVAL_REGION", "us-west-2")
    evaluator_name = "Builtin.ToolSelectionAccuracy"
    print(f"\n🏛️  evaluate() — {evaluator_name} (2턴 세션)")

    try:
        client = boto3.client("bedrock-agentcore", region_name=region)
        resp = client.evaluate(
            evaluatorId=evaluator_name,
            evaluationInput={"sessionSpans": adot_docs},
        )
    except Exception as e:
        print(f"❌ evaluate() 실패: {type(e).__name__}: {e}")
        return 1

    # 6. 결과
    results = resp.get("evaluationResults", [])
    if not results:
        print(f"⚠️  results 비어있음: {json.dumps(resp, default=str)[:400]}")
        return 1

    r0 = results[0]
    if "errorMessage" in r0:
        print(f"❌ 에러: {r0.get('errorCode')}: {r0.get('errorMessage')}")
        return 1

    print("\n" + "=" * 60)
    print("✅ AgentCore Evaluation — 멀티턴 SUCCESS")
    print("=" * 60)
    print(f"  evaluator    : {evaluator_name}")
    print(f"  value        : {r0.get('value')}")
    print(f"  label        : {r0.get('label')}")
    print(f"  Turn 1       : {t1_template}")
    print(f"  Turn 2       : {t2_template}")
    print(f"  explanation  : {(r0.get('explanation') or '')[:500]}")
    return 0


if __name__ == "__main__":
    sys.exit(run_eval())
