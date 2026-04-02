"""모호한 문의 패턴 분류 + clarifying question 생성

5가지 모호 패턴:
1. 환불/해지 — 정보 부족 (의도 명확, 상품/본인확인 없음)
2. 결제 문의 — 맥락 불명확 (예상치 못한 결제, 변경 요청)
3. 기능/이용 — 추상적 (뭔가 안 되는데 뭔지 모름)
4. 맥락 없음 — 이전 대화 연속 (새 채팅인데 맥락 끊김)
5. CS 범위 밖 — 투자 조언 등

매니저 응대 패턴 (A~D):
A. 본인 확인 요청 → 환불/해지/결제변경 등 계정 조작 시
B. 상품 특정 질문 → 복수 구독 시 어떤 상품인지
C. 증상 구체화 질문 → 기술 문의에서 증상 모호할 때
D. 오픈 질문 → 맥락 완전 부재 시
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

AMBIGUOUS_CLASSIFY_PROMPT = """\
당신은 금융 교육 플랫폼 CS 상담 분석 전문가입니다.
고객의 첫 문의 메시지(들)를 읽고, 아래 기준으로 분석하세요.

## 모호 패턴 분류 (5가지)
1. **환불_해지_정보부족**: 환불/해지/취소 의도는 명확하지만, 어떤 상품인지·본인 확인 정보가 없어서 바로 처리 불가
2. **결제_맥락불명**: 결제가 됐는데 왜 됐는지 모르거나, 결제 변경을 원하지만 맥락이 부족
3. **기능_이용_추상적**: 뭔가 안 되는데 정확히 뭐가 안 되는지 설명이 부족 (로그인, 영상, 앱 등)
4. **맥락없음**: 이전 대화에서 이어지는 것 같은데 새 채팅이라 맥락이 끊김
5. **CS범위밖**: 투자 조언, 종목 추천 등 CS에서 답할 수 없는 문의
6. **모호하지_않음**: 문의 내용이 충분히 명확하여 바로 답변 가능

## 트리거 시점 분석
- 메시지가 1개인지 여러 개인지
- 여러 개면: 하나의 의도를 끊어서 보낸 건지 vs 추가 정보를 덧붙인 건지
- 모든 메시지를 봐야 의도 파악이 되는지 vs 첫 메시지만으로 충분한지

## 필요한 추가 정보
- 본인 확인 (성함/전화번호)
- 상품 특정 (어떤 과정/마스터)
- 증상 구체화 (어떤 화면/어떤 상황)
- 기타

## 출력 (JSON)
{
  "pattern": "패턴명",
  "confidence": "높음|중간|낮음",
  "reasoning": "판단 근거 1줄",
  "trigger_timing": "first_message|all_messages|wait_more",
  "trigger_explanation": "트리거 시점 판단 이유",
  "missing_info": ["필요한 추가 정보 목록"],
  "response_type": "A|B|C|D",
  "response_type_label": "본인확인요청|상품특정|증상구체화|오픈질문"
}
"""

CLARIFYING_PROMPT = """\
당신은 금융 교육 플랫폼 "어스"의 CS 상담 매니저입니다.
고객의 모호한 문의에 대해 **추가 정보를 얻기 위한 clarifying question**을 작성하세요.

## 응대 유형: {response_type_label}
## 부족한 정보: {missing_info}

## 규칙
- 친절하고 정중한 톤 (존댓말)
- 100자 이내로 간결하게
- 고객이 답하기 쉬운 구체적인 질문
- 가능하면 선택지를 제시 (예: "OO과정과 XX과정 중 어떤 과정 환불 희망하실까요?")
- "안녕하세요, 어스입니다." 로 시작

## 분석 결과
{analysis_json}

## 고객 메시지
{messages}
"""


@dataclass
class AmbiguousAnalysis:
    pattern: str
    confidence: str
    reasoning: str
    trigger_timing: str
    trigger_explanation: str
    missing_info: list[str]
    response_type: str
    response_type_label: str
    clarifying_question: str = ""
    raw_messages: list[str] = None

    @property
    def pattern_label(self) -> str:
        labels = {
            "환불_해지_정보부족": "환불/해지 — 정보 부족",
            "결제_맥락불명": "결제 문의 — 맥락 불명확",
            "기능_이용_추상적": "기능/이용 — 추상적",
            "맥락없음": "맥락 없음 (이전 대화 연속)",
            "CS범위밖": "CS 범위 밖",
            "모호하지_않음": "명확한 문의",
        }
        return labels.get(self.pattern, self.pattern)

    @property
    def trigger_label(self) -> str:
        labels = {
            "first_message": "첫 메시지에서 즉시 감지",
            "all_messages": "전체 메시지 확인 후 감지",
            "wait_more": "추가 메시지 대기 필요",
        }
        return labels.get(self.trigger_timing, self.trigger_timing)


class AmbiguousClassifier:
    def __init__(self, region: str = "us-west-2", model_id: str = None, mock: bool = False):
        self.mock = mock
        self.model_id = model_id or MODEL_ID
        if not mock:
            self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        else:
            self.bedrock = None

    def _call_bedrock(self, system: str, user_text: str, max_tokens: int = 800) -> str:
        resp = self.bedrock.invoke_model(
            modelId=self.model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_text}],
            }),
        )
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"].strip()

    def analyze(self, messages: list[str]) -> AmbiguousAnalysis:
        """메시지 목록을 받아 모호 패턴 분석 수행"""
        formatted = "\n".join(f"[메시지 {i+1}] {m}" for i, m in enumerate(messages))

        if self.mock or not self.bedrock:
            analysis = self._mock_classify(messages)
        else:
            try:
                raw = self._call_bedrock(AMBIGUOUS_CLASSIFY_PROMPT, formatted)
                analysis = self._parse_json(raw)
            except Exception as e:
                logger.warning(f"분류 API 실패, mock fallback: {e}")
                analysis = self._mock_classify(messages)

        # clarifying question 생성
        if analysis.get("pattern") != "모호하지_않음":
            if self.mock or not self.bedrock:
                cq = self._mock_clarifying(analysis, messages)
            else:
                try:
                    cq = self._generate_clarifying(analysis, messages)
                except Exception as e:
                    logger.warning(f"clarifying 생성 실패: {e}")
                    cq = self._mock_clarifying(analysis, messages)
        else:
            cq = ""

        return AmbiguousAnalysis(
            pattern=analysis.get("pattern", "미분류"),
            confidence=analysis.get("confidence", "중간"),
            reasoning=analysis.get("reasoning", ""),
            trigger_timing=analysis.get("trigger_timing", "first_message"),
            trigger_explanation=analysis.get("trigger_explanation", ""),
            missing_info=analysis.get("missing_info", []),
            response_type=analysis.get("response_type", "D"),
            response_type_label=analysis.get("response_type_label", "오픈질문"),
            clarifying_question=cq,
            raw_messages=messages,
        )

    def _generate_clarifying(self, analysis: dict, messages: list[str]) -> str:
        formatted = "\n".join(f"[메시지 {i+1}] {m}" for i, m in enumerate(messages))
        prompt = CLARIFYING_PROMPT.format(
            response_type_label=analysis.get("response_type_label", "오픈질문"),
            missing_info=", ".join(analysis.get("missing_info", [])),
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            messages=formatted,
        )
        return self._call_bedrock("", prompt, max_tokens=300)

    def _parse_json(self, raw: str) -> dict:
        # JSON 블록 추출
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        # 앞뒤 공백 + 가끔 붙는 텍스트 제거
        raw = raw.strip()
        if raw.startswith("{"):
            return json.loads(raw)
        # JSON 시작점 찾기
        idx = raw.find("{")
        if idx >= 0:
            return json.loads(raw[idx:])
        return {}

    def _mock_classify(self, messages: list[str]) -> dict:
        """키워드 기반 mock 분류"""
        full = " ".join(messages).lower()
        msg_count = len(messages)

        if any(kw in full for kw in ["환불", "해지", "취소", "탈퇴", "자동결제", "자동결재"]):
            has_identity = any(kw in full for kw in ["010", "이름", "성함"])
            has_product = any(kw in full for kw in ["과정", "마스터", "클래스"])
            missing = []
            if not has_identity:
                missing.append("본인 확인 (성함/전화번호)")
            if not has_product:
                missing.append("상품 특정 (어떤 과정)")
            return {
                "pattern": "환불_해지_정보부족",
                "confidence": "높음",
                "reasoning": "환불/해지 의도 명확하나 " + ("상품/본인 정보 부족" if missing else "정보 충분"),
                "trigger_timing": "first_message" if msg_count == 1 else "all_messages",
                "trigger_explanation": f"{'단일 메시지로 의도 파악 가능' if msg_count == 1 else f'{msg_count}개 메시지를 종합해 판단'}",
                "missing_info": missing or ["상품 특정"],
                "response_type": "A" if not has_identity else "B",
                "response_type_label": "본인확인요청" if not has_identity else "상품특정",
            }

        if any(kw in full for kw in ["결제되", "결재", "금액", "할부", "결제일"]):
            return {
                "pattern": "결제_맥락불명",
                "confidence": "높음",
                "reasoning": "결제 관련 문의이나 구체적 맥락 부족",
                "trigger_timing": "first_message" if msg_count == 1 else "all_messages",
                "trigger_explanation": f"결제 키워드 감지, {'추가 메시지에서 상세 정보 확인' if msg_count > 1 else '단일 메시지'}",
                "missing_info": ["결제 상세 (어떤 결제인지)", "본인 확인"],
                "response_type": "A",
                "response_type_label": "본인확인요청",
            }

        if any(kw in full for kw in ["안돼", "안됩니다", "안 돼", "안되", "못해", "열리지", "끊겨", "오류"]):
            return {
                "pattern": "기능_이용_추상적",
                "confidence": "중간",
                "reasoning": "기능/이용 관련 문의이나 증상이 추상적",
                "trigger_timing": "first_message" if msg_count == 1 else "all_messages",
                "trigger_explanation": "증상 키워드 감지, 구체적 상황 설명 부족",
                "missing_info": ["증상 구체화 (어떤 화면/상황)", "기기/환경 정보"],
                "response_type": "C",
                "response_type_label": "증상구체화",
            }

        if any(kw in full for kw in ["어디", "어떻게", "언제", "뭐", "무엇"]):
            return {
                "pattern": "기능_이용_추상적",
                "confidence": "중간",
                "reasoning": "이용 방법 문의이나 대상이 불분명",
                "trigger_timing": "first_message",
                "trigger_explanation": "단순 질문형이지만 대상 특정 필요",
                "missing_info": ["문의 대상 특정"],
                "response_type": "C",
                "response_type_label": "증상구체화",
            }

        if len(full) < 30:
            return {
                "pattern": "맥락없음",
                "confidence": "높음",
                "reasoning": "메시지가 매우 짧아 맥락 파악 불가",
                "trigger_timing": "wait_more" if msg_count == 1 else "all_messages",
                "trigger_explanation": "맥락 부족, 추가 메시지 필요할 수 있음",
                "missing_info": ["문의 목적", "본인 확인"],
                "response_type": "D",
                "response_type_label": "오픈질문",
            }

        return {
            "pattern": "맥락없음",
            "confidence": "낮음",
            "reasoning": "명확한 패턴 매칭 불가",
            "trigger_timing": "all_messages",
            "trigger_explanation": "전체 메시지를 봐도 의도 불분명",
            "missing_info": ["문의 목적"],
            "response_type": "D",
            "response_type_label": "오픈질문",
        }

    def _mock_clarifying(self, analysis: dict, messages: list[str]) -> str:
        """패턴별 mock clarifying question"""
        pattern = analysis.get("pattern", "")
        resp_type = analysis.get("response_type", "D")

        if resp_type == "A":
            return (
                "안녕하세요, 어스입니다.\n"
                "문의 주셔서 감사합니다.\n"
                "확인을 위해 성함과 휴대전화 번호를 말씀해 주시겠어요?\n"
                "빠르게 확인 도와드리겠습니다."
            )
        if resp_type == "B":
            return (
                "안녕하세요, 어스입니다.\n"
                "현재 여러 과정을 수강하고 계신 것으로 보이는데요,\n"
                "어떤 과정에 대한 문의이신지 말씀해 주시겠어요?"
            )
        if resp_type == "C":
            return (
                "안녕하세요, 어스입니다.\n"
                "불편을 드려 죄송합니다.\n"
                "정확한 확인을 위해 어떤 화면에서 어떤 증상이 발생하는지\n"
                "조금 더 자세히 말씀해 주시겠어요?"
            )
        # D: 오픈질문
        return (
            "안녕하세요, 어스입니다.\n"
            "네 회원님, 무엇을 도와드릴까요?\n"
            "문의 내용을 말씀해 주시면 빠르게 안내드리겠습니다."
        )
