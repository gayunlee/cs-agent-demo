"""정책 기반 워크플로우 테스트

확정 정책:
  유저 식별 불가 → T6
  결제 없음 → T1
  미환불 결제 있음 → T2
  전부 환불됨 → T3
  카드 문의 → T8
  이전턴 T2 → T3
"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.workflow import WorkflowContext, run_workflow

TODAY = date.today().isoformat()

def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()

def ctx(
    messages=None, us_user_id="test_user", transactions=None,
    products=None, prev_turns=None, has_accessed=False,
):
    return WorkflowContext(
        user_messages=messages or ["환불해주세요"],
        us_user_id=us_user_id,
        products=products or [],
        transactions=transactions or [],
        has_accessed=has_accessed,
        conversation_turns=prev_turns or [],
    )


TESTS = [
    # ── 싱글턴 ──

    # T6: 유저 식별 불가
    ("T6 — 유저 식별 불가",
     ctx(us_user_id=""),
     "T6_본인확인_요청"),

    # T1: 결제 없음
    ("T1 — 결제 이력 없음",
     ctx(transactions=[]),
     "T1_구독해지_방법_앱"),

    # T2 전액: 미환불 + 3일 전 결제 + 미열람
    ("T2 전액 — 7일 이내 + 미열람",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 500000, "date": days_ago(3), "round": 1},
     ], has_accessed=False),
     "T2_환불_규정_금액"),

    # T2 부분: 미환불 + 5일 전 결제 + 열람 있음
    ("T2 부분 — 7일 이내 + 열람",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 500000, "date": days_ago(5), "round": 1},
     ], has_accessed=True),
     "T2_환불_규정_금액"),

    # T2 부분: 미환불 + 14일 전 결제 (7일 경과, 1/2 이내)
    ("T2 부분 — 7일 경과 (1/2 이내)",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 100000, "date": days_ago(14), "round": 1},
     ], has_accessed=True),
     "T2_환불_규정_금액"),

    # LLM fallback: 1/2 기간 경과 → 환불 불가 → LLM이 설명
    ("LLM fallback — 1/2 기간 경과 환불 불가",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 100000, "date": days_ago(20), "round": 1},
     ], has_accessed=True),
     "T_LLM_FALLBACK"),

    # T2: 미환불 결제 2건 (정기결제)
    ("T2 — 미환불 결제 2건 (정기결제도 T2)",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 100000, "date": days_ago(35), "round": 1},
         {"state": "purchased_success", "amount": 100000, "date": days_ago(5), "round": 2},
     ], has_accessed=True),
     "T2_환불_규정_금액"),

    # T3: 전부 환불됨
    ("T3 — 전부 환불됨 (1건 결제 + 1건 환불)",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 500000, "date": "2025-07-01", "round": 1},
         {"state": "purchased_refund", "amount": 360000, "date": "2025-07-15", "round": 1},
     ]),
     "T3_환불_접수_완료"),

    # T3: 전부 환불됨 (2건 결제 + 2건 환불)
    ("T3 — 전부 환불됨 (2건 결제 + 2건 환불)",
     ctx(transactions=[
         {"state": "purchased_success", "amount": 100000, "date": "2025-07-01", "round": 1},
         {"state": "purchased_refund", "amount": 100000, "date": "2025-07-10", "round": 1},
         {"state": "purchased_success", "amount": 100000, "date": "2025-08-01", "round": 2},
         {"state": "purchased_refund", "amount": 100000, "date": "2025-08-10", "round": 2},
     ]),
     "T3_환불_접수_완료"),

    # T8: 카드 문의
    ("T8 — 카드 변경 문의",
     ctx(messages=["카드 변경하고 싶습니다"]),
     "T8_카드변경_안내"),

    ("T8 — 카드 분실",
     ctx(messages=["카드 분실해서 재발급 받았는데 결제 카드변경 어떻게 하나요"]),
     "T8_카드변경_안내"),

    # ── 유저 메시지와 무관하게 데이터로 결정 ──

    # "해지해주세요" 지만 미환불 있으면 → T2
    ("T2 — 해지 메시지지만 미환불 있음",
     ctx(
         messages=["해지해주세요"],
         transactions=[{"state": "purchased_success", "amount": 500000, "date": days_ago(10), "round": 1}],
         has_accessed=True,
     ),
     "T2_환불_규정_금액"),

    # "환불해주세요" 지만 결제 없으면 → T1
    ("T1 — 환불 메시지지만 결제 없음",
     ctx(messages=["환불해주세요"], transactions=[]),
     "T1_구독해지_방법_앱"),

    # "환불 처리됐나요?" 지만 전부 환불됨 → T3
    ("T3 — 환불 확인 + 전부 환불됨",
     ctx(
         messages=["환불 처리됐나요?"],
         transactions=[
             {"state": "purchased_success", "amount": 500000, "date": days_ago(30), "round": 1},
             {"state": "purchased_refund", "amount": 360000, "date": days_ago(20), "round": 1},
         ],
     ),
     "T3_환불_접수_완료"),

    # ── 멀티턴 ──

    # 이전턴 T2 → T3
    ("T3 — 이전턴 T2 후 유저 동의",
     ctx(
         messages=["네 환불해주세요"],
         transactions=[{"state": "purchased_success", "amount": 500000, "date": days_ago(10), "round": 1}],
         prev_turns=[
             {"role": "manager", "text": "7일 이내 구독권 미개시 시 전액 환불... 환불 금액: 360,000원", "ts": 100},
             {"role": "user", "text": "네 환불해주세요", "ts": 200},
         ],
     ),
     "T3_환불_접수_완료"),

    # 이전턴에 매니저 있지만 T2 아님 → 정상 분기
    ("T2 — 이전턴 매니저 있지만 T2 아님",
     ctx(
         messages=["환불해주세요"],
         transactions=[{"state": "purchased_success", "amount": 500000, "date": days_ago(10), "round": 1}],
         has_accessed=True,
         prev_turns=[
             {"role": "manager", "text": "안녕하세요 회원님, 무엇을 도와드릴까요?", "ts": 100},
             {"role": "user", "text": "환불해주세요", "ts": 200},
         ],
     ),
     "T2_환불_규정_금액"),

    # ── Branch D: 본인확인 재질문 ──
    ("T6b — 이전턴 T6 후에도 식별 실패",
     ctx(
         messages=["확인해주세요"],
         us_user_id="",
         prev_turns=[
             {"role": "manager", "text": "성함/휴대전화 번호 말씀 주시면 확인 도와드리도록 하겠습니다.", "ts": 100},
             {"role": "user", "text": "김철수 010-1234-5678", "ts": 200},
         ],
     ),
     "T6b_본인확인_재질문"),

    ("T6b — 가족 번호 시그널",
     ctx(
         messages=["이거 제 가족 번호인데요 환불 되나요"],
         transactions=[{"state": "purchased_success", "amount": 100000, "date": days_ago(3), "round": 1}],
     ),
     "T6b_본인확인_재질문"),

    # ── Branch C: 환불 지연/미처리 ──
    ("T12 — 진행 중 환불 + 재촉",
     WorkflowContext(
         user_messages=["환불 왜 아직도 처리 안되나요"],
         us_user_id="u1",
         transactions=[{"state": "purchased_success", "amount": 500000, "date": days_ago(30), "round": 1}],
         refunds=[{
             "productName": "어스플러스 1개월",
             "createdAt": "2026-03-20T10:00:00Z",
             "refundHistory": {"refundAmount": 360000, "refundAt": ""},
         }],
     ),
     "T12_환불진행_상태안내"),

    # ── Branch A: 상품변경 차액환불 ──
    ("T10 — 상품 변경 + 차액 언급",
     ctx(
         messages=["50만원 결제한 거 취소하고 10만원짜리로 변경 부탁드려요"],
         transactions=[{"state": "purchased_success", "amount": 500000, "date": days_ago(5), "round": 1}],
         has_accessed=True,
     ),
     "T10_상품변경_차액환불"),

    # ── Branch B: 중복결제 ──
    ("T11 — 미환불 2건 + 중복 언급",
     ctx(
         messages=["이중으로 결제됐어요 하나 환불 부탁드립니다"],
         transactions=[
             {"state": "purchased_success", "amount": 100000, "date": days_ago(3), "round": 1},
             {"state": "purchased_success", "amount": 100000, "date": days_ago(3), "round": 2},
         ],
     ),
     "T11_중복결제_환불선택"),

    # ── LLM fallback: 환불 철회 ──
    ("LLM fallback — 환불 취소 의사",
     ctx(
         messages=["어제 환불 신청했는데 취소하고 계속 이용할게요"],
         transactions=[{"state": "purchased_success", "amount": 100000, "date": days_ago(5), "round": 1}],
     ),
     "T_LLM_FALLBACK"),

    # ── LLM fallback: 전부 환불 후 새 질문 ──
    ("LLM fallback — 전부 환불 후 재가입 문의",
     ctx(
         messages=["환불은 받았는데 다시 가입하려면 어떻게 해야하나요"],
         transactions=[
             {"state": "purchased_success", "amount": 500000, "date": days_ago(30), "round": 1},
             {"state": "purchased_refund", "amount": 360000, "date": days_ago(25), "round": 1},
         ],
     ),
     "T_LLM_FALLBACK"),
]


def run_tests():
    passed = 0
    failed = 0

    for name, context, expected in TESTS:
        result = run_workflow(context)
        ok = result == expected

        if ok:
            passed += 1
            print(f"  ✅ {name}")
        else:
            failed += 1
            path = " → ".join(context.path)
            print(f"  ❌ {name}")
            print(f"     예상: {expected}")
            print(f"     실제: {result}")
            print(f"     경로: {path}")

    print(f"\n{'='*50}")
    print(f"  {passed}/{passed+failed} 통과 ({passed/(passed+failed)*100:.0f}%)")
    print(f"{'='*50}")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
