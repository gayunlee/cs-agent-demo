"""DSL parser 단위 테스트.

Turing-incomplete 보장 + 기본 평가 동작 검증.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.dsl import DSLEvaluator, DSLError


def test_cases():
    e = DSLEvaluator()
    passed = 0
    failed = 0

    cases = [
        # 기본 비교
        ("상수 True", lambda: e.eval("True", {}) is True),
        ("상수 False", lambda: e.eval("False", {}) is False),
        ("숫자 비교", lambda: e.eval("3 > 2", {}) is True),
        ("문자열 비교", lambda: e.eval("'abc' == 'abc'", {}) is True),
        ("문자열 불일치", lambda: e.eval("'abc' != 'xyz'", {}) is True),

        # Context 참조
        ("context 이름", lambda: e.eval("x", {"x": 42}) == 42),
        ("속성 접근 dict", lambda: e.eval("user.name", {"user": {"name": "홍길동"}}) == "홍길동"),
        ("중첩 속성", lambda: e.eval(
            "ctx.user.id", {"ctx": {"user": {"id": "u123"}}}) == "u123"),

        # 논리 연산
        ("and 둘 다 True", lambda: e.eval("True and True", {}) is True),
        ("and 하나 False", lambda: e.eval("True and False", {}) is False),
        ("or 하나 True", lambda: e.eval("False or True", {}) is True),
        ("not True", lambda: e.eval("not True", {}) is False),
        ("복합 논리", lambda: e.eval("x > 0 and y == 'ok'", {"x": 5, "y": "ok"}) is True),

        # 비어있음 체크
        ("빈 문자열 체크", lambda: e.eval("uid != ''", {"uid": ""}) is False),
        ("빈 문자열 != 값", lambda: e.eval("uid != ''", {"uid": "u123"}) is True),

        # None 안전
        ("None 속성", lambda: e.eval("x.y", {"x": None}) is None),
        ("None 비교", lambda: e.eval("x == None", {"x": None}) is True),

        # in 연산자
        ("in list", lambda: e.eval("x in items", {"x": 1, "items": [1, 2, 3]}) is True),
        ("not in list", lambda: e.eval("x not in items", {"x": 5, "items": [1, 2, 3]}) is True),

        # 함수 호출 (whitelist)
        ("len_of 함수", lambda: e.eval("len_of(items) > 0", {"items": [1, 2]}) is True),
        ("is_empty 함수", lambda: e.eval("is_empty(items)", {"items": []}) is True),
        ("is_not_empty 함수", lambda: e.eval("is_not_empty(items)", {"items": [1]}) is True),

        # Turing-incomplete 보장: 금지된 구조 → DSLError
        ("lambda 금지", lambda: _expect_error(e, "lambda: 1", {})),
        ("대입 금지", lambda: _expect_error(e, "x = 1", {})),
        ("for 금지", lambda: _expect_error(e, "[i for i in range(10)]", {})),
        ("알 수 없는 함수", lambda: _expect_error(e, "unknown_fn()", {})),
        ("메서드 호출 금지", lambda: _expect_error(e, "'abc'.upper()", {})),
    ]

    # 커스텀 함수 등록 테스트
    e_custom = DSLEvaluator(functions={
        "has_keyword": lambda text, kw: kw in (text or "").lower(),
    })
    cases.extend([
        ("커스텀 함수: keyword 있음", lambda: e_custom.eval(
            "has_keyword(msg, '카드')", {"msg": "카드 변경하고 싶어요"}) is True),
        ("커스텀 함수: keyword 없음", lambda: e_custom.eval(
            "has_keyword(msg, '환불')", {"msg": "결제 방법"}) is False),
    ])

    # format_message 테스트
    cases.append((
        "format_message 기본",
        lambda: e.format_message(
            "상태: {user.status}",
            {"user": {"status": "ACTIVE"}}
        ) == "상태: ACTIVE"
    ))
    cases.append((
        "format_message 다중",
        lambda: e.format_message(
            "{name}님의 잔액은 {amount}원",
            {"name": "홍길동", "amount": 10000}
        ) == "홍길동님의 잔액은 10000원"
    ))

    for name, case_fn in cases:
        try:
            result = case_fn()
            if result is True:
                print(f"  ✅ {name}")
                passed += 1
            else:
                print(f"  ❌ {name} (result={result})")
                failed += 1
        except Exception as e_exc:
            print(f"  ❌ {name} — {type(e_exc).__name__}: {e_exc}")
            failed += 1

    print("\n" + "=" * 50)
    print(f"  {passed}/{passed + failed} 통과")
    print("=" * 50)
    return failed == 0


def _expect_error(evaluator: DSLEvaluator, expr: str, ctx: dict) -> bool:
    """DSLError 발생을 기대. 발생하면 True, 아니면 False."""
    try:
        evaluator.eval(expr, ctx)
        return False
    except DSLError:
        return True


if __name__ == "__main__":
    ok = test_cases()
    sys.exit(0 if ok else 1)
