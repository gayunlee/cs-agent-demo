"""LLM fallback — workflow의 어떤 branch에도 매칭되지 않은 환불 문의에 대한 자유 답변 생성

원칙: "항상 초안 생성". 규정 기반 템플릿이 적용되지 않는 edge 케이스여도
조회된 데이터와 대화 맥락을 LLM에 넘겨 상담사가 검토할 수 있는 초안을 생성한다.

초안에는 확신이 낮을 때 ⚠️ 태그가 붙어 상담사가 판단할 근거를 제공.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = """\
당신은 어스플러스(금융 교육 플랫폼)의 CS 어시스턴트입니다.
상담사가 검토 후 고객에게 전송할 **내부노트 초안**을 작성합니다.

## 환불 정책 핵심
- 7일 이내 + 구독권 미개시(미열람) → 전액 환불
- 7일 이내 + 열람 시작 → 1개월 정가 차감 + 잔여금의 10% 수수료 차감 후 환불
- 7일 경과 → 동일하게 1개월 정가 차감 + 10% 수수료
- 구독해지(다음 결제 중단)는 앱/웹 MY → 멤버십 관리 → 구독해지 경로

## 작성 지침
1. 공손하고 간결한 어투 ("안녕하세요 회원님, ..." 시작)
2. 조회된 데이터(결제 내역, 열람 여부, 환불 이력)를 근거로 설명
3. **확신이 없거나 정책에 없는 예외 상황이면, 답변 맨 앞에 "⚠️ 정책 밖 패턴 — 상담사 확인 필요" 한 줄 추가**
4. 상담사가 판단하기 쉽도록 근거와 제안 처리 방향을 명확히
5. 고객에게 추가 정보가 필요하면 정중하게 질문
6. 200~400자 범위

## 출력 형식 (JSON, 다른 텍스트 없이)
{"draft": "초안 텍스트", "confidence": "high|medium|low", "reason": "이 초안을 고른 근거"}
"""


@dataclass
class FallbackResult:
    draft: str
    confidence: str = "medium"
    reason: str = ""
    error: str = ""


class LLMFallback:
    def __init__(self, region: str = "us-west-2", model_id: str | None = None):
        self.model_id = model_id or MODEL_ID
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)

    def generate(
        self,
        user_messages: list[str],
        conversation_turns: list[dict],
        context_data: dict,
    ) -> FallbackResult:
        """자유 답변 초안 생성

        user_messages: 이번 턴 유저 메시지들
        conversation_turns: 이전 대화 이력 [{role, ts, text}, ...]
        context_data: 조회된 데이터 (success_txs, refund_txs, memberships, refunds, has_accessed 등)
        """
        prompt = self._build_prompt(user_messages, conversation_turns, context_data)
        try:
            resp = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 800,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
            data = self._parse_json(raw)
            return FallbackResult(
                draft=data.get("draft", ""),
                confidence=data.get("confidence", "medium"),
                reason=data.get("reason", ""),
            )
        except Exception as e:
            logger.error(f"LLM fallback 실패: {e}")
            return FallbackResult(
                draft="⚠️ 초안 생성 실패 — 상담사가 직접 확인 후 답변해 주세요.",
                confidence="low",
                error=str(e),
            )

    def _build_prompt(
        self,
        user_messages: list[str],
        conversation_turns: list[dict],
        context_data: dict,
    ) -> str:
        lines = ["## 이번 턴 유저 메시지"]
        for m in user_messages:
            lines.append(f"- {m}")

        if conversation_turns:
            lines.append("\n## 이전 대화 이력 (최근 10개)")
            for t in conversation_turns[-10:]:
                role = t.get("role", "?")
                text = (t.get("text") or "").replace("\n", " ")[:200]
                lines.append(f"[{role}] {text}")

        lines.append("\n## 조회된 유저 데이터")
        success_txs = context_data.get("success_txs") or []
        refund_txs = context_data.get("refund_txs") or []
        refunds = context_data.get("refunds") or []
        lines.append(f"- 결제 성공 건수: {len(success_txs)}")
        for t in success_txs[-5:]:
            amt = t.get("amount", 0)
            dt = (t.get("date") or t.get("created_at") or "")[:10]
            lines.append(f"  · {dt} / {amt}원 / {t.get('round', '?')}회차")
        lines.append(f"- 환불 완료 건수: {len(refund_txs)}")
        lines.append(f"- 환불 이력(진행중 포함): {len(refunds)}")
        for r in refunds[-3:]:
            lines.append(f"  · {r.get('productName', '')} / 접수 {r.get('createdAt', '')[:10]}")
        lines.append(f"- 콘텐츠 열람 여부: {context_data.get('has_accessed', False)}")

        memberships = context_data.get("memberships") or []
        if memberships:
            lines.append(f"- 멤버십: {len(memberships)}건")

        lines.append("\n위 정보를 바탕으로 초안을 작성하세요.")
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
        return {"draft": raw, "confidence": "low"}
