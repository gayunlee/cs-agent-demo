"""문의 유형 분류 + 정보 충분성 판단 (LLM 기반)

Bedrock Haiku로 분류:
  1. 유형: 환불/해지, 결제/과금, 로그인/계정, 기술오류, 강의/수강, 카드변경, 구독/가입, 기타
  2. 정보 충분성: 바로 답변 가능? / 부족하면 뭐가?
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

import boto3

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

CLASSIFY_PROMPT = """\
당신은 금융 교육 플랫폼 CS 문의 분류기입니다.
유저 메시지를 읽고 아래를 판단하세요.

## 문의 유형
1. 환불/해지 — 환불, 해지, 취소, 탈퇴, 자동결제 취소
2. 결제/과금 — 결제 금액, 결제일, 할부, 이중결제, 상품변경 결제
3. 로그인/계정 — 로그인 안됨, 비밀번호, 계정 변경, 가입 방법
4. 기술오류 — 영상 재생 안됨, 앱 오류, 접속 불가, 소리 안남
5. 강의/수강 — 수업 일정, 녹화본, 줌 입장, 오프라인 수업, 교재
6. 카드변경 — 카드 분실, 카드 변경, 카드 만료
7. 구독/가입 — 신규 가입, 멤버십 신청, 상품 문의, 구독 변경
8. 기타 — 위에 해당 없음

## 정보 충분성
- 충분(true): 이 메시지만으로 바로 답변 가능
- 부족(false): 추가 정보 필요 → 뭐가 부족한지 간결하게

## 출력 (JSON만, 다른 텍스트 없이)
{"type": "유형", "sufficient": true/false, "missing": "부족한 정보"}
"""


@dataclass
class ClassificationResult:
    inquiry_type: str
    sufficient: bool
    missing: str = ""
    raw: dict | None = None


class InquiryClassifier:
    def __init__(self, region: str = "us-west-2", model_id: str = None):
        self.model_id = model_id or MODEL_ID
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)

    def classify(self, message: str) -> ClassificationResult:
        try:
            resp = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 200,
                    "system": CLASSIFY_PROMPT,
                    "messages": [{"role": "user", "content": message}],
                }),
            )
            raw_text = json.loads(resp["body"].read())["content"][0]["text"].strip()
            data = self._parse_json(raw_text)

            return ClassificationResult(
                inquiry_type=data.get("type", "기타"),
                sufficient=data.get("sufficient", False),
                missing=data.get("missing", ""),
                raw=data,
            )
        except Exception as e:
            logger.error(f"분류 실패: {e}")
            return ClassificationResult(inquiry_type="기타", sufficient=False, missing=str(e))

    def classify_batch(self, messages: list[str]) -> list[ClassificationResult]:
        return [self.classify(msg) for msg in messages]

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
