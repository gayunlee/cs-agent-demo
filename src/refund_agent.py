"""환불 문의 처리 Tool-Use Agent

상담사 어시스턴트: 고객 문의를 받으면 필요한 정보를 자율적으로 조회하고
환불 답변 초안을 생성한다. 어떤 도구를 쓸지는 agent가 판단.

Tools:
  - search_user: 전화번호/이름으로 유저 검색 (GET /v3/users)
  - get_subscriptions: 구독 목록 조회 (GET /users/{id}/my-products)
  - get_refund_products: 환불 대상 상품 + 거래내역 (GET /cs/refund-user/{userId}/products)
  - get_membership_history: 멤버십 이용 이력 (GET /v1/users/{id}/membership-history)
  - get_refund_history: 기존 환불 이력 (GET /v1/users/{id}/membership-refund-history)
  - calculate_refund: 환불 금액 계산 (로컬 엔진)
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = """\
당신은 금융 교육 플랫폼 "어스"의 CS 상담 어시스턴트입니다.
고객의 환불/해지 관련 문의를 분석하고, 상담사가 답변할 수 있도록 필요한 정보를 조회하고 답변 초안을 작성합니다.

## 당신의 역할
1. 고객 메시지를 읽고 의도를 파악
2. userId가 있으면 도구로 정보 조회 (구독, 결제, 멤버십 이력, 환불 이력)
3. 조회 결과를 바탕으로 아래 6가지 답변 유형 중 적합한 것을 선택
4. 해당 템플릿에 조회된 정보를 채워 넣어 답변 초안 작성

## 환불 규정
- 7일 이내 + 콘텐츠 미열람(구독권 미개시): 전액 환불
- 7일 이내 + 콘텐츠 열람(구독권 개시): 이용일수 차감 후 부분 환불
- 7일 경과: 1개월 정가 금액 차감 + 잔여 금액의 10% 수수료 차감
- 구독해지 ≠ 환불 (해지는 다음 정기결제 중단, 환불은 이미 결제한 금액 반환)

## 6가지 답변 유형 + 템플릿

### 유형1: 자동결제 설명 (고객이 자동결제를 인지 못한 경우)
```
안녕하세요 회원님, 문의 주셔서 감사합니다.
{마스터명} 마스터 과정을 함께해 주시고 꾸준히 학습해 주심에 진심으로 감사드립니다.
본 과정은 정기적으로 제공되는 콘텐츠를 통해 투자 학습을 이어가는 구독형 스터디입니다.
회원님께서는 지난 {이전결제월} 과정에 이어 {현재결제월} 과정의 정기결제가 진행되었습니다.

구독해지를 희망하시면 아래 방법으로 진행해 주세요.
{구독해지_방법}

환불을 희망하시면 환불 규정에 따라 안내드리겠습니다.
```

### 유형2: 구독해지 방법 안내 (해지 방법만 필요한 경우)
```
안녕하세요 회원님,
다음 정기결제 구독해지방법 안내드립니다.

■정기결제 구독해지 방법
①어스플러스 앱 접속
②우측 상단 my 클릭 → 멤버십 관리 클릭
③회색글씨로 된 <내역보기> 클릭
④구독해지 클릭

❗주의사항
-구독해지 신청은 다음 정기결제에 대한 해지입니다.
-현재 구독 기간까지는 정상 이용 가능합니다.
```
웹에서 가입한 경우:
```
■정기결제 구독해지 방법
①어스플러스 홈페이지 접속 https://us-insight.com/
②우측 상단 my 클릭 → 멤버십 관리 클릭
③회색글씨로 된 <내역보기> 클릭
④구독해지 클릭
```

### 유형3: 환불 규정 안내 + 금액 제시 (조회 후 환불 가능 시)
```
안녕하세요 회원님, 문의 주셔서 감사합니다.

현재 어스플러스는 7일 이내 구독권 미개시 시, 전액 환불 접수를 도와드리나
구독권 개시 시, 환불 규정에 따른 금액으로 차감하여 환불이 진행됩니다.

회원님께서는 현재 열람이 시작(구독권 개시)되어 일부 환불금 차감 되어 진행됩니다.
이용에 따라 1개월 정가 금액이 차감이 되며, 잔여 금액에서의 10% 수수료 차감되어 환불 진행됩니다.

■ 환불 금액: {환불금액}원

상기 환불금액 확인하시고, 환불 진행 희망하시면 말씀 부탁드립니다.
환불 시 구독권은 즉시 종료되는 점 안내드립니다.
```

### 유형4: 환불 접수 완료
```
네 회원님, 환불 접수 완료되었습니다.

카드 결제를 해주시어 결제해주신 카드 취소로 진행될 예정입니다.
카드사 반영까지는 영업일 기준 최대 3일 정도 소요되실 수 있습니다.

[환불 안내사항]
1. 최종 카드 환불의 경우 카드사 사정으로 인해 영업일 기준 최대 3일가량 소요될 수 있습니다.
2. 취소 완료 시, 카드사에서 안내해 드리는 SMS는 회원님의 수신 설정에 따라 미수신 될 수 있습니다.
```

### 유형5: 본인확인 요청 (유저 식별 불가 시)
```
안녕하세요 회원님, 문의 주셔서 감사합니다.
성함/휴대전화 번호 말씀 주시면 확인 도와드리도록 하겠습니다.
```

### 유형6: 해지 확인 완료
```
안녕하세요 회원님,
구독해지 = 자동결제 해지 입니다 :)
현재 구독해지 잘 되어 있으시어, 다음 결제 진행되지 않을 예정입니다.
```

## 의사결정 트리 (순서대로 판단하세요)

### Step 1: 의도 분류 (도구 호출 전에 먼저 판단)
고객 메시지를 읽고 아래 중 어디에 해당하는지 판단하세요:
- A. 해지 방법 문의 ("해지하고 싶다", "구독취소", "탈퇴", "자동결제 끊고 싶다")
- B. 해지 확인 ("해지됐나요?", "처리 되었는지", "자동결제 해지는 따로?")
- C. 환불 요청 ("환불해주세요", "돈 돌려주세요", "취소하고 환불")
- D. 자동결제 불만 ("자동으로 결제됐다", "결제된 줄 몰랐다", "왜 결제됐냐")
- E. 환불 규정 문의 ("환불 되나요?", "환불 가능한가요?")

### Step 2: 의도별 처리

**A. 해지 방법 → 도구 호출 불필요, 바로 유형2 템플릿**
- userId 유무와 관계없이 고정 템플릿 즉시 제공

**B. 해지 확인 → userId 있으면 get_subscriptions 조회**
- 해지 완료 → 유형6
- 아직 활성 → 유형2 안내

**C. 환불 요청 → userId 필요**
- userId 없음 → 유형5 (본인확인)
- userId 있음 → get_refund_products + get_membership_history 조회 → calculate_refund → 유형3

**D. 자동결제 불만 → userId 있으면 조회**
- userId 없음 → 유형5
- userId 있음 → get_refund_products 조회 → 유형1 (자동결제 설명 + 해지/환불 안내)

**E. 환불 규정 문의 → userId 있으면 조회해서 구체적 금액 제시**
- userId 없음 → 유형5
- userId 있음 → 조회 → 유형3 (금액 포함)

## 출력 형식
반드시 아래 형식으로 작성하세요.

[상담사 요약]
- 고객 의도: (1줄)
- 조회 결과: (핵심만)
- 적용 유형: (유형1~6 중)
- 권장 액션: (환불 처리/해지 안내/추가 확인 필요/본인 확인 요청)

[답변 초안]
(위 템플릿에 조회된 정보를 채운 답변. 존댓말.)

## 중요 규칙
1. 당신은 고객과 직접 대화하는 것이 아닙니다. 상담사에게 정보와 초안을 제공하는 것입니다.
2. 최종 출력은 반드시 [상담사 요약]과 [답변 초안]을 포함해야 합니다.
3. userId가 시스템에서 제공되면 search_user 없이 바로 get_subscriptions 등으로 조회하세요.
4. userId도 없고 전화번호/이름도 없으면 도구 호출 없이 유형5 답변을 작성하세요.
5. 조회된 정보로 템플릿의 변수({마스터명}, {환불금액} 등)를 채우세요.
"""

TOOLS = [
    {
        "name": "search_user",
        "description": "전화번호 또는 닉네임으로 유저를 검색합니다. userId, 닉네임, 전화번호, 가입방법(direct/kakao/naver/google/apple), 최근접속일을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "전화번호 (예: 01012345678). 하이픈 없이.",
                },
                "name": {
                    "type": "string",
                    "description": "유저 닉네임",
                },
            },
        },
    },
    {
        "name": "get_subscriptions",
        "description": "유저의 구독 상품 목록을 조회합니다. 마스터명, 상품명, 유형, 상태(active/inactive), 가격, 결제횟수, 만료일을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "유저 ID"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "get_refund_products",
        "description": "환불 대상 멤버십 상품과 거래 내역을 조회합니다. 상품별로 결제 회차, 금액, 결제일, 카드정보, 상태를 반환합니다. 환불 판단의 핵심 데이터입니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "유저 ID"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "get_membership_history",
        "description": "유저의 멤버십 이용 이력을 조회합니다. 상품별 결제주기, 만료여부, 거래이력(결제일, 상태, 금액, 결제수단)을 반환합니다. 콘텐츠 열람/이용 여부 판단에 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "유저 ID"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "get_refund_history",
        "description": "유저의 기존 환불 이력을 조회합니다. 이전에 환불받은 상품, 결제금액, 환불금액, 환불일을 반환합니다. 중복 환불 방지 및 이전 환불 상태 확인에 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "유저 ID"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "calculate_refund",
        "description": "환불 금액을 계산합니다. 결제금액, 결제일, 콘텐츠 열람 여부를 입력하면 환불 가능 여부와 금액(차감금, 수수료 포함)을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "total_paid": {"type": "integer", "description": "결제 금액 (원)"},
                "monthly_price": {"type": "integer", "description": "1개월 정가 (원). 모르면 결제 금액과 동일."},
                "payment_date": {"type": "string", "description": "결제일 (YYYY-MM-DD)"},
                "content_accessed": {"type": "boolean", "description": "콘텐츠 열람 여부"},
            },
            "required": ["total_paid", "payment_date", "content_accessed"],
        },
    },
]


@dataclass
class AgentStep:
    """agent의 한 스텝 (thinking/tool_call/tool_result/answer)"""
    type: str  # "thinking" | "tool_call" | "tool_result" | "answer"
    content: str
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """agent 실행 결과"""
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str = ""
    tools_used: list[str] = field(default_factory=list)
    total_tokens: int = 0


class RefundAgent:
    """환불 문의 처리 Tool-Use Agent"""

    def __init__(self, region: str = "us-west-2", model_id: str = None, mock: bool = False):
        self.mock = mock
        self.model_id = model_id or MODEL_ID
        if not mock:
            self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        else:
            self.bedrock = None

        # mock 데이터
        self._mock_users = {
            "01012345678": {
                "user_id": "usr_12345",
                "name": "김어스",
                "phone": "01012345678",
                "signup_method": "카카오",
                "signup_state": "ACTIVE",
                "signup_date": "2025-06-15",
            },
        }
        self._mock_subscriptions = {
            "usr_12345": [
                {
                    "master_name": "박두환",
                    "product_name": "투자동행학교 6개월",
                    "type": "SUBSCRIPTION",
                    "status": "active",
                    "price": 550000,
                    "activated_at": "2026-03-15",
                    "expired_at": "2026-09-15",
                },
            ],
        }
        self._mock_payments = {
            "usr_12345": [
                {
                    "round": 1,
                    "state": "purchased_success",
                    "amount": 550000,
                    "created_at": "2026-03-15",
                    "method": "카드",
                    "method_info": "신한카드",
                },
            ],
        }

    def process(self, user_messages: list[str], chat_id: str = "", user_id: str = "") -> AgentResult:
        """유저 메시지를 받아 agent 루프 실행

        Args:
            user_messages: 고객 메시지 목록
            chat_id: 채팅방 ID
            user_id: 채널톡에서 매핑된 유저 ID (있으면 바로 조회)
        """
        parts = []
        if user_id:
            parts.append(f"[시스템 정보] 이 고객의 userId: {user_id} — search_user 없이 바로 get_subscriptions, get_refund_products 등으로 조회하세요.")
        parts.append("\n".join(f"고객: {m}" for m in user_messages))
        formatted = "\n\n".join(parts)

        if self.mock or not self.bedrock:
            return self._mock_process(user_messages, user_id=user_id)

        return self._run_agent_loop(formatted)

    def _run_agent_loop(self, user_input: str, max_turns: int = 5) -> AgentResult:
        """Bedrock tool-use agent 루프"""
        result = AgentResult()
        messages = [{"role": "user", "content": user_input}]

        for turn in range(max_turns):
            resp = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2000,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                    "tools": TOOLS,
                }),
            )
            body = json.loads(resp["body"].read())
            result.total_tokens += body.get("usage", {}).get("input_tokens", 0)
            result.total_tokens += body.get("usage", {}).get("output_tokens", 0)

            content_blocks = body.get("content", [])
            stop_reason = body.get("stop_reason", "")

            # assistant 메시지 구성
            assistant_content = []
            tool_results = []

            for block in content_blocks:
                if block["type"] == "text":
                    text = block["text"].strip()
                    if text:
                        result.steps.append(AgentStep(type="thinking", content=text))
                        assistant_content.append(block)

                elif block["type"] == "tool_use":
                    tool_name = block["name"]
                    tool_input = block["input"]
                    tool_id = block["id"]

                    result.steps.append(AgentStep(
                        type="tool_call",
                        content=f"{tool_name}({json.dumps(tool_input, ensure_ascii=False)})",
                        tool_name=tool_name,
                        tool_input=tool_input,
                    ))
                    result.tools_used.append(tool_name)
                    assistant_content.append(block)

                    # 도구 실행
                    tool_output = self._execute_tool(tool_name, tool_input)
                    result.steps.append(AgentStep(
                        type="tool_result",
                        content=tool_output,
                        tool_name=tool_name,
                    ))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": tool_output,
                    })

            messages.append({"role": "assistant", "content": assistant_content})

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            if stop_reason == "end_turn":
                # 최종 답변 추출
                for block in content_blocks:
                    if block["type"] == "text":
                        result.final_answer = block["text"].strip()
                        result.steps.append(AgentStep(type="answer", content=result.final_answer))
                break

        return result

    def _get_admin_client(self):
        """AdminAPIClient 인스턴스 (env에서 토큰/URL 읽음)"""
        from src.admin_api import AdminAPIClient
        return AdminAPIClient()

    def _execute_tool(self, name: str, input_data: dict) -> str:
        """도구 실행 — 실제 API 호출 또는 mock"""
        try:
            handler = {
                "search_user": self._tool_search_user,
                "get_subscriptions": self._tool_get_subscriptions,
                "get_refund_products": self._tool_get_refund_products,
                "get_membership_history": self._tool_get_membership_history,
                "get_refund_history": self._tool_get_refund_history,
                "calculate_refund": self._tool_calculate_refund,
                # 하위 호환 (이전 도구명)
                "get_payment_history": self._tool_get_refund_products,
                "check_content_access": self._tool_get_membership_history,
            }.get(name)
            if handler:
                return handler(input_data)
            return json.dumps({"error": f"알 수 없는 도구: {name}"})
        except Exception as e:
            logger.error(f"도구 실행 오류 [{name}]: {e}")
            return json.dumps({"error": str(e)})

    def _tool_search_user(self, input_data: dict) -> str:
        phone = input_data.get("phone", "")
        name = input_data.get("name", "")

        if self.mock:
            if phone in self._mock_users:
                return json.dumps(self._mock_users[phone], ensure_ascii=False)
            if name:
                for u in self._mock_users.values():
                    if name in u.get("name", ""):
                        return json.dumps(u, ensure_ascii=False)
            return json.dumps({"error": "유저를 찾을 수 없습니다. 전화번호 또는 이름을 확인해주세요."})

        client = self._get_admin_client()
        if phone:
            phone_clean = phone.replace("-", "").replace(" ", "")
            user_id = client.search_user_by_phone(phone_clean)
            if user_id:
                user = client.get_user(user_id)
                return json.dumps({
                    "user_id": user.user_id,
                    "name": user.name,
                    "phone": user.phone,
                    "signup_method": user.signup_method,
                    "signup_state": user.signup_state,
                    "signup_date": user.signup_date,
                    "last_accessed": user.last_accessed,
                    "content_view": user.content_view,
                }, ensure_ascii=False)
        if name:
            # 닉네임 검색은 /v3/users?nickName= 으로
            data = client._get("/v3/users", params={"nickName": name, "offset": 0, "limit": 5})
            users = data.get("users", [])
            if users:
                u = users[0]
                return json.dumps({
                    "user_id": str(u.get("id", "")),
                    "name": u.get("nickName", ""),
                    "phone": u.get("phoneNumber", ""),
                    "signup_method": u.get("signUpMethod", ""),
                    "last_accessed": u.get("lastAccessedAt", ""),
                }, ensure_ascii=False)
        return json.dumps({"error": "유저를 찾을 수 없습니다."})

    def _tool_get_subscriptions(self, input_data: dict) -> str:
        user_id = input_data["user_id"]

        if self.mock:
            subs = self._mock_subscriptions.get(user_id, [])
            return json.dumps(subs if subs else {"error": "구독 정보 없음"}, ensure_ascii=False)

        client = self._get_admin_client()
        products = client.get_products(user_id)
        return json.dumps([{
            "master_name": p.master_name,
            "product_name": p.product_name,
            "type": p.product_type,
            "status": p.status,
            "price": p.price,
            "purchased_count": p.purchased_count,
            "activated_at": p.activated_at,
            "expired_at": p.expired_at,
        } for p in products], ensure_ascii=False)

    def _tool_get_refund_products(self, input_data: dict) -> str:
        """GET /cs/refund-user/{userId}/products — 환불 대상 상품 + 거래내역"""
        user_id = input_data["user_id"]

        if self.mock:
            payments = self._mock_payments.get(user_id, [])
            return json.dumps(payments if payments else {"error": "결제 이력 없음"}, ensure_ascii=False)

        client = self._get_admin_client()
        products, transactions = client.get_refund_info(user_id)
        result = []
        for p in products:
            p_txs = [t for t in transactions if True]  # 전체 거래내역
            result.append({
                "product_id": p.my_product_id,
                "product_name": p.product_name,
                "status": p.status,
                "activated_at": p.activated_at,
                "expired_at": p.expired_at,
                "transactions": [{
                    "round": t.round,
                    "state": t.state,
                    "amount": t.amount,
                    "method": t.method,
                    "method_info": t.method_info,
                    "created_at": t.created_at,
                    "provider": t.provider,
                } for t in transactions],
            })
        return json.dumps(result, ensure_ascii=False)

    def _tool_get_membership_history(self, input_data: dict) -> str:
        """GET /v1/users/{id}/membership-history — 멤버십 이용 이력"""
        user_id = input_data["user_id"]

        if self.mock:
            return json.dumps({
                "memberships": [{
                    "productName": "박두환 투자동행학교 6개월",
                    "paymentCycle": 6,
                    "expiration": False,
                    "transactionHistories": [
                        {"createdAt": "2026-03-15", "state": "purchased_success", "purchasedAmount": "550000"},
                    ],
                }],
            }, ensure_ascii=False)

        client = self._get_admin_client()
        usage, memberships = client.get_membership_history(user_id)
        return json.dumps({
            "has_accessed": usage.has_accessed,
            "content_view_count": usage.content_view_count,
            "last_access_date": usage.last_access_date,
            "memberships": memberships,
        }, ensure_ascii=False)

    def _tool_get_refund_history(self, input_data: dict) -> str:
        """GET /v1/users/{id}/membership-refund-history — 기존 환불 이력"""
        user_id = input_data["user_id"]

        if self.mock:
            return json.dumps({"refunds": []}, ensure_ascii=False)

        client = self._get_admin_client()
        refunds = client.get_refund_history(user_id)
        return json.dumps({"refunds": refunds}, ensure_ascii=False)

    def _tool_calculate_refund(self, input_data: dict) -> str:
        from src.refund_engine import RefundEngine, RefundInput

        engine = RefundEngine()
        try:
            payment_date = datetime.strptime(input_data["payment_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            return json.dumps({"error": "결제일 형식이 잘못되었습니다 (YYYY-MM-DD)"})

        inp = RefundInput(
            total_paid=input_data["total_paid"],
            monthly_price=input_data.get("monthly_price", input_data["total_paid"]),
            payment_date=payment_date,
            payment_cycle_days=30,
            content_accessed=input_data.get("content_accessed", False),
        )
        result = engine.calculate(inp)
        return json.dumps({
            "refundable": result.refundable,
            "refund_amount": result.refund_amount,
            "deduction": result.deduction,
            "fee": result.fee,
            "explanation": result.explanation,
        }, ensure_ascii=False)

    def _mock_process(self, user_messages: list[str], user_id: str = "") -> AgentResult:
        """Mock 모드: 실제 LLM 없이 시뮬레이션"""
        result = AgentResult()
        full_text = " ".join(user_messages).lower()

        # Step 1: 의도 파악
        has_refund = any(kw in full_text for kw in ["환불", "취소", "반환"])
        has_cancel = any(kw in full_text for kw in ["해지", "구독취소", "구독해지", "자동결제", "자동결재"])
        has_phone = any(c.isdigit() for c in full_text if len([x for x in full_text if x.isdigit()]) >= 10)
        has_name = any(kw in full_text for kw in ["입니다", "이름", "성함"])

        # 전화번호 추출 시도
        import re
        phone_match = re.search(r'01[0-9][\s-]?\d{3,4}[\s-]?\d{4}', full_text.replace(" ", ""))
        phone = phone_match.group().replace("-", "").replace(" ", "") if phone_match else ""

        result.steps.append(AgentStep(
            type="thinking",
            content=f"고객 의도 분석: {'환불 요청' if has_refund else '해지 요청' if has_cancel else '문의'}\n"
                    f"본인 확인 정보: {'전화번호 있음' if phone else '없음'}\n"
                    f"→ {'유저 조회 진행' if phone else '본인 확인 필요'}",
        ))

        if phone:
            # Step 2: 유저 검색
            result.steps.append(AgentStep(
                type="tool_call",
                content=f'search_user(phone="{phone}")',
                tool_name="search_user",
                tool_input={"phone": phone},
            ))
            user_data = self._mock_users.get(phone, self._mock_users.get("01012345678"))
            result.steps.append(AgentStep(
                type="tool_result",
                content=json.dumps(user_data, ensure_ascii=False),
                tool_name="search_user",
            ))
            result.tools_used.append("search_user")

            user_id = user_data["user_id"]

            # Step 3: 구독 조회
            result.steps.append(AgentStep(
                type="tool_call",
                content=f'get_subscriptions(user_id="{user_id}")',
                tool_name="get_subscriptions",
                tool_input={"user_id": user_id},
            ))
            subs = self._mock_subscriptions.get(user_id, [])
            result.steps.append(AgentStep(
                type="tool_result",
                content=json.dumps(subs, ensure_ascii=False),
                tool_name="get_subscriptions",
            ))
            result.tools_used.append("get_subscriptions")

            if has_refund and subs:
                # Step 4: 결제 이력
                result.steps.append(AgentStep(
                    type="tool_call",
                    content=f'get_payment_history(user_id="{user_id}")',
                    tool_name="get_payment_history",
                    tool_input={"user_id": user_id},
                ))
                payments = self._mock_payments.get(user_id, [])
                result.steps.append(AgentStep(
                    type="tool_result",
                    content=json.dumps(payments, ensure_ascii=False),
                    tool_name="get_payment_history",
                ))
                result.tools_used.append("get_payment_history")

                # Step 5: 열람 여부
                result.steps.append(AgentStep(
                    type="tool_call",
                    content=f'check_content_access(user_id="{user_id}")',
                    tool_name="check_content_access",
                    tool_input={"user_id": user_id},
                ))
                access = {"has_accessed": True, "content_view_count": 12, "last_access_date": "2026-03-28"}
                result.steps.append(AgentStep(
                    type="tool_result",
                    content=json.dumps(access),
                    tool_name="check_content_access",
                ))
                result.tools_used.append("check_content_access")

                # Step 6: 환불 계산
                if payments:
                    pay = payments[0]
                    result.steps.append(AgentStep(
                        type="tool_call",
                        content=f'calculate_refund(total_paid={pay["amount"]}, payment_date="{pay["created_at"]}", content_accessed=true)',
                        tool_name="calculate_refund",
                        tool_input={
                            "total_paid": pay["amount"],
                            "payment_date": pay["created_at"],
                            "content_accessed": True,
                        },
                    ))
                    refund_output = self._tool_calculate_refund({
                        "total_paid": pay["amount"],
                        "monthly_price": pay["amount"],
                        "payment_date": pay["created_at"],
                        "content_accessed": True,
                    })
                    result.steps.append(AgentStep(
                        type="tool_result",
                        content=refund_output,
                        tool_name="calculate_refund",
                    ))
                    result.tools_used.append("calculate_refund")

                    refund_data = json.loads(refund_output)
                    result.final_answer = (
                        f"[상담사 요약]\n"
                        f"- 고객 의도: 환불 요청\n"
                        f"- 조회 결과: {user_data['name']}님, {subs[0]['product_name']} 구독 중\n"
                        f"  결제: {pay['amount']:,}원 ({pay['created_at']}), 콘텐츠 열람: {access['content_view_count']}건\n"
                        f"- 환불 가능 여부: {'가능' if refund_data['refundable'] else '불가'}\n"
                        f"- 환불 금액: {refund_data['refund_amount']:,}원 (차감 {refund_data['deduction']:,}원 + 수수료 {refund_data['fee']:,}원)\n"
                        f"- 권장 액션: 환불 금액 안내 후 고객 확인 받기\n\n"
                        f"[답변 초안]\n"
                        f"안녕하세요 회원님, 문의 주셔서 감사합니다.\n"
                        f"확인 결과 아래와 같이 부분 환불이 가능하십니다.\n\n"
                        f"■ 결제 금액: {pay['amount']:,}원\n"
                        f"■ 차감금: {refund_data['deduction']:,}원\n"
                        f"■ 수수료: {refund_data['fee']:,}원\n"
                        f"■ 환불 금액: {refund_data['refund_amount']:,}원\n\n"
                        f"({refund_data['explanation']})\n\n"
                        f"환불 진행 도와드릴까요?"
                    )
            else:
                # 해지만 요청
                result.final_answer = (
                    f"[상담사 요약]\n"
                    f"- 고객 의도: 구독 해지 요청\n"
                    f"- 조회 결과: {user_data['name']}님, {subs[0]['product_name'] if subs else '(구독 없음)'}\n"
                    f"- 권장 액션: 구독 해지 방법 안내\n\n"
                    f"[답변 초안]\n"
                    f"안녕하세요 회원님, 다음 정기결제 구독해지 방법 안내드립니다.\n\n"
                    f"■ 정기결제 구독해지 방법\n"
                    f"① 어스플러스 앱 접속\n"
                    f"② 우측 상단 my 클릭 → 멤버십 관리 클릭\n"
                    f"③ 회색글씨로 된 <내역보기> 클릭\n"
                    f"④ 구독해지 클릭\n\n"
                    f"❗ 구독해지는 다음 정기결제에 대한 해지입니다.\n"
                    f"현재 구독 기간까지는 정상 이용 가능합니다."
                )
        else:
            # 본인 확인 필요
            if has_refund:
                result.final_answer = (
                    "[상담사 요약]\n"
                    "- 고객 의도: 환불 요청\n"
                    "- 조회 결과: 유저 식별 불가 (전화번호/이름 없음)\n"
                    "- 권장 액션: 본인 확인 후 재조회\n\n"
                    "[답변 초안]\n"
                    "안녕하세요, 어스입니다.\n"
                    "환불 관련 확인을 위해 성함과 휴대전화 번호를 말씀해 주시겠어요?\n"
                    "빠르게 확인 도와드리겠습니다."
                )
            else:
                result.final_answer = (
                    "[상담사 요약]\n"
                    "- 고객 의도: 해지/기타 요청\n"
                    "- 조회 결과: 유저 식별 불가\n"
                    "- 권장 액션: 본인 확인 요청\n\n"
                    "[답변 초안]\n"
                    "안녕하세요, 어스입니다.\n"
                    "네 회원님, 무엇을 도와드릴까요?\n"
                    "확인을 위해 성함과 휴대전화 번호를 말씀해 주시면\n"
                    "빠르게 안내드리겠습니다."
                )

        result.steps.append(AgentStep(type="answer", content=result.final_answer))
        return result
