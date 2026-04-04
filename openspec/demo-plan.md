# CS AI Agent 데모 구현 계획

## Context

채널톡 CS 환불/해지 문의를 자동 처리하는 에이전트 데모.
워크플로우 엔진(100% 테스트), 템플릿, 환불 계산, admin API 연동은 완성.
**다음**: eval 시스템 → 에이전트 구현 → 채널톡 연동.

## 설계 원칙

```
코드 워크플로우: 데이터 분기 + 템플릿 선택 (확정, 결정론적)
LLM: 맥락 파악 + 모호 문의 감지 + 멀티턴 판단
AgentCore: Evaluation(검증) + Guardrail(방어) + Memory(맥락)
```

**깔끔한 분리**:
- 분기 = 코드 (테스트 가능)
- 판단 = LLM (프롬프트로 제어)
- 검증 = AgentCore Evaluation (자동화)
- 방어 = AgentCore Guardrail (자동 필터)
- 답변 = 상담사 패턴 템플릿 (자유 생성 X)

**모호한 문의 = 별도 유형이 아닌 "상태"**:
- 모든 문의 유형(환불, 로그인, 기술오류 등)에서 발생 가능
- 워크플로우 실행 중 "정보 부족" 감지 시 → 추가 질문 생성
- 추가 질문 패턴 (실제 매니저 데이터 기반):
  - A. 본인 확인 요청 (성함/전화번호)
  - B. 상품 특정 (어떤 과정?)
  - C. 증상 구체화 (어떤 화면에서?)
  - D. 오픈 질문 (무엇을 도와드릴까요?)
- 첫 메시지든 두번째든, 정보 부족하면 트리거

---

## 아키텍처

```
[입력]                     [에이전트]                    [출력]
채널톡 웹훅                 Strands SDK Agent            내부대화 전송
  or              →           ↓                    →      or
가상 UI                   [LLM] 맥락 파악                가상 UI 표시
  or                         ↓
BQ 배치 테스트            [코드] 워크플로우 분기
                             ↓
                          [도구] API 조회 + 환불 계산
                             ↓
                          [코드] 템플릿 + 변수 → 답변
                             ↓
                          [AgentCore]
                           ├─ Evaluation: 답변 정확도 자동 검증
                           ├─ Guardrail: PII 마스킹 + 출력 필터
                           └─ Memory: 멀티턴 맥락 유지
```

---

## 단계 1: Eval 시스템 (먼저)

> 에이전트 구현 전에 **검증 체계**부터 만든다.
> 수정 → 전수 테스트 → 엣지케이스 발견 → 수정 루프.

### 1-1. 테스트 데이터 준비 ✅ 완료

- BQ 2026년 1월 환불/해지 대화 100건 분석 완료
- **normal 84건** (워크플로우 커버 가능)
- **edge 13건** (특별 대응 필요)
- 데이터: `data/test_cases/refund_convos_jan.json`, `data/test_cases/refund_pattern_analysis.json`

### 1-2. Evaluation 설계

**normal eval (rule-based)** — 84건:
- 에이전트 → 템플릿 + 금액 맞는지 자동 체크
- 기준: template_id 일치, 환불금액 정확, API 호출 완전성

**edge eval (LLM-as-judge)** — 13건:
- 실제 매니저 답변에서 필수 요소 추출
- 에이전트 답변에 필수 요소 포함 여부 LLM 판단
- 필수 요소: 공감 표현, 규정 근거, 금액 제시, 열람 여부 언급 등

### 1-3. AgentCore Evaluation 연동

4개 평가자:

| 평가자 | 기준 | 입력 | 판정 |
|--------|------|------|------|
| **type_accuracy** | 워크플로우 분기가 맞는지 | 에이전트 선택 템플릿 vs 정책 기대값 | PASS/FAIL |
| **amount_accuracy** | 환불 금액이 정확한지 | 계산값 vs 규정 공식 | PASS/FAIL |
| **query_completeness** | 필요 API 다 호출했는지 | 호출된 도구 목록 vs 기대 목록 | PASS/FAIL |
| **pii_compliance** | 개인정보 노출 없는지 | 답변 텍스트 스캔 | PASS/FAIL |

### 1-3. Eval 루프 자동화

```
BQ 실제 대화 389건
  ↓
각 대화 → 에이전트 입력
  ↓
에이전트 답변 생성
  ↓
AgentCore Evaluation 4개 평가자 실행
  ↓
불일치 케이스 자동 분류:
  - 워크플로우 분기 잘못 → 코드 수정
  - LLM 판단 잘못 → 프롬프트 수정
  - 데이터 부족 → 도구 추가
  - 새 패턴 발견 → 워크플로우 추가
  ↓
수정 후 다시 389건 돌리기
```

---

## 단계 2: 에이전트 구현

### 2-1. Strands SDK Agent (`src/agentcore_agent.py`)

**도구 (Tools)**:

```python
@tool search_user(phone: str) -> dict
    """전화번호로 유저 검색. AdminAPIClient.search_user_by_phone + get_user"""

@tool get_user_data(user_id: str) -> dict
    """구독/결제/멤버십/환불 이력 한번에 조회. 4개 API 호출."""

@tool calculate_refund(payment_amount: int, payment_date: str, content_accessed: bool) -> dict
    """환불 금액 산출. RefundEngine.calculate"""

@tool select_template(user_data: dict) -> dict
    """워크플로우 분기 실행 → 템플릿 ID + 변수 반환. workflow.run_workflow"""

@tool render_answer(template_id: str, variables: dict) -> str
    """템플릿에 변수 채워서 최종 답변 생성"""
```

**시스템 프롬프트**: 워크플로우 정책을 자연어로 설명.
- LLM이 도구 호출 순서 결정
- 모호한 문의 감지 → 추가 질문 생성
- 멀티턴 맥락 파악

**모델**: Claude Haiku 4.5 on Bedrock

### 2-2. AgentCore Guardrail

| 위협 | 대응 |
|------|------|
| PII 노출 (전화번호/카드번호) | PII 마스킹 필터 |
| 환불 권한 밖 답변 | 출력 범위 제한 |
| 프롬프트 인젝션 | 입력 필터 |

### 2-3. AgentCore Memory — 웹훅 메시지 처리

**웹훅 메시지가 올 때마다 전체를 다시 읽지 않고, Memory로 맥락 유지:**

```
새 메시지 웹훅 수신 → chat_id로 Memory 조회
  ├─ Memory 없음 (첫 메시지):
  │   1. 유저 조회 (전화번호 → API)
  │   2. 데이터 조회 (구독/결제/열람)
  │   3. 워크플로우 분기 → 답변 생성
  │   4. Memory에 저장: {chat_id, user_data, 답변, 상태}
  │   5. 내부대화로 전송
  │
  └─ Memory 있음 (후속 메시지):
      1. Memory에서 이전 맥락 로드
      2. 새 메시지만 추가
      3. 상태 판단 (유저 동의? 추가 질문? 새 요청?)
      4. 재조회 불필요 — Memory의 user_data 재사용
      5. 답변 생성 → Memory 업데이트
```

```python
# chat_id별 대화 상태
memory = {
    "chat_id": "abc123",
    "state": "T2_안내됨",  # 현재 상태
    "user_data": { ... },   # 조회 결과 캐시 (재조회 불필요)
    "turns": [
        {"role": "user", "content": "환불해주세요"},
        {"role": "agent", "template": "T2", "refund_amount": 360000}
    ],
    "user_data": { ... },  # 조회 결과 캐시 (재조회 불필요)
}
```

---

## 단계 3: 데모 UI

### 3-1. 가상 채널톡 UI (`app_agent_demo.py`)

```
[가상 채팅창]              [Agent Trace]           [내부대화 결과]
유저 메시지 표시             도구 호출 과정           조회 근거
상담사 명령어 입력           LLM 판단 과정           답변 초안
                          Evaluation 결과          템플릿 ID
```

- 채널톡 공식 문서 참고한 UI 형태
- 웹훅 payload 형식 그대로 → 나중에 실제 채널톡 붙일 때 코드 변경 최소

### 3-2. 채널톡 연동 준비

- 웹훅 수신: `webhook_handler.py` (기존)
- 명령어 감지: `/환불조회` → 에이전트 트리거
- 내부대화 전송: `channeltalk_sender.py` (채널톡 Open API)
- 데모에서는 가상 UI로, 나중에 실제 채널톡으로 교체

---

## 단계 4: 전수 검증 + 엣지케이스

### 4-1. BQ 데이터로 배치 테스트

```python
# 389건 전수 테스트
for case in all_cases:
    result = agent.process(case.messages, case.phone)
    evaluation = agentcore.evaluate(result, case.expected)
    if not evaluation.passed:
        report_failure(case, result, evaluation)
```

### 4-2. 엣지케이스 자동 발견

- 불일치 패턴 자동 분류 (워크플로우 문제 vs LLM 문제 vs 데이터 문제)
- 새 패턴 발견 시 테스트 케이스에 추가
- eval 루프 반복

---

## 멀티턴 처리

**데모**: 명령어 트리거 (`/환불조회`, `/환불접수`)
**미래**: 자동 트리거 (실제 대화 패턴 분석 후 결정)

```
턴1: 상담사 /환불조회 → 에이전트 조회 + T2 답변 (Memory에 저장)
턴2: 유저 "네 환불해주세요" → 상담사 /환불접수 → Memory에서 맥락 → T3
```

---

## 구현 순서

**1단계: 문의 유형 분류 + 정보 충분성 판단 검증** ← 현재
- [ ] BQ에서 다양한 문의 유형 데이터 수집 (환불/해지, 로그인, 기술오류, 강의, 결제 등)
- [ ] 분류 테스트: 문의 유형을 정확히 분류하는지
- [ ] 각 유형에서 "바로 답변 가능?" 판단이 맞는지
- [ ] 정보 부족 시 → 적절한 추가 질문 생성하는지 (A~D 패턴)
- [ ] 실제 매니저 대응 패턴과 비교

**2단계: 환불 워크플로우 검증**
- [ ] 환불 유형 → 정책 워크플로우대로 답변 나오는지
- [ ] 389건 배치 테스트 + AgentCore Evaluation 연동

**3단계: 에이전트 구현**
- [ ] Strands SDK agent + 도구 정의
- [ ] 시스템 프롬프트 (분류 + 정책 + 모호 대응)
- [ ] Guardrail + Memory 연동
- [ ] eval 루프 돌려서 검증

**4단계: 데모 UI + 채널톡 연동**
- [ ] 가상 채널톡 UI
- [ ] Agent trace 시각화
- [ ] 채널톡 연동 준비 (웹훅 + 내부대화 API)

**5단계: 전수 검증 + 엣지케이스**
- [ ] 엣지케이스 발견 + 수정 루프
- [ ] 최종 정확도 리포트

---

## 결정사항

- [x] Strands SDK 사용 → AgentCore runtime 배포
- [x] 분기는 코드 워크플로우 — LLM은 맥락 파악 + 모호 문의 감지만
- [x] 답변은 상담사 패턴 템플릿 — LLM 자유 생성 X
- [x] 데모 UI = 가상 채널톡 — 웹훅/API 실제 구현, UI만 시뮬레이션
- [x] 멀티턴 = 데모에선 명령어 트리거 — 자동화는 데이터 분석 후
- [x] eval 먼저 → 에이전트 → UI 순서
- [x] AgentCore Evaluation + Guardrail + Memory 활용
- [ ] 채널톡 내부대화 API 엔드포인트 확인
- [ ] 관리자센터 인증 방식 확정

## 파일 구조

```
src/
├── agentcore_agent.py      ← 신규 (Strands SDK agent + tools)
├── agent_orchestrator.py   ← 신규 (통합 진입점)
├── channeltalk_sender.py   ← 신규 (내부대화 전송)
├── workflow.py             ← 기존 (정책 분기, 유지)
├── admin_api.py            ← 기존 (API + 토큰 자동 갱신)
├── templates.py            ← 기존 (답변 템플릿)
├── refund_engine.py        ← 기존 (환불 계산)
├── bigquery/               ← 기존 (BQ 데이터 조회)
tests/
├── test_workflow.py        ← 기존 (15/15)
├── test_agent_e2e.py       ← 기존 (189/189)
├── test_agent_eval.py      ← 신규 (AgentCore Evaluation)
app_agent_demo.py           ← 신규 (가상 채널톡 데모 UI)
```

---

## 현재 작업: 환불/해지 전수 패턴 분류 + eval 기준

### Step 1: 1~3월 분류 (500건 배치)
- [x] 1월 BQ 조회 → `jan_2026_raw.json` (3,939건)
- [x] 1월 LLM 분류 → 배치 1~30 완료 (3,400건)
- [ ] 1월 나머지 (배치 31~40)
- [ ] 2월 BQ 조회 + LLM 분류
- [ ] 3월 BQ 조회 + LLM 분류

### Step 2: 환불/해지 전체 대화 조회 + 워크플로우 매칭
- [x] 1월 100건 대화 조회 + 패턴 분석 (84% normal, 13% edge)
- [ ] 1월 나머지 1,114건 대화 조회
- [ ] LLM으로 워크플로우 매칭 (500건 배치)
- [ ] 2~3월 동일 처리

### Step 3: 유형별 chat_id 리스트
- [ ] `refund_cases_by_type.json` 생성

### Step 4: edge eval 기준
- [x] 13건 필수 요소 추출 (10/12 성공)
- [ ] 전체 edge 케이스 eval 기준 확정

---

## 에이전트 구현 시 사용할 패키지

### Strands SDK
- `strands-agents` — 에이전트 프레임워크 (@tool, Agent 클래스)
- `strands-agents-evals` — evaluation 프레임워크 (rule-based + LLM evaluator)
- 설치: `pip install strands-agents strands-agents-evals`

### Eval 통합 계획
- 현재: `scripts/eval_refund.py` (로컬 커스텀)
- 변경: `strands-agents-evals`로 마이그레이션
  - normal eval → rule-based evaluator (템플릿 + 금액 체크)
  - edge eval → LLM evaluator (필수 요소 포함 체크)
  - 데이터: `data/test_cases/refund_edge_reclassified.json` (eval 기준 포함)
- 에이전트 + eval 같이 AgentCore에 배포
