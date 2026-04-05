# 환불/해지 Agent 설계 v2 — 단일 Strands 하이브리드

> **작성일**: 2026-04-05
> **커밋**: `6612c85` (Phase 0~4 기반 인프라 구현 완료)
> **관계**: `refund-agent-flow.md` v1(2026-04-02)의 실행 아키텍처.
> v1은 **도메인 분석 + 답변 유형 7종** (무엇을), v2는 **에이전트 구현 구조** (어떻게).

---

## Context — 왜 v2가 필요했나

v1 작성 이후 구현 진척을 쌓아오면서 **정책과 LLM 판단의 분리**가 실전 설계의 핵심 고민으로 드러남. 2026-04-05 논의에서 Gayoon이 3가지 핵심 질문을 정리:

1. **답변 일관성**: 상담사 패턴(템플릿)이 매 답변마다 일관되게 유지돼야 함
2. **정보 부족 시 자동 재질문**: 에이전트가 누락 정보를 감지하고 자연어로 재질문
3. **모호/감정/맥락 전환 대응**: 정해진 분기로 답할 수 없는 케이스에서도 자연스러운 응답

또한 **`paymentCycle` 사건** (필드 이름과 실제 의미 불일치로 계산 오류)이 준 교훈: **LLM에 정책 판단을 맡기면 위험**. 정책은 결정적 코드로 강제되어야 함.

---

## 설계 원칙 (3개 소스에서 차용)

### 1. Gayoon 이전 프로젝트 (관리자센터 상품 세팅 에이전트)
- **YAML = Single Source of Truth**: 조건/규칙/템플릿이 한 파일에 선언. DiagnoseEngine + ActionHarness + KnowledgeHandler가 동일 YAML 참조
- **Turing-incomplete DSL**: `field.path == 'VALUE'` 같은 제한 문법. 무한 루프/사이드이펙트 불가 → 안전성 핵심
- **Harness 패턴**: tool 호출 전후에 pre/post validation. "LLM 프롬프트로 규칙 부탁" ❌, "코드로 강제" ✅
- **프롬프트 규칙 15→5개 축소**: 정책 텍스트를 프롬프트 밖으로 빼고 코드로 옮김

### 2. Rasa CALM (Conversational AI with Language Models)
- **Command Generator 개념**: LLM이 매 턴 "어떤 명령을 실행할지" 생성. Flow는 결정적, LLM은 라우팅만
- **Conversation patterns as tools**: Clarification / Chitchat / Cancellation / Correction / Handoff 등을 각각 flow/tool로 분리
- **Multi-turn state 자동 관리**: sliding window context

### 3. Anthropic "Building effective agents" (2024.12)
- **Simple first**: 복잡한 orchestration 프레임워크부터 쓰지 말고 단순 tool-use 루프부터
- **Graph 배제**: 명시적 graph는 LLM이 node 순서를 올바르게 결정한다는 가정에 의존 → 가장 취약한 부분
- **Sub-agent는 scale 증명된 후**: 단일 에이전트로 시작, 복잡도 폭발 시 분리

### 이 셋을 결합한 결론
**Command Generator ≡ Tool-loop**. Rasa의 "command 생성 → deterministic 실행" 패턴과 Anthropic의 "LLM이 tool 선택 → 코드로 실행" 패턴이 본질적으로 동일. Rasa는 graph로, 우리는 평탄한 tool 목록으로 구현. 실행 메커니즘은 달라도 철학은 같음.

---

## 아키텍처 청사진

### 단일 상담 Agent (Strands Agents SDK)

```
User (채널톡 메시지)
    ↓
┌─────────────────────────────────────────────────────┐
│ 상담 Agent (단일 Strands Agent)                     │
│                                                     │
│ System prompt: 대화 톤 + tool 사용 가이드만         │
│                (정책/규칙 텍스트 없음)              │
│                                                     │
│ Tool 카탈로그 (평탄한 14개):                        │
│  ├─ 데이터 조회 (5)                                 │
│  │  search_user_by_phone, get_user_profile,        │
│  │  get_membership_history_summary,                 │
│  │  get_refund_history_summary, get_transaction_list│
│  │                                                  │
│  ├─ 워크플로우 + Harness (3)                        │
│  │  diagnose_refund_case   ← YAML 체인 순회         │
│  │  calculate_refund_amount ← RefundEngine 호출     │
│  │  compose_template_answer ← 결정적 렌더링         │
│  │                                                  │
│  ├─ 대화 관리 (4) ← Rasa CALM 영감                  │
│  │  ask_clarification, handle_off_topic,           │
│  │  handle_emotional_distress,                     │
│  │  handle_cancellation_of_flow                    │
│  │                                                  │
│  └─ 폴백 (2)                                        │
│     llm_freeform_answer, handoff_to_human           │
│                                                     │
│ Multi-turn state: Strands SlidingWindow (20턴)      │
│ Model: Bedrock Claude Haiku 4.5                     │
└─────────────────────────────────────────────────────┘
        ↓ (참조)
┌─────────────────────────────────────────────────────┐
│ YAML SSoT (domain/)                                 │
│ - refund_chains.yaml   : 13개 라우팅 체인 + 키워드  │
│ - templates.yaml       : 16종 답변 템플릿           │
│ - (refund_rules.yaml)  : 용어/톤 규칙 (계획)        │
└─────────────────────────────────────────────────────┘
```

### 동작 순서 (LLM의 "필수 프로토콜")

1. 유저 메시지 수신 → context 요약과 함께 Agent 호출
2. LLM이 `diagnose_refund_case()` **즉시 호출** (system prompt로 강제)
   - 내부: DiagnoseEngine이 routing_order 순회 → 첫 매칭 체인의 `template_id` 반환
3. `template_id`에 따라:
   - `T2_환불_규정_금액` → `calculate_refund_amount()` → `compose_template_answer(..)` 
   - 다른 template_id → 바로 `compose_template_answer()`
   - `T_LLM_FALLBACK` → `llm_freeform_answer()`
4. 유저가 감정 표현 → `handle_emotional_distress` 먼저 호출 (opener 생성) → 이후 프로토콜
5. 범위 밖 질문 → `handle_off_topic` 또는 `handoff_to_human`

### v1 → v2 매핑

| v1 개념 (refund-agent-flow.md) | v2 구현 |
|---|---|
| 답변 유형 7종 + T99 기타 | `domain/templates.yaml` 16종 (T99는 `T_LLM_FALLBACK` + Phase 5 세부화) |
| 정보 조회 → 답변 유형 결정 매트릭스 | `domain/refund_chains.yaml` + `DiagnoseEngine` |
| 도구 매핑 (get_subscriptions 등) | `src/tools/data_tools.py` 5개 |
| 환불 금액 계산 | `calculate_refund_amount` tool + `RefundEngine` |
| Phase 4 추가 질문 생성 | `ask_clarification` tool (Phase 5 세부화 필요) |
| T4 리텐션 질문 | `domain/templates.yaml::T4_자동결제_설명` + Phase 5 트리거 로직 |

---

## YAML SSoT 구조

### `domain/refund_chains.yaml` — 13개 라우팅 체인

```yaml
chains:
  is_card_change_inquiry:
    requires:
      - id: CARD1
        check: "has_keyword(user_text, 'card_change_keywords')"
        fail_message: "카드 변경 키워드 없음"
    on_pass_template: T8_카드변경_안내

  has_no_payment:
    requires:
      - id: PAY1
        check: "is_empty(ctx.success_txs)"
    on_pass_template: T1_구독해지_방법_앱

  # ... 13개 총
```

**체인 목록** (routing_order 순서):
1. `is_card_change_inquiry` (최우선)
2. `is_prev_turn_t2_followup`
3. `needs_user_identification`
4. `is_other_person_number`
5. `is_refund_urging`
6. `has_no_payment`
7. `is_all_refunded_with_new_question`
8. `is_all_refunded`
9. `is_refund_withdrawal`
10. `is_product_change`
11. `is_duplicate_payment`
12. `can_refund_normally`

### `domain/templates.yaml` — 16종 템플릿

T1(앱/웹), T2(부분/전액), T3, T4, T6, T6b, T7(완료/미완료), T8, T10(다운그레이드/업그레이드), T11, T12, T_LLM_FALLBACK

각 템플릿은 `required_slots` + `text` 구조. `compose_template_answer` tool이 slot 완전성 체크 + 치환 + post harness(카드번호 마스킹).

### DSL 제한 (Turing-incomplete)

- 지원: `field.path OP value`, `and/or/not`, `in/not in`, whitelist 함수
- 금지: lambda, comprehension, 대입, import, method call, 임의 함수
- 구현: `src/domain/dsl.py` (Python `ast` 화이트리스트 기반)
- 테스트: `tests/test_dsl_parser.py` 31/31

---

## 14 Tools 카탈로그

### 데이터 조회 (5) — `src/tools/data_tools.py`
- `search_user_by_phone(phone)` — 전화번호 → userId
- `get_user_profile(us_user_id)` — 프로필 (nickName, signup 등)
- `get_membership_history_summary()` — 멤버십 이력 요약 (is_onetime 플래그 포함)
- `get_refund_history_summary()` — 환불 이력 + has_pending
- `get_transaction_list()` — 결제/환불 구분 + unrefunded_count

**실제/Mock 모드 자동 전환**: `ADMIN_API_BASE_URL` + `ADMIN_API_TOKEN` 환경변수 유무로 판단

### 워크플로우 + Harness (3) — `src/tools/workflow_tools.py`
- `diagnose_refund_case()` — DiagnoseEngine이 YAML 체인 순회 → template_id + trace 반환
- `calculate_refund_amount()` — 기존 `RefundEngine` 호출 + post-harness(refund_amount 유효성)
- `compose_template_answer(template_id, slots_json)` — 템플릿 렌더링 + pre/post harness

### 대화 관리 (4) — `src/tools/conversation_tools.py`
- `ask_clarification(missing_info, context_hint)` — 자연어 재질문
- `handle_off_topic(topic_summary)` — 범위 밖 안내
- `handle_emotional_distress(emotion_type)` — 공감 opener
- `handle_cancellation_of_flow(reason)` — flow 철회 처리

### 폴백 (2) — `src/tools/fallback_tools.py`
- `llm_freeform_answer(situation_summary)` — edge case 자유 응답
- `handoff_to_human(reason)` — 상담사 인계

---

## 현재 구현 상태 (Phase 0~4 완료)

### Phase 0 — Strands SDK 검증 ✅
- Python 3.11 venv 구성
- `strands-agents 1.34.1` 설치
- `scripts/phase0_poc.py`: Bedrock Haiku 4.5 + `@tool` + tool-loop 정상 동작 확인

### Phase 1 — YAML SSoT + DSL + DiagnoseEngine ✅
- `src/domain/dsl.py` (Turing-incomplete 평가기)
- `src/domain/loader.py` (YAML 로더 + mtime 캐시)
- `src/domain/diagnose_engine.py` (체인 순회 + first_failure)
- `src/domain/action_harness.py` (pre/post validation)
- `src/domain/functions.py` (DSL 함수 registry)
- `domain/refund_chains.yaml` (13 chains)
- `domain/templates.yaml` (16 templates)
- **Tests**: `test_dsl_parser.py` 31/31, `test_diagnose_engine.py` 11/11

### Phase 2 — 14 Tools 구현 ✅
- `src/tools/{data,workflow,conversation,fallback}_tools.py`
- Import 검증 완료

### Phase 3 — Consultant Agent 조립 ✅
- `src/agents/consultant.py` (Strands Agent + 14 tools + SlidingWindow + system prompt)
- E2E 검증: T2 골든셋 시나리오
  - `Tool #1: diagnose_refund_case` → `T2_환불_규정_금액`
  - `Tool #2: calculate_refund_amount` → 환불금 **45,000원**
  - `Tool #3: compose_template_answer` → 완성된 답변

### Phase 4 — 회귀 검증 ✅
- `tests/test_workflow.py` → **23/23** 유지
- `tests/test_api_contract.py` → **389건** 파싱 유지
- `scripts/eval_golden.py` → **8/8** 유지
- 신규 DSL/Engine **42/42** 전부 통과

**커밋**: `6612c85 feat: Strands 단일 에이전트 하이브리드 기반 인프라 구축`

---

## ⚠️ Phase 5 세부화 과제 (다음 세션)

현재 구현은 "큰 그림/기반 인프라" 수준. 실제 데이터 패턴에 맞춘 세부 구현이 남아있음.

### 참조 문서 (다음 세션 시작 시 반드시 읽을 것)
- `openspec/demo-plan.md` L76-90, L144-150 — edge eval 필수 요소 + AgentCore Evaluation 4개 평가자 + Guardrail
- `openspec/refund-agent-flow.md` — v1, 389건 + 340건 분석, T99 비정형 208건(61.2%)
- `openspec/data-exploration-results.md` — 고객 pain point
- `data/test_cases/refund_edge_reclassified.json` — B_LLM 10건 `eval_elements` rubric
- `.claude/notes/채널톡 어시스턴트/2026-04-04.md` — 17:00 evaluation 설계 결정

### 세부 구현 과제 6개

| # | 과제 | 현재 상태 | 필요 작업 |
|---|---|---|---|
| 1 | **T99 비정형 208건(61%) 처리** | `llm_freeform_answer` stub 1개 | RAG 또는 패턴 분류 구조 설계 |
| 2 | **감정/리텐션 대응** | `handle_emotional_distress` opener 4줄 | T4 리텐션 질문 연계 + eval_elements 기반 rubric |
| 3 | **Phase 4 추가 질문 생성** | `ask_clarification` 2줄 템플릿 stub | 조회 결과 기반 slot 부족 자동 감지 |
| 4 | **AgentCore Guardrail 3종** | 카드번호 마스킹만 (`action_harness.py`) | PII 전면 + 권한 밖 답변 + 프롬프트 인젝션 |
| 5 | **AgentCore Evaluation 4개 평가자** | 0 | `type_accuracy`, `amount_accuracy`, `query_completeness`, `pii_compliance` |
| 6 | **Edge eval rubric** | 미적용 | `refund_edge_reclassified.json` B_LLM 10건 `eval_elements`를 LLM judge rubric으로 |

### 프레임워크 마이그레이션 (Phase 5-B)
- **`strands-agents-evals`** 도입 → normal 389건 rule-based 평가
- **AgentCore Evaluation** → edge 10건 LLM judge (로컬 boto3 dataset evaluation 방식)

---

## 핵심 검증 원칙

### `paymentCycle` 사건의 교훈 (2026-04-05)

이 세션 중 확정된 것: **API 필드명과 실제 의미가 일치하지 않을 수 있다**.
- `membership-history.paymentCycle` = **결제 회차 카운트** (단위 없는 숫자)
- `ProductListData.paymentPeriod` = **결제 주기(개월)** (`ONE_MONTH`, `SIX_MONTH` 등)
- 증거: `apps/us-admin/.../MembershipHistoryAccordion.tsx:10-37` `getPaymentCycleLabel()`

**결론**: LLM이 필드 이름 보고 의미 추측 → 오류. 반드시 **코드에서 결정적 매핑** + **harness 검증**. 이게 v2의 근본 이유.

### Trust Boundary
- LLM = **클라이언트** (추측할 수 있음)
- Tool + Harness = **서버 측 validation** (항상 강제)
- 정책/계산/필드 의미 전부 **코드 경계**에서 강제

---

## v1과의 관계 요약

| 축 | v1 (refund-agent-flow.md) | v2 (이 문서) |
|---|---|---|
| **목적** | 무엇을 답할 것인가 (도메인 분석) | 어떻게 실행할 것인가 (에이전트 구현) |
| **데이터 소스** | 389건 + 340건 매니저 응답 | v1 결론 + Gayoon 이전 프로젝트 패턴 |
| **범위** | 답변 유형 7종 + 조회→응답 매트릭스 | Strands Agent + 14 tools + YAML SSoT |
| **작성일** | 2026-04-02 | 2026-04-05 |
| **상태** | 보존 (역사적 문서) | 활성 (현재 구현의 기준) |

**v1은 여전히 도메인 기준.** v2의 diagnose/compose tool이 v1의 분기 로직을 실행.

---

## 다음 세션 진입점

1. 이 문서(`openspec/refund-agent-flow-v2.md`) 먼저 읽기
2. Phase 5 세부화 과제 6개 중 우선순위 결정 (Gayoon 판단)
3. 각 과제는 관련 참조 문서(위 표)부터 확인 후 구현
4. 회귀 테스트 유지 (23/23, 389, 8/8, 42/42)
5. `strands-agents-evals` + AgentCore Evaluation 프레임워크 마이그레이션은 Phase 5-B로 분리

---

**문서 끝**. 자세한 논의 히스토리는 `.claude/notes/채널톡 어시스턴트/2026-04-05.md` 참조.
