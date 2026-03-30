"""RAG — 유사 과거 대화에서 매니저 답변 검색"""
from __future__ import annotations
import json
import os
import chromadb
from chromadb.utils import embedding_functions


class AnswerRAG:
    def __init__(self, db_path: str = "./chroma_db", data_path: str = None):
        self.db_path = db_path
        self.data_path = data_path or os.path.join(os.path.dirname(__file__), "../data/rag_pairs.json")
        self.client = chromadb.PersistentClient(path=db_path)
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="intfloat/multilingual-e5-small"
        )
        self.collection = self.client.get_or_create_collection(
            name="cs_qa_pairs",
            embedding_function=self.ef,
        )

    def index_if_needed(self):
        """QA 쌍 데이터가 있으면 인덱싱. 이미 인덱싱돼 있으면 스킵."""
        if self.collection.count() > 0:
            return self.collection.count()

        if not os.path.exists(self.data_path):
            return 0

        with open(self.data_path, encoding="utf-8") as f:
            pairs = json.load(f)

        if not pairs:
            return 0

        # 고객 질문으로 검색 → 매니저 답변을 반환하는 구조
        # document = 고객 질문 (검색 대상)
        # metadata에 매니저 답변 저장
        batch_size = 5000
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i:i + batch_size]
            self.collection.add(
                ids=[p["chat_id"] for p in batch],
                documents=[p["customer"][:500] for p in batch],
                metadatas=[{"manager": p["manager"][:500], "chat_id": p["chat_id"]} for p in batch],
            )

        return self.collection.count()

    def search(self, customer_text: str, n_results: int = 3) -> list[dict]:
        """고객 문의와 유사한 과거 대화를 검색, 매니저 답변 반환.

        Returns:
            [{"customer": str, "manager": str, "distance": float}, ...]
        """
        if self.collection.count() == 0:
            self.index_if_needed()

        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[customer_text[:500]],
            n_results=n_results,
        )

        matches = []
        if results and results["documents"]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                matches.append({
                    "customer": doc,
                    "manager": meta.get("manager", ""),
                    "distance": dist,
                })
        return matches

    def format_for_prompt(self, matches: list[dict]) -> str:
        """검색 결과를 LLM 프롬프트용 텍스트로 포맷"""
        if not matches:
            return "(유사 답변 없음)"

        lines = []
        for i, m in enumerate(matches, 1):
            lines.append(f"[참고 {i}] 유사도: {1 - m['distance']:.2f}")
            lines.append(f"  고객: {m['customer'][:200]}")
            lines.append(f"  매니저: {m['manager'][:300]}")
            lines.append("")
        return "\n".join(lines)
