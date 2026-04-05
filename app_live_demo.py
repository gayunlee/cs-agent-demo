"""CS Agent Live Demo — 채널톡 UI 흉내 (유저 문의 페이지 ↔ 어드민 수신함).

포트 8510 (8505 는 다른 앱이 쓰고 있음).

구조:
- 좌: 📱 유저 문의하기 (채널톡 채팅 위젯 스타일)
- 우: 🖥 어드민 (수신함 목록 + 선택된 대화 상세 + 내부대화 패널)

재생 플로우 (시나리오 선택 → ▶ 재생):
1. 유저 페이지에 유저 메시지 말풍선 추가
2. 어드민 수신함에 🔴 NEW 알림
3. 어드민 대화 상세에 유저 메시지 미러
4. 내부대화에 📋 admin API 조회 결과 주입 (첫 턴)
5. 내부대화에 🔄 AI 초안 생성 중... 로딩
6. wrapper agent 실행 (실 Bedrock 호출)
7. 내부대화에 🤖 초안 주입 (intent/template/환불금액 배지)
8. 다음 턴 있으면 1~7 반복

실제 채널톡 연동 시 swap 지점:
- 유저 메시지 → `mock_webhook_payload` → 실 webhook 수신
- 내부대화 post → `mock_internal_chat_post` → 실 채널톡 API

실행:
    streamlit run app_live_demo.py --server.port 8510
"""
from __future__ import annotations

import json
import sys
import time
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _aws_env  # noqa: F401
from src.agents.wrapper_agent import (
    clear_all_sessions,
    clear_session,
    get_agent_for_session,
)

GOLDEN_V2_DIR = ROOT / "data/mock_scenarios/golden/v2"


# ─────────────────────────────────────────────────────────────
# 시나리오 로드
# ─────────────────────────────────────────────────────────────


def _extract_user_turns(case: dict) -> list[str]:
    turns = case.get("conversation_turns") or []
    if turns:
        user_turns = [t.get("text", "") for t in turns if t.get("role") == "user" and t.get("text")]
        if user_turns:
            return user_turns
    return [m for m in (case.get("user_messages") or []) if m]


def load_scenarios() -> dict[str, dict]:
    scenarios: dict[str, dict] = {}
    for p in sorted(GOLDEN_V2_DIR.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            s = json.load(f)
        scenarios[p.stem] = {
            "name": p.stem,
            "title": s.get("scenario", p.stem),
            "description": s.get("description", ""),
            "user_turns": _extract_user_turns(s),
            "admin_data": s.get("admin_data", {}),
            "conversation_time": s.get("conversation_time", ""),
            "expected_template": (s.get("expected") or {}).get("template_id", ""),
            "expected_intent_domain": True,
        }
    # 비도메인 gate 시연용
    non_domain = [
        ("데모_비도메인_배송문의", "배송 언제 오나요?", "배송/주문 관련 — 환불 도메인 아님"),
        ("데모_비도메인_인사", "안녕하세요 문의드려요", "단순 인사 — 환불 도메인 아님"),
        ("데모_비도메인_쿠폰", "쿠폰 받을 수 있나요?", "프로모션 — 환불 도메인 아님"),
    ]
    for name, msg, desc in non_domain:
        scenarios[name] = {
            "name": name,
            "title": f"비도메인 gate 시연 — {msg}",
            "description": desc,
            "user_turns": [msg],
            "admin_data": {"ch_name": "테스트 고객", "phone": "01000000000", "us_user_id": ""},
            "conversation_time": "",
            "expected_template": "(gate skip)",
            "expected_intent_domain": False,
        }
    return scenarios


# ─────────────────────────────────────────────────────────────
# 채널톡 인터페이스 흉내 (실연동 시 swap 지점)
# ─────────────────────────────────────────────────────────────


def mock_webhook_payload(chat_id: str, user_message: str, admin_data: dict) -> dict:
    return {
        "event": "message",
        "refers": {
            "chat": {"id": chat_id},
            "user": {
                "memberId": admin_data.get("us_user_id", ""),
                "name": admin_data.get("ch_name", ""),
                "mobileNumber": admin_data.get("phone", ""),
            },
        },
        "entity": {"plainText": user_message, "personType": "user"},
    }


def mock_internal_chat_post(chat_id: str, content: str, post_type: str) -> dict:
    return {
        "method": "POST",
        "endpoint": f"https://api.channel.io/open/v5/.../chats/{chat_id}/messages",
        "body": {
            "plainText": content,
            "private": True,
            "root": {"type": post_type},
        },
    }


# ─────────────────────────────────────────────────────────────
# Tool result 파싱
# ─────────────────────────────────────────────────────────────


def extract_latest_tool_result(agent) -> dict:
    latest: dict = {}
    for m in agent.messages:
        content = m.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or "toolResult" not in b:
                continue
            for c in b["toolResult"].get("content", []):
                if isinstance(c, dict) and "text" in c:
                    try:
                        latest = json.loads(c["text"])
                    except Exception:
                        pass
    return latest


def summarize_admin_data(ad: dict) -> list[tuple[str, str]]:
    """(label, value) 튜플 리스트로 반환."""
    out: list[tuple[str, str]] = []
    if ad.get("ch_name"):
        out.append(("고객명", ad["ch_name"]))
    if ad.get("us_user_id"):
        out.append(("us_user_id", ad["us_user_id"]))
    products = ad.get("products") or []
    if products:
        p = products[0]
        out.append(("상품", f"{p.get('name', '')} ({p.get('status', '')})"))
        out.append(("정가", f"{p.get('price', 0):,}원"))
    txs = ad.get("transactions") or []
    if txs:
        t = txs[0]
        out.append(("결제", f"{t.get('amount', 0):,}원 · 회차 {t.get('round', '?')} · {t.get('state', '')}"))
    usage = ad.get("usage") or {}
    if "accessed" in usage:
        out.append(("열람", "있음" if usage.get("accessed") else "없음"))
    refunds = ad.get("refunds") or []
    out.append(("환불이력", f"{len(refunds)}건"))
    return out


# ─────────────────────────────────────────────────────────────
# HTML 렌더 helpers — 채널톡 스타일
# ─────────────────────────────────────────────────────────────

CUSTOMER_CSS = """
<style>
.ct-frame { background: #f5f5f5; border-radius: 18px; padding: 0; overflow: hidden; border: 1px solid #e0e0e0; min-height: 540px; display: flex; flex-direction: column; }
.ct-header { background: #5b21b6; color: white; padding: 14px 18px; font-weight: 600; font-size: 15px; }
.ct-header-sub { font-size: 11px; opacity: 0.8; margin-top: 2px; font-weight: 400; }
.ct-body { flex: 1; padding: 14px 14px 8px; background: #fafafa; overflow-y: auto; min-height: 400px; }
.ct-msg-user { background: #5b21b6; color: white; padding: 10px 14px; border-radius: 16px 16px 4px 16px; margin: 4px 0 4px auto; max-width: 80%; font-size: 14px; line-height: 1.5; display: block; width: fit-content; }
.ct-msg-user-wrap { display: flex; justify-content: flex-end; margin: 6px 0; }
.ct-msg-mgr { background: #e8e8e8; color: #212121; padding: 10px 14px; border-radius: 16px 16px 16px 4px; margin: 4px 0; max-width: 85%; font-size: 14px; line-height: 1.5; display: block; width: fit-content; }
.ct-msg-mgr-wrap { display: flex; justify-content: flex-start; margin: 6px 0; }
.ct-msg-mgr-label { font-size: 10px; color: #757575; margin-bottom: 2px; }
.ct-input { background: white; border-top: 1px solid #e0e0e0; padding: 12px 14px; color: #9e9e9e; font-size: 13px; }

.admin-inbox { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
.admin-inbox-header { background: #f5f5f5; padding: 10px 14px; font-weight: 600; font-size: 12px; color: #616161; border-bottom: 1px solid #e0e0e0; }
.admin-inbox-item { padding: 12px 14px; border-bottom: 1px solid #f0f0f0; display: flex; gap: 10px; align-items: flex-start; }
.admin-inbox-item-new { background: #fff8e1; border-left: 4px solid #f9a825; }
.admin-inbox-item-handled { background: #f5f5f5; opacity: 0.75; }
.admin-inbox-badge { flex-shrink: 0; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; }
.admin-inbox-badge-new { background: #f44336; color: white; }
.admin-inbox-badge-handled { background: #9e9e9e; color: white; }
.admin-inbox-meta { flex: 1; }
.admin-inbox-name { font-weight: 600; font-size: 13px; color: #212121; }
.admin-inbox-preview { font-size: 12px; color: #757575; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.admin-detail { background: white; border: 1px solid #e0e0e0; border-radius: 8px; margin-top: 14px; }
.admin-detail-header { background: #eeeeee; padding: 10px 14px; font-weight: 600; font-size: 12px; color: #424242; border-bottom: 1px solid #e0e0e0; }
.admin-detail-body { padding: 14px; }
.admin-section-label { font-size: 10px; font-weight: 700; color: #9e9e9e; text-transform: uppercase; margin: 12px 0 6px; letter-spacing: 0.5px; }
.admin-section-label:first-child { margin-top: 0; }
.admin-msg-user { background: #e3f2fd; padding: 10px 14px; border-radius: 12px 12px 12px 4px; margin: 6px 0; font-size: 13px; color: #212121; max-width: 85%; }

.internal-section { background: #fff3e0; border: 1.5px dashed #ff9800; border-radius: 8px; padding: 12px; margin: 10px 0; }
.internal-label { font-size: 10px; font-weight: 700; color: #e65100; letter-spacing: 0.5px; margin-bottom: 6px; }
.internal-lookup { background: white; border-left: 3px solid #7b1fa2; padding: 10px 12px; border-radius: 4px; margin: 6px 0; }
.internal-lookup-title { font-size: 11px; font-weight: 700; color: #7b1fa2; margin-bottom: 6px; }
.internal-lookup-row { font-size: 12px; color: #212121; padding: 2px 0; }
.internal-loading { background: white; border-left: 3px solid #1976d2; padding: 12px; border-radius: 4px; margin: 6px 0; font-size: 12px; color: #1976d2; font-style: italic; }
.internal-draft { background: white; border-left: 3px solid #2e7d32; padding: 10px 12px; border-radius: 4px; margin: 6px 0; }
.internal-draft-title { font-size: 11px; font-weight: 700; color: #2e7d32; margin-bottom: 6px; }
.internal-draft-badges { margin: 6px 0; }
.badge { display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; margin-right: 4px; color: white; }
.badge-intent { background: #2e7d32; }
.badge-template { background: #1976d2; }
.badge-amount { background: #f57f17; }
.internal-draft-text { font-size: 13px; color: #212121; line-height: 1.6; margin-top: 6px; white-space: pre-wrap; }
.internal-skip { background: #ffebee; border-left: 3px solid #c62828; padding: 10px 12px; border-radius: 4px; margin: 6px 0; }
.internal-skip-title { font-size: 11px; font-weight: 700; color: #c62828; margin-bottom: 6px; }
.internal-skip-body { font-size: 12px; color: #424242; }
</style>
"""


def render_customer_page(ph, messages: list[dict], user_name: str) -> None:
    """messages: [{"role": "user"|"manager", "text": str}]"""
    html = [CUSTOMER_CSS, '<div class="ct-frame">']
    html.append(f'<div class="ct-header">📱 채널톡 상담하기<div class="ct-header-sub">{escape(user_name or "고객")} · 실시간 상담</div></div>')
    html.append('<div class="ct-body">')
    for m in messages:
        text = escape(m["text"]).replace("\n", "<br>")
        if m["role"] == "user":
            html.append(f'<div class="ct-msg-user-wrap"><div class="ct-msg-user">{text}</div></div>')
        else:
            html.append(f'<div class="ct-msg-mgr-wrap"><div><div class="ct-msg-mgr-label">상담사</div><div class="ct-msg-mgr">{text}</div></div></div>')
    html.append('</div>')
    html.append('<div class="ct-input">💬 메시지 입력...</div>')
    html.append('</div>')
    ph.markdown("".join(html), unsafe_allow_html=True)


def render_admin_inbox(ph, inbox_item: dict | None) -> None:
    if not inbox_item:
        ph.markdown(CUSTOMER_CSS + '<div class="admin-inbox"><div class="admin-inbox-header">📥 수신함</div><div style="padding:20px; text-align:center; color:#9e9e9e; font-size:12px;">대기 중...</div></div>', unsafe_allow_html=True)
        return

    status = inbox_item.get("status", "NEW")
    badge_class = "admin-inbox-badge-new" if status == "NEW" else "admin-inbox-badge-handled"
    item_class = "admin-inbox-item-new" if status == "NEW" else "admin-inbox-item-handled"
    badge_text = "🔴 NEW" if status == "NEW" else "✓ 처리"

    html = [
        CUSTOMER_CSS,
        '<div class="admin-inbox">',
        '<div class="admin-inbox-header">📥 수신함 (1)</div>',
        f'<div class="admin-inbox-item {item_class}">',
        f'<div class="admin-inbox-badge {badge_class}">{badge_text}</div>',
        '<div class="admin-inbox-meta">',
        f'<div class="admin-inbox-name">{escape(inbox_item.get("user_name", "고객"))}</div>',
        f'<div class="admin-inbox-preview">{escape(inbox_item.get("preview", ""))}</div>',
        '</div></div></div>',
    ]
    ph.markdown("".join(html), unsafe_allow_html=True)


def render_admin_detail(ph, user_messages: list[str], internal_events: list[dict], chat_id: str | None) -> None:
    html = [CUSTOMER_CSS, '<div class="admin-detail">']
    html.append(f'<div class="admin-detail-header">🗨 대화 상세 — chat_id: <code>{escape(chat_id or "-")}</code></div>')
    html.append('<div class="admin-detail-body">')

    # 일반 대화 섹션
    html.append('<div class="admin-section-label">일반 대화 (유저와 상담사)</div>')
    if not user_messages:
        html.append('<div style="color:#9e9e9e; font-size:12px;">(아직 메시지 없음)</div>')
    else:
        for m in user_messages:
            html.append(f'<div class="admin-msg-user">{escape(m)}</div>')

    # 내부 대화 섹션
    if internal_events:
        html.append('<div class="admin-section-label">🔒 내부 대화 (상담사 전용, 유저 안 보임)</div>')
        html.append('<div class="internal-section">')
        html.append('<div class="internal-label">INTERNAL CHAT · private=true</div>')
        for ev in internal_events:
            t = ev.get("type")
            if t == "lookup":
                rows_html = "".join(
                    f'<div class="internal-lookup-row"><b>{escape(k)}</b>: {escape(str(v))}</div>'
                    for k, v in ev["rows"]
                )
                html.append(
                    f'<div class="internal-lookup"><div class="internal-lookup-title">📋 ADMIN API 조회 결과</div>{rows_html}</div>'
                )
            elif t == "loading":
                html.append(
                    '<div class="internal-loading">🔄 AI 초안 생성 중... (wrapper agent 실행)</div>'
                )
            elif t == "draft":
                if ev.get("should_respond"):
                    badges = []
                    if ev.get("intent"):
                        badges.append(f'<span class="badge badge-intent">intent: {escape(ev["intent"])}</span>')
                    if ev.get("template_id"):
                        badges.append(f'<span class="badge badge-template">template: {escape(ev["template_id"])}</span>')
                    if ev.get("refund_amount") is not None:
                        badges.append(f'<span class="badge badge-amount">환불금: {ev["refund_amount"]:,}원</span>')
                    draft_text = escape(ev.get("draft", "")).replace("\n", "<br>")
                    html.append(
                        '<div class="internal-draft">'
                        '<div class="internal-draft-title">🤖 AI 자동 답변 초안 (상담사 검토용)</div>'
                        f'<div class="internal-draft-badges">{"".join(badges)}</div>'
                        f'<div class="internal-draft-text">{draft_text}</div>'
                        '</div>'
                    )
                else:
                    intent_label = escape(ev.get("intent") or "비도메인/기타")
                    html.append(
                        '<div class="internal-skip">'
                        '<div class="internal-skip-title">🚫 DOMAIN GATE — 초안 생성 skip</div>'
                        f'<div class="internal-skip-body">intent = <b>{intent_label}</b> · 환불 도메인 whitelist 외 → 상담사에게 전달, 자동 답변 생성 안 함.</div>'
                        '</div>'
                    )
        html.append('</div>')

    html.append('</div></div>')
    ph.markdown("".join(html), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# 재생 state machine
# ─────────────────────────────────────────────────────────────


def play_scenario(scenario: dict, placeholders: dict, speed: float) -> None:
    """시나리오 전체를 blocking 으로 재생. 각 phase 마다 placeholder 업데이트."""
    name = scenario["name"]
    user_turns = scenario["user_turns"]
    admin_data = scenario["admin_data"]
    chat_id = f"demo_{name}"
    user_name = admin_data.get("ch_name", "고객") or "고객"
    expected_domain = scenario.get("expected_intent_domain", True)

    # 초기화
    customer_messages: list[dict] = []  # [{"role": "user"|"manager", "text": str}]
    admin_user_messages: list[str] = []
    admin_internal: list[dict] = []
    inbox: dict | None = None

    render_customer_page(placeholders["customer"], customer_messages, user_name)
    render_admin_inbox(placeholders["inbox"], inbox)
    render_admin_detail(placeholders["detail"], admin_user_messages, admin_internal, None)

    clear_session(chat_id)
    agent = get_agent_for_session(chat_id)

    for turn_idx, user_text in enumerate(user_turns):
        # Phase 1: 유저 메시지 전송 (유저 페이지)
        customer_messages.append({"role": "user", "text": user_text})
        render_customer_page(placeholders["customer"], customer_messages, user_name)
        time.sleep(max(0.5, speed * 0.7))

        # Phase 2: 어드민 수신함 알림 + 대화 상세 미러
        if inbox is None:
            inbox = {
                "user_name": user_name,
                "preview": user_text,
                "status": "NEW",
            }
        else:
            inbox["preview"] = user_text
        render_admin_inbox(placeholders["inbox"], inbox)
        time.sleep(max(0.3, speed * 0.3))

        admin_user_messages.append(user_text)
        render_admin_detail(placeholders["detail"], admin_user_messages, admin_internal, chat_id)
        time.sleep(max(0.3, speed * 0.4))

        # Phase 3: 첫 턴만 admin API 조회 (도메인일 때)
        if turn_idx == 0 and admin_data and expected_domain:
            admin_internal.append({
                "type": "lookup",
                "rows": summarize_admin_data(admin_data),
            })
            render_admin_detail(placeholders["detail"], admin_user_messages, admin_internal, chat_id)
            time.sleep(max(0.4, speed * 0.5))

        # Phase 4: 로딩 표시
        admin_internal.append({"type": "loading"})
        render_admin_detail(placeholders["detail"], admin_user_messages, admin_internal, chat_id)

        # Phase 5: agent 실행 (실 Bedrock 호출)
        if turn_idx == 0:
            msg = (
                f"{user_text}\n\n"
                f"<admin_data>{json.dumps(admin_data, ensure_ascii=False)}</admin_data>\n"
                f"<conversation_time>{scenario.get('conversation_time', '')}</conversation_time>"
            )
        else:
            msg = user_text

        try:
            draft_text = agent.handle_turn(msg)
            tool_result = extract_latest_tool_result(agent)
            should_respond = agent.should_respond()
            intent = getattr(agent, "last_intent", "") or tool_result.get("intent", "")
        except Exception as e:
            draft_text = f"[error] {type(e).__name__}: {e}"
            tool_result = {}
            should_respond = False
            intent = ""

        # Phase 6: 로딩 제거 + 초안 주입
        admin_internal.pop()  # loading 제거
        admin_internal.append({
            "type": "draft",
            "draft": draft_text,
            "should_respond": should_respond,
            "intent": intent,
            "template_id": tool_result.get("template_id", ""),
            "refund_amount": tool_result.get("refund_amount"),
        })
        render_admin_detail(placeholders["detail"], admin_user_messages, admin_internal, chat_id)

        # Phase 7: 유저 페이지에도 상담사 답변으로 표시 (멀티턴 핑퐁 효과)
        if should_respond and draft_text:
            time.sleep(max(0.5, speed * 0.5))
            # draft 에서 첫 3줄 정도만 유저 페이지에 표시 (간결하게)
            short_draft = "\n".join(draft_text.split("\n")[:6])
            if len(short_draft) > 300:
                short_draft = short_draft[:300] + "..."
            customer_messages.append({"role": "manager", "text": short_draft})
            render_customer_page(placeholders["customer"], customer_messages, user_name)

        time.sleep(max(0.8, speed * 1.2))

    # 시나리오 완료 — inbox 처리됨 표시
    if inbox:
        inbox["status"] = "handled"
        render_admin_inbox(placeholders["inbox"], inbox)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────


def main():
    st.set_page_config(page_title="CS Agent Live Demo", layout="wide")

    scenarios = load_scenarios()

    # Sidebar
    with st.sidebar:
        st.markdown("## 🎬 CS Agent Live Demo")
        st.caption("채널톡 UI 흉내 — 유저 위젯 ↔ 어드민 수신함")

        st.divider()
        st.markdown("### 시나리오")
        scenario_key = st.selectbox(
            "선택",
            options=list(scenarios.keys()),
            format_func=lambda k: f"{'✅' if scenarios[k]['expected_intent_domain'] else '🚫'} {k}",
            label_visibility="collapsed",
        )
        sc = scenarios[scenario_key]
        st.caption(f"**{sc['title']}**")
        st.caption(f"턴 수: {len(sc['user_turns'])} · 기대: `{sc['expected_template']}`")

        st.divider()
        speed = st.slider("재생 속도 배수", 0.3, 2.5, 1.0, 0.1, help="낮을수록 빠름. 1.0 = 턴당 약 2~3초")

        play_clicked = st.button("▶ 재생", type="primary", use_container_width=True)
        if st.button("🔄 전체 초기화", use_container_width=True):
            clear_all_sessions()
            st.rerun()

        st.divider()
        st.markdown("### AgentCore 스택")
        st.markdown("✅ Strands Agent + @tool")
        st.markdown("✅ AgentCore Memory")
        st.markdown("✅ Bedrock Guardrail (PII)")
        st.markdown("✅ Domain Gate")
        st.markdown("✅ AgentCore Evaluation")

        st.divider()
        st.caption(
            "**실채널톡 swap 지점**\n\n"
            "- `mock_webhook_payload` → 실 webhook 수신\n"
            "- `mock_internal_chat_post` → 실 내부대화 POST"
        )

    # Main layout: 좌 유저 / 우 어드민
    st.title("🖥 CS 에이전트 라이브 데모")
    st.caption("좌: 유저가 채널톡으로 문의 → 우: 어드민에서 수신 → 내부대화에 AI 초안 자동 주입")

    col_customer, col_admin = st.columns([1, 1.5])

    with col_customer:
        customer_ph = st.empty()

    with col_admin:
        inbox_ph = st.empty()
        detail_ph = st.empty()

    placeholders = {
        "customer": customer_ph,
        "inbox": inbox_ph,
        "detail": detail_ph,
    }

    # 초기 빈 상태
    render_customer_page(customer_ph, [], sc.get("admin_data", {}).get("ch_name", "고객") or "고객")
    render_admin_inbox(inbox_ph, None)
    render_admin_detail(detail_ph, [], [], None)

    # 재생
    if play_clicked:
        play_scenario(sc, placeholders, speed)
        st.success(f"✅ 완료 — {scenario_key}")


if __name__ == "__main__":
    main()
