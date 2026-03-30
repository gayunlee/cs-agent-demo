"""CS AI 에이전트 코어 — 의도 분류 + 정보 조회 + 답변 생성 (Bedrock)"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

import boto3

from src.tools import simulate_lookup, LookupResult
from src.rag import AnswerRAG

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

TEMPLATES = {
    "해지_방법": {
        "id": 1,
        "triggers": ["해지", "취소", "구독해지", "구독취소"],
        "content": (
            "■ 정기결제 구독해지 방법\n"
            "① 어스 앱 접속\n"
            "② 우측 상단 사람모양(마이페이지) 클릭\n"
            "③ '구독관리' 메뉴에서 해지하실 수 있습니다.\n\n"
            "해지 후에도 남은 구독 기간까지는 정상 이용 가능합니다."
        ),
    },
    "카드_변경": {
        "id": 2,
        "triggers": ["카드 변경", "카드변경", "카드 분실", "카드 만료"],
        "content": (
            "▶ 결제카드 변경 방법\n"
            "1. https://us-insight.com/ 웹페이지 접속\n"
            "2. 로그인 후 마이페이지 > 결제관리\n"
            "3. 카드 변경 버튼 클릭\n\n"
            "※ 프로모션 가격으로 구독 중이신 경우, 카드 변경 후에도 동일 가격이 유지됩니다."
        ),
    },
    "비밀번호_재설정": {
        "id": 6,
        "triggers": ["비밀번호", "비번", "패스워드"],
        "content": (
            "비밀번호 재설정 방법입니다.\n"
            "① 로그인 화면에서 '비밀번호 찾기' 클릭\n"
            "② 가입하신 이메일/휴대폰 번호 입력\n"
            "③ 인증 후 새 비밀번호 설정"
        ),
    },
    "PC_이용": {
        "id": 5,
        "triggers": ["PC에서", "컴퓨터로", "PC로", "웹에서"],
        "content": (
            "PC에서 이용하시려면 https://us-insight.com/ 에 접속하여\n"
            "앱과 동일한 계정으로 로그인해 주세요.\n"
            "일부 콘텐츠는 앱에서만 제공될 수 있습니다."
        ),
    },
    "환불_안내": {
        "id": 3,
        "triggers": ["환불", "취소하고 싶", "돈 돌려"],
        "content": (
            "환불 관련 안내드립니다.\n"
            "■ 결제일로부터 7일 이내 + 콘텐츠 미열람: 전액 환불\n"
            "■ 결제일로부터 7일 이내 + 콘텐츠 열람: 이용일수 차감 후 부분 환불\n"
            "■ 결제일로부터 7일 경과: 부분 환불 (잔여 기간 일할 계산)\n\n"
            "정확한 환불 금액은 확인 후 안내드리겠습니다."
        ),
    },
}

CLASSIFY_PROMPT = """\
당신은 금융 교육 플랫폼 CS 상담 분류 전문가입니다.
고객의 채널톡 문의를 읽고 아래 4가지 중 하나로 분류하세요.

## 분류 기준
1. **결제·환불**: 결제, 환불, 카드변경, 프로모션 가격, 이중결제
2. **구독·멤버십**: 구독해지, 가입방법, 상품변경, 업그레이드/다운그레이드
3. **콘텐츠·수강**: 강의 일정, 녹화본, 수업 내용, 플랫폼 이용법, PDF, 줌 접속
4. **기술·오류**: 로그인 실패, 영상 재생 오류, 앱 오류, 버그

## 출력 형식
분류: [카테고리명]
신뢰도: [높음/중간/낮음]
근거: [1줄 설명]
"""

DRAFT_PROMPT = """\
당신은 금융 교육 플랫폼 "어스"의 CS 상담 매니저입니다.
고객 문의에 대한 답변 초안을 작성하세요.

## 규칙
- 친절하고 정중한 톤 (존댓말)
- 200자 이내로 간결하게
- 구체적인 안내 (단계별 방법, 링크 등)
- 확인이 필요한 경우 "확인 후 안내드리겠습니다"로 마무리
- 투자 조언 절대 금지
- 환불은 직접 처리하지 않고 "확인 후 안내" 형태로

## 조회된 고객 정보
{lookup_info}

## 참고 답변 패턴
{reference_answers}

## 고객 문의
{conversation}
"""


@dataclass
class AgentResponse:
    chat_id: str
    category: str
    confidence: str
    reasoning: str
    draft_answer: str
    template_matched: str | None
    action: str
    lookup: LookupResult | None = None
    rag_matches: list = None  # RAG 검색 결과


class CSAgent:
    def __init__(self, region: str = "us-west-2", model_id: str = None, mock: bool = False):
        self.mock = mock
        self.model_id = model_id or MODEL_ID
        if not mock:
            self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        else:
            self.bedrock = None
        self.rag = None  # lazy init

    def _get_rag(self) -> AnswerRAG | None:
        if self.rag is None:
            try:
                self.rag = AnswerRAG()
                count = self.rag.index_if_needed()
                logger.info(f"RAG 인덱스: {count}건")
            except Exception as e:
                logger.warning(f"RAG 초기화 실패: {e}")
                self.rag = None
        return self.rag

    def _call_bedrock(self, system: str, user_text: str, max_tokens: int = 500) -> str:
        """Bedrock invoke_model 호출"""
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

    def classify(self, text: str) -> dict:
        """의도 분류"""
        if self.mock or not self.bedrock:
            return self._mock_classify(text)
        try:
            raw = self._call_bedrock(CLASSIFY_PROMPT, text[:2000], max_tokens=200)
            return self._parse_classification(raw)
        except Exception as e:
            logger.warning(f"분류 API 실패, mock fallback: {e}")
            return self._mock_classify(text)

    def match_template(self, text: str) -> tuple:
        """키워드 기반 템플릿 매칭"""
        text_lower = text.lower()
        for key, tmpl in TEMPLATES.items():
            for trigger in tmpl["triggers"]:
                if trigger in text_lower:
                    return key, tmpl["content"]
        return None, None

    def generate_draft(self, text: str, reference_answers: str = "", lookup_info: str = "") -> str:
        """LLM 답변 초안 생성"""
        if self.mock or not self.bedrock:
            return self._mock_draft(text)
        try:
            prompt = DRAFT_PROMPT.format(
                lookup_info=lookup_info or "(조회 정보 없음)",
                reference_answers=reference_answers or "(없음)",
                conversation=text[:3000],
            )
            return self._call_bedrock("", prompt, max_tokens=500)
        except Exception as e:
            logger.warning(f"답변 생성 API 실패, mock fallback: {e}")
            return self._mock_draft(text)

    def process(self, chat_id: str, text: str) -> AgentResponse:
        """전체 파이프라인: 분류 → RAG 검색 → 정보 조회 → 초안 생성"""
        # 1. 분류
        classification = self.classify(text)
        category = classification.get("category", "미분류")

        # 2. RAG — 유사 과거 대화에서 매니저 답변 검색
        rag = self._get_rag()
        rag_matches = []
        ref_text = ""
        if rag:
            rag_matches = rag.search(text[:500], n_results=3)
            ref_text = rag.format_for_prompt(rag_matches)

        # 3. 고객 정보 조회 (시뮬레이션)
        lookup = simulate_lookup(text, category)
        lookup_info = lookup.to_display()

        # 4. 템플릿 매칭
        tmpl_key, tmpl_content = self.match_template(text)

        # 5. 행동 결정 + 답변 생성
        if tmpl_content and classification.get("confidence") == "높음":
            action = "auto_template"
            draft = tmpl_content
        elif classification.get("confidence") == "낮음":
            action = "escalate"
            draft = self.generate_draft(text, ref_text, lookup_info)
        else:
            action = "llm_draft"
            draft = self.generate_draft(text, ref_text, lookup_info)

        return AgentResponse(
            chat_id=chat_id,
            category=category,
            confidence=classification.get("confidence", "중간"),
            reasoning=classification.get("reasoning", ""),
            draft_answer=draft,
            template_matched=tmpl_key,
            action=action,
            lookup=lookup,
            rag_matches=rag_matches,
        )

    def _mock_classify(self, text: str) -> dict:
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["환불", "결제", "카드", "금액", "프로모션", "이중결제"]):
            return {"category": "결제·환불", "confidence": "높음", "reasoning": "환불/결제 관련 키워드 감지"}
        if any(kw in text_lower for kw in ["해지", "구독", "가입", "변경", "업그레이드"]):
            return {"category": "구독·멤버십", "confidence": "높음", "reasoning": "구독/해지 관련 키워드 감지"}
        if any(kw in text_lower for kw in ["강의", "수업", "녹화", "pdf", "줌", "zoom", "다운로드", "플랫폼"]):
            return {"category": "콘텐츠·수강", "confidence": "중간", "reasoning": "콘텐츠/수강 관련 키워드 감지"}
        if any(kw in text_lower for kw in ["로그인", "오류", "안 돼", "안돼", "버그", "재생"]):
            return {"category": "기술·오류", "confidence": "중간", "reasoning": "기술 오류 관련 키워드 감지"}
        return {"category": "기타", "confidence": "낮음", "reasoning": "명확한 의도 파악 어려움 (mock 분류)"}

    def _mock_draft(self, text: str) -> str:
        _, tmpl = self.match_template(text)
        if tmpl:
            return tmpl
        return (
            "안녕하세요, 어스입니다.\n"
            "문의해 주신 내용 확인하였습니다.\n"
            "담당자 확인 후 빠르게 안내드리겠습니다.\n"
            "감사합니다.\n\n"
            "[⚠️ mock 모드 — 실제 LLM 생성이 아닌 기본 응답입니다]"
        )

    def _parse_classification(self, text: str) -> dict:
        result = {"category": "미분류", "confidence": "중간", "reasoning": ""}
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("분류:"):
                result["category"] = line.replace("분류:", "").strip()
            elif line.startswith("신뢰도:"):
                result["confidence"] = line.replace("신뢰도:", "").strip()
            elif line.startswith("근거:"):
                result["reasoning"] = line.replace("근거:", "").strip()
        return result
