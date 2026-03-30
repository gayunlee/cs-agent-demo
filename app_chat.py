"""CS AI 에이전트 데모 — Gradio 챗 UI
분류 → RAG 검색 → 정보 조회 → 답변 초안 생성"""
import os
import random
import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from src.data_loader import load_golden_set
from src.agent import CSAgent

DATA_DIR = os.getenv("DATA_DIR", "/Users/gygygygy/Documents/ai/letter-post-weekly-report/data/channel_io")

mock_mode = os.getenv("MOCK_MODE", "0") == "1"
agent = CSAgent(region="us-west-2", mock=mock_mode)

# 대화 데이터 미리 로드
all_convos = load_golden_set(DATA_DIR)


def load_random_conversation():
    conv = random.choice(all_convos)
    info = f"**[{conv.chat_id[:12]}]** route: {conv.route}"
    if conv.topics:
        info += f" | 라벨: {', '.join(conv.topics)}"
    return conv.text[:800], info, conv.chat_id


def process_conversation(conversation_text: str, chat_id: str):
    if not conversation_text.strip():
        return "", "", "", "", "", ""

    result = agent.process(chat_id or "manual", conversation_text)

    # 분류 결과
    action_display = {
        "auto_template": "🟢 자동 응답 (템플릿 매칭)",
        "llm_draft": "🟡 LLM 초안 생성 (RAG 참조)",
        "escalate": "🔴 에스컬레이션 권장",
    }
    status = (
        f"**분류**: {result.category}\n"
        f"**신뢰도**: {result.confidence}\n"
        f"**판단**: {action_display.get(result.action, result.action)}\n"
        f"**근거**: {result.reasoning}"
    )
    if result.template_matched:
        status += f"\n**매칭 템플릿**: {result.template_matched}"

    # 조회 정보
    lookup_display = result.lookup.to_display() if result.lookup else ""

    # RAG 검색 결과
    rag_display = ""
    if result.rag_matches:
        lines = []
        for i, m in enumerate(result.rag_matches, 1):
            sim = 1 - m["distance"]
            lines.append(f"**[참고 {i}]** 유사도: {sim:.0%}")
            lines.append(f"> 고객: {m['customer'][:150]}...")
            lines.append(f"> 매니저: {m['manager'][:200]}...")
            lines.append("")
        rag_display = "\n".join(lines)

    return result.draft_answer, status, lookup_display, rag_display, result.action, result.category


def create_chat_interface():
    with gr.Blocks(title="CS AI 에이전트 데모", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# CS AI 에이전트 — 답변 생성 데모")
        gr.Markdown("분류 → RAG(유사 답변 검색) → 고객 정보 조회 → 답변 초안 생성")

        chat_id_state = gr.State("")

        with gr.Row():
            # 왼쪽: 고객 문의
            with gr.Column(scale=1):
                gr.Markdown("### 고객 문의")
                conversation_input = gr.Textbox(
                    label="대화 내용",
                    placeholder="고객 문의를 붙여넣거나, 아래 버튼으로 실제 대화를 로드하세요...",
                    lines=12,
                )
                conv_info = gr.Markdown("")

                with gr.Row():
                    load_btn = gr.Button("🔀 랜덤 대화 로드", variant="secondary")
                    process_btn = gr.Button("🤖 답변 생성", variant="primary")

            # 오른쪽: 에이전트 분석
            with gr.Column(scale=1):
                gr.Markdown("### 에이전트 분석")
                status_output = gr.Markdown()

                gr.Markdown("### 조회된 고객 정보")
                lookup_output = gr.Markdown()

                action_output = gr.Textbox(visible=False)
                category_output = gr.Textbox(visible=False)

        with gr.Row():
            # 왼쪽: RAG 참고 답변
            with gr.Column(scale=1):
                gr.Markdown("### RAG — 유사 과거 답변")
                rag_output = gr.Markdown()

            # 오른쪽: 생성된 초안
            with gr.Column(scale=1):
                gr.Markdown("### 생성된 답변 초안")
                draft_output = gr.Textbox(
                    label="초안 (CS팀이 검수 후 전송)",
                    lines=8,
                    interactive=True,
                )
                with gr.Row():
                    approve_btn = gr.Button("✅ 그대로 전송", variant="primary")
                    edit_btn = gr.Button("✏️ 수정 후 전송", variant="secondary")
                    reject_btn = gr.Button("❌ 폐기", variant="stop")

        # 이벤트
        load_btn.click(
            load_random_conversation,
            outputs=[conversation_input, conv_info, chat_id_state],
        )
        process_btn.click(
            process_conversation,
            inputs=[conversation_input, chat_id_state],
            outputs=[draft_output, status_output, lookup_output, rag_output, action_output, category_output],
        )

    return demo


if __name__ == "__main__":
    demo = create_chat_interface()
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False)
