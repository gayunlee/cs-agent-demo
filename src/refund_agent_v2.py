"""환불/해지 Agent v2 — 템플릿 매칭 기반

3단계 파이프라인:
  Step 1: 의도 분류 → 템플릿 매칭 (LLM)
  Step 2: 필요 도구 호출 → 변수 수집
  Step 3: 템플릿 + 변수 조합 → 최종 답변

검증 포인트:
  ✅ 올바른 템플릿이 매칭되는가
  ✅ 올바른 도구가 호출되는가
  ✅ 조회된 값으로 템플릿이 완성되는가
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import boto3

from src.templates import TEMPLATES, INTENT_TEMPLATE_MAP

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# ── Step 1: 의도 분류 프롬프트 ──
CLASSIFY_PROMPT = """\
당신은 금융 교육 플랫폼 CS 문의 분류기입니다.
고객 메시지를 읽고 아래 의도 중 하나를 선택하세요.

## 의도 목록
- 해지_방법: 구독/자동결제 해지 방법을 물어봄 ("해지하고 싶다", "구독취소", "탈퇴하고 싶다", "자동결제 끊고 싶다")
- 해지_확인: 이미 해지했는데 확인 ("해지됐나요?", "처리됐는지", "자동결제해지는 따로?")
- 환불_요청: 환불을 요청함 ("환불해주세요", "환불 부탁", "돈 돌려주세요", "취소하고 환불", "남은 기간 환불")
- 자동결제_불만: 자동결제가 된 걸 몰랐거나 불만 ("자동으로 결제됐다", "왜 결제됐냐", "결제된 줄 몰랐다", "연장되어 결제가")
- 환불_규정_문의: 환불이 가능한지, 규정이 뭔지 물어봄 ("환불 가능한가요?", "환불 되나요?", "수수료는?")
- 카드변경: 결제카드 변경 관련 ("카드 변경", "카드 분실", "카드 재발급")
- 기타: 위에 해당 안 됨

## 출력 (JSON만, 다른 텍스트 없이)
{"intent": "의도명", "confidence": "높음|중간|낮음", "reasoning": "1줄 근거"}
"""

# ── Step 3: 변수 채우기 프롬프트 (필요시) ──
COMPOSE_PROMPT = """\
아래 템플릿의 {변수}를 조회된 데이터로 채워서 완성된 답변을 작성하세요.
변수에 해당하는 데이터가 없으면 적절히 대체하세요.
템플릿 구조는 최대한 유지하되, 데이터에 맞게 자연스럽게 조정하세요.

## 템플릿
{template}

## 조회된 데이터
{data}

## 고객 메시지
{messages}

완성된 답변만 출력하세요. 설명이나 코멘트 없이.
"""


@dataclass
class AgentStepV2:
    step: str  # "classify" | "tool_call" | "tool_result" | "compose" | "final"
    content: str
    detail: dict = field(default_factory=dict)


@dataclass
class AgentResultV2:
    intent: str = ""
    confidence: str = ""
    reasoning: str = ""
    template_id: str = ""
    template_name: str = ""
    tools_called: list[str] = field(default_factory=list)
    tool_results: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    final_answer: str = ""
    steps: list[AgentStepV2] = field(default_factory=list)
    tokens_used: int = 0


class RefundAgentV2:
    def __init__(self, region: str = "us-west-2", model_id: str = None, mock: bool = False):
        self.mock = mock
        self.model_id = model_id or MODEL_ID
        if not mock:
            self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        else:
            self.bedrock = None

    def process(
        self,
        user_messages: list[str],
        chat_id: str = "",
        phone: str = "",
        admin_data: dict | None = None,
        conversation_time: str = "",
        conversation_turns: list[dict] | None = None,
    ) -> AgentResultV2:
        """워크플로우 기반 처리

        실제 플로우:
          1. 전화번호 → 유저 검색 (API)
          2. 구독/결제/열람 조회 (API)
          3. 조회 결과 기반 룰로 템플릿 선택 (코드)
          4. 변수 채우기 → 초안 생성

        Args:
            phone: 유저 전화번호. 봇이 수집했거나 채널톡 프로필에서 획득.
            admin_data: 미리 수집된 유저 정보 (eval용). 있으면 API 호출 안 함.
            conversation_turns: 같은 채팅방 내 이전 대화 턴.
        """
        result = AgentResultV2()

        # ── Step 1: 의도 힌트 (유저 메시지 기반) ──
        intent, confidence, reasoning = self._classify(user_messages, result)
        result.intent = intent
        result.confidence = confidence
        result.reasoning = reasoning

        # ── Step 2: 유저 검색 + 정보 조회 ──
        has_data = bool(admin_data and admin_data.get("products") is not None)
        collected_data = {}
        us_user_id = ""

        if has_data:
            # eval 모드 — enriched 데이터
            collected_data = self._use_enriched_data(admin_data, conversation_time, result)
            us_user_id = admin_data.get("us_user_id", "")
        elif phone:
            # 실제 모드 — 전화번호로 검색 → API 조회
            us_user_id = self._search_user(phone, result)
            if us_user_id:
                collected_data = self._call_all_tools(us_user_id, result)
            else:
                result.steps.append(AgentStepV2(step="tool_result", content=f"유저 검색 실패: {phone}"))

        has_user = bool(us_user_id) or has_data

        # ── Step 3: 워크플로우 그래프 → 템플릿 선택 ──
        from src.workflow import WorkflowContext, run_workflow

        wf_ctx = WorkflowContext(
            user_messages=user_messages,
            phone=phone,
            conversation_turns=conversation_turns or [],
            us_user_id=us_user_id,
            intent=intent,
            products=collected_data.get("get_subscriptions", {}).get("products", []),
            transactions=collected_data.get("get_refund_products", {}).get("transactions", []),
            memberships=collected_data.get("get_membership_history", {}).get("memberships", []),
            refunds=collected_data.get("get_refund_history", {}).get("refunds", []),
            has_accessed=collected_data.get("get_membership_history", {}).get("has_accessed", False),
        )
        template_id = run_workflow(wf_ctx)

        result.steps.append(AgentStepV2(
            step="classify",
            content=f"워크플로우 경로: {' → '.join(wf_ctx.path)} → {template_id}",
            detail={"path": wf_ctx.path, "template": template_id},
        ))

        # 워크플로우에서 설정한 변수 가져오기
        if wf_ctx.template_variables:
            collected_data["workflow_variables"] = wf_ctx.template_variables

        tmpl = TEMPLATES.get(template_id, TEMPLATES.get("T6_본인확인_요청", {}))
        result.template_id = template_id
        result.template_name = tmpl.get("name", "")

        # 환불 계산 필요 시
        if template_id == "T2_환불_규정_금액" and "calculate_refund" not in collected_data:
            refund_input = self._prepare_refund_input(collected_data)
            if refund_input:
                refund_result = self._call_calculate_refund(refund_input, result)
                collected_data["calculate_refund"] = refund_result

        # ── Step 4: 템플릿 + 변수 → 초안 완성 ──
        if not tmpl.get("required_tools") and not collected_data:
            result.final_answer = tmpl["template"]
            result.steps.append(AgentStepV2(step="final", content=f"고정 템플릿: {template_id}"))
        elif collected_data:
            result.final_answer = self._compose(tmpl, collected_data, user_messages, result)
        else:
            result.final_answer = TEMPLATES["T6_본인확인_요청"]["template"]
            result.steps.append(AgentStepV2(step="final", content="유저 식별 불가 → 본인확인 요청"))

        return result

    def _search_user(self, phone: str, result: AgentResultV2) -> str:
        """전화번호로 어스 유저 검색"""
        result.steps.append(AgentStepV2(
            step="tool_call",
            content=f"search_user(phone={phone})",
            detail={"phone": phone},
        ))
        from src.admin_api import AdminAPIClient
        client = AdminAPIClient()
        phone_clean = phone.replace("-", "").replace(" ", "")
        user_id = client.search_user_by_phone(phone_clean)
        if user_id:
            user = client.get_user(user_id)
            result.steps.append(AgentStepV2(
                step="tool_result",
                content=f"유저 찾음: {user.name} ({user.signup_method}, {user.signup_state})",
                detail={"user_id": user_id, "name": user.name},
            ))
        else:
            result.steps.append(AgentStepV2(
                step="tool_result",
                content=f"유저 못 찾음: {phone}",
            ))
        return user_id or ""

    # ── 조회 결과 기반 템플릿 선택 ──

    def _use_enriched_data(self, admin_data: dict, conv_time: str, result: AgentResultV2) -> dict:
        """enriched 데이터를 대화 시점 기준으로 필터링 + 상태 복원"""
        products = admin_data.get("products", [])
        transactions = admin_data.get("transactions", [])
        usage = admin_data.get("usage", {})
        memberships = admin_data.get("memberships", [])
        refunds = admin_data.get("refunds", [])

        # 대화 시점 기준 필터 — conv_time 이전 거래만
        # 핵심: 대화 당일 처리된 환불은 "아직 안 됐던" 상태로 봐야 함
        # → 거래 날짜가 대화 날짜 **이전**(strictly before)인 것만 포함
        conv_date = conv_time[:10] if conv_time else ""  # YYYY-MM-DD
        if conv_date:
            transactions = [t for t in transactions if (t.get("date") or "")[:10] <= conv_date]
            # 환불은 대화 당일 건은 제외 (대화 시점에는 아직 환불 전)
            if isinstance(refunds, list):
                refunds = [r for r in refunds if (r.get("createdAt") or "")[:10] < conv_date]

        # ── 핵심: 대화 시점 기준 상품 status 복원 ──
        # 현재 status는 "지금" 기준이라 맞지 않음.
        # 거래 이력에서 "대화 시점에 active였는지" 판단:
        #   - 결제 성공 건이 있고
        #   - 그 결제가 아직 환불 안 됐으면
        #   → 대화 시점에는 active였음
        success_txs = [t for t in transactions if t.get("state") == "purchased_success"]
        refund_txs = [t for t in transactions if t.get("state") == "purchased_refund"]

        # 상품별로 active 여부 재계산
        restored_products = []
        for p in products:
            p_copy = dict(p)
            # 이 상품의 결제가 있고, 전부 환불되지 않았으면 → 대화 시점에 active
            if success_txs:
                all_refunded = len(refund_txs) >= len(success_txs) and len(refund_txs) > 0
                p_copy["status"] = "inactive" if all_refunded else "active"
            restored_products.append(p_copy)

        n_active = sum(1 for p in restored_products if p.get("status") == "active")

        collected = {
            "get_subscriptions": {"products": restored_products},
            "get_refund_products": {"products": restored_products, "transactions": transactions},
            "get_membership_history": {"has_accessed": usage.get("accessed", False), "content_view_count": usage.get("count", 0), "memberships": memberships},
            "get_refund_history": {"refunds": refunds},
        }

        result.steps.append(AgentStepV2(
            step="tool_result",
            content=f"enriched (시점복원) — 상품 {len(restored_products)}개(active:{n_active}), 거래 {len(transactions)}건(성공:{len(success_txs)}, 환불:{len(refund_txs)})",
            detail={"products": len(restored_products), "active": n_active, "success_txs": len(success_txs), "refund_txs": len(refund_txs)},
        ))

        return collected

    def _select_template(self, intent: str, data: dict, has_user: bool, result: AgentResultV2, turns: list[dict] | None = None) -> str:
        """의도 + 조회 결과 + 이전 대화 맥락 → 최종 템플릿 선택"""
        if not has_user:
            selected = "T6_본인확인_요청"
            result.steps.append(AgentStepV2(step="classify", content=f"유저 식별 불가 → {selected}"))
            return selected

        # 조회 데이터 추출
        products = data.get("get_subscriptions", {}).get("products", [])
        transactions = data.get("get_refund_products", {}).get("transactions", [])
        membership = data.get("get_membership_history", {})
        refunds_data = data.get("get_refund_history", {})
        refunds = refunds_data.get("refunds", []) if isinstance(refunds_data, dict) else []

        success_txs = [t for t in transactions if t.get("state") == "purchased_success"]
        refund_txs = [t for t in transactions if t.get("state") == "purchased_refund"]
        has_accessed = membership.get("has_accessed", False)

        # ── 이전 대화 맥락 분석 ──
        prev_manager_msgs = []
        if turns:
            prev_manager_msgs = [t['text'].lower() for t in turns if t.get('role') == 'manager']

        # 이전 턴에서 이미 환불 규정 안내 (T2) → 다음은 T3 (접수 완료)
        if prev_manager_msgs:
            prev_had_t2 = any(
                '환불 규정' in m or '7일 이내 구독권' in m or '환불금' in m or '환불 금액' in m
                for m in prev_manager_msgs
            )
            prev_had_t3 = any('환불 접수 완료' in m for m in prev_manager_msgs)
            prev_had_t1 = any('구독해지방법' in m or '구독해지 방법' in m for m in prev_manager_msgs)
            prev_had_t4 = any('구독형 스터디' in m or '정기적으로 제공되는' in m for m in prev_manager_msgs)

            # 이전 턴에서 T2 안내됨 + 유저가 환불 동의 → T3
            # 단, 매니저가 이미 2턴 이상 답변한 경우에만 (첫 응답이 T2인 건 제외)
            mgr_count = len(prev_manager_msgs)
            if prev_had_t2 and not prev_had_t3 and mgr_count >= 2:
                if intent in ("환불_요청",):  # 환불 요청 의도가 명확할 때만
                    selected = "T3_환불_접수_완료"
                    result.steps.append(AgentStepV2(step="classify", content=f"이전 T2 안내 + 매니저 {mgr_count}턴 + 유저 환불 동의 → {selected}"))
                    return selected

            # 이미 T4 안내됨 → 유저가 환불/해지 결정
            if prev_had_t4:
                if intent in ("환불_요청", "환불_규정_문의"):
                    selected = "T2_환불_규정_금액"
                    result.steps.append(AgentStepV2(step="classify", content=f"이전 턴에서 T4(자동결제설명) → 유저 환불 의사 → {selected}"))
                    return selected
                elif intent in ("해지_방법",):
                    selected = "T1_구독해지_방법_앱"
                    result.steps.append(AgentStepV2(step="classify", content=f"이전 턴에서 T4 → 유저 해지 의사 → {selected}"))
                    return selected

        # 카드 관련 → T8
        if intent == "카드변경":
            selected = "T8_카드변경_안내"
            result.steps.append(AgentStepV2(step="classify", content=f"카드변경 의도 → {selected}"))
            return selected

        # 결제 이력 기반 판단 (status가 아닌 거래 기록으로)
        has_payments = len(success_txs) > 0
        has_refunds = len(refund_txs) > 0

        if has_payments:
            latest_tx = success_txs[-1]
            tx_date = (latest_tx.get("date") or latest_tx.get("created_at") or "")[:10]
            tx_amount = latest_tx.get("amount", 0)
            if isinstance(tx_amount, str):
                try:
                    tx_amount = int(tx_amount)
                except ValueError:
                    tx_amount = 0

            # 최근 결제 환불됐는지 확인 (같은 round의 환불 기록 or 같은 금액 환불)
            latest_round = latest_tx.get("round", 0)
            latest_refunded = any(
                (t.get("round") == latest_round or t.get("amount") == latest_tx.get("amount"))
                and t.get("state") == "purchased_refund"
                for t in refund_txs
            )
            # 전체 환불 비율 — 환불 건이 결제 건의 과반이면 이미 처리 완료
            all_refunded = len(refund_txs) >= len(success_txs) and len(refund_txs) > 0

            # 이미 전부 환불됨 → T3
            if all_refunded:
                selected = "T3_환불_접수_완료"
                result.steps.append(AgentStepV2(step="classify", content=f"환불 건 >= 결제 건 (전부 환불됨) → {selected}"))
                return selected

            # 자동결제 불만 → T4
            if intent == "자동결제_불만":
                selected = "T4_자동결제_설명"
                result.steps.append(AgentStepV2(step="classify", content=f"자동결제 불만 + 결제이력({tx_amount:,}원, {tx_date}) → {selected}"))
                return selected

            # 환불 의도 + 결제이력 → T2
            if intent in ("환불_요청", "환불_규정_문의"):
                if latest_refunded:
                    # 이미 환불된 결제 → T3 접수 완료
                    selected = "T3_환불_접수_완료"
                    result.steps.append(AgentStepV2(step="classify", content=f"환불 의도이나 최근 결제 이미 환불됨 → {selected}"))
                else:
                    selected = "T2_환불_규정_금액"
                    result.steps.append(AgentStepV2(step="classify", content=f"환불 의도 + 결제이력({tx_amount:,}원, {tx_date}) → {selected}"))
                return selected

            # 해지 의도 → 유저가 "환불"을 직접 언급했는지 확인
            if intent in ("해지_방법", "해지_확인"):
                selected = "T1_구독해지_방법_앱"
                result.steps.append(AgentStepV2(step="classify", content=f"해지 의도 → T1 (해지 방법 안내 우선)"))
                return selected

            # 기타 의도 + 결제이력
            if latest_refunded:
                selected = "T3_환불_접수_완료"
                result.steps.append(AgentStepV2(step="classify", content=f"기타 의도 + 환불 처리됨 → {selected}"))
            else:
                selected = "T2_환불_규정_금액"
                result.steps.append(AgentStepV2(step="classify", content=f"기타 의도 + 미환불 결제 있음 → {selected}"))
            return selected

        # 결제 이력 없음
        if intent in ("해지_방법", "해지_확인"):
            selected = "T1_구독해지_방법_앱"
            result.steps.append(AgentStepV2(step="classify", content=f"해지 의도 + 결제이력 없음 → {selected}"))
            return selected

        # 폴백
        selected = "T6_본인확인_요청"
        result.steps.append(AgentStepV2(step="classify", content=f"폴백 → {selected}"))
        return selected

    def _call_all_tools(self, user_id: str, result: AgentResultV2) -> dict:
        """실제 API 호출로 전체 데이터 수집"""
        collected = {}
        for tool_name in ["get_subscriptions", "get_refund_products", "get_membership_history", "get_refund_history"]:
            tool_result = self._call_tool(tool_name, user_id, result)
            collected[tool_name] = tool_result
            result.tools_called.append(tool_name)
            result.tool_results[tool_name] = tool_result
        return collected

    # ── Step 1 구현 ──

    def _classify(self, messages: list[str], result: AgentResultV2) -> tuple[str, str, str]:
        formatted = "\n".join(f"고객: {m}" for m in messages)

        if self.mock or not self.bedrock:
            return self._mock_classify(messages)

        try:
            resp = self._call_llm(CLASSIFY_PROMPT, formatted, max_tokens=200)
            data = self._parse_json(resp)
            return (
                data.get("intent", "기타"),
                data.get("confidence", "중간"),
                data.get("reasoning", ""),
            )
        except Exception as e:
            logger.warning(f"분류 실패: {e}")
            return self._mock_classify(messages)

    def _mock_classify(self, messages: list[str]) -> tuple[str, str, str]:
        full = " ".join(messages).lower()

        if any(kw in full for kw in ["카드 변경", "카드변경", "카드 분실"]):
            return "카드변경", "높음", "카드변경 키워드"
        if any(kw in full for kw in ["자동으로 결제", "자동결제", "자동결재", "왜 결제", "결제된 줄 몰", "연장되어"]):
            return "자동결제_불만", "높음", "자동결제 관련 키워드"
        if any(kw in full for kw in ["해지됐", "해지 됐", "처리 되었는지", "해지 확인", "자동결제해지는 따로"]):
            return "해지_확인", "높음", "해지 확인 키워드"
        if any(kw in full for kw in ["해지", "구독취소", "구독해지", "탈퇴", "자동결제 끊", "그만두"]):
            return "해지_방법", "높음", "해지 관련 키워드"
        if any(kw in full for kw in ["환불해", "환불 부탁", "환불 신청", "돈 돌려", "취소하고 환불", "남은 기간 환불", "환불처리", "환불요청"]):
            return "환불_요청", "높음", "환불 요청 키워드"
        if any(kw in full for kw in ["환불 가능", "환불 되나", "환불이 가능", "수수료"]):
            return "환불_규정_문의", "높음", "환불 규정 문의 키워드"
        if any(kw in full for kw in ["환불"]):
            return "환불_요청", "중간", "환불 단어 포함"
        return "기타", "낮음", "매칭 키워드 없음"

    # ── Step 2 구현 ──

    def _call_tool(self, tool_name: str, user_id: str, result: AgentResultV2) -> dict:
        result.steps.append(AgentStepV2(
            step="tool_call",
            content=f"{tool_name}(user_id={user_id})",
            detail={"tool": tool_name, "user_id": user_id},
        ))

        if self.mock:
            output = self._mock_tool(tool_name, user_id)
        else:
            output = self._real_tool(tool_name, user_id)

        result.steps.append(AgentStepV2(
            step="tool_result",
            content=json.dumps(output, ensure_ascii=False)[:300],
            detail=output,
        ))
        return output

    def _real_tool(self, tool_name: str, user_id: str) -> dict:
        from src.admin_api import AdminAPIClient
        client = AdminAPIClient()

        try:
            if tool_name == "get_subscriptions":
                products = client.get_products(user_id)
                return {"products": [{
                    "master_name": p.master_name,
                    "product_name": p.product_name,
                    "type": p.product_type,
                    "status": p.status,
                    "price": p.price,
                    "purchased_count": p.purchased_count,
                    "activated_at": p.activated_at,
                    "expired_at": p.expired_at,
                } for p in products]}

            elif tool_name == "get_refund_products":
                products, transactions = client.get_refund_info(user_id)
                return {
                    "products": [{"product_name": p.product_name, "status": p.status, "expired_at": p.expired_at} for p in products],
                    "transactions": [{"round": t.round, "state": t.state, "amount": t.amount, "method": t.method, "method_info": t.method_info, "created_at": t.created_at} for t in transactions],
                }

            elif tool_name == "get_membership_history":
                usage, memberships = client.get_membership_history(user_id)
                return {
                    "has_accessed": usage.has_accessed,
                    "content_view_count": usage.content_view_count,
                    "memberships": memberships,
                }

            elif tool_name == "get_refund_history":
                refunds = client.get_refund_history(user_id)
                return {"refunds": refunds}

        except Exception as e:
            logger.error(f"도구 호출 실패 [{tool_name}]: {e}")
            return {"error": str(e)}

        return {"error": f"알 수 없는 도구: {tool_name}"}

    def _mock_tool(self, tool_name: str, user_id: str) -> dict:
        if tool_name == "get_subscriptions":
            return {"products": [{"master_name": "박두환", "product_name": "투자동행학교 6개월", "status": "active", "price": 550000, "activated_at": "2026-03-01", "expired_at": "2026-09-01"}]}
        elif tool_name == "get_refund_products":
            return {"products": [{"product_name": "투자동행학교 6개월", "status": "active"}], "transactions": [{"round": 1, "state": "purchased_success", "amount": 550000, "method": "card", "method_info": "신한카드", "created_at": "2026-03-01"}]}
        elif tool_name == "get_membership_history":
            return {"has_accessed": True, "content_view_count": 15, "memberships": [{"productName": "투자동행학교 6개월", "paymentCycle": 6}]}
        elif tool_name == "get_refund_history":
            return {"refunds": []}
        return {}

    def _prepare_refund_input(self, collected_data: dict) -> dict | None:
        """조회된 데이터에서 환불 계산 입력값 추출"""
        refund_products = collected_data.get("get_refund_products", {})
        membership = collected_data.get("get_membership_history", {})

        transactions = refund_products.get("transactions", [])
        success_txs = [t for t in transactions if t.get("state") == "purchased_success"]
        if not success_txs:
            return None

        latest_tx = success_txs[-1]
        amount = latest_tx.get("amount", 0)
        if isinstance(amount, str):
            try:
                amount = int(amount)
            except ValueError:
                return None

        payment_date = latest_tx.get("created_at", "")[:10]
        has_accessed = membership.get("has_accessed", False)

        return {
            "total_paid": amount,
            "monthly_price": amount,  # TODO: 1개월 정가 별도 조회
            "payment_date": payment_date,
            "content_accessed": has_accessed,
        }

    def _call_calculate_refund(self, input_data: dict, result: AgentResultV2) -> dict:
        result.steps.append(AgentStepV2(
            step="tool_call",
            content=f"calculate_refund({json.dumps(input_data, ensure_ascii=False)})",
        ))

        from src.refund_engine import RefundEngine, RefundInput
        engine = RefundEngine()
        try:
            payment_date = datetime.strptime(input_data["payment_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            return {"error": "결제일 파싱 실패"}

        inp = RefundInput(
            total_paid=input_data["total_paid"],
            monthly_price=input_data.get("monthly_price", input_data["total_paid"]),
            payment_date=payment_date,
            payment_cycle_days=30,
            content_accessed=input_data.get("content_accessed", False),
        )
        calc = engine.calculate(inp)
        output = {
            "refundable": calc.refundable,
            "refund_amount": calc.refund_amount,
            "deduction": calc.deduction,
            "fee": calc.fee,
            "explanation": calc.explanation,
        }

        result.steps.append(AgentStepV2(
            step="tool_result",
            content=json.dumps(output, ensure_ascii=False),
            detail=output,
        ))
        result.variables = output
        return output

    # ── Step 3 구현 ──

    def _compose(self, tmpl: dict, collected_data: dict, messages: list[str], result: AgentResultV2) -> str:
        """조회된 데이터로 템플릿 변수를 채움"""
        template_text = tmpl["template"]

        # 환불 템플릿: 전액/부분 분기
        calc = collected_data.get("calculate_refund", {})
        if calc and tmpl.get("template_full_refund") and calc.get("deduction", 0) == 0:
            template_text = tmpl["template_full_refund"]

        # 단순 변수 치환 시도
        variables = {}

        # calculate_refund 결과
        if calc and not calc.get("error"):
            variables["환불금액"] = f"{calc.get('refund_amount', 0):,}"
            variables["차감금"] = f"{calc.get('deduction', 0):,}"
            variables["수수료"] = f"{calc.get('fee', 0):,}"
            variables["결제금액"] = f"{calc.get('refund_amount', 0) + calc.get('deduction', 0) + calc.get('fee', 0):,}"

        # 구독 정보
        refund_data = collected_data.get("get_refund_products", {})
        products = refund_data.get("products", [])
        transactions = refund_data.get("transactions", [])
        if products:
            variables["상품명"] = products[0].get("product_name", "")
        if transactions:
            dates = [t.get("created_at", "")[:7] for t in transactions]  # YYYY-MM
            if len(dates) >= 2:
                variables["이전결제월"] = dates[-2].replace("-", "년 ") + "월" if "-" in dates[-2] else dates[-2]
                variables["현재결제월"] = dates[-1].replace("-", "년 ") + "월" if "-" in dates[-1] else dates[-1]
            elif dates:
                variables["현재결제월"] = dates[0].replace("-", "년 ") + "월" if "-" in dates[0] else dates[0]

        # 구독 정보에서 마스터명
        subs = collected_data.get("get_subscriptions", {})
        sub_products = subs.get("products", [])
        if sub_products:
            variables["마스터명"] = sub_products[0].get("master_name", "")

        # 변수 치환
        answer = template_text
        for key, val in variables.items():
            answer = answer.replace(f"{{{key}}}", str(val))

        # 아직 남은 {변수}가 있으면 LLM으로 마무리
        if "{" in answer and self.bedrock and not self.mock:
            answer = self._llm_compose(template_text, collected_data, messages, result)
        elif "{" in answer:
            # mock: 남은 변수는 placeholder 표시
            pass

        result.steps.append(AgentStepV2(
            step="compose",
            content=f"변수 채움: {list(variables.keys())}",
            detail=variables,
        ))

        return answer

    def _llm_compose(self, template: str, data: dict, messages: list[str], result: AgentResultV2) -> str:
        prompt = COMPOSE_PROMPT.format(
            template=template,
            data=json.dumps(data, ensure_ascii=False, default=str)[:2000],
            messages="\n".join(f"고객: {m}" for m in messages),
        )
        return self._call_llm("", prompt, max_tokens=500)

    # ── 유틸 ──

    def _call_llm(self, system: str, user_text: str, max_tokens: int = 500) -> str:
        resp = self.bedrock.invoke_model(
            modelId=self.model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_text}],
            }),
        )
        body = json.loads(resp["body"].read())
        self._last_tokens = body.get("usage", {}).get("input_tokens", 0) + body.get("usage", {}).get("output_tokens", 0)
        return body["content"][0]["text"].strip()

    def _parse_json(self, raw: str) -> dict:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        raw = raw.strip()
        idx = raw.find("{")
        if idx >= 0:
            end = raw.rfind("}") + 1
            return json.loads(raw[idx:end])
        return {}
