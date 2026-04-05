"""AgentCore Memory 연동 — cs-agent-demo.

us-product-agent/src/memory.py 패턴 복사. 채널톡 CS 상담 세션 멀티턴 유지용.

설계:
- Short-term: 같은 세션(chat_id)의 최근 5턴 → system_prompt 에 주입
- Long-term: actor 전역 semantic search → 같은 유저 이전 상담 이력 검색
- Memory resource 없거나 설정 실패 시 None 반환 → wrapper 는 memory 없이도 동작
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_ID_PATH = Path(__file__).parent.parent / "memory_id.json"
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")

# 시스템 프롬프트에 주입할 컨텍스트 최대 글자 수
MAX_CONTEXT_CHARS = 2000


@dataclass
class AgentMemory:
    """에이전트 Memory 세션 래퍼. actor_id/session_id 를 함께 들고 다님."""

    manager: object  # MemorySessionManager
    actor_id: str
    session_id: str


def _load_memory_id() -> str | None:
    try:
        with open(MEMORY_ID_PATH) as f:
            return json.load(f)["memoryId"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None


def create_memory_session(
    session_id: str,
    actor_id: str = "cs_agent_default",
) -> AgentMemory | None:
    """AgentCore Memory 세션을 생성/재개. 실패 시 None."""
    memory_id = _load_memory_id()
    if not memory_id:
        logger.warning("memory_id.json 없음 — Memory 없이 동작")
        return None
    try:
        from bedrock_agentcore.memory.session import MemorySessionManager

        manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
        manager.create_memory_session(actor_id=actor_id, session_id=session_id)
        return AgentMemory(manager=manager, actor_id=actor_id, session_id=session_id)
    except Exception as e:
        logger.warning(f"AgentCore Memory 초기화 실패 (memory 없이 동작): {e}")
        return None


def save_turn(mem: AgentMemory | None, role: str, content: str) -> None:
    """대화 턴을 Memory 에 저장."""
    if mem is None or not content:
        return
    try:
        from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

        msg_role = MessageRole.USER if role == "user" else MessageRole.ASSISTANT
        mem.manager.add_turns(
            actor_id=mem.actor_id,
            session_id=mem.session_id,
            messages=[ConversationalMessage(content, msg_role)],
        )
    except Exception as e:
        logger.debug(f"Memory 저장 실패: {e}")


def _extract_record_text(record) -> str:
    if hasattr(record, "content"):
        text = record.content.get("text", "") if isinstance(record.content, dict) else str(record.content)
    elif isinstance(record, dict):
        text = record.get("content", {}).get("text", "")
    else:
        text = str(record)
    text = re.sub(r'<topic name="([^"]*)">\s*', lambda m: f"[{m.group(1)}] ", text)
    text = re.sub(r"</topic>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_context(text: str, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    result: list[str] = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > max_chars:
            result.append("... (이전 이력 생략)")
            break
        result.append(line)
        total += len(line) + 1
    return "\n".join(result)


def get_context_for_prompt(mem: AgentMemory | None, query: str) -> str:
    """System prompt 에 주입할 memory context.

    1. Short-term: 같은 세션 최근 대화
    2. Long-term: actor 전역 semantic search (이전 상담 이력)
    """
    if mem is None:
        return ""

    parts: list[str] = []

    # Short-term
    try:
        turns = mem.manager.get_last_k_turns(
            actor_id=mem.actor_id, session_id=mem.session_id, k=5
        )
        if turns:
            recent = []
            for turn_group in turns[-3:]:
                for msg in turn_group:
                    role = msg.get("role", "")
                    text = msg.get("content", {}).get("text", "")
                    if role and text:
                        label = "유저" if role == "USER" else "어시스턴트"
                        recent.append(f"- {label}: {text[:150]}")
            if recent:
                parts.append("## 최근 대화\n" + "\n".join(recent))
    except Exception as e:
        logger.debug(f"Short-term memory 조회 실패: {e}")

    # Long-term
    try:
        records = mem.manager.search_long_term_memories(
            query=query,
            namespace_prefix=f"/actors/{mem.actor_id}/",
            top_k=5,
        )
        if records:
            facts = []
            for r in records:
                text = _extract_record_text(r)
                if text:
                    facts.append(f"- {text[:200]}")
            if facts:
                parts.append("## 이전 상담 이력\n" + "\n".join(facts))
    except Exception as e:
        logger.debug(f"Long-term memory 검색 실패: {e}")

    if not parts:
        return ""
    return _truncate_context("\n\n".join(parts))
