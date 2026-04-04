"""Eval 루프: agent 답변 vs 실제 매니저 답변 비교

1. eval 세트 로드 (userId 포함)
2. agent 실행 (userId로 실제 API 조회)
3. LLM으로 유사도 평가
4. 차이점 패턴 분석
"""
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.refund_agent import RefundAgent


def run_eval(eval_path: str = "data/test_cases/eval_set_20.json", mock: bool = False):
    agent = RefundAgent(region="us-west-2", mock=mock)

    with open(eval_path) as f:
        cases = json.load(f)

    results = []
    for i, case in enumerate(cases):
        user_msgs = case["user_messages"]
        user_id = case.get("user_id", "")
        manager_resp = case.get("manager_responses", [""])[0]

        print(f"\n[{i+1}/{len(cases)}] {case.get('category', '?')} | {user_msgs[0][:50]}...")

        start = time.time()
        result = agent.process(user_msgs, case.get("chat_id", ""), user_id=user_id)
        elapsed = time.time() - start

        print(f"  Tools: {result.tools_used}")
        print(f"  Time: {elapsed:.1f}s, Tokens: {result.total_tokens}")

        # 답변 초안 추출
        agent_draft = ""
        if "[답변 초안]" in result.final_answer:
            agent_draft = result.final_answer.split("[답변 초안]")[1].strip()
        else:
            agent_draft = result.final_answer

        print(f"  Agent: {agent_draft[:100]}...")
        print(f"  Manager: {manager_resp[:100]}...")

        results.append({
            "chat_id": case.get("chat_id", ""),
            "category": case.get("category", ""),
            "user_messages": user_msgs,
            "user_id": user_id,
            "agent_answer": result.final_answer,
            "agent_draft": agent_draft,
            "manager_response": manager_resp,
            "tools_used": result.tools_used,
            "steps_count": len(result.steps),
            "tokens": result.total_tokens,
            "elapsed_sec": round(elapsed, 1),
        })

    # 저장
    output_path = "data/eval_results/eval_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n\n=== 결과 저장: {output_path} ===")

    # 요약
    total = len(results)
    with_tools = sum(1 for r in results if r["tools_used"])
    avg_tokens = sum(r["tokens"] for r in results) / total if total else 0
    avg_time = sum(r["elapsed_sec"] for r in results) / total if total else 0

    print(f"\n총 {total}건 처리")
    print(f"도구 사용: {with_tools}건 ({with_tools/total*100:.0f}%)")
    print(f"평균 토큰: {avg_tokens:.0f}")
    print(f"평균 시간: {avg_time:.1f}s")

    # 도구 사용 분포
    tool_counts = {}
    for r in results:
        for t in r["tools_used"]:
            tool_counts[t] = tool_counts.get(t, 0) + 1
    print(f"\n도구 사용 분포:")
    for t, c in sorted(tool_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}회")


if __name__ == "__main__":
    mock = "--mock" in sys.argv
    eval_path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "data/test_cases/eval_set_20.json"
    run_eval(eval_path, mock=mock)
