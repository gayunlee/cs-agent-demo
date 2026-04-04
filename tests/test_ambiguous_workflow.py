"""모호한 문의 워크플로우 테스트

규칙 기반 분기:
  환불/해지 + userId 없음 → AMB_T1 (본인확인)
  환불/해지 + 복수 상품 → AMB_T2 (상품특정)
  결제 + userId 없음 → AMB_T3 (본인확인)
  결제 + 맥락 부족 → AMB_T4 (맥락확인)
  기능 추상적 → AMB_T5 (증상구체화)
  짧은 메시지 → AMB_T6 (오픈질문)
  이전 대화 연속 → AMB_T7 (맥락확인)
  CS 범위 밖 → AMB_T8 (범위밖)
  정보 충분 → NOT_AMBIGUOUS
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ambiguous_workflow import AmbiguousContext, run_ambiguous_workflow


def amb_ctx(
    messages=None,
    us_user_id="",
    active_products=None,
    prev_turns=None,
):
    return AmbiguousContext(
        user_messages=messages or ["환불해주세요"],
        us_user_id=us_user_id,
        active_products=active_products or [],
        conversation_turns=prev_turns or [],
    )


TESTS = [
    # ── 전략 A: 본인확인 요청 ──

    ("AMB-001 환불 + userId 없음",
     amb_ctx(messages=["환불요망"], us_user_id=""),
     "AMB_T1_본인확인_환불"),

    ("AMB-001 구독취소 + userId 없음",
     amb_ctx(messages=["구독취소 원합니다"], us_user_id=""),
     "AMB_T1_본인확인_환불"),

    ("AMB-001 자동결제 + userId 없음",
     amb_ctx(messages=["자동결제 해지하고 싶습니다"], us_user_id=""),
     "AMB_T1_본인확인_환불"),

    ("AMB-003 결제 문의 + userId 없음",
     amb_ctx(messages=["오늘 50만원이 결제되었는데요"], us_user_id=""),
     "AMB_T3_본인확인_결제"),

    ("AMB-003 할부 문의 + userId 없음",
     amb_ctx(messages=["할부로 변경하고 싶어요"], us_user_id=""),
     "AMB_T3_본인확인_결제"),

    # ── 전략 B: 상품 특정 / 맥락 확인 ──

    ("AMB-002 환불 + 복수 상품",
     amb_ctx(
         messages=["환불해주세요"],
         us_user_id="user123",
         active_products=[{"name": "투자동행학교"}, {"name": "경제스쿨"}],
     ),
     "AMB_T2_상품특정_환불"),

    ("AMB-002 해지 + 복수 상품",
     amb_ctx(
         messages=["해지하고 싶습니다"],
         us_user_id="user123",
         active_products=[{"name": "자산배분 필수반"}, {"name": "실전투자교실"}],
     ),
     "AMB_T2_상품특정_환불"),

    ("AMB-004 결제 맥락 부족 + userId 있음",
     amb_ctx(messages=["결제가 왜 됐죠?"], us_user_id="user123"),
     "AMB_T4_맥락확인_결제"),

    # ── 전략 C: 증상 구체화 ──

    ("AMB-005 앱 안 열림",
     amb_ctx(messages=["어플이 열리지 않네요"]),
     "AMB_T5_증상구체화"),

    ("AMB-005 전송 안됨",
     amb_ctx(messages=["전송이 안돼요"]),
     "AMB_T5_증상구체화"),

    ("AMB-005 영상 끊김",
     amb_ctx(messages=["영상이 계속 끊겨요"]),
     "AMB_T5_증상구체화"),

    # ── 전략 D: 오픈질문 / 맥락확인 / 범위밖 ──

    ("AMB-007 짧은 메시지 — '네' (이전 대화 연속 추정)",
     amb_ctx(messages=["네"]),
     "AMB_T7_맥락확인"),

    ("AMB-006 짧은 메시지 — 단답",
     amb_ctx(messages=["안녕하세요"]),
     "AMB_T6_오픈질문"),

    ("AMB-007 이전 대화 연속 추정",
     amb_ctx(
         messages=["네 진행해주세요"],
         us_user_id="",
         prev_turns=[],  # 매니저 응답 없음 = 맥락 없음
     ),
     "AMB_T7_맥락확인"),

    ("AMB-007 이전 대화 참조",
     amb_ctx(
         messages=["저번 말씀드렸던것 취소 부탁드려요"],
         us_user_id="",
         prev_turns=[],
     ),
     "AMB_T1_본인확인_환불"),  # "취소" 키워드 → AMB-001이 먼저 매칭

    ("AMB-008 투자 조언 문의",
     amb_ctx(messages=["테스 얼마일때 팔아야하나요? 오르고 있는데 목표가를 모르겠어요"]),
     "AMB_T8_범위밖"),

    ("AMB-008 종목 추천 문의",
     amb_ctx(messages=["어떤 종목 사면 좋을까요?"]),
     "AMB_T8_범위밖"),

    # ── NOT_AMBIGUOUS: 정보 충분 ──

    ("명확한 환불 + userId + 단일상품",
     amb_ctx(
         messages=["환불해주세요"],
         us_user_id="user123",
         active_products=[{"name": "투자동행학교"}],
     ),
     "NOT_AMBIGUOUS"),

    ("명확한 해지 + userId + 상품 없음 (0건)",
     amb_ctx(
         messages=["해지하고 싶습니다"],
         us_user_id="user123",
         active_products=[],
     ),
     "NOT_AMBIGUOUS"),

    ("카드 변경 → 모호 워크플로우 스킵",
     amb_ctx(messages=["카드 변경하고 싶습니다"]),
     "NOT_AMBIGUOUS"),

    ("카드 분실 → 모호 워크플로우 스킵",
     amb_ctx(messages=["카드 분실해서 변경하고 싶어요"]),
     "NOT_AMBIGUOUS"),

    ("기술 문의 + 증상 상세 충분",
     amb_ctx(messages=["아이폰에서 앱 화면이 로딩 중에 멈추고 에러 메시지가 나옵니다"]),
     "NOT_AMBIGUOUS"),

    # ── 멀티턴: 2턴째 정보 제공 후 ──

    ("2턴 — 본인확인 후 환불 요청",
     amb_ctx(
         messages=["홍길동입니다 환불해주세요"],
         us_user_id="user123",  # 이제 식별됨
         active_products=[{"name": "투자동행학교"}],
         prev_turns=[
             {"role": "manager", "text": "성함과 휴대전화 번호를 말씀해 주시겠어요?", "ts": 100},
             {"role": "user", "text": "홍길동입니다 환불해주세요", "ts": 200},
         ],
     ),
     "NOT_AMBIGUOUS"),
]


def run_tests():
    passed = 0
    failed = 0

    for name, context, expected in TESTS:
        result = run_ambiguous_workflow(context)
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
