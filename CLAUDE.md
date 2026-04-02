# CS AI 에이전트 데모

## 프로젝트 개요
채널톡 CS 문의에 대해 자동 분류 + 답변 초안 생성하는 에이전트 데모.
기존 3개월 채널톡 대화 데이터로 오프라인 시뮬레이션.

## 실행
```bash
# 대시보드 (배치 처리 + 평가)
streamlit run app_dashboard.py

# 챗 UI (대화형 시뮬레이션)
python app_chat.py
```

## 데이터
- 원본 대화: `letter-post-weekly-report/data/channel_io/` (참조, 복사하지 않음)
- RAG 벡터 DB: `./chroma_db/`

### /note 기본 설정
- **category**: `채널톡 어시스턴트`
- **trigger**: `채널톡 CS 에이전트`
- 이 프로젝트에서 `/note` 실행 시 위 category를 기본값으로 사용한다 (F.F.md 추론 생략)

## 구조
```
src/
├── agent.py        # 의도 분류 + 답변 생성 (Claude API)
├── data_loader.py  # 채널톡 대화 데이터 로드
└── rag.py          # 유사 답변 검색 (ChromaDB)
app_dashboard.py    # Streamlit 대시보드
app_chat.py         # Gradio 챗 UI
```
