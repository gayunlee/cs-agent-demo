"""템플릿 매칭 엔진 — 벡터 유사도 기반

80건 테스트 케이스의 유저 메시지를 템플릿 유형별로 임베딩 → 인덱스.
새 문의 → 가장 가까운 템플릿 유형 반환 → 변수 채움 → 답변 생성.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from src.admin_api import AdminAPIClient, LookupResult
from src.refund_engine import RefundEngine, RefundInput, RefundResult

logger = logging.getLogger(__name__)

MAPPING_PATH = Path(__file__).parent.parent / "config" / "template_mapping.json"
TEST_CASES_PATH = Path(__file__).parent.parent / "data" / "template_test_cases.json"
CHROMA_PATH = Path(__file__).parent.parent / "chroma_db" / "templates"
EMBED_MODEL = "intfloat/multilingual-e5-small"
SIMILARITY_THRESHOLD = 0.35  # 이 거리 이하면 매칭 (낮을수록 유사)


@dataclass
class MatchResult:
    template_id: str
    template_name: str
    category: str
    rendered_answer: str
    confidence: float  # 0.0 ~ 1.0
    needs_llm: bool = False
    refund_result: RefundResult | None = None
    lookup: LookupResult | None = None
    distance: float = 0.0  # 벡터 거리


class TemplateMatcher:
    def __init__(self, admin_client: AdminAPIClient | None = None):
        self.templates = self._load_templates()
        self.admin_client = admin_client
        self.refund_engine = RefundEngine()
        self._embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        self._collection = self._init_vector_index()

    def _load_templates(self) -> list[dict]:
        try:
            with open(MAPPING_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("templates", [])
        except FileNotFoundError:
            logger.error(f"매핑 테이블 없음: {MAPPING_PATH}")
            return []

    def _init_vector_index(self) -> chromadb.Collection:
        """테스트 케이스를 벡터 인덱스에 넣기"""
        client = chromadb.Client()  # in-memory
        collection = client.get_or_create_collection(
            name="template_index",
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

        if collection.count() > 0:
            return collection

        # 테스트 케이스 로드
        if not TEST_CASES_PATH.exists():
            logger.warning(f"테스트 케이스 없음: {TEST_CASES_PATH}")
            return collection

        with open(TEST_CASES_PATH, encoding="utf-8") as f:
            cases = json.load(f)

        docs = []
        metas = []
        ids = []
        for i, case in enumerate(cases):
            text = " ".join(case.get("user_messages", []))
            if not text.strip():
                continue
            docs.append(text[:500])
            metas.append({"template_type": case["template_type"]})
            ids.append(f"case_{i}")

        if docs:
            collection.add(documents=docs, metadatas=metas, ids=ids)
            logger.info(f"벡터 인덱스: {len(docs)}건 로드")

        return collection

    def match(self, text: str, user_id: str = "") -> MatchResult | None:
        """문의 텍스트 → 벡터 유사도 매칭 → 답변 생성"""
        if not text.strip():
            return None

        # 1. 벡터 유사도 검색
        results = self._collection.query(query_texts=[text[:500]], n_results=3)

        if not results["distances"] or not results["distances"][0]:
            return None

        best_distance = results["distances"][0][0]
        best_type = results["metadatas"][0][0]["template_type"]

        # threshold 체크
        if best_distance > SIMILARITY_THRESHOLD:
            logger.info(f"유사도 낮음: {best_type} (distance={best_distance:.3f} > {SIMILARITY_THRESHOLD})")
            return None

        confidence = max(0.0, 1.0 - best_distance)

        # 2. template_type → template_id 매핑
        TYPE_TO_ID = {
            "환불_확정": "refund_full",
            "환불_접수": "refund_full",
            "전액환불": "refund_full",
            "부분환불": "refund_partial",
            "구독해지": "unsubscribe_guide",
            "카드변경": "card_change",
            "상품변경": "product_change",
            "상품링크": "product_link",
            "앱설치": "pc_usage",
            "회원가입": "product_link",
            "로그인변경": "login_guide",
            "종료인사": "closing",
            "본인확인": "identity_check",
            "기술오류": "tech_error",
            "수강안내": "course_info",
            "플랫폼혼동": "platform_confusion",
        }
        template_id = TYPE_TO_ID.get(best_type, best_type)

        # 환불은 계산 결과에 따라 분기
        if template_id in ("refund_full", "refund_partial", "refund_rejected"):
            template_id = "refund_full"  # 계산 후 _render_refund에서 분기

        best_template = self._get_template(template_id)
        if not best_template:
            return None

        # 3. 유저 정보 조회
        lookup = None
        if user_id and self.admin_client and best_template.get("required_apis"):
            lookup = self.admin_client.lookup_all(user_id)

        # 4. 환불 계산
        refund_result = None
        if best_template.get("needs_refund_calc") and lookup:
            refund_result = self._calculate_refund(lookup)

        # 5. 렌더링
        rendered = self._render(best_template, lookup, refund_result, text)

        if rendered is None:
            return MatchResult(
                template_id=template_id,
                template_name=best_template["name"],
                category=best_template["category"],
                rendered_answer="",
                confidence=confidence,
                needs_llm=True,
                refund_result=refund_result,
                lookup=lookup,
                distance=best_distance,
            )

        return MatchResult(
            template_id=template_id,
            template_name=best_template["name"],
            category=best_template["category"],
            rendered_answer=rendered,
            confidence=confidence,
            needs_llm=False,
            refund_result=refund_result,
            lookup=lookup,
            distance=best_distance,
        )

    def _render(self, template: dict, lookup: LookupResult | None, refund_result: RefundResult | None, text: str) -> str | None:
        """템플릿에 변수 채워서 렌더링"""
        tmpl_id = template["id"]

        # LLM 생성 필요 (고정 템플릿 없음)
        if template.get("template") is None:
            return None

        # 변수 수집
        variables = self._collect_variables(template, lookup, refund_result, text)

        # 환불: 계산 결과에 따라 다른 템플릿 사용
        if template.get("needs_refund_calc") and refund_result:
            return self._render_refund(refund_result, variables)

        # 로그인: 가입 방법별 분기
        if tmpl_id == "login_guide":
            return self._render_login(template, variables)

        # 일반 템플릿 렌더링
        tmpl_str = template["template"]
        try:
            return tmpl_str.format(**variables)
        except KeyError as e:
            logger.warning(f"템플릿 변수 누락: {e} (template={tmpl_id})")
            # 누락된 변수를 기본값으로 채움
            for field in template.get("required_fields", []):
                if field not in variables:
                    variables[field] = "(확인 필요)"
            try:
                return tmpl_str.format(**variables)
            except KeyError:
                return tmpl_str  # 변수 없이 반환

    def _collect_variables(self, template: dict, lookup: LookupResult | None, refund_result: RefundResult | None, text: str) -> dict:
        """조회 결과에서 템플릿 변수 수집"""
        v = {}

        if lookup:
            if lookup.user:
                v["user_name"] = lookup.user.name or "(확인 필요)"
                v["signup_method"] = lookup.user.signup_method or "direct"
                v["phone"] = lookup.user.phone or ""

            if lookup.products:
                p = lookup.products[0]
                v["product_name"] = p.product_name or "(확인 필요)"
                v["master_name"] = p.master_name or "(확인 필요)"
                v["current_product"] = f"{p.master_name} {p.product_name}"
                v["platform"] = "어스플러스"  # 기본값, 실제는 상품에 따라 다름
                v["next_payment_date"] = p.expired_at[:10] if p.expired_at else "(확인 필요)"

            if lookup.transactions:
                # 최신 성공 거래
                success = [t for t in lookup.transactions if t.state == "purchased_success"]
                tx = success[-1] if success else lookup.transactions[-1]
                v["payment_amount"] = f"{tx.amount:,}"
                v["payment_date"] = tx.created_at[:10] if tx.created_at else "(확인 필요)"
                v["payment_method"] = f"{tx.method} {tx.method_info}"

        if refund_result:
            v["refund_amount"] = f"{refund_result.refund_amount:,}" if refund_result.refundable else "0"
            v["deduction"] = f"{refund_result.deduction:,}"
            v["fee"] = f"{refund_result.fee:,}"
            v["rejection_reason"] = refund_result.explanation

        # 텍스트에서 마스터명 추출 시도
        if "master_name" not in v:
            v["master_name"] = self._extract_master_name(text)

        # 기본값
        v.setdefault("product_name", "(확인 필요)")
        v.setdefault("payment_amount", "(확인 필요)")
        v.setdefault("next_payment_date", "(확인 필요)")

        return v

    def _render_refund(self, refund_result: RefundResult, variables: dict) -> str:
        """환불 계산 결과에 따라 적절한 환불 템플릿 선택 + 렌더링"""
        if not refund_result.refundable:
            tmpl = self._get_template("refund_rejected")
        elif refund_result.deduction == 0:
            tmpl = self._get_template("refund_full")
        else:
            tmpl = self._get_template("refund_partial")

        if tmpl and tmpl.get("template"):
            try:
                return tmpl["template"].format(**variables)
            except KeyError as e:
                logger.warning(f"환불 템플릿 변수 누락: {e}")
                for field in tmpl.get("required_fields", []):
                    variables.setdefault(field, "(확인 필요)")
                return tmpl["template"].format(**variables)
        return refund_result.to_display()

    def _render_login(self, template: dict, variables: dict) -> str:
        """로그인 안내: 가입 방법별 분기"""
        method = variables.get("signup_method", "default")
        method_templates = template.get("template_by_method", {})
        login_guide = method_templates.get(method, method_templates.get("default", "가입 방법을 확인 중입니다."))
        variables["login_guide"] = login_guide
        try:
            return template["template"].format(**variables)
        except KeyError:
            return f"안녕하세요 회원님,\n{login_guide}"

    def _get_template(self, template_id: str) -> dict | None:
        for tmpl in self.templates:
            if tmpl["id"] == template_id:
                return tmpl
        return None

    def _extract_master_name(self, text: str) -> str:
        """텍스트에서 마스터명 추출 (간단 키워드 매칭)"""
        known_masters = ["박두환", "김영익", "이항영", "강환국", "체밀턴", "홍춘욱", "서재형", "이종윤", "백훈종", "천백만"]
        for m in known_masters:
            if m in text:
                return m
        return "(확인 필요)"
