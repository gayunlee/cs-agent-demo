"""채널톡 대화 전처리 모듈

chatId별로 메시지를 그룹핑하고, 사용자 메시지만 연결하여
분류 단위(1대화 = 1분류)로 변환합니다.

전처리 규칙 (spec.md):
- 분류 단위: chatId 기준 1대화 = 1분류
- 사용자 메시지만 연결
- 앞 500자 + 마지막 200자
- 메시지 dedup (동일 chatId+personType+plainText+createdAt)
- 워크플로우 버튼 파싱
- route 판정 (manager_message_count + state 기반)
"""
from typing import List, Dict, Any, Optional, Set
from collections import defaultdict

# 알려진 워크플로우 버튼 텍스트
WORKFLOW_BUTTONS = {
    "빠른 문의", "구독신청", "기타 문의", "상품변경",
    "회원가입", "이용방법", "1:1 상담", "구독해지 및 환불",
    "사이트 및 동영상 오류", "수강 및 상품문의",
}


def dedup_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(chatId, personType, plainText, createdAt) 기준 중복 제거

    채널톡 데이터에서 모든 메시지가 2번씩 존재하는 문제 해결.
    """
    seen = set()
    deduped = []
    for msg in messages:
        key = (
            msg.get("chatId", ""),
            msg.get("personType", ""),
            msg.get("plainText", ""),
            msg.get("createdAt", ""),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(msg)
    return deduped


def parse_workflow_buttons(user_messages: List[Dict[str, Any]]) -> List[str]:
    """사용자 메시지에서 워크플로우 버튼 텍스트 분리

    Returns:
        감지된 워크플로우 버튼 텍스트 리스트 (순서 유지)
    """
    buttons = []
    for msg in user_messages:
        text = (msg.get("plainText") or "").strip()
        if text in WORKFLOW_BUTTONS:
            buttons.append(text)
    return buttons


def detect_route(
    manager_message_count: int,
    user_message_count: int,
    chat_state: Optional[str] = None,
) -> str:
    """해결 경로 판정 (규칙 기반)

    Args:
        manager_message_count: 매니저 메시지 수
        user_message_count: 사용자 메시지 수
        chat_state: chats 테이블의 state ("closed" / "opened")

    Returns:
        "manager_resolved" | "bot_resolved" | "abandoned"
    """
    if chat_state == "closed":
        if manager_message_count > 0:
            return "manager_resolved"
        else:
            return "bot_resolved"
    else:  # opened 또는 state 정보 없음
        return "abandoned"


def group_by_chat(messages: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """chatId별로 메시지 그룹핑 (시간순 정렬 유지)"""
    groups = defaultdict(list)
    for msg in messages:
        chat_id = msg.get("chatId")
        if chat_id:
            groups[chat_id].append(msg)

    # 각 그룹 내 시간순 정렬
    for chat_id in groups:
        groups[chat_id].sort(key=lambda m: m.get("createdAt", ""))

    return dict(groups)


def extract_user_text(messages: List[Dict[str, Any]], max_front: int = 500, max_tail: int = 200) -> str:
    """사용자 메시지만 연결 → 앞 500자 + 마지막 200자

    Args:
        messages: 한 chatId의 메시지 리스트 (시간순)
        max_front: 앞부분 최대 글자 수
        max_tail: 뒷부분 최대 글자 수

    Returns:
        연결된 사용자 텍스트
    """
    user_texts = []
    for msg in messages:
        if msg.get("personType") == "user":
            text = (msg.get("plainText") or "").strip()
            if text:
                user_texts.append(text)

    if not user_texts:
        return ""

    full_text = "\n".join(user_texts)

    if len(full_text) <= max_front + max_tail:
        return full_text

    front = full_text[:max_front]
    tail = full_text[-max_tail:]
    return f"{front}\n...\n{tail}"


def build_chat_items(
    messages: List[Dict[str, Any]],
    min_user_chars: int = 10,
    chat_states: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """메시지 리스트 → 분류용 아이템 리스트 변환

    Args:
        messages: 전체 메시지 리스트
        min_user_chars: 최소 사용자 텍스트 길이 (이하 제외)
        chat_states: chatId → state 매핑 (chats 테이블에서 조회)

    Returns:
        chatId별 분류용 아이템 리스트
        [{chatId, text, message_count, ..., workflow_buttons, has_free_text, route}]
    """
    if chat_states is None:
        chat_states = {}

    groups = group_by_chat(messages)
    items = []

    for chat_id, chat_messages in groups.items():
        text = extract_user_text(chat_messages)

        if len(text) < min_user_chars:
            continue

        user_messages = [m for m in chat_messages if m.get("personType") == "user"]
        user_count = len(user_messages)
        bot_count = sum(1 for m in chat_messages if m.get("personType") == "bot")
        manager_count = sum(1 for m in chat_messages if m.get("personType") == "manager")

        # 워크플로우 버튼 파싱
        buttons = parse_workflow_buttons(user_messages)

        # 자유 텍스트 존재 여부 (버튼 외 텍스트가 있는지)
        free_text_msgs = [
            m for m in user_messages
            if (m.get("plainText") or "").strip()
            and (m.get("plainText") or "").strip() not in WORKFLOW_BUTTONS
        ]
        has_free_text = len(free_text_msgs) > 0

        # route 판정
        chat_state = chat_states.get(chat_id)
        route = detect_route(manager_count, user_count, chat_state)

        items.append({
            "chatId": chat_id,
            "text": text,
            "message_count": len(chat_messages),
            "user_message_count": user_count,
            "bot_message_count": bot_count,
            "manager_message_count": manager_count,
            "first_message_at": chat_messages[0].get("createdAt", ""),
            "last_message_at": chat_messages[-1].get("createdAt", ""),
            "source": "channel_io",
            "workflow_buttons": buttons,
            "has_free_text": has_free_text,
            "route": route,
        })

    return items
