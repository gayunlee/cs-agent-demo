"""채널톡 대화 데이터 로드 및 전처리"""
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Conversation:
    chat_id: str
    text: str
    route: str
    message_count: int = 0
    user_message_count: int = 0
    bot_message_count: int = 0
    manager_message_count: int = 0
    workflow_buttons: list = field(default_factory=list)
    topic: str = ""
    topics: list = field(default_factory=list)


def load_conversations(data_dir: str, filter_route: str = "manager_resolved") -> list[Conversation]:
    """classified 데이터에서 대화 로드. manager_resolved만 필터링 (실제 답변이 있는 건)"""
    path = Path(data_dir) / "classified_2025-08-01_2025-12-01.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    convos = []
    for item in data["items"]:
        if filter_route and item["route"] != filter_route:
            continue
        convos.append(Conversation(
            chat_id=item["chatId"],
            text=item["text"],
            route=item["route"],
            message_count=item.get("message_count", 0),
            user_message_count=item.get("user_message_count", 0),
            bot_message_count=item.get("bot_message_count", 0),
            manager_message_count=item.get("manager_message_count", 0),
            workflow_buttons=item.get("workflow_buttons", []),
        ))
    return convos


def load_golden_set(data_dir: str) -> list[Conversation]:
    """검수 완료된 golden set 로드 (라벨 포함)"""
    path = Path(data_dir) / "golden/golden_multilabel_270.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return [
        Conversation(
            chat_id=item["chatId"],
            text=item["text"],
            route=item.get("route", ""),
            workflow_buttons=item.get("workflow_buttons", []),
            topic=item.get("topic", ""),
            topics=item.get("topics", []),
        )
        for item in data
    ]


def load_answer_patterns(data_dir: str) -> list[dict]:
    """매니저 답변 패턴을 RAG용으로 추출.
    manager_resolved 대화에서 고객 질문 + 매니저 답변 쌍을 만든다."""
    convos = load_conversations(data_dir, filter_route="manager_resolved")
    patterns = []
    for c in convos:
        if c.manager_message_count >= 1:
            patterns.append({
                "chat_id": c.chat_id,
                "text": c.text,
                "route": c.route,
                "workflow_buttons": c.workflow_buttons,
            })
    return patterns
