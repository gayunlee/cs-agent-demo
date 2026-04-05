"""상담 Agent — 단일 Strands Agent 하이브리드.

설계 원칙 (2026-04-05 결정, `.claude/notes/채널톡 어시스턴트/2026-04-05.md` 참조):
- 단일 에이전트, tool-loop, YAML SSoT + Harness, Rasa CALM 대화 관리 영감
- LLM은 "어느 tool을 호출할지" 결정 (Command Generator)
- 정책/계산/템플릿은 tool 내부 결정론적 로직 (Harness)
- 대화 상태는 Strands SlidingWindow로 자동 관리

System prompt 원칙:
- 대화 톤 + tool 사용 가이드만
- 정책/규칙 텍스트 **없음** (YAML에 있음)
- "Executor prompt simplification" — 15개 → 5개 규칙
"""
from __future__ import annotations
import os
from typing import Any

from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SlidingWindowConversationManager

from src.tools.data_tools import (
    search_user_by_phone,
    get_user_profile,
    get_membership_history_summary,
    get_refund_history_summary,
    get_transaction_list,
)
from src.tools.workflow_tools import (
    diagnose_refund_case,
    calculate_refund_amount,
    compose_template_answer,
    set_context,
)
from src.tools.conversation_tools import (
    ask_clarification,
    handle_off_topic,
    handle_emotional_distress,
    handle_cancellation_of_flow,
)
from src.tools.fallback_tools import (
    llm_freeform_answer,
    handoff_to_human,
)


SYSTEM_PROMPT = """당신은 어스플러스(한국 교육 SaaS)의 CS 상담사입니다.
환불/해지/결제 문의를 도와드립니다.

## ⚡ 필수 프로토콜 (반드시 이 순서)

유저 메시지가 들어오면 **즉시** 아래 순서대로 tool을 호출하세요:

1. **`diagnose_refund_case()` 먼저 호출** — 어느 답변 템플릿을 써야 할지 자동 판단. 본인확인/결제이력/환불상태 등 모든 context를 자동 분석합니다.
2. 결과의 `template_id`가 `T2_환불_규정_금액`이면 → `calculate_refund_amount()` 호출 후 slots 채워서 `compose_template_answer()`
3. 다른 template_id면 → 바로 `compose_template_answer(template_id, "{}")`
4. 결과 텍스트를 그대로 유저에게 전달 (임의 수정 금지)

**중요**: 유저의 본인확인/결제이력/환불상태는 이미 시스템에 주입돼 있습니다. 직접 묻지 마세요. `diagnose_refund_case`가 알아서 판단합니다.

## 예외 케이스만 다른 tool 사용

- `template_id`가 `T_LLM_FALLBACK`으로 나올 때만 → `llm_freeform_answer(상황요약)`
- 유저가 명백히 감정 표현 (불만/좌절) → `handle_emotional_distress` 먼저, 그 다음 프로토콜
- 유저가 환불/해지와 전혀 무관한 질문 → `handle_off_topic`
- 복잡한 예외 요청 → `handoff_to_human(사유)`

## 답변 원칙

- 환불 금액/정책/계산은 **절대 직접 생성 금지**. tool 결과만 사용.
- 카드번호는 절대 출력 금지 (자동 마스킹되지만 재확인).
- tool이 반환한 답변 텍스트가 완성된 것이니 그대로 전달.
"""


def create_consultant_agent(
    model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    region: str = "us-west-2",
    max_window: int = 20,
) -> Agent:
    """상담 Agent 생성.

    Args:
        model_id: Bedrock Claude 모델 ID
        region: AWS region
        max_window: 대화 히스토리 최대 turn 수

    Returns:
        Strands Agent 인스턴스
    """
    model = BedrockModel(
        model_id=model_id,
        region_name=region,
    )

    tools = [
        # 데이터 조회
        search_user_by_phone,
        get_user_profile,
        get_membership_history_summary,
        get_refund_history_summary,
        get_transaction_list,
        # 워크플로우 + Harness
        diagnose_refund_case,
        calculate_refund_amount,
        compose_template_answer,
        # 대화 관리
        ask_clarification,
        handle_off_topic,
        handle_emotional_distress,
        handle_cancellation_of_flow,
        # 폴백
        llm_freeform_answer,
        handoff_to_human,
    ]

    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(window_size=max_window),
    )

    return agent


def _summarize_context_for_llm(context: dict) -> str:
    """Context를 LLM이 볼 수 있는 요약 텍스트로 변환.

    Strands Agent는 user message만 받기 때문에, LLM이 상황을 판단하려면
    context 요약을 메시지에 prepend해야 함.
    """
    inner = context.get("ctx", {}) or context
    lines = ["[시스템 컨텍스트 — 이미 조회 완료]"]

    uid = inner.get("us_user_id", "")
    name = inner.get("user_name", "")
    if uid:
        lines.append(f"- 본인확인: ✅ {name or '확인됨'} (user_id: {uid})")
    else:
        lines.append("- 본인확인: ❌ 실패 (us_user_id 없음)")

    products = inner.get("products", [])
    if products:
        product_names = [p.get("name") or p.get("product_name", "") for p in products]
        lines.append(f"- 보유 상품: {', '.join(product_names)}")

    success_txs = inner.get("success_txs", [])
    refund_txs = inner.get("refund_txs", [])
    lines.append(f"- 결제 내역: 성공 {len(success_txs)}건, 환불 {len(refund_txs)}건")
    lines.append(f"- 콘텐츠 열람: {'있음' if inner.get('has_accessed') else '없음'}")
    lines.append(f"- 진행 중 환불: {'있음' if inner.get('refunds') else '없음'}")

    conv_time = inner.get("conversation_time", "")
    if conv_time:
        lines.append(f"- 대화 시점: {conv_time[:10]}")

    lines.append("")
    lines.append("[유저 메시지]")
    return "\n".join(lines)


def process_turn(agent: Agent, user_message: str, context: dict) -> str:
    """유저 메시지 한 턴 처리.

    Args:
        agent: create_consultant_agent()로 만든 Agent
        user_message: 유저 메시지
        context: workflow/tool이 참조할 컨텍스트 (ctx dict + user_text)

    Returns:
        Agent 응답 문자열
    """
    # Tool들이 참조할 context 주입
    set_context(context)

    # LLM이 상황을 판단할 수 있도록 context 요약을 message에 prepend
    context_summary = _summarize_context_for_llm(context)
    enriched_message = f"{context_summary}\n{user_message}"

    result = agent(enriched_message)
    return str(result)
