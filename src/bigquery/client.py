"""BigQuery 클라이언트 모듈"""
import os
from typing import List, Dict, Any, Optional
from google.cloud import bigquery
from google.oauth2 import service_account


class BigQueryClient:
    """BigQuery 연결 및 데이터 조회를 담당하는 클라이언트"""

    def __init__(self, credentials_path: str = "./accountKey.json"):
        """
        BigQuery 클라이언트 초기화

        Args:
            credentials_path: 서비스 계정 키 파일 경로
        """
        self.credentials = service_account.Credentials.from_service_account_file(
            credentials_path
        )
        self.client = bigquery.Client(
            credentials=self.credentials,
            project=self.credentials.project_id
        )
        self.project_id = self.credentials.project_id

    def list_datasets(self) -> List[str]:
        """
        프로젝트 내의 모든 데이터셋 목록 조회

        Returns:
            데이터셋 ID 리스트
        """
        datasets = list(self.client.list_datasets())
        return [dataset.dataset_id for dataset in datasets]

    def list_tables(self, dataset_id: str) -> List[str]:
        """
        특정 데이터셋의 모든 테이블 목록 조회

        Args:
            dataset_id: 데이터셋 ID

        Returns:
            테이블 ID 리스트
        """
        dataset_ref = self.client.dataset(dataset_id)
        tables = list(self.client.list_tables(dataset_ref))
        return [table.table_id for table in tables]

    def get_table_schema(self, dataset_id: str, table_id: str) -> List[Dict[str, str]]:
        """
        테이블 스키마 정보 조회

        Args:
            dataset_id: 데이터셋 ID
            table_id: 테이블 ID

        Returns:
            스키마 정보 (필드명, 타입, 모드)
        """
        table_ref = f"{self.project_id}.{dataset_id}.{table_id}"
        table = self.client.get_table(table_ref)

        schema_info = []
        for field in table.schema:
            schema_info.append({
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode
            })

        return schema_info

    def query_sample(
        self,
        dataset_id: str,
        table_id: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        테이블에서 샘플 데이터 조회

        Args:
            dataset_id: 데이터셋 ID
            table_id: 테이블 ID
            limit: 조회할 행 수

        Returns:
            샘플 데이터 리스트
        """
        query = f"""
        SELECT *
        FROM `{self.project_id}.{dataset_id}.{table_id}`
        LIMIT {limit}
        """

        query_job = self.client.query(query)
        results = query_job.result()

        return [dict(row) for row in results]

    def execute_query(self, query: str) -> List[Dict[str, Any]]:
        """
        커스텀 쿼리 실행

        Args:
            query: 실행할 SQL 쿼리

        Returns:
            쿼리 결과
        """
        query_job = self.client.query(query)
        results = query_job.result()

        return [dict(row) for row in results]
