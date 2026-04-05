# AgentCore Integration Plan (해커톤 4/8 미팅)

## Context
cs-agent-demo는 AWS AgentCore 해커톤 제출용. 기술 효율성이 아니라 **쇼케이스 + 심사 임팩트** 관점에서 AgentCore 스택을 활용해야 함.

핵심 제약:
- 4/8 오전 미팅 마감
- 개발팀 내부 데모는 4/6에 동작해야 함
- 기존에 검증된 것(legacy RefundAgentV2, workflow.py, v2 골든셋 8건, strands-evals 7/8)은 **건드리지 않음** (회귀 0)
- 새로 만드는 건 **top 레이어만** (Strands Agent wrapper)

## 최종 아키텍처 (2026-04-05 확정)

```
유저 메시지
    ↓
🤖 Strands Wrapper Agent (src/agents/wrapper_agent.py, 2026-04-05 신규)
    │  • SlidingWindowConversationManager (멀티턴 Memory 자동)
    │  • Guardrail 훅 (Bedrock invoke_model)
    │  • OTel span → AgentCore Evaluation
    │
    └─ @tool run_refund_workflow(user_msg, admin_data_json, ...)
           ↓
           RefundAgentV2.process()  ← 검증된 legacy, 0 변경
           (workflow.py + refund_engine + templates + LLM intent classifier)
```

**원칙**: "Strands 껍데기 + 검증된 legacy 뇌".

## 4/6 (내일) — 개발팀 내부 데모 동작

### 오전 [1h] wrapper agent 완성 + smoke test
- `src/agents/wrapper_agent.py` 는 **2026-04-05 커밋에 이미 있음**
- 문제: Agent가 tool call 안 하고 clarifying question 생성
- 수정: SYSTEM_PROMPT 강화 — "환불/해지 키워드가 있으면 **반드시** run_refund_workflow tool을 첫 턴에 호출한 뒤 답변"
- smoke: v2 골든셋 6건 중 2건 (T1/T2_1month) 에서 tool call → 답변 확인

### 오전 [30m] 대시보드에 Strands Wrapper 탭 추가
- `app_refund_v2.py` 의 radio 옵션에 "🤖 Strands Wrapper (신규)"
- `_run_wrapper_on_case(case)` 함수 — `get_agent_for_session(chat_id)` 호출
- 기존 "v2 골든셋" 탭은 그대로 유지 (비교용)

### 오후 [1h] Guardrail 붙이기
- AWS Console에서 Bedrock Guardrail 생성 (카드번호/전화/이메일 masking)
- `guardrail_id.json` 에 ID 저장 (us-product-agent 패턴)
- `wrapper_agent.py` 의 BedrockModel 설정에 guardrail 연결
- 시연 케이스 1건에서 PII 마스킹 확인

### 오후 [2h] AgentCore Evaluation 트랙 (선택, 시간 되면)
- `bedrock-agentcore-sdk` 설치
- `scripts/telemetry_setup.py` — OTel in-memory exporter
- `convert_strands_to_adot` 로 span 변환
- `scripts/run_evals_agentcore.py` — `Builtin.ToolSelectionAccuracy` 1개만 먼저
- `evaluator_ids.json` 저장
- 기존 strands-evals 트랙과 병행

### 저녁 [30m] 커밋 + 4/7 메모

## 4/7 — 폴리싱

- 오전: wrapper agent 버그 수정 (tool call 실패 케이스)
- 오전: 시연 시나리오 2~3건 선정 + 대시보드에서 확인
- 오후: README에 AgentCore 활용 섹션 (심사관용)
- 오후: KB 시드 (선택, 정책 원문 3~4 chunk만 구색 맞추기)
- 저녁: 리허설

## 4/8 오전 — 최종 리허설 + 미팅

## 사용하는 AgentCore 컴포넌트

| 컴포넌트 | 사용 방식 | 구현 난이도 | 필수 여부 |
|---|---|---|---|
| **Memory (SlidingWindow)** | Strands 내장 `SlidingWindowConversationManager(window_size=20)` | 하 (자동) | ✅ 필수 |
| **Guardrail** | Bedrock Guardrail + `invoke_model.guardrailIdentifier` | 하 | ✅ 필수 |
| **Evaluation** | OTel span → ADOT → AgentCore evaluator | 중상 | 🟡 선택 |
| **Knowledge Base** | (4/7 구색 맞추기용, 최소 scope) | 중 | ⏸ 후순위 |

## 사용하지 않기로 한 것 (시간 제약)

- **consultant.py** (Phase 3 산출물) — 검증 안 됨. Wrapper가 대신.
- **YAML diagnose engine** — `domain/refund_chains.yaml` 에 T4/T7 체인 추가했으나 **현재 dead code** (legacy workflow가 우선). 4/7 이후 wrapper agent 가 `diagnose_refund_case` tool 을 직접 쓰는 방향으로 전환 검토.
- **AgentCore Memory (장기 기억 API)** — SlidingWindow로 충분.
- **Graph 리팩토링** — 원래 배제 결정(16:30 노트), Anthropic "simple first".

## 리스크 + 완화

1. **Wrapper agent tool call 실패** (4/5 확인됨)
   → 4/6 오전에 SYSTEM_PROMPT 강화 + 1차 user message 에 "context injection" 명시
2. **Guardrail 통합 중 Bedrock 응답 포맷 변화**
   → us-product-agent 의 기존 guardrail 사용 패턴 복사
3. **시간 부족**
   → Memory + Guardrail 2개 확보가 최소 목표. Evaluation/KB 는 시간 여유시.

## 레퍼런스

- `~/Documents/ai/us-product-agent` — 같은 AgentCore 스택, `eval_scenarios.py`, `guardrail_id.json`, `pyproject.toml`
- `openspec/refund-agent-flow-v2.md` — 환불 agent v2 아키텍처 (최종 설계 원본)
- `.claude/notes/채널톡 어시스턴트/2026-04-05.md` L10~170 — 아키텍처 결정 기록

---

**문서 끝**. 4/6 오전 첫 10분: wrapper_agent.py SYSTEM_PROMPT 강화 + tool call smoke test 부터.
