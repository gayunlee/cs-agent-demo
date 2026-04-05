# 데모 앱 플랜 v2 (2026-04-05)

> **목적**: 지금까지 구축한 agent(유형별 라우팅 + 환불 공식 + 템플릿)를 **데모로 제대로 보여줄 수 있는 앱**을 만든다.
>
> **2파트 구성**: (A) 채널톡 흉내 실시간 대화 앱 + (B) 상담사 환불처리 비교 시스템.
>
> 둘 다 shadow evaluation 아키텍처(상담사 원래 workflow 불변 + agent가 병렬로 초안 생성)를 전제로 한다.

---

## Part A — 채널톡 흉내 실시간 대화 데모

### 목적
데모에서 **"유저가 실제로 메시지를 보내면 agent가 답을 생성해서 대화를 이어간다"**를 시연.  
청중(경영진/CS팀)이 채널톡과 비슷한 UI를 보면서 agent 동작을 실시간으로 체감할 수 있어야 함.

### UI 구조
```
┌────────────────────────────────────────────┐
│  채널톡 스타일 대화창                        │
│  ┌───────────────────────────────────┐    │
│  │ 고객:  환불해주세요                │    │
│  │ 고객:  지난번에 산 거요             │    │
│  │                                   │    │
│  │ 🤖 초안:  안녕하세요 회원님, ...     │    │
│  │       ┌─────────────────────┐   │    │
│  │       │ [보내기] [수정] [취소]│   │    │
│  │       └─────────────────────┘   │    │
│  │                                   │    │
│  │ 고객:  네 진행해주세요              │    │
│  │                                   │    │
│  │ 🤖 초안: 네 회원님, 환불 접수 ...   │    │
│  └───────────────────────────────────┘    │
│                                            │
│  [유저 메시지 입력...  ]  [전송]            │
└────────────────────────────────────────────┘
```

### 동작 플로우
1. 데모 시작 시 시나리오 선택 (mock user + 해당 admin_data 로드)
2. 유저 메시지 입력 → 대화창에 고객 말풍선 추가
3. **2분 debounce 타이머 시작** (refund-agent-flow-v2.md 처리 타이밍 정책 참조)
4. 타이머 만료 전 추가 메시지 입력 → 타이머 리셋, 메시지 누적
5. 타이머 만료 → **쌓인 유저 메시지 전부 묶어서** agent 1회 실행 → 초안 생성
6. 초안은 **"상담사 승인 대기"** 상태로 대화창에 노란색 박스로 표시 (실제 발송 아님)
7. 상담사 역할: [보내기] 클릭 → 대화 기록에 추가 / [수정] → 편집 / [취소] → 버림
8. 상담사가 [보내기] → 다음 유저 턴 대기
9. 유저가 또 메시지 → step 3~8 반복

### 데모 시연 시나리오 (3~5개)
골든셋 v2에서 골라 쓰기:
1. **T1 2턴** (결제 없음 → 해지 방법 재안내)
2. **T4 → T3 핑퐁** (자동결제 불만 → 리텐션 → 환불 결정 → 접수 완료) — 가장 임팩트 큰 시나리오
3. **T2 1개월권** (부분환불 계산 동작)
4. **T6** (유저 미식별 → 본인확인)
5. **T8** (카드변경 안내)

### 데모 앱 구현 스코프
- **신규 Streamlit 앱**: `app_live_chat_demo.py` (별도 포트, 예: 8505)
- **핵심 state**: `st.session_state`에 대화 기록 + 타이머 상태 + 쌓인 메시지 리스트
- **Debounce 구현**: `time.time()`으로 마지막 메시지 timestamp 추적 + `st.rerun()` 폴링 또는 수동 "생성" 버튼
  - 첫 버전은 **수동 버튼** ("지금 생성" + 쌓인 메시지 표시)이 Streamlit 한계상 가장 단순
  - 자동 타이머는 JavaScript 또는 Streamlit component 필요 (후속)
- **상담사 승인 UI**: 생성된 초안 아래 3 버튼 (보내기/수정/취소)
- **멀티턴 context**: agent 호출 시 이전 턴 전부 `conversation_turns`로 전달

### 관련 파일
- 신규: `app_live_chat_demo.py`
- 재사용: `src/refund_agent_v2.py::RefundAgentV2.process`, `data/mock_scenarios/golden/v2/*.json`

---

## Part B — 상담사 환불처리 비교 시스템

### 목적
**매일 들어오는 실제 환불 문의 대해**, agent가 미리 답안을 생성해 두고, 나중에 상담사가 처리한 답변과 **자동 대조**해서 일치/불일치 통계 산출. 이게 **continuous evaluation + 템플릿 drift 감지** 역할을 동시에 한다.

### 핵심 제약 (2026-04-05 Gayoon 확정)

**1. 비영업시간 window 에서만 수집**
- **평일**: 퇴근시간 (18:30) 이후 ~ 자정
- **주말**: 금요일 18:30 이후 ~ 일요일 자정 직전
- 이유: 영업시간 중에는 상담사가 실시간으로 답해버려서 "에이전트가 먼저 답 만들어두고 상담사 답변 나온 뒤 비교" 의 **비교 window 가 생기지 않음**. 비영업시간에 쌓인 문의는 다음 영업일 오전에 상담사가 일괄 답변 → 그 사이에 에이전트가 미리 돌려놓으면 shadow 비교 가능.

**2. 스냅샷 기반 mock data**
- 에이전트가 admin API 를 조회한 **그 시점** 의 응답을 JSON 으로 snapshot 저장
- 이유: 다음날 상담사가 답변할 때쯤이면 admin 데이터가 변할 수 있음 (환불 처리 반영, 상품 상태 변경 등). 에이전트 실행 시점과 상담사 답변 시점이 어긋나면 "같은 입력에 대한 두 답변" 이 아니게 됨.
- Snapshot 은 `data/shadow/snapshots/{date}/{chat_id}.json` 에 고정 → 나중에 재현용으로도 활용 가능
- 추후 골든셋 확장 재료로 자동 유입

**3. 도메인 gate 통과 건만 스냅샷**
- Shadow collector 가 받은 문의는 일단 wrapper agent 로 intent 분류 (LLM 1회 호출)
- **환불 도메인 whitelist (REFUND_DOMAIN_INTENTS)** 에 해당되면 → 스냅샷 + agent 실행 + 비교 대상
- 비도메인 (기타/수업/배송 등) → skip 하되 로그만 남김 (향후 도메인 확장 시 재료)

### 아키텍처 (shadow evaluation)

```
            ┌─────────────────────┐
  유저 ───> │  채널톡 (실제)       │ <─── 상담사
            │  (우리 수정 안 함)   │
            └──────────┬──────────┘
                       │ pull (N분 주기)
                       ▼
        ┌──────────────────────────────┐
        │  Shadow Collector            │
        │  - 미처리 환불 대화 pull     │
        │  - 2분 debounce 체크          │
        │  - admin_data snapshot        │
        └──────────────┬───────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  Agent 초안 생성              │
        │  - 각 유저 턴마다 실행        │
        │  - predictions.jsonl 저장    │
        └──────────────┬───────────────┘
                       │
                       ▼ (시간 경과 후 — 상담사가 답함)
        ┌──────────────────────────────┐
        │  Comparison Engine            │
        │  - 같은 chat_id 재 pull       │
        │  - 상담사 실답변 추출         │
        │  - agent 초안 vs 상담사 diff  │
        │  - 템플릿 match / 금액 match │
        │  - 결과 CSV + 대시보드        │
        └──────────────────────────────┘
```

### 데이터 흐름

**비영업시간 수집 루프** (스케줄러로 15분 주기):
1. 현재 시각이 비영업시간 window 인지 체크 → 아니면 skip
2. 채널톡에서 최근 N분 대화 pull → `data/shadow/inbox/{date}/{chat_id}.json`
3. 미처리 필터: 상담사 답변 없음 or 진행 중
4. 각 대화에 대해 debounce 체크: 마지막 유저 메시지 후 2분 경과?
5. Wrapper agent intent 분류 → 환불 도메인 whitelist 통과?
6. 통과 → us_user_id로 admin API 호출 → **admin_data snapshot 파일 저장**
   `data/shadow/snapshots/{date}/{chat_id}.json`
7. Wrapper agent 실행 (snapshot 을 admin_data 로 주입) → 초안 저장
   `data/shadow/predictions/{date}/{chat_id}.json`

**다음 영업일 비교 루프** (예: 오전 11시 1회):
8. 전날 수집된 chat_id 들 재 pull → 상담사 답변 추가됐는지 확인
9. 상담사 답변이 들어온 대화만 필터
10. 각 유저 턴마다: agent 초안 (snapshot 기준) vs 상담사 실답변 대조
11. 평가 기록: `data/shadow/eval/{date}.csv`
    - `chat_id, turn_idx, agent_template, manager_template_inferred, template_match, agent_amount, manager_amount, amount_diff, notes`
12. 대시보드 업데이트 (일별 요약 + 불일치 케이스 드릴다운)

**핵심 불변조건**: 스냅샷 시점의 admin_data 와 상담사가 실제로 본 admin_data 가 다를 수 있음 (예: 상담사가 환불 처리하면 transactions 에 refund 가 추가됨). 비교는 **스냅샷 시점 기준** 으로만 유효. 상담사 답변이 스냅샷 이후 admin 변경을 반영하고 있으면 불일치로 기록하되 "admin 상태 변경으로 인한 불일치" 태그로 구분.

### 평가자 (4종)
1. **template_accuracy** — agent 고른 template_id vs 상담사 답변에서 추론한 template
2. **amount_accuracy** — T2 케이스에서 환불 금액 ±1원 일치
3. **api_call_coverage** — agent가 필요한 조회 API 다 호출했는가 (시나리오별 매트릭스)
4. **pii_compliance** — agent 답변에 카드/전화/이메일 노출 없는가

### 연계 효과 (덤)
- **템플릿 drift 감지**: 상담사 답변 클러스터링 → 기존 `openspec/refund-templates.md` 원문과 diff → 매크로 변경 자동 감지
- **데이터 축적**: 매일 N건씩 쌓여서 Phase 5-B 평가셋 자동 확장
- **회귀 방지**: 코드 변경 후 그날 데이터로 바로 돌려서 점수 하락 감지

### 구현 스코프 (단계)
**Phase B1 — 수동 pull + 단건 파이프라인** (첫 버전)
- `scripts/shadow_pull.py` — 채널톡 API 또는 수동 CSV 입력
- `scripts/shadow_run_agent.py` — pull된 대화에 agent 실행
- `scripts/shadow_compare.py` — 나중에 매니저 답변 들어오면 대조

**Phase B2 — 자동화 + 대시보드**
- 크론/스케줄러로 15분 주기 pull
- 8504 대시보드에 "Shadow Eval" 탭 추가 — 일별 요약 + 불일치 드릴다운

**Phase B3 — 연속 개선 루프**
- 드리프트 감지 alert (템플릿 클러스터 변화)
- 평가 점수 대시보드 (시계열)
- 코드 변경 시 자동 회귀 돌리기

### 관련 파일
- 신규: `scripts/shadow_*.py`, `data/shadow/` 디렉토리 트리
- 재사용: `src/refund_agent_v2.py`, `src/admin_api.py`, 기존 평가 로직

---

## Part A ↔ Part B 관계

두 부분은 **같은 엔진을 공유**하고 다른 용도로 쓴다:

| 측면 | Part A (실시간 데모) | Part B (shadow 비교 시스템) |
|---|---|---|
| **목적** | 청중에게 보여주기 | 자동 품질 검증 |
| **입력** | 데모용 시나리오 (골든셋) | 실제 오늘 들어온 환불 문의 |
| **유저** | 시연자 (가짜 유저) | 실제 고객 |
| **상담사** | 데모 진행자가 승인 버튼 누름 | 실제 CS팀 (원래 workflow) |
| **출력** | 스크린에 대화창 | CSV + 대시보드 통계 |
| **Debounce** | 2분 (또는 즉시 버튼) | 2분 |
| **Agent 엔진** | 공통 `RefundAgentV2` | 공통 `RefundAgentV2` |

Part A는 "우리 agent 이렇게 동작합니다" 시연,  
Part B는 "실제로 얼마나 정확하게 동작하는지 매일 검증합니다" 증명.

## 우선순위

1. **Part B Phase B1** — 평가 자동화가 회귀 방지/데이터 축적을 열어주니 가장 임팩트 큼
2. **Part A 초안** — 데모 필요 시점에 맞춰 구축 (현재 데모용 8504 탭으로도 어느 정도 시연 가능)
3. **Part B Phase B2/B3** — 장기 연속 개선

---

## 다음 세션 진입점

1. 이 문서 (`openspec/demo-app-plan.md`) 읽기
2. `openspec/refund-agent-flow-v2.md`의 처리 타이밍 정책 + Phase 5-B 참조
3. Part B Phase B1 착수 or Part A 착수 선택

**문서 끝**. Gayoon 확정 후 scripts/ 및 app_live_chat_demo.py 구현 시작.
