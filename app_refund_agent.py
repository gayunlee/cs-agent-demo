"""환불 문의 Agent 데모

실제 채널톡 환불 문의 389건으로 agent의 tool-use 과정을 시각화.
- 좌: 고객 메시지 (실제 대화)
- 중: Agent 사고 과정 (thinking → tool calls → results)
- 우: 최종 답변 초안 vs 실제 매니저 응답
"""
from __future__ import annotations
import os
import json
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.refund_agent import RefundAgent, AgentStep

DATA_PATH = Path(__file__).parent / "data" / "refund_test_cases.json"


def init_agent(mock: bool = False):
    return RefundAgent(region="us-west-2", mock=mock)


@st.cache_data(ttl=60)
def load_test_cases():
    if not DATA_PATH.exists():
        return []
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def render_chat_bubble(text: str, role: str = "user", index: int = 0):
    if role == "user":
        bg = "#e3f2fd"
        border = "12px 12px 12px 0"
        label_color = "#1565c0"
        label = f"고객 메시지 {index + 1}"
    else:
        bg = "#e8f5e9"
        border = "12px 12px 0 12px"
        label_color = "#2e7d32"
        label = "매니저 (실제)"

    st.markdown(
        f"""<div style="background:{bg}; border-radius:{border}; padding:10px 14px; margin:4px 0; font-size:14px; line-height:1.6;">
            <span style="color:{label_color}; font-size:11px; font-weight:bold;">{label}</span><br>
            {text.replace(chr(10), '<br>')}
        </div>""",
        unsafe_allow_html=True,
    )


def render_agent_step(step: AgentStep, index: int):
    if step.type == "thinking":
        st.markdown(
            f"""<div style="background:#f3e5f5; border-left:3px solid #7b1fa2; padding:8px 12px; margin:4px 0; font-size:13px;">
                <span style="color:#7b1fa2; font-weight:bold;">🧠 Thinking</span><br>
                {step.content.replace(chr(10), '<br>')}
            </div>""",
            unsafe_allow_html=True,
        )
    elif step.type == "tool_call":
        st.markdown(
            f"""<div style="background:#fff3e0; border-left:3px solid #e65100; padding:8px 12px; margin:4px 0; font-size:13px;">
                <span style="color:#e65100; font-weight:bold;">🔧 Tool Call: {step.tool_name}</span><br>
                <code>{step.content}</code>
            </div>""",
            unsafe_allow_html=True,
        )
    elif step.type == "tool_result":
        # JSON을 가독성 있게
        try:
            data = json.loads(step.content)
            display = json.dumps(data, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            display = step.content
        st.markdown(
            f"""<div style="background:#e8f5e9; border-left:3px solid #2e7d32; padding:8px 12px; margin:4px 0; font-size:12px;">
                <span style="color:#2e7d32; font-weight:bold;">📋 Result: {step.tool_name}</span>
                <pre style="margin:4px 0; font-size:11px; overflow-x:auto;">{display[:500]}</pre>
            </div>""",
            unsafe_allow_html=True,
        )
    elif step.type == "answer":
        st.markdown(
            f"""<div style="background:#fff8e1; border:2px solid #f9a825; border-radius:8px; padding:12px; margin:8px 0; font-size:14px;">
                <span style="color:#f57f17; font-weight:bold;">✨ Agent 최종 답변</span><br><br>
                {step.content.replace(chr(10), '<br>')}
            </div>""",
            unsafe_allow_html=True,
        )


def categorize_case(messages: list[str]) -> str:
    """케이스 하위 분류"""
    full = " ".join(messages).lower()
    if any(kw in full for kw in ["전액", "전부", "다 환불"]):
        return "전액 환불 요청"
    if any(kw in full for kw in ["자동결제", "자동결재", "자동으로", "연장"]):
        return "자동결제 환불"
    if any(kw in full for kw in ["해지", "구독취소", "구독해지"]):
        return "해지 요청"
    if any(kw in full for kw in ["변경", "바꾸", "전환"]):
        return "상품 변경"
    if any(kw in full for kw in ["중복", "이중"]):
        return "중복결제 환불"
    if "환불" in full:
        return "일반 환불 요청"
    return "기타"


def main():
    st.set_page_config(page_title="환불 Agent 데모", layout="wide")
    st.title("🤖 환불 문의 Agent 데모")
    st.caption(
        "실제 채널톡 환불 대화를 agent에 입력 → tool-use 과정 시각화 → 답변 초안 생성"
    )

    cases = load_test_cases()

    if not cases:
        st.error("테스트 케이스 없음. data/refund_test_cases.json 확인")
        return

    with st.sidebar:
        st.header("API 설정")
        admin_token = st.text_input(
            "관리자센터 토큰",
            value=os.getenv("ADMIN_API_TOKEN", ""),
            type="password",
            help="브라우저 개발자도구 > Network > Authorization 헤더 값",
        )
        admin_base = st.text_input(
            "Base URL",
            value=os.getenv("ADMIN_API_BASE_URL", "https://dev-api-admin.us-insight.com"),
        )
        use_mock = st.checkbox("Mock 모드 (API 없이 테스트)", value=not admin_token)

        if admin_token:
            os.environ["ADMIN_API_TOKEN"] = admin_token
            os.environ["ADMIN_API_BASE_URL"] = admin_base

        st.divider()

        st.header("카테고리")

        # 카테고리 분류
        categories = {}
        for c in cases:
            cat = categorize_case(c["user_messages"])
            categories.setdefault(cat, [])
            categories[cat].append(c)

        for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
            st.markdown(f"- **{cat}**: {len(items)}건")

        selected_cat = st.selectbox(
            "필터",
            ["전체"] + sorted(categories.keys(), key=lambda x: -len(categories[x])),
        )

        st.divider()
        st.header("Agent 도구")
        st.markdown("""
        1. `search_user` — 전화번호/이름으로 유저 검색
        2. `get_subscriptions` — 구독 상품 조회
        3. `get_payment_history` — 결제/거래 이력
        4. `check_content_access` — 콘텐츠 열람 여부
        5. `calculate_refund` — 환불 금액 계산
        """)

    agent = init_agent(mock=use_mock)

    # 필터링
    if selected_cat == "전체":
        filtered = cases
    else:
        filtered = categories.get(selected_cat, [])

    # 통계
    total = len(filtered)
    single_msg = sum(1 for c in filtered if c["msg_count"] == 1)
    multi_msg = total - single_msg
    has_mgr = sum(1 for c in filtered if c.get("manager_responses"))

    cols = st.columns(4)
    cols[0].metric("테스트 케이스", f"{total}건")
    cols[1].metric("단일 메시지", f"{single_msg}건")
    cols[2].metric("복수 메시지", f"{multi_msg}건")
    cols[3].metric("매니저 응답 있음", f"{has_mgr}건")

    st.divider()

    # 모드 선택
    mode = st.radio("실행 모드", ["하나씩 보기", "배치 실행"], horizontal=True)

    if mode == "하나씩 보기":
        # 케이스 선택
        case_options = []
        for i, c in enumerate(filtered):
            preview = c["user_messages"][0][:50] if c["user_messages"] else ""
            cat = categorize_case(c["user_messages"])
            case_options.append(f"[{i+1}] {cat} — {preview}...")

        selected_idx = st.selectbox("케이스 선택", range(len(filtered)), format_func=lambda i: case_options[i])
        case = filtered[selected_idx]

        # 실행
        mode_tag = "mock" if use_mock else "live"
        cache_key = f"result_{mode_tag}_{case['chat_id']}"

        if st.button("Agent 실행", use_container_width=True):
            # 버튼 누를 때마다 새로 실행
            with st.spinner("Agent 처리 중..."):
                st.session_state[cache_key] = agent.process(case["user_messages"], case["chat_id"], user_id=case.get("user_id", ""))

        if cache_key in st.session_state:
            result = st.session_state[cache_key]
        else:
            result = None

        if result:

            col1, col2, col3 = st.columns([1, 1.2, 1])

            with col1:
                st.markdown("##### 💬 고객 메시지")
                for i, msg in enumerate(case["user_messages"]):
                    render_chat_bubble(msg, "user", i)

                if case.get("gaps_sec"):
                    st.caption(f"메시지 간격: {', '.join(f'{g:.0f}초' for g in case['gaps_sec'])}")

                st.markdown(f"**카테고리**: {categorize_case(case['user_messages'])}")
                if case.get("user_id"):
                    st.caption(f"userId: `{case['user_id']}`")
                else:
                    st.warning("userId 없음")

            with col2:
                st.markdown("##### 🧠 Agent 사고 과정")
                st.caption(f"사용한 도구: {', '.join(result.tools_used) if result.tools_used else '없음'}")

                for i, step in enumerate(result.steps):
                    render_agent_step(step, i)

            with col3:
                st.markdown("##### 📝 답변 비교")

                st.markdown("**Agent 초안:**")
                if result.final_answer:
                    # 답변 초안 부분만 추출
                    answer_part = result.final_answer
                    if "[답변 초안]" in answer_part:
                        answer_part = answer_part.split("[답변 초안]")[1].strip()
                    st.info(answer_part[:500])

                if case.get("manager_responses"):
                    st.markdown("**실제 매니저 응답:**")
                    for mgr_msg in case["manager_responses"][:2]:
                        render_chat_bubble(mgr_msg[:300], "manager")

    else:  # 배치 실행
        n = st.slider("실행 건수", 5, min(50, len(filtered)), 10)

        if st.button("배치 실행", use_container_width=True):
            progress = st.progress(0)
            results = []

            for i, case in enumerate(filtered[:n]):
                result = agent.process(case["user_messages"], case["chat_id"], user_id=case.get("user_id", ""))
                results.append((case, result))
                progress.progress((i + 1) / n)

            st.session_state["batch_results"] = results

        if "batch_results" in st.session_state:
            results = st.session_state["batch_results"]

            # 통계
            tool_usage = {}
            for _, r in results:
                for t in r.tools_used:
                    tool_usage[t] = tool_usage.get(t, 0) + 1

            has_answer = sum(1 for _, r in results if r.final_answer)
            needs_identity = sum(1 for _, r in results if "본인 확인" in r.final_answer)
            has_refund_calc = sum(1 for _, r in results if "calculate_refund" in r.tools_used)

            st.markdown("### 배치 결과 요약")
            cols = st.columns(4)
            cols[0].metric("처리 완료", f"{has_answer}/{len(results)}")
            cols[1].metric("본인 확인 필요", f"{needs_identity}건")
            cols[2].metric("환불 계산 수행", f"{has_refund_calc}건")
            cols[3].metric("도구 호출 총합", f"{sum(tool_usage.values())}회")

            st.markdown("**도구별 사용 횟수:**")
            for tool, cnt in sorted(tool_usage.items(), key=lambda x: -x[1]):
                st.markdown(f"- `{tool}`: {cnt}회")

            st.divider()

            for i, (case, result) in enumerate(results):
                cat = categorize_case(case["user_messages"])
                preview = case["user_messages"][0][:40]
                tools = ", ".join(result.tools_used) if result.tools_used else "없음"

                with st.expander(f"[{i+1}] {cat} | \"{preview}...\" | 도구: {tools}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**고객:**")
                        for msg in case["user_messages"]:
                            st.caption(f"💬 {msg[:100]}")
                        st.markdown("**Agent:**")
                        # 요약만
                        if "[상담사 요약]" in result.final_answer:
                            summary = result.final_answer.split("[답변 초안]")[0]
                            st.code(summary, language=None)
                    with col2:
                        if "[답변 초안]" in result.final_answer:
                            draft = result.final_answer.split("[답변 초안]")[1].strip()
                            st.markdown("**Agent 초안:**")
                            st.info(draft[:300])
                        if case.get("manager_responses"):
                            st.markdown("**실제 매니저:**")
                            st.success(case["manager_responses"][0][:300])


if __name__ == "__main__":
    main()
