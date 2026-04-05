"""환불 Agent v2 데모 — mock 데이터 기반

mock 시나리오 선택 → 유저 메시지 입력 → 조회 결과 + 판단 근거 + 답변 초안
"""
from __future__ import annotations
import os
import re
import json
from pathlib import Path
import streamlit as st
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

from src.refund_agent_v2 import RefundAgentV2, AgentResultV2
from src.workflow import WorkflowContext, run_workflow
from src.templates import TEMPLATES

GOLDEN_DIR = Path("data/mock_scenarios/golden")
TEST_CASES_DIR = Path("data/test_cases")


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
                f"""<div style="color:#212121; background:#e3f2fd; border-radius:12px 12px 12px 0; padding:10px 14px; margin:4px 0; font-size:14px;">{msg}</div>""",
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
        st.markdown(f"**유저**: `{admin.get('ch_name', '?')}` ({admin.get('phone', '?')})")

        # 상품 전체
        products = admin.get("products") or []
        if products:
            st.markdown(f"**상품** ({len(products)}건)")
            for p in products:
                master = p.get('master') or p.get('master_name') or ''
                name = p.get('name', '')
                price = p.get('price') or 0
                status = p.get('status', '')
                status_icon = "🟢" if status == "active" else ("⚫" if status == "inactive" else "⚪")
                st.markdown(
                    f"- {status_icon} **{master}** / {name}  \n"
                    f"  └ 가격: {price:,}원 · status: `{status}`"
                )

        # 결제 내역 전체
        txs = admin.get("transactions") or []
        if txs:
            st.markdown(f"**결제 내역** ({len(txs)}건)")
            for t in txs:
                amt = t.get("amount", 0)
                if isinstance(amt, str):
                    try: amt = int(amt)
                    except: amt = 0
                state = t.get("state", "")
                date = (t.get("date") or t.get("created_at") or "")[:10]
                info = t.get("info") or t.get("method") or ""
                round_no = t.get("round", "")
                icon = "💳" if state == "purchased_success" else ("↩️" if state == "purchased_refund" else "❓")
                label = "결제" if state == "purchased_success" else ("환불" if state == "purchased_refund" else state)
                round_txt = f" {round_no}회차" if round_no else ""
                st.caption(f"{icon} {date} · **{amt:,}원** · {label}{round_txt} · {info}")
        else:
            st.caption("결제 이력 없음")

        # 멤버십 정보
        memberships = admin.get("memberships") or []
        if memberships:
            st.markdown(f"**멤버십** ({len(memberships)}건)")
            for mb in memberships:
                pname = mb.get("productName", "")
                cycle = mb.get("paymentCycle", "")
                expired = mb.get("expiration", False)
                mtype = mb.get("memberShipType") or mb.get("membershipType") or ""
                exp_icon = "⏰ 만료" if expired else "✅ 활성"
                st.caption(
                    f"- {pname}  \n"
                    f"  └ 결제회차: {cycle} · 타입: `{mtype}` · {exp_icon}"
                )

        # 환불 이력
        refunds = admin.get("refunds") or []
        if refunds:
            st.markdown(f"**환불 이력** ({len(refunds)}건)")
            for r in refunds:
                rh = r.get("refundHistory") or r
                refund_at = (rh.get("refundAt") or rh.get("createdAt") or "")[:10]
                refund_amt = rh.get("refundAmount", 0)
                pending = not rh.get("refundAt")
                status = "⏳ 진행중" if pending else "✅ 완료"
                st.caption(f"- {status} · {refund_at} · {refund_amt:,}원")

        # 열람
        st.markdown(f"**콘텐츠 열람**: {'있음' if admin.get('usage', {}).get('accessed') else '없음'} ({admin.get('usage', {}).get('count', 0)}건)")

        st.divider()
        st.markdown("##### 🔀 워크플로우 경로")
        for step in result.steps:
            if step.step == "classify":
                path = step.detail.get("path") or []
                if path:
                    st.code(" → ".join(path))
                break

    with col3:
        st.markdown(f"##### 🤖 Agent 답변 초안")
        st.caption(f"템플릿: {result.template_id}")
        if result.template_id == expected:
            st.success(f"✓ 기대 템플릿 일치")
        elif expected:
            st.error(f"✗ 기대: {expected}")

        if result.final_answer:
            st.markdown(
                f"""<div style="color:#212121; background:#fff8e1; border-left:4px solid #f9a825; border-radius:4px; padding:12px; font-size:13px; line-height:1.7; white-space:pre-wrap; max-height:420px; overflow-y:auto;">{result.final_answer}</div>""",
                unsafe_allow_html=True,
            )

        # ── 실제 상담사 답변 (source_chat_id 있으면 enriched에서 pull) ──
        st.markdown("##### 👤 실제 상담사 답변")
        src_id = scenario.get("source_chat_id")
        if src_id:
            enriched_map = _load_enriched_for_manager()
            src = enriched_map.get(src_id)
            if src:
                mr = src.get("manager_responses") or []
                mr_text = "\n\n".join(mr) if isinstance(mr, list) else str(mr)
                if mr_text:
                    st.caption(f"source: `{src_id[:16]}...`")
                    st.markdown(
                        f"""<div style="color:#212121; background:#e8f5e9; border-left:4px solid #43a047; border-radius:4px; padding:12px; font-size:13px; line-height:1.7; white-space:pre-wrap; max-height:420px; overflow-y:auto;">{mr_text[:3000]}</div>""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("(매니저 답변 데이터 없음)")
            else:
                st.caption(f"source_chat_id `{src_id}` enriched에서 못 찾음")
        else:
            st.caption("(수동 작성 mock — 실제 상담사 답변 없음)")


# ───────────────────────────────────────────────────────────────
# v2 골든셋 구축 진행 상황 — data/mock_scenarios/golden/v2/*.json
# Gayoon이 수동 작성한 시나리오 + expected 대비 agent 실제 결과 대조
# ───────────────────────────────────────────────────────────────

GOLDEN_V2_DIR = GOLDEN_DIR / "v2"

_MANAGER_AMOUNT_RE = re.compile(r"환불\s*금액[^0-9\n]{0,10}([0-9,]+)\s*원")


def load_golden_v2_cases() -> list[dict]:
    """v2 골든셋 디렉토리 모든 json 로드."""
    if not GOLDEN_V2_DIR.exists():
        return []
    cases = []
    for p in sorted(GOLDEN_V2_DIR.glob("*.json")):
        with open(p) as f:
            s = json.load(f)
        s["_file"] = p.name
        cases.append(s)
    return cases


def extract_agent_refund_amount(result: AgentResultV2) -> int | None:
    """result에서 agent가 계산한 환불금액 추출.

    우선순위:
    1. steps 중 [final] step의 detail.variables.환불금액 (workflow path)
    2. result.variables.환불금액 (tool-call path)
    3. result.final_answer에서 정규식 파싱 (fallback)
    """
    # 1. final step의 variables
    for step in (result.steps or []):
        if step.step == "final":
            vars_ = (step.detail or {}).get("variables") or {}
            amt = vars_.get("환불금액")
            if amt:
                try:
                    return int(str(amt).replace(",", ""))
                except (ValueError, TypeError):
                    pass
    # 2. result.variables
    amt_str = (result.variables or {}).get("환불금액")
    if amt_str:
        try:
            return int(str(amt_str).replace(",", ""))
        except (ValueError, TypeError):
            pass
    # 3. final_answer regex
    if result.final_answer:
        m = _MANAGER_AMOUNT_RE.search(result.final_answer)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


@st.cache_data(show_spinner=False)
def _load_enriched_for_manager():
    """매니저 실답변 대조용 enriched 로드."""
    p = TEST_CASES_DIR / "refund_test_cases_enriched.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return {e["chat_id"]: e for e in json.load(f)}


def _run_agent_on_case(case: dict) -> dict:
    """1 case 실행 → 전체 결과 딕셔너리 반환."""
    agent = RefundAgentV2(mock=True)
    try:
        result = agent.process(
            user_messages=case["user_messages"],
            chat_id=case.get("source_chat_id") or case.get("scenario", "v2"),
            admin_data=case["admin_data"],
            conversation_time=case.get("conversation_time", ""),
            conversation_turns=case.get("conversation_turns") or [],
        )
        return {
            "ok": True,
            "template_id": result.template_id or "",
            "final_answer": result.final_answer or "",
            "steps": result.steps,
            "agent_amt": extract_agent_refund_amount(result),
            "error": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "template_id": "",
            "final_answer": "",
            "steps": [],
            "agent_amt": None,
            "error": f"{type(e).__name__}: {e}",
        }


def _run_agent_multi_turn(case: dict) -> list[dict]:
    """멀티턴 실행 — 각 user turn마다 agent 실행 + 다음 manager turn 매칭.

    Returns list of turn records:
    [
      {
        "turn_idx": int,            # user turn index in conversation_turns
        "user_text": str,
        "prior_turns": list[dict],  # 이 턴 이전 모든 턴 (context)
        "agent": {...run dict...},  # agent 실행 결과
        "manager_text": str | None, # 다음 manager 턴 text (없으면 None)
      }, ...
    ]
    """
    turns = case.get("conversation_turns") or []
    if not turns:
        return []

    records = []
    for i, turn in enumerate(turns):
        if turn.get("role") != "user":
            continue
        user_text = turn.get("text") or ""
        if not user_text:
            continue

        # 이 턴 기준의 누적 유저 메시지 = 지금까지의 user turn text 전부
        user_msgs_so_far = [t.get("text", "") for t in turns[:i + 1] if t.get("role") == "user" and t.get("text")]
        prior_turns = turns[:i + 1]  # context (현재 턴 포함)

        # agent 실행 — 이 케이스의 admin_data로, user_messages는 **이 턴까지 누적**, conversation_turns는 **이 턴까지의 전체**
        sub_case = {
            **case,
            "user_messages": user_msgs_so_far,
            "conversation_turns": prior_turns,
        }
        run = _run_agent_on_case(sub_case)

        # 다음 manager turn 찾기 (이 user turn 이후 첫 manager)
        next_mgr_text = None
        for j in range(i + 1, len(turns)):
            t = turns[j]
            if t.get("role") == "manager":
                next_mgr_text = t.get("text") or ""
                break
            if t.get("role") == "user":
                break  # 유저가 또 말함 → 매니저 답 없는 턴

        records.append({
            "turn_idx": i,
            "user_text": user_text,
            "prior_turns": prior_turns,
            "agent": run,
            "manager_text": next_mgr_text,
        })

    return records


def _format_evidence(case: dict) -> dict:
    """case admin_data에서 '근거' 섹션 데이터 추출."""
    ad = case.get("admin_data") or {}
    products = ad.get("products") or []
    txs = ad.get("transactions") or []
    success_txs = [t for t in txs if t.get("state") == "purchased_success"]
    refund_txs = [t for t in txs if t.get("state") == "purchased_refund"]
    usage = ad.get("usage") or {}

    product = products[0] if products else None
    tx = success_txs[0] if success_txs else None

    # 경과일 계산
    from datetime import datetime
    days_elapsed = None
    if tx and case.get("conversation_time"):
        try:
            pay = datetime.fromisoformat((tx.get("date") or "").replace("Z", "+00:00"))
            conv = datetime.fromisoformat(case["conversation_time"].replace("Z", "+00:00"))
            days_elapsed = (conv.date() - pay.date()).days
        except Exception:
            pass

    return {
        "ch_name": ad.get("ch_name") or "(미식별)",
        "us_user_id": ad.get("us_user_id") or "",
        "phone": ad.get("phone") or "",
        "product": product,
        "products": products,
        "tx": tx,
        "transactions": txs,
        "success_txs": success_txs,
        "refund_txs": refund_txs,
        "memberships": ad.get("memberships") or [],
        "refunds": ad.get("refunds") or [],
        "total_paid": tx.get("amount") if tx else 0,
        "pay_date": (tx.get("date") or "")[:10] if tx else "",
        "conv_date": (case.get("conversation_time") or "")[:10],
        "days_elapsed": days_elapsed,
        "accessed": usage.get("accessed", False),
        "usage_count": usage.get("count", 0),
        "all_refunded": bool(refund_txs) and len(refund_txs) >= len(success_txs) and len(success_txs) > 0,
        "num_products": len(products),
        "num_success_txs": len(success_txs),
        "num_refund_txs": len(refund_txs),
    }


def _render_evidence_panel(ev: dict):
    """근거 Evidence 블록 (좌측 공통). 결제내역/멤버십/환불이력 상세 포함."""
    st.markdown("##### 📋 근거 (Evidence)")
    st.markdown(f"**유저**: `{ev['ch_name']}` ({ev['phone'] or '전화번호 없음'})")

    # 상품 목록 (여러 개 가능)
    if ev["products"]:
        st.markdown(f"**상품** ({len(ev['products'])}건)")
        for p in ev["products"]:
            master = p.get('master') or p.get('master_name') or ''
            name = p.get('name', '')
            price = p.get('price') or 0
            status = p.get('status', '')
            status_icon = "🟢" if status == "active" else ("⚫" if status == "inactive" else "⚪")
            st.markdown(
                f"- {status_icon} **{master}** / {name}  \n"
                f"  └ 가격: {price:,}원 · status: `{status}`"
            )
    else:
        st.caption("보유 상품 없음")

    # 결제 내역 (전체)
    if ev["transactions"]:
        st.markdown(f"**결제 내역** ({len(ev['transactions'])}건)")
        for t in ev["transactions"]:
            amt = t.get("amount", 0)
            if isinstance(amt, str):
                try: amt = int(amt)
                except: amt = 0
            state = t.get("state", "")
            date = (t.get("date") or t.get("created_at") or "")[:10]
            info = t.get("info") or t.get("method") or ""
            round_no = t.get("round", "")
            icon = "💳" if state == "purchased_success" else ("↩️" if state == "purchased_refund" else "❓")
            label = "결제" if state == "purchased_success" else ("환불" if state == "purchased_refund" else state)
            round_txt = f" {round_no}회차" if round_no else ""
            st.caption(f"{icon} {date} · **{amt:,}원** · {label}{round_txt} · {info}")
    else:
        st.caption("결제 이력 없음")

    # 멤버십 정보
    if ev["memberships"]:
        st.markdown(f"**멤버십** ({len(ev['memberships'])}건)")
        for mb in ev["memberships"]:
            pname = mb.get("productName", "")
            cycle = mb.get("paymentCycle", "")
            expired = mb.get("expiration", False)
            mtype = mb.get("memberShipType") or mb.get("membershipType") or ""
            exp_icon = "⏰ 만료" if expired else "✅ 활성"
            st.caption(
                f"- {pname}  \n"
                f"  └ 결제회차: {cycle} · 타입: `{mtype}` · {exp_icon}"
            )

    # 환불 이력 (refundHistory)
    if ev["refunds"]:
        st.markdown(f"**환불 이력** ({len(ev['refunds'])}건)")
        for r in ev["refunds"]:
            rh = r.get("refundHistory") or r
            refund_at = (rh.get("refundAt") or rh.get("createdAt") or "")[:10]
            refund_amt = rh.get("refundAmount", 0)
            if isinstance(refund_amt, str):
                try: refund_amt = int(refund_amt)
                except: refund_amt = 0
            pending = not rh.get("refundAt")
            status = "⏳ 진행중" if pending else "✅ 완료"
            st.caption(f"- {status} · {refund_at} · {refund_amt:,}원")

    # 열람
    st.markdown(f"**콘텐츠 열람**: {'있음' if ev['accessed'] else '없음'} ({ev['usage_count']}건)")

    # 경과일
    if ev["days_elapsed"] is not None:
        st.markdown(f"**요청 시점**: {ev['conv_date']} → 경과 **{ev['days_elapsed']}일**")


def _render_turn_pingpong(records: list[dict], src_id: str):
    """멀티턴 핑퐁 표시 — 각 유저 턴마다 Agent vs 매니저 side-by-side.

    단일턴/매니저 답변 누락 케이스는 source_chat_id로 enriched에서 매니저 답변 pull.
    """
    st.markdown("##### 🔄 턴별 핑퐁 (Agent vs 상담사)")
    if not records:
        st.caption("유저 턴 없음")
        return

    # source의 매니저 전체 답변 (fallback용)
    enriched_mgr_full = ""
    if src_id:
        enriched_map = _load_enriched_for_manager()
        src = enriched_map.get(src_id)
        if src:
            mr = src.get("manager_responses") or []
            enriched_mgr_full = "\n\n".join(mr) if isinstance(mr, list) else str(mr)

    for rec in records:
        st.markdown(
            f"**[Turn {rec['turn_idx']}] 유저 메시지**  \n"
            f"> {rec['user_text']}"
        )
        run = rec["agent"]
        agent_tid = run["template_id"]
        agent_ans = run["final_answer"] or "(답변 없음)"
        mgr = rec["manager_text"]

        # fallback: turn 매칭 실패 + enriched에 매니저 답변 있음 → 전체 답변을 첫 턴에만 1회 표시
        if not mgr and enriched_mgr_full and rec["turn_idx"] == records[0]["turn_idx"]:
            mgr = enriched_mgr_full
            mgr_label = "**👤 실제 상담사** (enriched 전체 답변 fallback)"
        else:
            mgr_label = "**👤 실제 상담사**"

        col_a, col_m = st.columns(2)
        with col_a:
            st.markdown(f"**🤖 Agent** `({agent_tid})`")
            if run["error"]:
                st.error(run["error"])
            else:
                st.markdown(
                    f"""<div style="color:#212121; background:#fff8e1; border-left:4px solid #f9a825; padding:8px 10px; font-size:12px; line-height:1.55; white-space:pre-wrap; border-radius:4px; max-height:360px; overflow-y:auto;">{agent_ans}</div>""",
                    unsafe_allow_html=True,
                )
        with col_m:
            st.markdown(mgr_label)
            if mgr:
                st.markdown(
                    f"""<div style="color:#212121; background:#e8f5e9; border-left:4px solid #43a047; padding:8px 10px; font-size:12px; line-height:1.55; white-space:pre-wrap; border-radius:4px; max-height:360px; overflow-y:auto;">{mgr}</div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("(이 턴 이후 상담사 답변 없음)")
        st.divider()


def _render_case_card(case: dict, idx: int):
    """케이스 1건 Evidence Chain 카드 렌더링. 멀티턴 지원."""
    exp = case.get("expected") or {}
    ev = _format_evidence(case)

    # 멀티턴 실행
    turn_records = _run_agent_multi_turn(case)

    # 헤더 요약: 첫 턴 기준으로 템플릿 일치 여부
    first_run = turn_records[0]["agent"] if turn_records else _run_agent_on_case(case)
    expected_tid = exp.get("template_id", "")
    agent_tid = first_run["template_id"]
    tid_ok = agent_tid == expected_tid
    tid_badge = "✅" if tid_ok else f"❌ (agent: {agent_tid})"

    expected_amt = exp.get("refund_amount_policy")
    agent_amt = first_run.get("agent_amt")
    if expected_amt is not None and agent_amt is not None:
        diff = agent_amt - expected_amt
        amt_badge = "✅ 금액 일치" if abs(diff) <= 1 else (f"≈ diff {diff:+d}" if abs(diff) <= 100 else f"❌ diff {diff:+d}")
    elif expected_amt is None:
        amt_badge = "— 금액 불요"
    else:
        amt_badge = "⚠️ agent 금액 없음"

    n_turns = len(turn_records)
    turn_badge = f"🔄 {n_turns}턴" if n_turns > 1 else "1턴"

    header = f"#{idx+1} · {case.get('scenario','?')}  —  {tid_badge}  ·  {amt_badge}  ·  {turn_badge}"
    with st.expander(header, expanded=(idx < 2)):
        st.caption(case.get("description", ""))
        src_id = case.get("source_chat_id", "")
        if src_id:
            st.caption(f"🔗 source: `{src_id}`")

        if first_run.get("error"):
            st.error(f"실행 에러: {first_run['error']}")
            return

        # ── 2 columns: 좌측(근거+계산) / 우측(턴별 핑퐁)
        col_left, col_right = st.columns([1, 1.5])

        with col_left:
            _render_evidence_panel(ev)

            st.divider()
            st.markdown("##### 🔀 정책 판단 + 🧮 계산")
            applied_rule = exp.get("applied_rule", "")
            if applied_rule:
                st.markdown(f"**적용 규칙**: {applied_rule}")
            pf = exp.get("period_fraction", "")
            if pf:
                st.markdown(f"**경과율**: {pf}")
            mp = exp.get("monthly_price")
            if mp is not None:
                src = exp.get("monthly_price_source", "")
                src_txt = f" ({src})" if src else ""
                st.markdown(f"**1개월 정가**: {mp:,}원{src_txt}")

            if expected_amt is not None:
                ded = exp.get("deduction_policy") or 0
                fee = exp.get("fee_policy") or 0
                total = ev["total_paid"] or 0
                remaining = total - ded
                if exp.get("refund_type") == "full":
                    st.success(f"**정책 환불: {expected_amt:,}원** (전액)")
                else:
                    st.markdown(
                        f"- 결제 총액: `{total:,}원`  \n"
                        f"- 차감: `−{ded:,}`  \n"
                        f"- 잔여: `{remaining:,}`  \n"
                        f"- 수수료 10%: `−{fee:,}`  \n"
                        f"- **정책 환불**: **`{expected_amt:,}원`**"
                    )
            else:
                st.caption("계산 불요 (비-T2 유형)")

            st.divider()
            st.markdown(f"**Agent 첫 턴 템플릿**: `{agent_tid or '(없음)'}`")
            if agent_amt is not None:
                st.markdown(f"**Agent 첫 턴 환불액**: `{agent_amt:,}원`")

            # Agent path (첫 턴)
            for step in (first_run.get("steps") or []):
                if step.step == "classify":
                    path = (step.detail or {}).get("path") or []
                    if path:
                        st.caption(f"경로: {' → '.join(path)}")
                    break

        with col_right:
            _render_turn_pingpong(turn_records, src_id)


def render_t2_spot_check():
    st.markdown("### 📊 v2 골든셋 — 유형별 Evidence Chain")
    st.caption(
        "`data/mock_scenarios/golden/v2/` 수동 작성 시나리오. "
        "각 케이스마다 **근거(Evidence) → 정책 판단 → 계산 breakdown → Agent 답변 vs 실제 상담사 답변** 나란히 표시."
    )

    cases = load_golden_v2_cases()
    st.caption(f"로드된 v2 골든셋: **{len(cases)}건** ({', '.join(c.get('_file','') for c in cases)})")

    if not cases:
        st.warning("`data/mock_scenarios/golden/v2/` 비어있음.")
        return

    if not st.button("전체 실행", type="primary", use_container_width=True):
        st.info("위 버튼 클릭 → 8건 전부 실행 + 카드 렌더링 (약 5~10초)")
        return

    # 요약 메트릭 계산 (먼저 전체 돌려서 상태 집계)
    results = []
    with st.spinner("v2 골든셋 전체 실행 중..."):
        for c in cases:
            run = _run_agent_on_case(c)
            exp = c.get("expected") or {}
            expected_tid = exp.get("template_id", "")
            expected_amt = exp.get("refund_amount_policy")
            tid_ok = run["template_id"] == expected_tid
            if expected_amt is not None and run["agent_amt"] is not None:
                diff = run["agent_amt"] - expected_amt
                amt_state = "ok" if abs(diff) <= 1 else ("close" if abs(diff) <= 100 else "bad")
            elif expected_amt is None:
                amt_state = "na"
            else:
                amt_state = "missing"
            results.append({"tid_ok": tid_ok, "amt": amt_state, "err": bool(run["error"])})

    total = len(results)
    tid_matched = sum(1 for r in results if r["tid_ok"])
    amt_ok = sum(1 for r in results if r["amt"] == "ok")
    amt_close = sum(1 for r in results if r["amt"] == "close")
    amt_bad = sum(1 for r in results if r["amt"] in ("bad", "missing"))
    errors = sum(1 for r in results if r["err"])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("전체", total)
    c2.metric("템플릿 일치", f"{tid_matched}/{total}")
    c3.metric("금액 ±1원", amt_ok)
    c4.metric("금액 ±100원", amt_close)
    c5.metric("금액 깨짐/에러", amt_bad + errors)

    st.divider()

    # 각 케이스 카드 렌더링
    for idx, c in enumerate(cases):
        _render_case_card(c, idx)


def main():
    st.set_page_config(page_title="환불 Agent v2", layout="wide")
    st.title("환불/해지 상담 어시스턴트")
    st.caption("mock 시나리오 선택 → 메시지 입력 → 조회 결과 + 판단 근거 + 답변 초안")

    # 데이터셋 선택: Legacy vs Golden vs T2 스팟체크
    dataset = st.radio(
        "데이터셋",
        [
            "🌟 골든셋 (신규, api-interfaces.md 기반)",
            "📦 Legacy mock",
            "📊 v2 골든셋 구축 (신규)",
        ],
        horizontal=True,
    )

    if dataset.startswith("📊"):
        render_t2_spot_check()
        return

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
                    f"""<div style="color:#212121; background:#e3f2fd; border-radius:12px 12px 12px 0; padding:10px 14px; margin:4px 0; font-size:14px;">
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
                    f"""<div style="color:#212121; background:#fff8e1; border:2px solid #f9a825; border-radius:8px; padding:14px; font-size:14px; line-height:1.8;">
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
