"""모호한 문의 워크플로우 — 규칙 기반 분기

데이터 조건만으로 follow-up 질문 유형 결정.
config/ambiguous_rules.json의 규칙을 순서대로 매칭 (first-match).

응대 전략 4종:
  A. 본인 확인 요청
  B. 상품 특정 / 맥락 확인
  C. 증상 구체화
  D. 오픈 질문 / 범위 밖 안내
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

RULES_PATH = Path(__file__).parent.parent / "config" / "ambiguous_rules.json"

# 의도 키워드 그룹 — 어떤 그룹에도 안 걸리면 no_clear_intent
INTENT_KEYWORD_GROUPS = [
    ["환불", "해지", "취소", "탈퇴", "자동결제", "자동결재", "구독취소", "구독해지"],
    ["결제", "결재", "금액", "할부", "결제일"],
    ["카드 변경", "카드변경", "카드 분실", "카드 만료"],
    ["로그인", "비밀번호", "비번"],
    ["강의", "수업", "녹화", "pdf", "줌", "zoom"],
    ["안돼", "안됩니다", "안 돼", "안되", "오류", "에러", "끊겨", "열리지", "멈춤"],
]

# 증상 상세로 간주되는 단어
SYMPTOM_DETAIL_WORDS = [
    "화면", "로딩", "에러 메시지", "버전", "업데이트", "재설치",
    "아이폰", "안드로이드", "갤럭시", "크롬", "사파리",
    "wifi", "와이파이", "데이터",
]

# 전화번호 패턴
PHONE_PATTERN = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")


@dataclass
class AmbiguousContext:
    """모호 문의 워크플로우 컨텍스트"""
    # 입력
    user_messages: list[str] = field(default_factory=list)
    conversation_turns: list[dict] = field(default_factory=list)
    us_user_id: str = ""
    active_products: list[dict] = field(default_factory=list)

    # 파생 (시그널)
    has_identity: bool = False
    has_user_id: bool = False
    has_product_specified: bool = False
    has_symptom_detail: bool = False
    has_prev_context: bool = False
    message_length: int = 0
    no_clear_intent: bool = False
    detected_intent_keywords: list[str] = field(default_factory=list)

    # 출력
    is_ambiguous: bool = False
    matched_rule_id: str = ""
    template_id: str = ""
    response_strategy: str = ""
    follow_up_response: str = ""
    missing_info: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)


def _load_rules() -> dict:
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_ambiguous_workflow(ctx: AmbiguousContext) -> str:
    """모호 문의 워크플로우 실행

    Returns:
        template_id (e.g. "AMB_T1_본인확인_환불") 또는 "NOT_AMBIGUOUS"
    """
    rules_data = _load_rules()

    # Node 0: 시그널 추출
    _extract_signals(ctx)

    # Node 1: 이미 충분한지 확인
    if _check_sufficient(ctx):
        ctx.path.append("정보_충분 → NOT_AMBIGUOUS")
        return "NOT_AMBIGUOUS"

    # Node 2: 규칙 매칭
    matched = _match_rule(ctx, rules_data["rules"])
    if not matched:
        ctx.path.append("규칙_미매칭 → NOT_AMBIGUOUS")
        return "NOT_AMBIGUOUS"

    # Node 3: 템플릿 렌더링
    ctx.is_ambiguous = True
    ctx.matched_rule_id = matched["id"]
    ctx.template_id = matched["template_id"]
    ctx.response_strategy = matched["response_strategy"]
    ctx.missing_info = matched.get("missing_info", [])

    _render_template(ctx, matched, rules_data.get("templates", {}))

    ctx.path.append(f"{matched['id']}_{matched['label']} → {matched['template_id']}")
    return matched["template_id"]


def _extract_signals(ctx: AmbiguousContext):
    """유저 메시지에서 boolean 시그널 추출"""
    text = " ".join(ctx.user_messages)
    text_lower = text.lower()

    ctx.message_length = len(text.strip())

    # has_user_id
    ctx.has_user_id = bool(ctx.us_user_id)

    # has_identity: 전화번호 패턴 또는 userId가 있으면
    ctx.has_identity = ctx.has_user_id or bool(PHONE_PATTERN.search(text))

    # has_product_specified: 구체적 상품명 언급 여부
    product_indicators = ["과정", "마스터", "클래스", "스쿨", "학교", "스터디"]
    ctx.has_product_specified = any(kw in text_lower for kw in product_indicators)

    # has_symptom_detail: 메시지 30자 초과 + 구체적 증상 단어
    if ctx.message_length > 30:
        ctx.has_symptom_detail = any(w in text_lower for w in SYMPTOM_DETAIL_WORDS)

    # has_prev_context: 이전 대화에서 매니저 응답이 있었는지
    ctx.has_prev_context = any(
        t.get("role") == "manager" for t in ctx.conversation_turns
    )

    # no_clear_intent: 어떤 의도 키워드 그룹에도 안 걸림
    matched_any = False
    for group in INTENT_KEYWORD_GROUPS:
        for kw in group:
            if kw in text_lower:
                matched_any = True
                ctx.detected_intent_keywords.append(kw)
    ctx.no_clear_intent = not matched_any


def _check_sufficient(ctx: AmbiguousContext) -> bool:
    """정보가 이미 충분하면 True (= 모호하지 않음)"""
    # 카드 문의는 별도 T8 처리이므로 모호 워크플로우에서 스킵
    text_lower = " ".join(ctx.user_messages).lower()
    if any(kw in text_lower for kw in ["카드 변경", "카드변경", "카드 분실", "카드 만료", "카드 재발급"]):
        ctx.path.append("카드_문의 → 스킵")
        return True

    # 환불/해지 의도 + userId 있음 + 단일 상품 → 충분
    refund_kws = ["환불", "해지", "취소", "탈퇴", "구독취소", "구독해지", "자동결제", "자동결재"]
    has_refund_intent = any(kw in text_lower for kw in refund_kws)
    if has_refund_intent and ctx.has_user_id:
        if len(ctx.active_products) <= 1:
            ctx.path.append("환불의도+userId+단일상품 → 충분")
            return True
        if ctx.has_product_specified:
            ctx.path.append("환불의도+userId+상품특정 → 충분")
            return True

    # 결제 의도 + userId 있음 + 상품 특정 → 충분
    payment_kws = ["결제", "결재", "금액", "할부", "결제일"]
    has_payment_intent = any(kw in text_lower for kw in payment_kws)
    if has_payment_intent and ctx.has_user_id and ctx.has_product_specified:
        ctx.path.append("결제의도+userId+상품특정 → 충분")
        return True

    # 기술 문의 + 증상 상세 → 충분
    tech_kws = ["안돼", "안됩니다", "안 돼", "안되", "오류", "에러", "끊겨", "열리지", "멈춤"]
    has_tech_intent = any(kw in text_lower for kw in tech_kws)
    if has_tech_intent and ctx.has_symptom_detail:
        ctx.path.append("기술의도+증상상세 → 충분")
        return True

    return False


def _match_rule(ctx: AmbiguousContext, rules: list[dict]) -> dict | None:
    """규칙 목록을 순서대로 순회, 모든 조건 만족하는 첫 규칙 반환"""
    text_lower = " ".join(ctx.user_messages).lower()

    for rule in rules:
        conditions = rule.get("conditions", {})
        if _check_conditions(ctx, conditions, text_lower):
            return rule
    return None


def _check_conditions(ctx: AmbiguousContext, conditions: dict, text_lower: str) -> bool:
    """조건 딕셔너리의 모든 키를 AND 로직으로 평가"""
    for key, value in conditions.items():
        if key == "intent_keywords_any":
            if not any(kw in text_lower for kw in value):
                return False

        elif key == "continuation_keywords_any":
            first_msg = ctx.user_messages[0].strip() if ctx.user_messages else ""
            if not any(first_msg.startswith(kw) or first_msg == kw for kw in value):
                return False

        elif key == "has_user_id":
            if ctx.has_user_id != value:
                return False

        elif key == "has_identity":
            if ctx.has_identity != value:
                return False

        elif key == "has_product_specified":
            if ctx.has_product_specified != value:
                return False

        elif key == "active_products_count_min":
            if len(ctx.active_products) < value:
                return False

        elif key == "has_symptom_detail":
            if ctx.has_symptom_detail != value:
                return False

        elif key == "message_length_max":
            if ctx.message_length > value:
                return False

        elif key == "no_clear_intent":
            if ctx.no_clear_intent != value:
                return False

        elif key == "has_prev_context":
            if ctx.has_prev_context != value:
                return False

    return True


def _render_template(ctx: AmbiguousContext, rule: dict, templates: dict):
    """매칭된 규칙의 템플릿을 변수로 채워서 follow_up_response 생성"""
    template_id = rule["template_id"]
    template = templates.get(template_id, "")
    if not template:
        ctx.follow_up_response = ""
        return

    # 템플릿 변수 준비
    variables = {}

    # intent_label: 의도 키워드에서 추출
    intent_map = {
        "환불": "환불", "해지": "해지", "취소": "취소", "탈퇴": "탈퇴",
        "자동결제": "자동결제", "자동결재": "자동결제",
        "구독취소": "구독 취소", "구독해지": "구독 해지",
    }
    intent_label = "문의"
    for kw in ctx.detected_intent_keywords:
        if kw in intent_map:
            intent_label = intent_map[kw]
            break
    variables["intent_label"] = intent_label

    # product_list: 활성 상품 이름 나열
    if ctx.active_products:
        names = [p.get("name", "알 수 없는 과정") for p in ctx.active_products]
        variables["product_list"] = ", ".join(names)
    else:
        variables["product_list"] = "여러"

    try:
        ctx.follow_up_response = template.format(**variables)
    except KeyError:
        ctx.follow_up_response = template
