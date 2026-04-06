"""Gate 동작 확인 — wrapper.should_respond() 로 환불 도메인 판단.

별도 gate 모듈 없음. wrapper 내부 intent classifier 결과를 재사용 (LLM 중복 0).
장기적으로 모든 CS 유형 대응하면 REFUND_DOMAIN_INTENTS whitelist 만 제거.

검증:
- 환불 도메인 4건 → should_respond()=True
- 비도메인 4건 → should_respond()=False (caller 는 draft 저장 skip)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401

from src.agents.wrapper_agent import clear_all_sessions, get_agent_for_session

# (메시지, 기대 should_respond)
CASES: list[tuple[str, bool]] = [
    # 환불 도메인 — 모두 respond 기대
    ("환불 부탁드립니다", True),
    ("구독 해지하고 싶어요", True),
    ("자동결제 됐는데 취소해주세요", True),
    ("카드 변경하고 싶어요", True),
    # 비도메인/모호 — 모두 respond 기대 (재질문 "무엇을 도와드릴까요?")
    # Gayoon 정책: 모호하면 skip 아니라 재질문 (2026-04-06)
    ("안녕하세요", True),
    ("배송 언제 오나요?", True),
    ("쿠폰 받을 수 있나요?", True),
    ("수업 질문이 있어요", True),
]


def main() -> int:
    clear_all_sessions()

    print("=" * 75)
    print("Domain Gate 스모크 — wrapper.should_respond() 기반")
    print("(환불 4건 respond / 비도메인 4건 skip 기대)")
    print("=" * 75)

    passed = 0
    failed = 0
    for i, (msg, expect_respond) in enumerate(CASES):
        session_id = f"gate_smoke_{i}"
        agent = get_agent_for_session(session_id)
        try:
            agent.handle_turn(msg)
        except Exception as e:
            print(f"  ❌ [error] {msg} — {e}")
            failed += 1
            continue

        should_respond = agent.should_respond()
        intent = getattr(agent, "last_intent", "?")
        ok = should_respond == expect_respond
        mark = "✅" if ok else "❌"
        exp = "respond" if expect_respond else "skip"
        act = "respond" if should_respond else "skip"
        print(
            f"  {mark} [{exp}→{act}] intent={intent:<27} | {msg}"
        )
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 75)
    print(f"통과: {passed}/{len(CASES)} · 실패: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
