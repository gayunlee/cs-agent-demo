"""환불 Agent v2 데모 — mock 데이터 기반

mock 시나리오 선택 → 유저 메시지 입력 → 조회 결과 + 판단 근거 + 답변 초안
"""
from __future__ import annotations
import os
import json
from pathlib import Path
import streamlit as st
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from src.refund_agent_v2 import RefundAgentV2, AgentResultV2
from src.workflow import WorkflowContext, run_workflow
from src.templates import TEMPLATES

GOLDEN_DIR = Path("data/mock_scenarios/golden")


def load_golden_scenarios():
    """골든셋 시나리오 로드 (api-interfaces.md 스펙 기반 mock)"""
    scenarios = {}
    if not GOLDEN_DIR.exists():
        return scenarios
    for p in sorted(GOLDEN_DIR.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
            key = p.stem  # 파일명 (확장자 제외)
            scenarios[key] = s
    return scenarios


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def load_mocks():
    with open("data/mock_scenarios/mock_api_responses.json") as f:
        mocks = json.load(f)
    # 날짜 플레이스홀더 치환
    raw = json.dumps(mocks)
    raw = raw.replace('"RECENT_3DAYS"', f'"{days_ago(3)}"')
    raw = raw.replace('"RECENT_5DAYS"', f'"{days_ago(5)}"')
    raw = raw.replace('"RECENT_20DAYS"', f'"{days_ago(20)}"')
    raw = raw.replace('"RECENT_3DAYS_PLUS_6M"', f'"{days_ago(-180)}"')
    raw = raw.replace('"RECENT_5DAYS_PLUS_6M"', f'"{days_ago(-175)}"')
    raw = raw.replace('"RECENT_5DAYS_PLUS_1M"', f'"{days_ago(-25)}"')
    raw = raw.replace('"RECENT_20DAYS_PLUS_1M"', f'"{days_ago(-10)}"')
    return json.loads(raw)


def render_evidence(mock_data, wf_ctx, template_id):
    """조회 결과 + 판단 근거"""
    st.markdown("##### 📋 조회 결과")

    # 유저 정보
    user = mock_data.get("search_result")
    if user:
        st.markdown(f"""
**회원 정보**
- 이름: **{user.get('name', '?')}**
- 가입: {user.get('signup_method', '?')}
- 전화번호: {user.get('phone', '?')}
        """)
    elif mock_data.get("us_user_id"):
        st.markdown(f"**회원 ID**: `{mock_data['us_user_id']}`")
    else:
        st.warning("유저 식별 불가")

    # 상품
    products = mock_data.get("products", [])
    if products:
        st.markdown("**보유 상품**")
        for p in products:
            name = p.get("name", "?")
            master = p.get("master", "")
            status = p.get("status", "?")
            price = p.get("price", 0)
            st.markdown(f"- {master} / {name} (`{status}`) {price:,}원")

    # 결제 이력
    transactions = mock_data.get("transactions", [])
    if transactions:
        st.markdown("**결제 이력**")
        for t in transactions:
            amt = t.get("amount", 0)
            if isinstance(amt, str):
                try: amt = int(amt)
                except: amt = 0
            dt = (t.get("date") or "")[:10]
            state = t.get("state", "")
            if "success" in state:
                st.markdown(f"- 결제 **{amt:,}원** ({dt}) — {t.get('info', '')}")
            elif "refund" in state:
                st.markdown(f"- ~~환불 {amt:,}원 ({dt})~~")
    else:
        st.caption("결제 이력 없음")

    # 열람
    usage = mock_data.get("usage", {})
    accessed = usage.get("accessed", False)
    count = usage.get("count", 0)
    st.markdown(f"**콘텐츠 열람**: {'있음' if accessed else '없음'} ({count}건)")

    # 판단 근거
    st.divider()
    st.markdown("##### 🔀 판단 근거")
    st.markdown(f"**경로**: `{' → '.join(wf_ctx.path)}`")

    # 환불 계산
    vars = wf_ctx.template_variables
    refund_type = vars.get("환불유형", "")
    if refund_type == "full":
        st.success(f"전액 환불 — **{vars.get('환불금액', '?')}원**")
    elif refund_type == "partial":
        st.info(f"""
부분 환불
- 환불 금액: **{vars.get('환불금액', '?')}원**
- 차감금: {vars.get('차감금', '?')}원
- 수수료: {vars.get('수수료', '?')}원
        """)


def render_answer(template_id, wf_ctx):
    """템플릿 + 변수 → 최종 답변"""
    tmpl = TEMPLATES.get(template_id, {})
    template_text = tmpl.get("template", "")

    # 전액/부분 분기
    vars = wf_ctx.template_variables
    if vars.get("환불유형") == "full" and tmpl.get("template_full_refund"):
        template_text = tmpl["template_full_refund"]

    # 변수 치환
    for key, val in vars.items():
        template_text = template_text.replace(f"{{{key}}}", str(val))

    return template_text


def render_golden_scenario(scenario: dict, use_real_llm: bool):
    """골든셋 시나리오 — RefundAgentV2.process 전체 경로 (T_LLM_FALLBACK 포함)"""
    agent = RefundAgentV2(mock=not use_real_llm)
    result = agent.process(
        user_messages=scenario["user_messages"],
        chat_id=scenario.get("scenario", "golden"),
        admin_data=scenario["admin_data"],
        conversation_time=scenario.get("conversation_time", ""),
        conversation_turns=scenario.get("conversation_turns") or [],
    )
    expected = scenario.get("expected", {}).get("template_id", "")

    col1, col2, col3 = st.columns([1, 1.2, 1])

    with col1:
        st.markdown("##### 💬 고객 메시지")
        for msg in scenario["user_messages"]:
            st.markdown(
                f"""<div style="background:#e3f2fd; border-radius:12px 12px 12px 0; padding:10px 14px; margin:4px 0; font-size:14px;">{msg}</div>""",
                unsafe_allow_html=True,
            )
        st.divider()
        st.markdown(f"**시나리오**: {scenario['scenario']}")
        st.caption(scenario.get("description", ""))
        if scenario.get("conversation_time"):
            st.caption(f"대화 시점: {scenario['conversation_time'][:10]}")

    with col2:
        st.markdown("##### 📋 조회 결과 (mock)")
        admin = scenario["admin_data"]
        st.markdown(f"- 유저: **{admin.get('ch_name', '?')}** ({admin.get('phone', '?')})")
        txs = admin.get("transactions", [])
        if txs:
            st.markdown(f"- 결제 {len(txs)}건")
            for t in txs:
                state_icon = "💳" if "success" in (t.get("state") or "") else "↩️"
                st.caption(f"  {state_icon} {t.get('date','')[:10]} · {t.get('amount',0):,}원 · {t.get('info','')}")
        refunds = admin.get("refunds") or []
        if refunds:
            st.markdown(f"- 환불 이력 {len(refunds)}건")
            for r in refunds:
                rh = r.get("refundHistory") or {}
                pending = not rh.get("refundAt")
                status = "⏳ 진행중" if pending else "✅ 완료"
                st.caption(f"  {status} · {rh.get('refundAmount', 0):,}원")
        st.markdown(f"- 열람: {'있음' if admin.get('usage', {}).get('accessed') else '없음'}")

        st.divider()
        st.markdown("##### 🔀 워크플로우 경로")
        for step in result.steps:
            if step.step == "classify":
                path = step.detail.get("path") or []
                if path:
                    st.code(" → ".join(path))
                break

    with col3:
        st.markdown(f"##### 📝 답변 초안")
        st.caption(f"템플릿: {result.template_id}")
        if result.template_id == expected:
            st.success(f"✓ 기대 템플릿 일치")
        elif expected:
            st.error(f"✗ 기대: {expected}")

        if result.final_answer:
            st.markdown(
                f"""<div style="background:#fff8e1; border:2px solid #f9a825; border-radius:8px; padding:14px; font-size:14px; line-height:1.8;">{result.final_answer.replace(chr(10), '<br>')}</div>""",
                unsafe_allow_html=True,
            )


def main():
    st.set_page_config(page_title="환불 Agent v2", layout="wide")
    st.title("환불/해지 상담 어시스턴트")
    st.caption("mock 시나리오 선택 → 메시지 입력 → 조회 결과 + 판단 근거 + 답변 초안")

    # 데이터셋 선택: Legacy vs Golden
    dataset = st.radio(
        "데이터셋",
        ["🌟 골든셋 (신규, api-interfaces.md 기반)", "📦 Legacy mock"],
        horizontal=True,
    )

    if dataset.startswith("🌟"):
        golden = load_golden_scenarios()
        if not golden:
            st.warning("골든셋이 비어있습니다. `data/mock_scenarios/golden/` 확인.")
            return
        col_sel, col_llm = st.columns([3, 1])
        with col_sel:
            key = st.selectbox(
                "골든셋 시나리오",
                list(golden.keys()),
                format_func=lambda k: f"{golden[k]['scenario']}",
            )
        with col_llm:
            use_real = st.checkbox("LLM fallback 실제 호출", value=False,
                                    help="체크 시 T_LLM_FALLBACK 케이스에서 실제 Bedrock 호출")
        with st.expander("시나리오 상세"):
            st.markdown(f"**설명**: {golden[key].get('description', '')}")
            st.json(golden[key], expanded=False)
        if st.button("초안 생성", use_container_width=True, type="primary"):
            render_golden_scenario(golden[key], use_real_llm=use_real)
        return

    mocks = load_mocks()

    # 사이드바 — 시나리오 + 템플릿
    with st.sidebar:
        st.header("답변 정책")
        st.markdown("""
| 데이터 상태 | 답변 |
|---|---|
| 유저 식별 불가 | 본인확인 요청 |
| 결제 없음 | 해지 방법 안내 |
| 미환불 + 미열람 | 환불 규정 (전액) |
| 미환불 + 열람 | 환불 규정 (부분) |
| 전부 환불됨 | 접수 완료 |
| 카드 키워드 | 카드변경 안내 |
        """)

    # 메인 — 시나리오 + 메시지
    col_scenario, col_msg = st.columns([1, 1])

    with col_scenario:
        scenario = st.selectbox(
            "유저 데이터 시나리오",
            list(mocks.keys()),
            format_func=lambda k: f"{k} — {mocks[k]['description']}",
        )

    with col_msg:
        presets = {
            "환불해주세요": "환불해주세요",
            "해지요청": "해지요청",
            "자동결제 됐어요": "어제 자동으로 구독이 연장되어 결제가 진행되었습니다. 구독 취소 부탁드립니다.",
            "환불 가능한가요?": "환불이 가능한가요?",
            "카드 변경": "카드 변경하고 싶습니다",
            "직접 입력": "",
        }
        preset = st.selectbox("메시지 프리셋", list(presets.keys()))
        user_msg = st.text_area("고객 메시지", value=presets[preset], height=80)

    if st.button("초안 생성", use_container_width=True, type="primary") and user_msg:
        mock = mocks[scenario]
        messages = [m.strip() for m in user_msg.strip().split("\n") if m.strip()]

        # 워크플로우 실행
        wf_ctx = WorkflowContext(
            user_messages=messages,
            us_user_id=mock.get("us_user_id", ""),
            products=mock.get("products", []),
            transactions=mock.get("transactions", []),
            has_accessed=mock.get("usage", {}).get("accessed", False),
            memberships=mock.get("memberships", []),
            refunds=mock.get("refunds", []),
        )
        template_id = run_workflow(wf_ctx)
        answer = render_answer(template_id, wf_ctx)

        # 3단 레이아웃
        col1, col2, col3 = st.columns([1, 1.2, 1])

        with col1:
            st.markdown("##### 💬 고객 메시지")
            for i, msg in enumerate(messages):
                st.markdown(
                    f"""<div style="background:#e3f2fd; border-radius:12px 12px 12px 0; padding:10px 14px; margin:4px 0; font-size:14px;">
                        {msg}
                    </div>""",
                    unsafe_allow_html=True,
                )
            st.divider()
            st.markdown(f"**시나리오**: `{scenario}`")
            st.caption(mock["description"])

        with col2:
            render_evidence(mock, wf_ctx, template_id)

        with col3:
            st.markdown(f"##### 📝 답변 초안")
            st.caption(f"템플릿: {template_id}")
            if answer:
                st.markdown(
                    f"""<div style="background:#fff8e1; border:2px solid #f9a825; border-radius:8px; padding:14px; font-size:14px; line-height:1.8;">
                        {answer.replace(chr(10), '<br>')}
                    </div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.warning("답변 생성 실패")

            # 기대 템플릿과 비교
            expected = mock.get("expected_template", "")
            if expected == template_id:
                st.success(f"✓ 기대 템플릿 일치: {expected}")
            elif expected:
                st.error(f"✗ 기대: {expected} / 실제: {template_id}")


if __name__ == "__main__":
    main()
