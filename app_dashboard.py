"""CS AI 에이전트 데모 — Streamlit 대시보드
주요 문의 패턴별 자동 답변 생성 데모"""
from __future__ import annotations
import os
import json
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.data_loader import load_golden_set
from src.agent import CSAgent
from src.admin_api import AdminAPIClient
from src.refund_engine import RefundEngine, RefundInput
from datetime import date, datetime

DATA_DIR = os.getenv("DATA_DIR", "/Users/gygygygy/Documents/ai/letter-post-weekly-report/data/channel_io")

# 주요 문의 패턴 — golden set에서 선별한 대표 케이스
DEMO_PATTERNS = [
    {
        "label": "환불 요청 (자동결제 불만)",
        "category": "결제·환불",
        "chat_id": "demo_refund_auto",
        "text": (
            "제가 한달만 결제 했는데 자동으로 또 결제가 되었네요\n"
            "매도 타이밍이 없어서 제가 힘드네요\n"
            "환불 부탁드립니다\n"
            "제가 한달만 결제했는데요"
        ),
    },
    {
        "label": "카드 변경 + 상품 변경",
        "category": "결제·환불",
        "chat_id": "demo_card_change",
        "text": (
            "26년 1월 박두환투자교실 정기자동결제 취소요청합니다.\n"
            "1회-6개월만 강의듣고 탈퇴하겠습니다.\n"
            "그게 규칙이면 3개월로 끊어주시고 결제카드도 바꿀 생각입니다."
        ),
    },
    {
        "label": "구독 해지 방법 문의",
        "category": "구독·멤버십",
        "chat_id": "demo_unsubscribe",
        "text": (
            "안녕하세요\n"
            "현재 이정윤세무사님 포트폴리오 멤버십 가입 중인데\n"
            "가입하라는 문자가 또 와서요. 재결제하란건가요?\n"
            "없습니다 답변 감사합니다!"
        ),
    },
    {
        "label": "신규 가입 문의 (고령 고객)",
        "category": "구독·멤버십",
        "chat_id": "demo_signup",
        "text": (
            "박두환쎔 안녕하세요?\n"
            "떨리는맘으로 문의드립니다\n"
            "저는 53년생 74세(여)이고요\n"
            "전 2023년 2차전지 밧데리주식을시작으로 노후자금1억6천 투자하여 마이너스47프로물렸다가\n"
            "몇달전 우연히 박쌤유투브 접하면서 에코프로에서 두산우로 갈아타면서 조금만회는 했으나\n"
            "지금은어둠속에 헤매고있습니다\n"
            "어떻게 가입을 해야할지도 좀 알려주세요"
        ),
    },
    {
        "label": "상품 업그레이드 (1개월→6개월)",
        "category": "구독·멤버십",
        "chat_id": "demo_upgrade",
        "text": (
            "전 한달 회원가입한 해외에 있는 사람입니다\n"
            "한달이 지나 6개월 회원으로 하고 싶은데 어떻게 하나요.\n"
            "6개월은 50만원으로 디시가 된다고 알고있습니다"
        ),
    },
    {
        "label": "강의 내용 불만 + 개선 요청",
        "category": "콘텐츠·수강",
        "chat_id": "demo_content_complaint",
        "text": (
            "70대중반할배라그런지 참 쉽지않네요.\n"
            "6주차까지들어보지만 강의내용이 주식학문을공부하는학생들에게 필요한내용이네요.\n"
            "주식매매현장에서 이후부턴 상승장일것이고 조정오면사라고하는데\n"
            "바로말씀못하면 눈치라도챌수있도록 수익으로연결되는강의부탁드립니다."
        ),
    },
    {
        "label": "수업 일정 + 교재 미배송",
        "category": "콘텐츠·수강",
        "chat_id": "demo_schedule",
        "text": (
            "홍매화반 수업날짜가 111일 남았는데\n"
            "서재형 투자학교 등록하라고 하면서\n"
            "방송이 끊기고 매매관리 첫걸음 책도 오지 않았습니다\n"
            "이찌 된건가요?"
        ),
    },
    {
        "label": "로그인 방법 변경",
        "category": "기술·오류",
        "chat_id": "demo_login",
        "text": (
            "카카오로 시작되기 로그인으로 설정했는데\n"
            "카카오톡을 지워야하는 사정이라 로그인 방법을 바꾸고싶습니다.\n"
            "비번은 어떻게하나요\n"
            "핸드폰 번호도 바꿔주세요"
        ),
    },
    {
        "label": "앱 오류 (영상 재생 + 가로보기)",
        "category": "기술·오류",
        "chat_id": "demo_app_error",
        "text": (
            "안드로이드 갤럭시 S23 사용중입니다.\n"
            "1. 알람을 눌러 들어가면 동영상이든 음성이든 재생이 안되는 경우가 왕왕있습니다.\n"
            "2. 태블릿 사용시 가로보기 화면도 지원해주실 수 없을까요?\n"
            "3. 동영상 전체보기 화면 이후 다시 기존 화면으로 돌아오면 해당 동영상이 사라집니다."
        ),
    },
    {
        "label": "오프라인 행사 동반 참여 문의",
        "category": "기타",
        "chat_id": "demo_event",
        "text": (
            "미과장님 강의 관련 11월 16일 서울 팀장급 주주총회가 있는데,\n"
            "동반 지인 1인도 같이 참여가 가능할지 문의드려요.\n"
            "지난 번엔 됐던 거 같은데 이번에도 그런지..\n"
            "아하 네. 확인 감사드립니다"
        ),
    },
]


@st.cache_resource
def init_agent():
    mock = os.getenv("MOCK_MODE", "0") == "1"
    return CSAgent(region="us-west-2", mock=mock)


CACHE_PATH = Path(__file__).parent / "data" / "demo_results.json"


def _serialize_result(r):
    """AgentResponse → dict"""
    return {
        "chat_id": r.chat_id,
        "category": r.category,
        "confidence": r.confidence,
        "reasoning": r.reasoning,
        "draft_answer": r.draft_answer,
        "template_matched": r.template_matched,
        "action": r.action,
        "lookup_display": r.lookup.to_display() if r.lookup else "",
        "rag_matches": r.rag_matches or [],
    }


def _save_cache(results: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {k: _serialize_result(v) for k, v in results.items()}
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text())
        from src.agent import AgentResponse
        from src.tools import LookupResult
        results = {}
        for k, v in data.items():
            results[k] = AgentResponse(
                chat_id=v["chat_id"],
                category=v["category"],
                confidence=v["confidence"],
                reasoning=v["reasoning"],
                draft_answer=v["draft_answer"],
                template_matched=v.get("template_matched"),
                action=v["action"],
                lookup=None,  # display용 텍스트는 별도 저장
                rag_matches=v.get("rag_matches", []),
            )
            results[k]._lookup_display = v.get("lookup_display", "")
        return results
    except Exception:
        return None


@st.cache_data(show_spinner="답변 생성 중...")
def generate_all_results():
    """캐시 파일이 있으면 로드, 없으면 생성 후 저장"""
    cached = _load_cache()
    if cached:
        return cached

    agent = init_agent()
    results = {}
    for p in DEMO_PATTERNS:
        results[p["chat_id"]] = agent.process(p["chat_id"], p["text"])
    _save_cache(results)
    return results


def render_action_badge(action: str):
    badges = {
        "auto_template": "🟢 자동 응답 (템플릿)",
        "llm_draft": "🟡 LLM 초안 (RAG 참조)",
        "escalate": "🔴 에스컬레이션 권장",
    }
    return badges.get(action, action)


def main():
    st.set_page_config(page_title="CS AI 에이전트 데모", layout="wide")
    st.title("CS AI 에이전트 데모")
    st.caption("주요 문의 패턴별 자동 답변 생성 결과 — 분류 → RAG 검색 → 정보 조회 → 초안 생성")

    # 앱 로드 시 전체 생성
    results = generate_all_results()

    # 통계
    total = len(results)
    auto = sum(1 for r in results.values() if r.action == "auto_template")
    draft = sum(1 for r in results.values() if r.action == "llm_draft")
    esc = sum(1 for r in results.values() if r.action == "escalate")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 패턴", f"{total}건")
    c2.metric("🟢 자동 응답", f"{auto}건", f"{auto/total*100:.0f}%")
    c3.metric("🟡 LLM 초안", f"{draft}건", f"{draft/total*100:.0f}%")
    c4.metric("🔴 에스컬레이션", f"{esc}건", f"{esc/total*100:.0f}%")

    st.divider()

    # 사이드바
    with st.sidebar:
        # ── 관리자센터 API 연동 ──
        st.header("관리자센터 API")
        admin_token = st.text_input(
            "인증 토큰",
            value=os.getenv("ADMIN_API_TOKEN", ""),
            type="password",
            help="브라우저 개발자도구 > Network > 아무 API 요청 > Authorization 헤더 값 복사",
        )
        admin_base = st.text_input(
            "Base URL",
            value=os.getenv("ADMIN_API_BASE_URL", "https://dev-admin.us-insight.com"),
        )

        if admin_token:
            os.environ["ADMIN_API_TOKEN"] = admin_token
            os.environ["ADMIN_API_BASE_URL"] = admin_base

        st.divider()

        # ── 실시간 유저 조회 테스트 ──
        st.header("유저 조회 테스트")
        test_phone = st.text_input("전화번호", placeholder="01012345678")
        test_user_id = st.text_input("또는 userId", placeholder="12345")

        if st.button("조회", use_container_width=True):
            if admin_token:
                client = AdminAPIClient(base_url=admin_base, token=admin_token)
                with st.spinner("조회 중..."):
                    if test_user_id:
                        lookup = client.lookup_all(test_user_id)
                    elif test_phone:
                        lookup = client.lookup_by_phone(test_phone)
                    else:
                        lookup = None

                if lookup:
                    st.success("조회 성공")
                    st.session_state["last_lookup"] = lookup
                else:
                    st.error("유저를 찾을 수 없습니다")
            else:
                st.warning("토큰을 입력해주세요")

        # 조회 결과 표시
        if "last_lookup" in st.session_state:
            lookup = st.session_state["last_lookup"]
            st.markdown(lookup.to_display())

            # 환불 시뮬레이션
            if lookup.payments:
                st.divider()
                st.header("환불 시뮬레이션")
                pay = lookup.payments[0]
                st.caption(f"상품: {pay.product_name}")
                st.caption(f"결제금액: {pay.amount:,}원 / 결제일: {pay.payment_date}")

                monthly = st.number_input("1개월 정가", value=pay.monthly_price or pay.amount)
                accessed = st.checkbox("콘텐츠 열람 여부", value=lookup.usage.has_accessed if lookup.usage else False)

                if st.button("환불 계산", use_container_width=True):
                    engine = RefundEngine()
                    try:
                        pdate = datetime.strptime(pay.payment_date[:10], "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        pdate = date.today()
                    inp = RefundInput(
                        total_paid=pay.amount,
                        monthly_price=monthly,
                        payment_date=pdate,
                        payment_cycle_days=pay.payment_cycle_days,
                        content_accessed=accessed,
                    )
                    result = engine.calculate(inp)
                    if result.refundable:
                        st.success(result.to_display())
                    else:
                        st.error(result.to_display())

        st.divider()

        # ── 카테고리 필터 ──
        st.header("카테고리 필터")
        categories = sorted(set(p["category"] for p in DEMO_PATTERNS))
        selected_cat = st.radio("카테고리", ["전체"] + categories)

    if selected_cat == "전체":
        patterns = DEMO_PATTERNS
    else:
        patterns = [p for p in DEMO_PATTERNS if p["category"] == selected_cat]

    # 패턴별 결과
    for i, pattern in enumerate(patterns):
        result = results[pattern["chat_id"]]

        with st.expander(
            f"{render_action_badge(result.action)}  |  **{pattern['category']}** — {pattern['label']}",
            expanded=True,
        ):
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("**고객 문의**")
                st.text_area("", pattern["text"], height=180, disabled=True, key=f"orig_{i}")

            with col2:
                st.markdown("**에이전트 분석**")
                st.markdown(f"분류: **{result.category}** ({result.confidence})")
                st.caption(result.reasoning)

                lookup_text = result.lookup.to_display() if result.lookup else getattr(result, "_lookup_display", "")
                if lookup_text:
                    st.markdown(lookup_text)

                if result.rag_matches:
                    st.markdown("---")
                    st.markdown("**RAG 유사 답변**")
                    for j, m in enumerate(result.rag_matches[:2], 1):
                        sim = 1 - m["distance"]
                        st.caption(f"[참고 {j}] 유사도 {sim:.0%}")
                        st.caption(f"매니저: {m['manager'][:150]}...")

            with col3:
                st.markdown(f"**답변 초안** — {render_action_badge(result.action)}")
                if result.template_matched:
                    st.caption(f"템플릿: {result.template_matched}")
                st.info(result.draft_answer)


if __name__ == "__main__":
    main()
