"""AgentCore Evaluation POC — wrapper agent 의 tool selection accuracy.

us-product-agent/scripts/eval_poc.py 패턴 복사. Builtin.ToolSelectionAccuracy
평가기 하나로 "wrapper agent 가 환불 요청에 올바른 tool 을 선택하는가" 검증.

파이프라인:
1. StrandsEvalsTelemetry in-memory span exporter 세팅
2. wrapper agent.handle_turn() 으로 1건 실행 → OTel span 수집
3. strands.telemetry.tracer scope 확인 + span/event 유효성
4. convert_strands_to_adot() 로 ADOT 포맷 변환
5. boto3 bedrock-agentcore.evaluate() 호출 (evaluatorId="Builtin.ToolSelectionAccuracy")
6. 결과 출력 (value, label, explanation)

실행:
  .venv311/bin/python -m scripts.run_eval_agentcore
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401  — AWS credentials 로드


def _fix_tuples(obj):
    """ADOT 문서의 tuple 을 list 로 변환 (boto3 직렬화 호환)."""
    if isinstance(obj, dict):
        return {k: _fix_tuples(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_fix_tuples(i) for i in obj]
    return obj


def run_eval() -> int:
    print("=" * 60)
    print("AgentCore Evaluation POC — Builtin.ToolSelectionAccuracy")
    print("=" * 60)

    # 1. telemetry — InMemory exporter
    from strands_evals.telemetry import StrandsEvalsTelemetry

    telemetry = StrandsEvalsTelemetry().setup_in_memory_exporter()
    print("✅ InMemory span exporter 연결")

    # 2. wrapper agent 실행
    from src.agents.wrapper_agent import clear_all_sessions, get_agent_for_session

    clear_all_sessions()

    case_path = ROOT / "data/mock_scenarios/golden/v2/T2_68b9602_1month_50k.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    admin_json = json.dumps(case["admin_data"], ensure_ascii=False)
    user_text = case["user_messages"][0]
    msg = (
        f"{user_text}\n\n"
        f"<admin_data>{admin_json}</admin_data>\n"
        f"<conversation_time>{case.get('conversation_time','')}</conversation_time>"
    )

    agent = get_agent_for_session("eval_poc_session")
    print(f"\n🧪 케이스: {case_path.name}")
    print(f"   유저 메시지: {user_text}")

    try:
        answer = agent.handle_turn(msg)
    except Exception as e:
        print(f"❌ agent 실행 실패: {e}")
        return 1

    print(f"   응답 (앞 200자): {answer[:200]}")

    # 3. span 수집 + 유효성 확인
    raw_spans = list(telemetry.in_memory_exporter.get_finished_spans())
    print(f"\n📦 수집된 span: {len(raw_spans)}개")
    if not raw_spans:
        print("❌ span 0개 — telemetry 연결 실패")
        return 1

    scope_names = {s.instrumentation_scope.name for s in raw_spans if s.instrumentation_scope}
    print(f"   scope names: {scope_names}")
    if "strands.telemetry.tracer" not in scope_names:
        print("❌ scope 'strands.telemetry.tracer' 없음")
        return 1
    print("✅ scope whitelist 일치")

    total_events = sum(len(s.events) for s in raw_spans)
    print(f"   총 event: {total_events}개")
    if total_events == 0:
        print("⚠️  event 0개 — evaluate() 실패 가능성")

    # 4. OTel → ADOT 변환
    from bedrock_agentcore.evaluation.span_to_adot_serializer import convert_strands_to_adot

    try:
        adot_docs = convert_strands_to_adot(raw_spans)
        adot_docs = _fix_tuples(adot_docs)
    except Exception as e:
        print(f"❌ ADOT 변환 실패: {e}")
        return 1
    print(f"✅ ADOT 변환 성공: {len(adot_docs)}개 doc")

    # 5. AgentCore evaluate 호출
    import boto3

    region = os.environ.get("EVAL_REGION", "us-west-2")
    evaluator_name = "Builtin.ToolSelectionAccuracy"
    print(f"\n🏛️  bedrock-agentcore-control.evaluate() — {evaluator_name} (region={region})")

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
        print(f"⚠️  evaluationResults 비어있음: {json.dumps(resp, default=str)[:400]}")
        return 1

    r0 = results[0]
    if "errorMessage" in r0:
        print(f"❌ 평가 에러: {r0.get('errorCode')}: {r0.get('errorMessage')}")
        return 1

    print("\n" + "=" * 60)
    print("✅ AgentCore Evaluation SUCCESS")
    print("=" * 60)
    print(f"  evaluator   : {evaluator_name}")
    print(f"  value       : {r0.get('value')}")
    print(f"  label       : {r0.get('label')}")
    print(f"  explanation : {(r0.get('explanation') or '')[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(run_eval())
