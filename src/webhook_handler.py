"""채널톡 webhook 수신 + 라우팅"""
from __future__ import annotations
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

from src.admin_api import AdminAPIClient, LookupResult
from src.dropout_detector import DropoutDetector, DropoutType
from src.agent import CSAgent

logger = logging.getLogger(__name__)

app = FastAPI(title="CS AI Agent — Webhook Receiver")

WEBHOOK_SECRET = os.getenv("CHANNELTALK_WEBHOOK_SECRET", "")

# 싱글턴
_admin_client: AdminAPIClient | None = None
_dropout_detector: DropoutDetector | None = None
_agent: CSAgent | None = None


def get_admin_client() -> AdminAPIClient:
    global _admin_client
    if _admin_client is None:
        _admin_client = AdminAPIClient()
    return _admin_client


def get_dropout_detector() -> DropoutDetector:
    global _dropout_detector
    if _dropout_detector is None:
        _dropout_detector = DropoutDetector()
    return _dropout_detector


def get_agent() -> CSAgent:
    global _agent
    if _agent is None:
        _agent = CSAgent(mock=True)  # 데모: mock 모드 (Bedrock 없이)
    return _agent


# ── Webhook 검증 ──

def verify_signature(body: bytes, signature: str) -> bool:
    """채널톡 webhook 서명 검증"""
    if not WEBHOOK_SECRET:
        return True  # 데모 모드: 시크릿 미설정 시 검증 스킵
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── 이벤트 파싱 ──

class ParsedEvent:
    """채널톡 webhook 이벤트를 파싱한 결과"""
    def __init__(self, raw: dict):
        self.raw = raw
        self.event_type = raw.get("event", raw.get("type", ""))
        self.refers = raw.get("refers", {})

        # 채팅 정보
        chat = self.refers.get("chat", raw.get("entity", {}).get("chat", {}))
        self.chat_id = str(chat.get("id", raw.get("chatId", "")))

        # 유저 정보
        user = self.refers.get("user", raw.get("entity", {}).get("user", {}))
        self.member_id = str(user.get("memberId", user.get("id", "")))
        self.phone = user.get("mobileNumber", user.get("phoneNumber", ""))
        self.user_name = user.get("name", user.get("profile", {}).get("name", ""))

        # 메시지 내용
        message = raw.get("entity", {}).get("plainText", "")
        if not message:
            message = raw.get("entity", {}).get("message", {}).get("plainText", "")
        self.message = message

        # 봇 관련
        self.is_bot_message = raw.get("entity", {}).get("personType") == "bot"
        self.plugin_key = raw.get("entity", {}).get("pluginKey", "")
        self.buttons_clicked = self._extract_buttons(raw)

    def _extract_buttons(self, raw: dict) -> list[str]:
        """워크플로우 봇 버튼 클릭 추출"""
        buttons = []
        options = raw.get("entity", {}).get("options", [])
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    buttons.append(opt.get("text", opt.get("value", "")))
                elif isinstance(opt, str):
                    buttons.append(opt)
        submit = raw.get("entity", {}).get("submittedInputs", {})
        if submit:
            buttons.extend(str(v) for v in submit.values())
        return [b for b in buttons if b]

    @property
    def has_meaningful_message(self) -> bool:
        """실질적인 문의 내용이 있는지"""
        if not self.message:
            return False
        stripped = self.message.strip()
        if len(stripped) <= 2:
            return False
        return True


# ── 처리 결과 ──

class ProcessResult(BaseModel):
    chat_id: str = ""
    user_id: str = ""
    user_name: str = ""
    event_type: str = ""
    dropout_type: str | None = None
    dropout_response: str | None = None
    lookup_summary: str = ""
    category: str = ""
    draft_answer: str = ""
    action: str = ""  # auto_reply, draft, escalate, dropout_followup
    timestamp: str = ""


# ── API 엔드포인트 ──

@app.post("/webhook/channeltalk")
async def handle_webhook(request: Request):
    """채널톡 webhook 이벤트 수신"""
    body = await request.body()

    # 서명 검증
    signature = request.headers.get("x-signature", "")
    if not verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    raw = json.loads(body)
    event = ParsedEvent(raw)

    logger.info(f"[webhook] event={event.event_type} chat={event.chat_id} member={event.member_id}")

    result = ProcessResult(
        chat_id=event.chat_id,
        event_type=event.event_type,
        user_name=event.user_name,
        timestamp=datetime.now().isoformat(),
    )

    # 1. 유저 식별
    user_id = await identify_user(event)
    result.user_id = user_id or ""

    # 2. 봇 이탈 패턴 감지
    detector = get_dropout_detector()
    dropout = detector.detect(event)
    if dropout:
        result.dropout_type = dropout.type.value
        result.dropout_response = dropout.followup_message
        result.action = "dropout_followup"
        logger.info(f"[dropout] type={dropout.type.value} chat={event.chat_id}")
        return result

    # 3. 에이전트 파이프라인: 분류 → 조회 → 환불 계산 → 답변 생성
    agent = get_agent()
    agent_response = agent.process(
        chat_id=event.chat_id,
        text=event.message,
        user_id=user_id or "",
    )

    result.category = agent_response.category
    result.draft_answer = agent_response.draft_answer
    result.action = agent_response.action
    if agent_response.admin_lookup:
        result.lookup_summary = agent_response.admin_lookup.to_display()
    elif agent_response.lookup:
        result.lookup_summary = agent_response.lookup.to_display()

    return result


async def identify_user(event: ParsedEvent) -> str | None:
    """채널톡 이벤트에서 유저 식별 → 관리자센터 userId"""
    client = get_admin_client()

    # 방법 1: memberId가 곧 userId인 경우 (프론트엔드에서 주입한 경우)
    if event.member_id and event.member_id.isdigit():
        user = client.get_user(event.member_id)
        if user.name:
            return event.member_id

    # 방법 2: 전화번호로 검색
    if event.phone:
        user_id = client.search_user_by_phone(event.phone)
        if user_id:
            return user_id

    logger.warning(f"유저 식별 실패: member={event.member_id} phone={event.phone}")
    return None


# ── 데모용 수동 테스트 엔드포인트 ──

@app.post("/test/process")
async def test_process(phone: str = "", user_id: str = "", message: str = ""):
    """데모용: 전화번호 or userId + 메시지로 직접 테스트"""
    client = get_admin_client()
    result = ProcessResult(timestamp=datetime.now().isoformat())

    # 유저 식별
    if user_id:
        result.user_id = user_id
    elif phone:
        result.user_id = client.search_user_by_phone(phone) or ""

    if not result.user_id:
        result.action = "user_not_found"
        return result

    # 유저 정보 조회
    lookup = client.lookup_all(result.user_id)
    result.lookup_summary = lookup.to_display()
    result.action = "lookup_complete"

    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cs-agent-webhook"}
