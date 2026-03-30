"""RAG 인덱스 빌드 — BigQuery에서 고객질문 + 매니저답변 쌍을 추출하여 벡터 DB에 저장

실행: python scripts/build_rag_index.py
"""
from __future__ import annotations
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# letter-post-weekly-report의 BigQuery 모듈 사용
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../letter-post-weekly-report"))

from src.bigquery.client import BigQueryClient
from src.bigquery.channel_queries import ChannelQueryService
from src.bigquery.channel_preprocessor import dedup_messages, group_by_chat, detect_route

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "../data/rag_pairs.json")


def extract_qa_pairs(messages: list, chat_states: dict) -> list:
    """메시지를 chatId별로 그룹핑하고, 고객 질문 + 매니저 답변 쌍을 추출.
    route 판단 대신 매니저 메시지 유무로 필터링."""
    deduped = dedup_messages(messages)
    grouped = group_by_chat(deduped)

    pairs = []
    for chat_id, msgs in grouped.items():
        user_texts = []
        manager_texts = []

        for msg in msgs:
            text = (msg.get("plainText") or "").strip()
            if not text:
                continue
            if msg.get("personType") == "user":
                user_texts.append(text)
            elif msg.get("personType") == "manager":
                manager_texts.append(text)

        # 매니저 응답이 있는 대화만
        if not user_texts or not manager_texts:
            continue

        customer_text = "\n".join(user_texts)
        manager_text = "\n".join(manager_texts)

        if len(customer_text) < 10 or len(manager_text) < 10:
            continue

        pairs.append({
            "chat_id": chat_id,
            "customer": customer_text[:800],
            "manager": manager_text[:800],
        })

    return pairs


def main():
    print("BigQuery에서 채널톡 메시지 조회 중...")
    client = BigQueryClient()
    cq = ChannelQueryService(client)

    # 3개월 데이터
    messages, chat_states = cq.get_weekly_conversations("2025-08-01", "2025-12-01")
    print(f"  메시지: {len(messages)}건")

    print("QA 쌍 추출 중...")
    pairs = extract_qa_pairs(messages, chat_states)
    print(f"  추출된 QA 쌍: {len(pairs)}건")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(pairs, f, ensure_ascii=False, indent=2)
    print(f"  저장: {OUTPUT_PATH}")

    # 샘플 출력
    if pairs:
        p = pairs[0]
        print(f"\n=== 샘플 ===")
        print(f"고객: {p['customer'][:150]}...")
        print(f"매니저: {p['manager'][:150]}...")


if __name__ == "__main__":
    main()
