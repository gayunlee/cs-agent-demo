"""Phase 3 E2E 테스트 — 골든셋 T2 시나리오를 Consultant Agent로 돌려본다.

목적:
- Strands Agent + 14 tools + tool-loop가 실제로 동작하는지
- diagnose_refund_case tool이 올바른 template_id 반환하는지
- compose_template_answer가 답변 생성까지 완성하는지
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.consultant import create_consultant_agent, process_turn


def build_context_from_golden(scenario: dict) -> dict:
    """골든셋 JSON → tool context 변환"""
    admin = scenario.get("admin_data", {})
    transactions = admin.get("transactions", [])
    success_txs = [t for t in transactions if t.get("state") == "purchased_success"]
    refund_txs = [t for t in transactions if t.get("state") == "purchased_refund"]

    return {
        "user_text": " ".join(scenario.get("user_messages", [])),
        "ctx": {
            "us_user_id": admin.get("us_user_id", ""),
            "user_name": admin.get("ch_name", ""),
            "products": admin.get("products", []),
            "transactions": transactions,
            "success_txs": success_txs,
            "refund_txs": refund_txs,
            "memberships": admin.get("memberships", []),
            "refunds": admin.get("refunds", []),
            "has_accessed": admin.get("usage", {}).get("accessed", False),
            "conversation_time": scenario.get("conversation_time", ""),
            "all_refunded": len(refund_txs) >= len(success_txs) and len(refund_txs) > 0,
            "prev_had_t2": False,
            "prev_had_t6": False,
            "prev_manager_count": 0,
        },
    }


def main():
    print("=" * 60)
    print("Phase 3 E2E — Consultant Agent × 골든셋 T2")
    print("=" * 60)

    # 골든셋 로드
    with open("data/mock_scenarios/golden/T2_partial.json") as f:
        scenario = json.load(f)

    print(f"\n[시나리오] {scenario['scenario']}")
    print(f"[설명] {scenario['description']}")
    print(f"[유저 메시지] {scenario['user_messages']}")
    print(f"[기대 템플릿] {scenario['expected']['template_id']}")

    # Agent 생성
    agent = create_consultant_agent()
    print(f"\n[Agent tools] {len(agent.tool_registry.registry)}개 등록")

    # Context 구성
    context = build_context_from_golden(scenario)
    user_msg = context["user_text"]

    print("\n" + "─" * 60)
    print("Agent 실행 중...")
    print("─" * 60)

    answer = process_turn(agent, user_msg, context)

    print("\n" + "=" * 60)
    print("AGENT FINAL ANSWER")
    print("=" * 60)
    print(answer)
    print("=" * 60)

    # 기대 템플릿 포함 확인 (단순 키워드 매칭)
    expected_keywords = ["환불", "회원님"]
    has_key = all(kw in answer for kw in expected_keywords)
    print(f"\n{'✅' if has_key else '❌'} 기본 답변 패턴 포함: {has_key}")


if __name__ == "__main__":
    main()
