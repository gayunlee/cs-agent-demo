# 모호 문의 재질문 처리 계획

> **목적**: 환불 도메인에만 국한되지 않고, 유저의 첫 메시지가 **모호/정보 부족** 할 때 에이전트가 상담사처럼 추가 질문(clarifying question)을 던져 정보를 확보하도록 한다.
>
> **배경**: 2026-04-05 밤 Gayoon 결정. 지금까지는 환불 도메인 내부에서만 본인확인 재질문(T6) 이 동작. 환불 외 유형이거나 어느 도메인인지조차 불분명한 첫 메시지는 wrapper 가 그냥 domain gate 로 skip 해버려서 "무엇을 도와드릴까요?" 수준의 기본 응대도 안 하는 상태.

## 문제 정의

유저가 봇 버튼 클릭이나 단독 키워드만 보내고 나가버리는 실제 케이스:

| 유저 입력 | 현재 wrapper 동작 | 기대 동작 |
|---|---|---|
| "환불해지" (봇 버튼) | T6 재질문 or skip | "환불 문의 주셔서 감사합니다. 성함/휴대전화 번호 알려주시면 확인 도와드리겠습니다." |
| "문의드립니다" | gate skip (intent=기타) | "안녕하세요. 어떤 부분을 도와드릴까요? 결제/환불/수업/기술 문제 등 편하게 말씀해주세요." |
| "해지" 단독 | T1 해지 방법 안내 OK | 현재 동작 OK (환불 도메인) |
| "앱이 이상해요" | gate skip | "어떤 화면에서 어떤 증상인가요? 사용 기기(iOS/Android/PC)도 알려주시면 확인 도와드리겠습니다." |
| "수업 어디서 들어요" | gate skip | "어느 상품의 수업인지 말씀해주시면 수강 경로 안내 도와드리겠습니다." |
| "쿠폰 있나요" | gate skip | "현재 적용 가능한 프로모션은 상품별로 다릅니다. 관심 상품 말씀주시면 확인 도와드리겠습니다." |

현재 wrapper 의 한계:
- **환불 도메인 whitelist** 밖이면 즉시 gate skip → 상담사에게 전달
- "모호함" 을 별도 상태로 다루지 않음
- 유저가 봇 UI 를 정상 사용한 결과(키워드만 입력) 가 오히려 에이전트를 침묵하게 만듦

## 원칙 (`openspec/demo-plan.md` 에서 재확인)

> **모호한 문의 = 별도 유형이 아닌 "상태"**
> - 모든 문의 유형(환불, 로그인, 기술오류 등)에서 발생 가능
> - 워크플로우 실행 중 "정보 부족" 감지 시 → 추가 질문 생성
> - 추가 질문 패턴 (실제 매니저 데이터 기반):
>   - A. 본인 확인 요청 (성함/전화번호)
>   - B. 상품 특정 (어떤 과정?)
>   - C. 증상 구체화 (어떤 화면에서?)
>   - D. 오픈 질문 (무엇을 도와드릴까요?)

## 잠재 자산 (재활용 가능)

프로젝트에 이미 과거 세션에서 만들어진 **연결되지 않은 자산** 이 있음:

- `config/ambiguous_rules.json` — AMB-001~008 규칙 (본인확인, 상품특정, 증상구체화, 범위밖 등)
- `src/ambiguous_classifier.py` — 규칙 매칭 로직
- `tests/test_ambiguous_workflow.py` — 멀티턴 재질문 검증 케이스
- `src/agent.py` (legacy `CSAgent`) — 위 셋을 호출하는 경로. **현재 wrapper 경로에 연결 안 됨.**

wrapper 경로 (`RefundAgentV2` + `workflow.py`) 는 T6 본인확인만 환불 도메인 안에서 지원. `ambiguous_classifier` 는 통합 대기 상태.

## 구현 플랜

### Phase 1 — 실 데이터 기반 모호 케이스 카탈로그 작성 (선행)

**목표**: 상상으로 만들지 말고 실제 채널톡 데이터에서 모호 첫 메시지 유형을 추출.

- 소스: `data/test_cases/refund_convos_jan.json`, `data/test_cases/refund_pattern_analysis.json`, `letter-post-weekly-report/data/channel_io/` (원본 참조)
- 추출 기준:
  - 유저 첫 메시지가 5단어 이내 or 봇 버튼 클릭 (`options`/`buttons_clicked` 존재) 직후 침묵
  - 매니저가 "성함/번호/상품/증상/무엇을" 같은 재질문으로 응답한 케이스
- 클러스터링: 상담사 재질문의 핵심 의도별 grouping
- 결과물: `openspec/ambiguous-cases-catalog.md` (케이스 예시 + 빈도 + 매니저 재질문 패턴)

### Phase 2 — 도메인 불문 "의도 분류 + 모호 감지" 통합 classifier

- 현재 `src/intent_classifier.py` 의 14-intent + `src/refund_agent_v2.py` 의 7-intent 가 **이중 존재**. 정리 필요.
- 통합 classifier 출력:
  - `intent`: 환불/해지/결제/기술/수업/일반문의/…
  - `needs_clarification`: bool — 같은 intent 안에서도 정보 부족이면 true
  - `clarification_reason`: "본인확인필요" / "상품특정필요" / "증상구체화필요" / "일반오픈질문" / …
- wrapper SYSTEM_PROMPT 에 "needs_clarification=true 면 상황에 맞는 재질문 생성" 지시

### Phase 3 — Clarification tool 신규

wrapper 에 `@tool ask_clarification(reason, context)` 추가:
- `reason` 에 따라 적절한 재질문 템플릿 리턴
- 기존 `config/ambiguous_rules.json` 의 템플릿 재활용 가능
- legacy `RefundAgentV2.process` 가 `needs_clarification` 감지하면 이 tool 쪽으로 분기

### Phase 4 — Gate 완화

- 현재 `ALLOWED_INTENTS` whitelist 는 모호 clarification 을 허용하도록 확장
- 또는 **별도 레이어**: "clarification 가능 여부" 를 intent 와 독립적으로 판단
- 비도메인이어도 "오픈 재질문" 정도는 generate 해서 내부대화에 노출 (상담사가 빠르게 승인/수정)

## 우선순위

1. **Phase 1 (실 데이터 카탈로그) — 선행 필수**. 안 하면 Phase 2~4 가 상상 기반이 됨.
2. **Phase 2 (classifier 통합) — 4/7~4/8 작업**.
3. **Phase 3 (tool 추가) — Phase 2 이후 바로**.
4. **Phase 4 (gate 완화) — Phase 3 완료 후 신중하게**. False positive (비도메인에 엉뚱한 재질문) 위험.

## 리스크

1. **재질문 품질**: 실 데이터 기반이 아니면 기계적 질문 ("무엇을 도와드릴까요?") 만 나와서 유저 경험 저하. → Phase 1 카탈로그 필수.
2. **상담사 혼란**: 내부대화에 재질문 초안이 뜨면 상담사가 "이미 답변 만들어진 건가?" 헷갈릴 수 있음. 초안에 "⚠️ 재질문 (clarification)" 태그 필요.
3. **멀티턴 복잡도**: 재질문 → 유저 추가 답변 → 재분류 루프에서 wrapper 의 `turn_log` + `admin_cache` closure 와 맞물리는 부분. 테스트 필수.

## 다음 세션 진입점

1. 이 문서 읽기
2. `openspec/demo-plan.md` 의 "모호한 문의 = 상태" 섹션 재확인
3. `config/ambiguous_rules.json` + `src/ambiguous_classifier.py` 로 어떤 규칙이 이미 있는지 훑기
4. Phase 1 착수 — 실 데이터에서 모호 첫 메시지 추출 스크립트 작성

---

**문서 끝**. 2026-04-05 밤 Gayoon 요구로 신규 작성. 해커톤 (4/8) 이후 본격 착수 예정.
