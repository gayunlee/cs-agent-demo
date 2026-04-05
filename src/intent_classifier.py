"""LLM Intent Classifier — 환불/해지 문의 유형 분류

워크플로우의 키워드 기반 분기를 대체한다. LLM이 유저 메시지와 대화 맥락을
읽고 유형을 결정. 실제 처리(API 조회, 계산, 답변 생성)는 여전히 코드가 담당.

**트러스트 경계**:
- LLM: "유저 의도가 뭔지" 분류만
- 코드: 정책 판단, 금액 계산, 데이터 조회, 답변 생성 (paymentCycle 사건 교훈)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# 분류 enum — workflow.py에서 분기 조건으로 사용
INTENT_ENUM = [
    "refund_request",          # T2 계산 경로 후보: "환불해주세요"
    "cancel_method_inquiry",   # T1: "해지 방법 알려주세요"
    "cancel_check",            # T7: "해지 처리 됐나요?"
    "auto_payment_complaint",  # T4: "자동결제 됐는데 취소해주세요"
    "card_change",             # T8: "카드 바꾸고 싶어요"
    "product_change",          # T10: "다른 상품으로 바꿔주세요"
    "duplicate_payment",       # T11: "중복 결제됐어요"
    "refund_delay",            # T12: "환불 언제 돼요?"
    "refund_withdrawal",       # edge: "환불 취소할게요, 계속 쓸게요"
    "exception_refund",        # edge: "건강 사유로 예외 환불"
    "emotional_escalation",    # edge: 강한 감정/불만
    "system_error",            # edge: "앱 오류", "로그인 안 돼"
    "compound_issue",          # edge: 여러 도메인 엮임
    "other",                   # 위 어느 것에도 해당 안 됨 → LLM fallback
]

SYSTEM_PROMPT = """\
당신은 어스플러스(금융 교육 플랫폼) CS 문의의 **의도 분류기**입니다.
유저 메시지와 대화 맥락을 읽고 아래 enum 중 **정확히 하나**를 선택합니다.

## 분류 enum (반드시 이 중 하나만)
- **refund_request**: 환불 요청 ("환불해주세요", "돈 돌려주세요")
- **cancel_method_inquiry**: 해지 방법 문의 ("해지하려면 어떻게?", "다음 결제 해지")
- **cancel_check**: 이미 해지 신청한 상태의 처리 확인 ("해지 처리 됐나요?", "해지신청했는데 확인")
- **auto_payment_complaint**: 자동/정기결제 불만 ("자동으로 결제됐어요", "연장되어 결제", "왜 결제됐죠?")
- **card_change**: 카드 관련 변경 ("카드 바꾸고 싶어요", "결제 방법 변경", "카드 만료")
- **product_change**: 상품 변경 요청 ("다른 상품으로 바꿔", "상품 변경 차액 환불")
- **duplicate_payment**: 중복/이중 결제 ("두 번 결제됐어요", "중복 결제")
- **refund_delay**: 이미 접수된 환불 지연/상태 문의 ("환불 언제 되나요?", "환불 안 왔어요")
- **refund_withdrawal**: 환불 요청 철회 ("환불 취소할게요", "다시 이용할래요")
- **exception_refund**: 규정 외 예외 환불 요청 ("건강 때문에", "특별히", "전액 환불해주세요")
- **emotional_escalation**: 강한 불만/감정 표현 ("화가 나요", "사기", "소비자원 신고")
- **system_error**: 기술 오류 ("앱 오류", "로그인 안 돼", "라이브 입장 불가")
- **compound_issue**: 여러 문제 동시 ("환불도 받고 다른 것도")
- **other**: 위 어느 것에도 명확히 해당 안 됨

## 판단 규칙
1. **유저 메시지의 핵심 의도**를 본다. 표면 키워드에 현혹되지 말고 문맥으로 판단.
   - "환불 신청했는데 취소할게요" → **refund_withdrawal** (환불 요청 아님)
   - "해지신청했는데 처리 됐나요?" → **cancel_check** (해지 방법 문의 아님)
   - "카드로 결제방법 변경" → **card_change** (product_change 아님)
2. 이전 대화 맥락도 고려. 매니저가 이미 환불 견적 안내한 후 유저가 "네 진행해주세요" → refund_request (T2 후속)
3. 여러 의도가 섞이면 **가장 중심 되는 것** 하나 선택. 진짜 여러 도메인이면 compound_issue.
4. 애매하면 other, 뻔하지 않은 경우 other를 두려워하지 말 것.

## 출력 형식 (JSON only, 다른 텍스트 없이)
{"intent": "<enum 값>", "confidence": "high|medium|low", "reason": "한 줄 근거"}
"""


@dataclass
class IntentResult:
    intent: str = "other"
    confidence: str = "low"
    reason: str = ""
    error: str = ""

    @property
    def is_valid(self) -> bool:
        return self.intent in INTENT_ENUM and not self.error


class IntentClassifier:
    def __init__(self, region: str = "us-west-2", model_id: Optional[str] = None, mock: bool = False):
        self.mock = mock
        self.model_id = model_id or MODEL_ID
        self.bedrock = None if mock else boto3.client("bedrock-runtime", region_name=region)

    def classify(
        self,
        user_messages: list[str],
        conversation_turns: Optional[list[dict]] = None,
    ) -> IntentResult:
        """유저 의도 분류.

        Args:
            user_messages: 이번 턴 유저 메시지들 (여러 fragment 가능)
            conversation_turns: 같은 대화방의 이전 턴들 (context)
        """
        if self.mock or self.bedrock is None:
            return self._mock_classify(user_messages, conversation_turns or [])

        prompt = self._build_prompt(user_messages, conversation_turns or [])
        try:
            resp = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 200,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
            data = self._parse_json(raw)
            intent = data.get("intent", "other")
            if intent not in INTENT_ENUM:
                logger.warning(f"LLM returned invalid intent: {intent}, falling back to other")
                intent = "other"
            return IntentResult(
                intent=intent,
                confidence=data.get("confidence", "medium"),
                reason=data.get("reason", ""),
            )
        except Exception as e:
            logger.error(f"intent classify 실패: {e}")
            return IntentResult(intent="other", confidence="low", error=str(e))

    def _build_prompt(self, user_messages: list[str], conversation_turns: list[dict]) -> str:
        lines = ["## 이번 턴 유저 메시지"]
        for m in user_messages:
            lines.append(f"- {m}")

        if conversation_turns:
            lines.append("\n## 이전 대화 맥락 (최근 8개 턴)")
            for t in conversation_turns[-8:]:
                role = t.get("role", "?")
                text = (t.get("text") or "").replace("\n", " ")[:200]
                lines.append(f"[{role}] {text}")

        lines.append("\n위 메시지의 의도를 분류하세요. JSON 1개만 출력.")
        return "\n".join(lines)

    def _parse_json(self, raw: str) -> dict:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        raw = raw.strip()
        idx = raw.find("{")
        if idx >= 0:
            end = raw.rfind("}") + 1
            try:
                return json.loads(raw[idx:end])
            except json.JSONDecodeError:
                pass
        return {"intent": "other", "confidence": "low"}

    def _mock_classify(self, user_messages: list[str], conversation_turns: list[dict]) -> IntentResult:
        """Mock 모드 — 간단한 키워드 기반 대체. 테스트/로컬 개발용.

        실제 운영은 LLM 경로 사용. 이건 Bedrock 호출 없이도 회귀 테스트가 돌게 하기 위한 fallback.
        """
        text = " ".join(user_messages)

        # 순서 중요 — 더 구체적인 것부터
        if any(kw in text for kw in ["환불 취소", "환불취소", "환불 안 할", "다시 이용", "계속 이용"]):
            return IntentResult(intent="refund_withdrawal", confidence="medium", reason="mock: withdrawal")
        if "해지" in text and any(kw in text for kw in ["처리 되었", "처리됐", "처리 됐", "확인", "됐나", "됐는지", "신청했는데"]):
            return IntentResult(intent="cancel_check", confidence="medium", reason="mock: cancel_check")
        if any(kw in text for kw in ["카드 변경", "카드변경", "카드 바꾸", "새 카드", "카드 해지", "카드 만료", "결제 방법 변경", "결제방법 변경"]):
            return IntentResult(intent="card_change", confidence="medium", reason="mock: card")
        if any(kw in text for kw in ["자동으로 결제", "자동결제", "자동결재", "자동으로 구독", "연장되어", "연장됐"]):
            return IntentResult(intent="auto_payment_complaint", confidence="medium", reason="mock: auto")
        if any(kw in text for kw in ["중복", "이중", "두 번 결제", "두번 결제"]):
            return IntentResult(intent="duplicate_payment", confidence="medium", reason="mock: duplicate")
        if any(kw in text for kw in ["상품 변경", "상품변경", "다른 상품", "바꿔서", "으로 변경"]) and "카드" not in text:
            return IntentResult(intent="product_change", confidence="medium", reason="mock: product_change")
        if any(kw in text for kw in ["환불 언제", "언제 돼", "아직 안", "안 왔", "처리 안", "처리안", "왜 아직", "2주 넘", "일주일 넘"]) and "환불" in text:
            return IntentResult(intent="refund_delay", confidence="medium", reason="mock: delay")
        # 전부 환불 후 재가입/재결제 문의 → other (LLM fallback)
        if any(kw in text for kw in ["다시 가입", "재가입", "재결제", "다시 결제", "새로 가입"]) and "환불" in text:
            return IntentResult(intent="other", confidence="medium", reason="mock: post_refund_question")
        if any(kw in text for kw in ["건강", "병원", "수술", "사정", "예외", "특별"]) and "환불" in text:
            return IntentResult(intent="exception_refund", confidence="medium", reason="mock: exception")
        if any(kw in text for kw in ["해지", "구독 취소", "구독취소", "탈퇴", "그만"]) and "환불" not in text:
            return IntentResult(intent="cancel_method_inquiry", confidence="medium", reason="mock: cancel_method")
        if any(kw in text for kw in ["환불", "돈 돌려", "취소"]):
            return IntentResult(intent="refund_request", confidence="medium", reason="mock: refund")

        return IntentResult(intent="other", confidence="low", reason="mock: fallthrough")
