"""모호한 문의 패턴 감지 + 응답 생성 데모

실제 채널톡 대화 데이터를 넣고:
1. 모호 패턴 5가지 중 어디에 해당하는지 분류
2. 트리거 시점 (첫 메시지 vs 전체) 판단
3. clarifying question 초안 생성
"""
from __future__ import annotations
import os
import json
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.ambiguous_classifier import AmbiguousClassifier, AmbiguousAnalysis

DATA_DIR = os.getenv("DATA_DIR", "/Users/gygygygy/Documents/ai/letter-post-weekly-report/data/channel_io")

# ── 실제 데이터에서 추출한 대표 샘플 ──
DEMO_SAMPLES = [
    # 패턴 1: 환불/해지 — 정보 부족
    {
        "id": "refund_01",
        "label": "환불 단순 요청 (정보 없음)",
        "pattern_expected": "환불_해지_정보부족",
        "messages": ["환불해주세요"],
        "note": "상품명·본인확인 없이 환불만 요청",
    },
    {
        "id": "refund_02",
        "label": "해지 요청 + 전화번호 제공",
        "pattern_expected": "환불_해지_정보부족",
        "messages": [
            "안녕하세요 다름이아니라 피치못할사정으로 박두환투자교실 탈퇴하려합니다. 자동이체 해지해주시면 고맙겠습니다.",
            "010 3464 0726",
        ],
        "note": "상품명은 있지만 두 번째 메시지에서 전화번호 추가 — 끊어 보내기 패턴",
    },
    {
        "id": "refund_03",
        "label": "자동결제 여부 확인",
        "pattern_expected": "환불_해지_정보부족",
        "messages": [
            "박두환쌤의 학우입니다",
            "8일이 재결재일인데 자동결재가 되는것인지 궁금합니다",
        ],
        "note": "두 메시지에 걸쳐 맥락 전달 — 본인확인 없음",
    },
    {
        "id": "refund_04",
        "label": "자동결제 취소 단문",
        "pattern_expected": "환불_해지_정보부족",
        "messages": ["자동결제 취소 부탁드려요"],
        "note": "가장 짧은 형태의 해지 요청",
    },
    # 패턴 2: 결제 맥락 불명
    {
        "id": "payment_01",
        "label": "다음 결제일 문의",
        "pattern_expected": "결제_맥락불명",
        "messages": [
            "안녕하세요",
            "제가 박두환 선생님 맴버십 가입자인데 한달만 결제를 했는데 다음달 결제는 어떻게 해야 하나요",
            "7월에 7월 5일날 결제 했는데 다음 결제일에 알람이 오는지 궁금합니다",
        ],
        "note": "3개 메시지에 걸쳐 끊어 보냄 — 인사 → 질문 → 상세",
    },
    {
        "id": "payment_02",
        "label": "법인카드 결제 오류",
        "pattern_expected": "결제_맥락불명",
        "messages": ["법인카드로 결재가 오류가 나는데 법인카드는 원래 못쓰나요?"],
        "note": "단일 메시지, 결제 수단 관련 문의",
    },
    {
        "id": "payment_03",
        "label": "1개월→6개월 변경",
        "pattern_expected": "결제_맥락불명",
        "messages": [
            "1개월로 결재했는데 6개월로 변경하고 싶어서요",
        ],
        "note": "상품 변경 의도이나 본인확인 없음",
    },
    {
        "id": "payment_04",
        "label": "예상치 못한 결제",
        "pattern_expected": "결제_맥락불명",
        "messages": [
            "결재신청을 하지 않았는데 결재진행이 되었습니다.",
            "바로 반환 처리 부탁합니다.",
            "고객센터는 전화통확 안되고 있습니다.",
        ],
        "note": "긴급 문의, 감정적 — 3메시지 연속",
    },
    # 패턴 3: 기능/이용 — 추상적
    {
        "id": "feature_01",
        "label": "녹화본 위치 문의",
        "pattern_expected": "기능_이용_추상적",
        "messages": [
            "줌라이브를 시간이 안맞아 못듣는데 녹화본은 ㅇ니디에 있나요?",
            "라이브 녹화는 어디서 듣나요",
        ],
        "note": "같은 질문을 다른 표현으로 반복",
    },
    {
        "id": "feature_02",
        "label": "앱 오류 (추상적)",
        "pattern_expected": "기능_이용_추상적",
        "messages": ["음성파일이 열리지 않고 반복적으로 오류발생 문의하라고 뜸니다. 빠른해결원합니다."],
        "note": "기술 오류이나 기기/환경 정보 없음",
    },
    {
        "id": "feature_03",
        "label": "라이브 입장 어려움",
        "pattern_expected": "기능_이용_추상적",
        "messages": [
            "입장이 너무어려워요",
            "라이브 수업입장너무어려워요",
        ],
        "note": "추상적 불만 → 약간 구체화, 하지만 여전히 증상 불분명",
    },
    {
        "id": "feature_04",
        "label": "결제 재시도 안됨",
        "pattern_expected": "기능_이용_추상적",
        "messages": [
            "박두환",
            "멤버쉽   결제",
            "다시 하고 싶은데   안되어서 ~",
        ],
        "note": "3메시지 끊어 보내기 — 키워드만 나열",
    },
    # 패턴 4: 맥락 없음
    {
        "id": "nocontext_01",
        "label": "구독 만료 확인 (맥락 부재)",
        "pattern_expected": "맥락없음",
        "messages": ["이정윤샘 구독이 17일까진데요", "자동연장인가요?"],
        "note": "이전 상담 이어서 온 듯 — 본인확인 없음",
    },
    {
        "id": "nocontext_02",
        "label": "이번달까지만 (맥락 부재)",
        "pattern_expected": "맥락없음",
        "messages": ["네! 이번달 까지만 진행하겠습니다"],
        "note": "이전 대화의 답변인 듯 — 뭘 이번달까지인지 불분명",
    },
    {
        "id": "nocontext_03",
        "label": "오프라인 불참 알림",
        "pattern_expected": "맥락없음",
        "messages": [
            "안녕하세요",
            "참석하기로 한 오프라인 강의에 못 갈거 같아요",
        ],
        "note": "어떤 강의인지, 본인이 누구인지 없음",
    },
    {
        "id": "nocontext_04",
        "label": "수업 신청 (정보만 나열)",
        "pattern_expected": "맥락없음",
        "messages": ["최선옥 010-4181-4066  박두한선생님 수업 신청"],
        "note": "이름+번호+요청만 — 어떤 수업인지 없음",
    },
    # Track 2 대상: 정보 조회형 (명확한 문의)
    {
        "id": "lookup_01",
        "label": "[Track2] 환불 — 정보 충분",
        "pattern_expected": "모호하지_않음",
        "messages": [
            "안녕하세요. 체밀턴 짱짱구 이성분입니다",
            "8월초에 1등매니저 따라하기 55만원에 신청했는데 취소하고자 합니다. 취소 부탁드려요.",
        ],
        "note": "이름+상품명+금액 모두 있음 → 바로 정보 조회 가능 (Track 2)",
    },
    {
        "id": "lookup_02",
        "label": "[Track2] 로그인 방법 문의 — 가입방법 조회 필요",
        "pattern_expected": "모호하지_않음",
        "messages": [
            "카카오로 시작되기 로그인으로 설정했는데 카카오톡을 지워야하는 사정이라 로그인 방법을 바꾸고싶습니다.",
        ],
        "note": "의도 명확 → 가입방법 조회해서 대안 안내 (Track 2)",
    },
]

# ── 커스텀 메시지 입력용 ──
CUSTOM_PRESETS = {
    "직접 입력": [],
    "환불요망 (한 줄)": ["환불요망"],
    "로그인이 안돼요": ["로그인이 안돼요"],
    "결제일 변경 불가 한가요???": ["결제일 변경 불가 한가요??? 개인적인 사정으로 변경 하고 싶습니다"],
    "끊어 보내기 (3줄)": ["안녕하세요", "구독 취소 하고 싶어요", "환불도 가능한가요?"],
}


@st.cache_resource
def init_classifier():
    mock = os.getenv("MOCK_MODE", "0") == "1"
    return AmbiguousClassifier(region="us-west-2", mock=mock)


def render_chat_bubbles(messages: list[str]):
    """채팅 버블 UI 렌더링"""
    for i, msg in enumerate(messages):
        st.markdown(
            f"""<div style="
                background: #e3f2fd;
                border-radius: 12px 12px 12px 0;
                padding: 10px 14px;
                margin: 4px 0;
                max-width: 85%;
                font-size: 14px;
                line-height: 1.5;
            ">
                <span style="color: #666; font-size: 11px;">유저 메시지 {i+1}</span><br>
                {msg}
            </div>""",
            unsafe_allow_html=True,
        )


def render_ai_response(question: str):
    """AI 응답 버블"""
    st.markdown(
        f"""<div style="
            background: #fff3e0;
            border-radius: 12px 12px 0 12px;
            padding: 10px 14px;
            margin: 4px 0 4px auto;
            max-width: 85%;
            font-size: 14px;
            line-height: 1.5;
            text-align: left;
        ">
            <span style="color: #e65100; font-size: 11px;">AI 초안 (내부대화)</span><br>
            {question.replace(chr(10), '<br>')}
        </div>""",
        unsafe_allow_html=True,
    )


def render_analysis_card(analysis: AmbiguousAnalysis):
    """분석 결과 카드"""
    # 패턴 뱃지
    pattern_colors = {
        "환불_해지_정보부족": "#e53935",
        "결제_맥락불명": "#fb8c00",
        "기능_이용_추상적": "#7b1fa2",
        "맥락없음": "#546e7a",
        "CS범위밖": "#78909c",
        "모호하지_않음": "#43a047",
    }
    color = pattern_colors.get(analysis.pattern, "#666")

    st.markdown(f"""
    **패턴**: <span style="color:{color}; font-weight:bold">{analysis.pattern_label}</span>
    &nbsp;|&nbsp; 신뢰도: **{analysis.confidence}**
    """, unsafe_allow_html=True)

    st.caption(f"근거: {analysis.reasoning}")

    # 트리거 시점
    trigger_icons = {
        "first_message": "⚡",
        "all_messages": "📋",
        "wait_more": "⏳",
    }
    icon = trigger_icons.get(analysis.trigger_timing, "❓")
    st.markdown(f"**트리거**: {icon} {analysis.trigger_label}")
    st.caption(analysis.trigger_explanation)

    # 부족한 정보
    if analysis.missing_info:
        st.markdown("**부족한 정보:**")
        for info in analysis.missing_info:
            st.markdown(f"- {info}")

    # 응대 유형
    resp_icons = {"A": "🪪", "B": "📦", "C": "🔍", "D": "💬"}
    st.markdown(
        f"**응대 유형**: {resp_icons.get(analysis.response_type, '')} "
        f"**{analysis.response_type}. {analysis.response_type_label}**"
    )


def load_real_conversations(n: int = 50) -> list[dict]:
    """실제 데이터에서 모호 패턴 후보 대화 로드"""
    path = Path(DATA_DIR) / "classified_2025-08-01_2025-12-01.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    candidates = []
    for item in data["items"]:
        um = item["user_message_count"]
        if um < 1 or um > 4:
            continue
        text = item["text"].strip()
        if not text or len(text) > 300:
            continue
        messages = [l.strip() for l in text.split("\n") if l.strip()]
        if not messages:
            continue
        candidates.append({
            "id": item["chatId"],
            "messages": messages,
            "route": item["route"],
            "user_msgs": um,
            "mgr_msgs": item["manager_message_count"],
        })
        if len(candidates) >= n:
            break
    return candidates


def main():
    st.set_page_config(page_title="모호한 문의 패턴 감지 데모", layout="wide")
    st.title("모호한 문의 패턴 감지 + 응답 생성 데모")
    st.caption(
        "채널톡 첫 문의가 모호한 패턴에 해당하면 → 분류 + 트리거 시점 판단 + clarifying question 초안 생성"
    )

    classifier = init_classifier()

    # ── 사이드바: 모드 선택 ──
    with st.sidebar:
        st.header("설정")
        mode = st.radio("데이터 소스", ["대표 샘플 (큐레이션)", "실제 대화 (랜덤)", "직접 입력"])

        st.divider()
        st.header("패턴 분류 기준")
        st.markdown("""
        1. **환불/해지 정보 부족** — 의도 O, 상품/본인확인 X
        2. **결제 맥락 불명** — 결제 관련이지만 왜/뭘 모름
        3. **기능/이용 추상적** — 안 되는데 뭐가 안 되는지 모호
        4. **맥락 없음** — 이전 대화 연속인 듯
        5. **CS 범위 밖** — 투자 조언 등
        """)

        st.divider()
        st.header("매니저 응대 유형")
        st.markdown("""
        - **A. 본인확인 요청** — 성함/전화번호
        - **B. 상품 특정** — 어떤 과정?
        - **C. 증상 구체화** — 어떤 화면/상황?
        - **D. 오픈 질문** — 무엇을 도와드릴까요?
        """)

    # ── 메인 영역 ──
    if mode == "대표 샘플 (큐레이션)":
        # 패턴 필터
        patterns = sorted(set(s["pattern_expected"] for s in DEMO_SAMPLES))
        selected = st.multiselect(
            "패턴 필터",
            patterns,
            default=patterns,
            format_func=lambda x: {
                "환불_해지_정보부족": "1. 환불/해지 정보 부족",
                "결제_맥락불명": "2. 결제 맥락 불명",
                "기능_이용_추상적": "3. 기능/이용 추상적",
                "맥락없음": "4. 맥락 없음",
                "모호하지_않음": "✅ 명확한 문의 (Track 2 대상)",
            }.get(x, x),
        )
        samples = [s for s in DEMO_SAMPLES if s["pattern_expected"] in selected]

        # 통계
        total = len(samples)
        by_pattern = {}
        for s in samples:
            by_pattern.setdefault(s["pattern_expected"], 0)
            by_pattern[s["pattern_expected"]] += 1

        cols = st.columns(len(by_pattern) + 1)
        cols[0].metric("전체", f"{total}건")
        for i, (p, cnt) in enumerate(by_pattern.items(), 1):
            cols[i].metric(p.replace("_", " "), f"{cnt}건")

        st.divider()

        # 각 샘플 처리
        for sample in samples:
            with st.expander(
                f"**{sample['label']}** — 메시지 {len(sample['messages'])}개",
                expanded=True,
            ):
                st.caption(f"💡 {sample['note']}")

                # 분석 실행 (캐시)
                cache_key = f"analysis_{sample['id']}"
                if cache_key not in st.session_state:
                    st.session_state[cache_key] = classifier.analyze(sample["messages"])
                analysis = st.session_state[cache_key]

                col1, col2, col3 = st.columns([1, 1, 1])

                with col1:
                    st.markdown("##### 고객 메시지")
                    render_chat_bubbles(sample["messages"])

                with col2:
                    st.markdown("##### AI 분석")
                    render_analysis_card(analysis)

                    # 기대 패턴과 비교
                    if analysis.pattern == sample["pattern_expected"]:
                        st.success(f"✓ 기대 패턴 일치")
                    else:
                        st.warning(
                            f"△ 기대: {sample['pattern_expected']} → 실제: {analysis.pattern}"
                        )

                with col3:
                    st.markdown("##### 생성된 초안")
                    if analysis.clarifying_question:
                        render_ai_response(analysis.clarifying_question)
                    else:
                        st.info("이 문의는 모호하지 않아 clarifying question 불필요 → Track 2 (정보 조회) 대상")

    elif mode == "실제 대화 (랜덤)":
        st.info("실제 3개월 대화 데이터에서 짧은 문의(1~4 메시지)를 로드합니다.")

        n = st.slider("로드할 대화 수", 10, 100, 30)
        if st.button("대화 로드 + 분석", use_container_width=True):
            convos = load_real_conversations(n)
            if not convos:
                st.error("데이터 파일을 찾을 수 없습니다.")
                return

            progress = st.progress(0)
            results = []
            for i, c in enumerate(convos):
                analysis = classifier.analyze(c["messages"])
                results.append((c, analysis))
                progress.progress((i + 1) / len(convos))

            st.session_state["real_results"] = results

        if "real_results" in st.session_state:
            results = st.session_state["real_results"]

            # 통계
            pattern_counts = {}
            trigger_counts = {}
            for _, a in results:
                pattern_counts[a.pattern] = pattern_counts.get(a.pattern, 0) + 1
                trigger_counts[a.trigger_timing] = trigger_counts.get(a.trigger_timing, 0) + 1

            st.markdown("### 분석 결과 요약")
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**패턴 분포**")
                for p, cnt in sorted(pattern_counts.items(), key=lambda x: -x[1]):
                    pct = cnt / len(results) * 100
                    st.markdown(f"- {p}: **{cnt}건** ({pct:.0f}%)")
            with col2:
                st.markdown("**트리거 시점 분포**")
                trigger_labels = {
                    "first_message": "⚡ 첫 메시지에서 즉시",
                    "all_messages": "📋 전체 메시지 필요",
                    "wait_more": "⏳ 추가 대기",
                }
                for t, cnt in sorted(trigger_counts.items(), key=lambda x: -x[1]):
                    st.markdown(f"- {trigger_labels.get(t, t)}: **{cnt}건**")

            st.divider()

            # 패턴 필터
            filter_pattern = st.selectbox(
                "패턴 필터",
                ["전체"] + sorted(pattern_counts.keys()),
            )

            for c, a in results:
                if filter_pattern != "전체" and a.pattern != filter_pattern:
                    continue

                with st.expander(
                    f"[{c['id'][:8]}] {a.pattern_label} | 메시지 {len(c['messages'])}개 | {c['route']}",
                ):
                    col1, col2, col3 = st.columns([1, 1, 1])
                    with col1:
                        render_chat_bubbles(c["messages"])
                    with col2:
                        render_analysis_card(a)
                    with col3:
                        if a.clarifying_question:
                            render_ai_response(a.clarifying_question)

    else:  # 직접 입력
        st.markdown("### 직접 메시지를 입력해 패턴 감지를 테스트하세요")

        preset = st.selectbox("프리셋", list(CUSTOM_PRESETS.keys()))

        if preset != "직접 입력":
            default_text = "\n".join(CUSTOM_PRESETS[preset])
        else:
            default_text = ""

        user_input = st.text_area(
            "고객 메시지 (줄바꿈 = 개별 메시지)",
            value=default_text,
            height=120,
            placeholder="환불해주세요\n상품명은 뭐뭐입니다",
        )

        if st.button("분석", use_container_width=True) and user_input.strip():
            messages = [l.strip() for l in user_input.strip().split("\n") if l.strip()]

            with st.spinner("분석 중..."):
                analysis = classifier.analyze(messages)

            col1, col2, col3 = st.columns([1, 1, 1])

            with col1:
                st.markdown("##### 고객 메시지")
                render_chat_bubbles(messages)

            with col2:
                st.markdown("##### AI 분석")
                render_analysis_card(analysis)

            with col3:
                st.markdown("##### 생성된 초안")
                if analysis.clarifying_question:
                    render_ai_response(analysis.clarifying_question)
                else:
                    st.info("명확한 문의 → Track 2 대상")


if __name__ == "__main__":
    main()
