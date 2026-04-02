# 환불 에이전트 정책 기반 구현 계획

> 확정 정책: `DEVCRAFT/환불 처리/환불 답변 정책 비교 및 확정안.md`
> 케이스 맵: `DEVCRAFT/환불 처리/환불 케이스 전체 맵.md`

## 확정 정책

```
유저 식별 불가 → 본인확인 요청
카드 키워드 → 카드변경 안내
결제 없음 → 해지 방법 안내
전부 환불됨 → 접수 완료
미환불 + 7일 이내 + 미열람 → 환불 규정 (전액) + 금액
미환불 + 7일 이내 + 열람 → 환불 규정 (부분) + 금액 계산
미환불 + 7일 경과 → 환불 규정 (부분) + 금액 계산
이전턴 환불규정 안내됨 + 유저 동의 → 접수 완료
```

폐기: T4(리텐션), T9(해지처리완료). 에셋 제외(멤버십 구독만).

## 구현 단계

### Step 1: 테스트 케이스 준비

**목표**: 실제 대화 데이터에서 유저 메시지 수집 + 워크플로우 분기별 mock API 응답 세트 준비

1. 유저 메시지 케이스 수집 (`data/test_messages.json`)
   - 실제 대화에서 추출한 다양한 표현
   - 패턴별: 환불 직접 요청, 규정 문의, 자동결제 불만, 처리 확인, 해지/취소, 카드, 기타

2. mock API 응답 세트 정의 (`data/mock_api_responses.json`)
   - 각 워크플로우 분기를 태울 수 있는 데이터 조합:

   ```
   mock_no_user:     유저 검색 실패 (전화번호 없음)
   mock_no_payment:  유저 있음 + 결제 이력 없음
   mock_full_refund: 유저 있음 + 미환불 1건 + 7일 이내 + 미열람
   mock_partial_7d:  유저 있음 + 미환불 1건 + 7일 이내 + 열람 있음
   mock_partial_exp: 유저 있음 + 미환불 1건 + 7일 경과
   mock_all_refunded:유저 있음 + 전부 환불됨
   mock_multi_pay:   유저 있음 + 미환불 3건 (정기결제)
   ```

3. 테스트 매트릭스: 유저 메시지 × mock 응답 = 기대 답변
   - 모든 조합에서 데이터 상태가 답변을 결정 (메시지와 무관)
   - 예외: 카드 키워드 → 카드변경

### Step 2: 워크플로우 최종 구현

1. `src/workflow.py` — 정책 기반 분기 (이미 뼈대 있음)
   - T2 안에서 전액/부분 분기 추가 (7일 + 열람 체크)
   - 이전 턴 맥락 처리 수정 (last_user_ts 기준)

2. `src/templates.py` — T1, T2(전액/부분), T3, T6, T8만 유지
   - T2 전액 템플릿 + T2 부분 템플릿 분리
   - {환불금액}, {결제금액} 변수 자리

3. `src/refund_engine.py` — 환불 금액 계산 (이미 있음, 검증만)
   - 입력: 결제금액, 결제일, 1개월 정가, 열람 여부
   - 출력: 환불 가능 여부, 환불금액, 차감금, 수수료

### Step 3: agent 대화 테스트

1. `tests/test_agent_e2e.py` — 유저 메시지 × mock 응답 조합 테스트
   ```python
   # 예시
   def test_refund_request_with_unpaid_payment():
       result = agent.process(
           messages=["환불해주세요"],
           phone="01012345678",
           mock_api=MOCK_PARTIAL_7D,  # 미환불 + 7일 이내 + 열람
       )
       assert "환불 규정" in result.final_answer
       assert "환불 금액:" in result.final_answer
       assert result.template_id == "T2_환불_규정_금액"
   ```

2. 검증 항목:
   - [ ] 올바른 템플릿 선택 (데이터 분기)
   - [ ] 환불 금액 계산 정확성
   - [ ] 템플릿 변수 채우기 ({환불금액} 등)
   - [ ] 카드 키워드 감지
   - [ ] 이전 턴 맥락 (T2 후 → T3)
   - [ ] 유저 식별 불가 시 T6 폴백

### Step 4: 실제 API 연동 테스트

1. admin API 토큰 + 실제 전화번호
2. 조회 → 워크플로우 → 답변 생성 end-to-end
3. 생성된 답변의 환불 금액이 실제 규정과 맞는지

## 파일 구조

```
src/
├── workflow.py          # 정책 기반 분기 (T2 전액/부분 포함)
├── templates.py         # T1, T2(전액/부분), T3, T6, T8
├── refund_engine.py     # 환불 금액 계산 (기존)
├── refund_agent_v2.py   # agent 메인 (workflow 호출)
├── admin_api.py         # API 클라이언트 (기존)
tests/
├── test_workflow.py     # 워크플로우 유닛 테스트 (정책 기반)
├── test_agent_e2e.py    # agent e2e 테스트 (메시지 × mock)
data/
├── test_messages.json   # 유저 메시지 케이스 (실제 데이터)
├── mock_api_responses.json  # 분기별 mock API 응답
```

## 현재 상태

- [x] 정책 확정 (케이스 맵 + 비교 자료)
- [x] 워크플로우 뼈대 (`workflow.py`, 유닛 테스트 14/14)
- [x] 환불 계산 엔진 (`refund_engine.py`)
- [x] admin API 클라이언트 (`admin_api.py`)
- [ ] Step 1: 테스트 케이스 + mock 응답 준비
- [ ] Step 2: T2 전액/부분 분기 구현
- [ ] Step 3: e2e 테스트
- [ ] Step 4: 실제 API 연동
