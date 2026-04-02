"""채널톡 데이터 BigQuery 쿼리 모듈

채널톡(channel.io) CS 데이터를 BigQuery에서 조회합니다.
데이터소스: us-service-data.channel_io.messages

사용법:
    client = BigQueryClient()
    cq = ChannelQueryService(client)
    messages = cq.get_weekly_messages("2026-02-09", "2026-02-16")
"""
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta
from .client import BigQueryClient


class ChannelQueryService:
    """채널톡 메시지 조회 서비스"""

    def __init__(self, client: BigQueryClient):
        self.client = client
        # 채널톡 데이터는 별도 데이터셋
        self.project_id = client.project_id
        self.dataset_id = "channel_io"

    def get_weekly_messages(
        self, start_date: str, end_date: str, limit: int = 0
    ) -> List[Dict[str, Any]]:
        """주간 채널톡 메시지 전체 조회 (chatId별 그룹핑 전)"""
        start_ts = self._kst_to_unix_ms(start_date)
        end_ts = self._kst_to_unix_ms(end_date)

        limit_clause = f"LIMIT {limit}" if limit > 0 else ""

        query = f"""
        SELECT
            chatId,
            personType,
            plainText,
            createdAt,
            personId,
            id as messageId
        FROM `{self.project_id}.{self.dataset_id}.messages`
        WHERE
            createdAt >= {start_ts}
            AND createdAt < {end_ts}
        ORDER BY chatId, createdAt
        {limit_clause}
        """
        return self.client.execute_query(query)

    def get_sample_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """탐색용 샘플 메시지"""
        query = f"""
        SELECT
            chatId,
            personType,
            plainText,
            createdAt,
            personId,
            id as messageId
        FROM `{self.project_id}.{self.dataset_id}.messages`
        ORDER BY createdAt DESC
        LIMIT {limit}
        """
        return self.client.execute_query(query)

    def get_schema_info(self) -> List[Dict[str, str]]:
        """messages 테이블 스키마 조회"""
        return self.client.get_table_schema(self.dataset_id, "messages")

    def get_person_type_distribution(
        self, start_date: str = None, end_date: str = None
    ) -> List[Dict[str, Any]]:
        """personType 분포 조회"""
        where_clause = "WHERE 1=1"
        if start_date and end_date:
            start_ts = self._kst_to_unix_ms(start_date)
            end_ts = self._kst_to_unix_ms(end_date)
            where_clause = f"WHERE createdAt >= {start_ts} AND createdAt < {end_ts}"

        query = f"""
        SELECT
            personType,
            COUNT(*) as count,
            COUNT(DISTINCT chatId) as chat_count
        FROM `{self.project_id}.{self.dataset_id}.messages`
        {where_clause}
        GROUP BY personType
        ORDER BY count DESC
        """
        return self.client.execute_query(query)

    def get_chat_stats(
        self, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """chatId별 메시지 통계"""
        start_ts = self._kst_to_unix_ms(start_date)
        end_ts = self._kst_to_unix_ms(end_date)

        query = f"""
        SELECT
            chatId,
            COUNT(*) as message_count,
            COUNT(DISTINCT personType) as person_type_count,
            MIN(createdAt) as first_message,
            MAX(createdAt) as last_message,
            COUNTIF(personType = 'user') as user_messages,
            COUNTIF(personType = 'bot') as bot_messages,
            COUNTIF(personType = 'manager') as manager_messages
        FROM `{self.project_id}.{self.dataset_id}.messages`
        WHERE
            createdAt >= {start_ts}
            AND createdAt < {end_ts}
        GROUP BY chatId
        ORDER BY message_count DESC
        """
        return self.client.execute_query(query)

    def get_chat_states(self, chat_ids: List[str]) -> Dict[str, str]:
        """chats 테이블에서 chatId → state 매핑 일괄 조회

        Returns:
            {chatId: state} 딕셔너리 (state: "closed" / "opened")
        """
        if not chat_ids:
            return {}

        # BigQuery IN 절 최대 크기 제한 고려하여 배치 처리
        batch_size = 5000
        result = {}
        for i in range(0, len(chat_ids), batch_size):
            batch = chat_ids[i:i + batch_size]
            ids_str = ", ".join(f"'{cid}'" for cid in batch)
            query = f"""
            SELECT id as chatId, state
            FROM `{self.project_id}.{self.dataset_id}.chats`
            WHERE id IN ({ids_str})
            """
            rows = self.client.execute_query(query)
            for row in rows:
                result[row["chatId"]] = row.get("state", "")
        return result

    def get_weekly_conversations(
        self, start_date: str, end_date: str
    ) -> tuple:
        """messages + chats JOIN으로 메시지와 state 함께 조회

        Returns:
            (messages, chat_states) 튜플
            - messages: 메시지 리스트
            - chat_states: {chatId: state} 딕셔너리
        """
        messages = self.get_weekly_messages(start_date, end_date)
        if not messages:
            return [], {}

        # 고유 chatId 추출
        chat_ids = list({msg["chatId"] for msg in messages if msg.get("chatId")})
        chat_states = self.get_chat_states(chat_ids)

        return messages, chat_states

    @staticmethod
    def _kst_to_unix_ms(date_str: str) -> int:
        """KST 날짜 문자열 → Unix timestamp (milliseconds)"""
        date = datetime.strptime(date_str, "%Y-%m-%d")
        utc_time = date - timedelta(hours=9)
        return int(utc_time.timestamp() * 1000)
