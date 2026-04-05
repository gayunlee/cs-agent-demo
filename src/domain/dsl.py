"""Turing-incomplete 표현식 평가기.

안전성 원칙 (Gayoon 이전 프로젝트 visibility_chain.yaml 패턴):
- Python ast 모듈로 파싱, whitelist 노드만 허용
- 무한 루프/사이드이펙트 불가 (loop/lambda/comprehension 금지)
- 함수 호출은 외부 주입 registry에 등록된 것만 (whitelist)
- 대입/import/exec/eval 전부 차단

지원 문법:
- 필드 비교: `ctx.field == 'VALUE'`, `!=`, `<`, `<=`, `>`, `>=`
- Boolean: `not X`, `A and B`, `A or B`
- 상수: string / number / bool / None
- 함수 호출: whitelist된 함수만 (예: `has_keyword(user_text, 'card_kws')`)
- 속성 접근: `ctx.user.id` (dict OR attribute)
- 리스트 길이: `len_of(list_field)` (함수 등록 필요)

사용 예:
    >>> evaluator = DSLEvaluator(functions={"has_keyword": has_keyword_fn})
    >>> evaluator.eval("ctx.us_user_id != ''", {"ctx": wf_ctx})
    True
    >>> evaluator.eval("has_keyword(user_text, 'card_change')", {"user_text": "카드 변경"})
    True
"""
from __future__ import annotations
import ast
import operator
from typing import Any, Callable


class DSLError(Exception):
    """DSL 파싱/평가 에러"""
    pass


ALLOWED_CMPOP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

ALLOWED_BOOLOP = {
    ast.And: all,
    ast.Or: any,
}


class DSLEvaluator:
    """표현식 평가기. functions에 외부 헬퍼 등록 가능.

    Attributes:
        functions: name → callable 딕셔너리. 표현식 내 `fn_name(args)` 호출 허용됨.
    """

    def __init__(self, functions: dict[str, Callable] | None = None):
        self.functions = dict(functions or {})
        # 내장 헬퍼
        self.functions.setdefault("len_of", lambda x: len(x) if x is not None else 0)
        self.functions.setdefault("is_empty", lambda x: not x)
        self.functions.setdefault("is_not_empty", lambda x: bool(x))

    def eval(self, expr: str, context: dict) -> Any:
        """표현식을 평가해 결과 반환.

        Args:
            expr: DSL 표현식 문자열
            context: 이름 → 값 딕셔너리 (표현식에서 참조 가능한 변수들)

        Raises:
            DSLError: 파싱/평가 실패 시
        """
        if not isinstance(expr, str):
            raise DSLError(f"Expression must be str, got {type(expr).__name__}")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise DSLError(f"Syntax error in '{expr}': {e}")
        return self._eval_node(tree.body, context)

    def _eval_node(self, node: ast.AST, context: dict) -> Any:
        # 상수 (string/number/bool/None)
        if isinstance(node, ast.Constant):
            return node.value

        # 이름 참조 (context 또는 키워드)
        if isinstance(node, ast.Name):
            if node.id in context:
                return context[node.id]
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            if node.id == "None":
                return None
            raise DSLError(f"Unknown name: {node.id}")

        # 속성 접근 (obj.attr 또는 dict['attr'])
        if isinstance(node, ast.Attribute):
            obj = self._eval_node(node.value, context)
            if obj is None:
                return None
            if isinstance(obj, dict):
                return obj.get(node.attr)
            return getattr(obj, node.attr, None)

        # 첨자 접근 (obj[key])
        if isinstance(node, ast.Subscript):
            obj = self._eval_node(node.value, context)
            if obj is None:
                return None
            key = self._eval_node(node.slice, context)
            try:
                return obj[key]
            except (KeyError, IndexError, TypeError):
                return None

        # 비교 (==, !=, <, <=, >, >=, in, not in)
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_node(comparator, context)
                op_type = type(op)
                if op_type not in ALLOWED_CMPOP:
                    raise DSLError(f"Comparator not allowed: {op_type.__name__}")
                if not ALLOWED_CMPOP[op_type](left, right):
                    return False
                left = right
            return True

        # 논리 연산 (and, or)
        if isinstance(node, ast.BoolOp):
            op_type = type(node.op)
            if op_type not in ALLOWED_BOOLOP:
                raise DSLError(f"BoolOp not allowed: {op_type.__name__}")
            values = [self._eval_node(v, context) for v in node.values]
            return ALLOWED_BOOLOP[op_type](values)

        # 단항 연산 (not)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not self._eval_node(node.operand, context)
            if isinstance(node.op, ast.USub):
                return -self._eval_node(node.operand, context)
            raise DSLError(f"UnaryOp not allowed: {type(node.op).__name__}")

        # 함수 호출 (whitelist만)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise DSLError("Only simple function calls allowed (no method calls)")
            fn_name = node.func.id
            if fn_name not in self.functions:
                raise DSLError(f"Unknown function: {fn_name}")
            if node.keywords:
                raise DSLError("Keyword arguments not allowed")
            args = [self._eval_node(a, context) for a in node.args]
            return self.functions[fn_name](*args)

        raise DSLError(f"Node type not allowed: {type(node).__name__}")

    def format_message(self, template: str, context: dict) -> str:
        """fail_message 템플릿에 context 값 치환.

        `{field.path}` 형태를 찾아 context에서 해당 값을 넣음.

        예: "오피셜클럽이 {master.status} 상태입니다"
            context = {"master": {"status": "PRIVATE"}}
            → "오피셜클럽이 PRIVATE 상태입니다"
        """
        import re

        def replace(match: re.Match) -> str:
            path = match.group(1)
            try:
                value = self.eval(path, context)
                return str(value) if value is not None else ""
            except DSLError:
                return match.group(0)  # 실패 시 원본 유지

        return re.sub(r"\{([^{}]+)\}", replace, template)
