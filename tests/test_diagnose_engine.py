"""DiagnoseEngine + refund_chains.yaml 통합 테스트.

Phase 1 완료 기준: workflow.py의 분기 로직이 YAML 엔진으로 동일 결과 재현.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.diagnose_engine import DiagnoseEngine
from src.domain.functions import DEFAULT_FUNCTIONS
from src.domain.loader import get_loader


def build_context(**kwargs):
    """테스트 케이스용 context dict 빌더"""
    default = {
        "user_text": "",
        "ctx": {
            "us_user_id": "u123",
            "prev_had_t2": False,
            "prev_had_t6": False,
            "prev_manager_count": 0,
            "success_txs": [],
            "refund_txs": [],
            "refunds": [],
            "all_refunded": False,
        },
    }
    default.update(kwargs)
    return default


def run_chain(engine: DiagnoseEngine, chain_id: str, context: dict):
    """체인 평가 후 (passed, chain_id) 반환"""
    r = engine.evaluate_chain(chain_id, context)
    return r.passed


def route(engine: DiagnoseEngine, context: dict) -> str:
    """routing_order 순회 → 첫 passing chain의 on_pass_template 반환"""
    loader = get_loader()
    chains_file = loader.load("refund_chains.yaml")
    routing_order = chains_file.get("routing_order", [])
    chains = chains_file.get("chains", {})

    for chain_id in routing_order:
        r = engine.evaluate_chain(chain_id, context)
        if r.passed:
            chain = chains.get(chain_id, {})
            return chain.get("on_pass_template", f"<NO_TEMPLATE: {chain_id}>")
    return "<NO_MATCH>"


def test_all():
    engine = DiagnoseEngine(functions=DEFAULT_FUNCTIONS)
    passed, failed = 0, 0

    cases = [
        # Node 0: 카드 문의
        (
            "T8 카드 변경 키워드",
            build_context(user_text="카드변경 하고 싶어요"),
            "T8_카드변경_안내",
        ),
        (
            "T8 카드 분실",
            build_context(user_text="카드 분실해서 재발급 받았는데"),
            "T8_카드변경_안내",
        ),

        # Node 1: 이전 턴 T2 → T3
        (
            "T3 이전 T2 + 매니저 응답",
            build_context(
                user_text="네 환불해주세요",
                ctx={"us_user_id": "u1", "prev_had_t2": True, "prev_manager_count": 1,
                     "prev_had_t6": False, "success_txs": [{"round": 1, "amount": 50000}],
                     "refund_txs": [], "refunds": [], "all_refunded": False},
            ),
            "T3_환불_접수_완료",
        ),

        # Branch D: 타인 번호
        (
            "T6b 가족 번호 시그널",
            build_context(
                user_text="가족 번호인데 환불이 가능한가요",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0, "success_txs": [], "refund_txs": [],
                     "refunds": [], "all_refunded": False},
            ),
            "T6b_본인확인_재질문",
        ),

        # Node 4: 결제 없음
        (
            "T1 결제 없음",
            build_context(
                user_text="해지 방법 알려주세요",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0, "success_txs": [], "refund_txs": [],
                     "refunds": [], "all_refunded": False},
            ),
            "T1_구독해지_방법_앱",
        ),

        # Node 5: 전부 환불됨 + 새 질문 → LLM fallback
        (
            "LLM fallback 전부환불 + 재가입",
            build_context(
                user_text="재가입하고 싶은데",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [{"round": 1, "amount": 50000}],
                     "refund_txs": [{"round": 1, "amount": 50000}],
                     "refunds": [], "all_refunded": True},
            ),
            "T_LLM_FALLBACK",
        ),

        # Node 5: 전부 환불됨 → T3
        (
            "T3 전부 환불됨",
            build_context(
                user_text="환불 확인 부탁드려요",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [{"round": 1, "amount": 50000}],
                     "refund_txs": [{"round": 1, "amount": 50000}],
                     "refunds": [], "all_refunded": True},
            ),
            "T3_환불_접수_완료",
        ),

        # Fallback: 환불 철회
        (
            "LLM fallback 환불 철회",
            build_context(
                user_text="환불 취소할게요, 계속 이용하겠습니다",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [{"round": 1, "amount": 50000}],
                     "refund_txs": [], "refunds": [], "all_refunded": False},
            ),
            "T_LLM_FALLBACK",
        ),

        # Branch A: 상품 변경
        (
            "T10 상품 변경 + 차액",
            build_context(
                user_text="상품 변경해서 차액 환불받고 싶어요",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [{"round": 1, "amount": 500000}],
                     "refund_txs": [], "refunds": [], "all_refunded": False},
            ),
            "T10_상품변경_차액환불",
        ),

        # Branch B: 중복 결제
        (
            "T11 중복 결제",
            build_context(
                user_text="두 번 결제됐어요 중복 환불 부탁",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [
                         {"round": 1, "amount": 50000},
                         {"round": 2, "amount": 50000},
                     ],
                     "refund_txs": [], "refunds": [], "all_refunded": False},
            ),
            "T11_중복결제_환불선택",
        ),

        # Node 6: 정상 환불 → T2
        (
            "T2 정상 환불",
            build_context(
                user_text="환불 가능한가요",
                ctx={"us_user_id": "u1", "prev_had_t2": False, "prev_had_t6": False,
                     "prev_manager_count": 0,
                     "success_txs": [{"round": 1, "amount": 50000}],
                     "refund_txs": [], "refunds": [], "all_refunded": False},
            ),
            "T2_환불_규정_금액",
        ),
    ]

    for name, ctx, expected in cases:
        try:
            result = route(engine, ctx)
            if result == expected:
                print(f"  ✅ {name} → {result}")
                passed += 1
            else:
                print(f"  ❌ {name} → got {result}, expected {expected}")
                failed += 1
        except Exception as e:
            print(f"  ❌ {name} — {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"  {passed}/{passed + failed} 통과")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = test_all()
    sys.exit(0 if ok else 1)
