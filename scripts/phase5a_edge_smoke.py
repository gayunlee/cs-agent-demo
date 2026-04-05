"""Phase 5-A Edge 라우팅 smoke test.

5개 신규 edge 체인(is_system_error, is_compound_issue, is_emotional_escalation,
is_flow_cancellation, is_exception_refund_request)이 합성 입력으로 올바르게
매칭되는지 확인한다.

DiagnoseEngine의 first_passing_chain을 routing_order 그대로 호출해서
"edge 패턴 입력 → 해당 edge 체인으로 라우팅되는지" 검증한다.

실제 유저 메시지가 아닌 합성 입력 기준이라 Phase 5-B LLM-as-judge 평가는 별도.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.loader import get_loader
from src.domain.diagnose_engine import DiagnoseEngine
from src.domain.functions import DEFAULT_FUNCTIONS


# (label, user_text, expected_chain_id)
CASES: list[tuple[str, str, str]] = [
    # is_system_error
    ("앱 로그인 오류", "앱 로그인 안 돼요. 에러 메시지 떠요.", "is_system_error"),
    ("라이브 입장 불가", "라이브 수업 입장 불가합니다. 재생 안 돼요.", "is_system_error"),
    ("다운로드 불가", "다운로드 안 돼서 강의 못 들어요.", "is_system_error"),
    # is_compound_issue
    (
        "환불 + 배송 복합",
        "환불 신청하고 싶고 그리고 또 책 배송 상태도 확인 부탁드려요.",
        "is_compound_issue",
    ),
    (
        "해지 + 이메일 재발송",
        "해지하고 싶은데 추가로 이메일 재발송도 해주세요.",
        "is_compound_issue",
    ),
    # is_emotional_escalation
    ("강한 불만", "진짜 너무 짜증나고 실망입니다. 화가 나요.", "is_emotional_escalation"),
    ("소비자원 위협", "소비자원에 신고할 겁니다. 사기 아닌가요?", "is_emotional_escalation"),
    # is_flow_cancellation
    (
        "환불 철회",
        "환불 신청했는데 취소할게요. 다시 생각해봤어요.",
        "is_flow_cancellation",
    ),
    (
        "해지 보류",
        "해지 요청 없었던 걸로 해주세요. 계속 이용할게요.",
        "is_flow_cancellation",
    ),
    # is_exception_refund_request
    ("건강 사유", "건강이 너무 안 좋아서 병원 입원 중입니다. 한 번만 봐주세요.", "is_exception_refund_request"),
    ("사망/장례", "가족 장례로 사정이 생겨서요. 선처 부탁드립니다.", "is_exception_refund_request"),
    # routing_order 앞쪽의 is_card_change_inquiry가 여전히 잡는지 회귀
    ("카드 변경 (회귀)", "카드 변경하고 싶어요", "is_card_change_inquiry"),
]


def build_empty_ctx(user_text: str) -> dict:
    """edge 체인은 user_text 키워드만 참조하므로 나머지 필드는 최소 default."""
    return {
        "user_text": user_text,
        "ctx": {
            "us_user_id": "test_user",
            "user_name": "테스트",
            "products": [],
            "transactions": [],
            "success_txs": [],
            "refund_txs": [],
            "memberships": [],
            "refunds": [],
            "has_accessed": False,
            "all_refunded": False,
            "prev_had_t2": False,
            "prev_had_t6": False,
            "prev_manager_count": 0,
            "conversation_time": "",
        },
    }


def main() -> int:
    print("=" * 60)
    print("Phase 5-A Edge 라우팅 smoke test")
    print("=" * 60)

    loader = get_loader()
    engine = DiagnoseEngine(loader, DEFAULT_FUNCTIONS)

    routing_order = loader.load("refund_chains.yaml").get("routing_order", [])
    print(f"\nrouting_order: {len(routing_order)}개")
    print(f"cases: {len(CASES)}개\n")

    passed = 0
    failed: list[str] = []

    for i, (label, user_text, expected) in enumerate(CASES, 1):
        ctx = build_empty_ctx(user_text)
        # DiagnoseEngine은 flat ctx를 받음 — has_keyword가 user_text를 어떻게 참조하는지 확인
        # refund_chains.yaml의 check는 "has_keyword(user_text, '...')" 형태
        # 즉 DSL 컨텍스트의 최상위 변수 user_text를 참조
        dsl_ctx = {
            "user_text": user_text,
            "ctx": ctx["ctx"],
        }

        result = engine.first_passing_chain(routing_order, dsl_ctx)
        matched = result.chain_id if result and result.passed else None
        ok = matched == expected
        marker = "✅" if ok else "❌"
        print(f"[{i:2d}/{len(CASES)}] {marker} {label}")
        print(f"       입력: {user_text}")
        print(f"       기대: {expected}")
        print(f"       실제: {matched}")
        if ok:
            passed += 1
        else:
            failed.append(f"{label}: expected={expected}, got={matched}")
        print()

    print("=" * 60)
    print(f"결과: {passed}/{len(CASES)} 통과")
    print("=" * 60)
    if failed:
        print("\n실패 케이스:")
        for f in failed:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
