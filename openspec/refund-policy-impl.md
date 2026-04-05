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

---

## 환불 계산 공식 — 확정 ground truth (2026-04-05)

**정책 원문 (운영 공지)**:
> 1. 결제 후 7일 이내, 구독권 미개시 및 콘텐츠 미열람 시 **전액 환불**
> 2. 구독권 개시 후: 이용 기간에 따라 **이용 금액 차감** + **수수료 10%**
> 3. 전체 기간 1/3 경과 전: **1/3 차감**
> 4. 전체 기간 1/2 경과 전: **1/2 차감**
> 5. 환불 기준일 = 회원이 최초로 환불을 요청한 날짜
> 6. **1개월 = 30일 기준**
> 7. 결제주기가 2개월 이상인 구독 상품도 환불 시 **1개월 정가 기준**으로 이용 금액을 산정하여 차감
> 8. 전액 환불 대상은 수수료 없음

### 확정 공식 (2026-04-05 Gayoon 확정)

```python
# 입력
total_paid         # 유저가 결제한 금액
cycle_months       # 상품 결제주기 (1, 2, 3, 4, 6, 12 등)
days_elapsed       # 결제일 ~ 유저 환불 요청일 사이 경과일
content_accessed   # 콘텐츠 열람 여부
monthly_price      # 1개월 정가 (아래 출처 참조)

# 계산
total_period_days = cycle_months × 30         # 정책 6번
period_fraction   = days_elapsed / total_period_days

if days_elapsed <= 7 and not content_accessed:
    refund = total_paid                        # 전액 환불, 수수료 없음 (정책 1번, 8번)

else:
    if period_fraction <= 1/3:
        deduction = monthly_price × (1/3)      # 정책 3번
    else:
        deduction = monthly_price × (1/2)      # 정책 4번 + 1/2 경과 후도 동일 적용
                                               # (1/2 경과 후는 정책 본문 침묵 → 1/2 차감 규칙 유지,
                                               #  예외 판단은 CS팀 재량)
    remaining = total_paid - deduction
    fee       = floor(remaining × 0.10)        # 정책 2번 수수료
    refund    = remaining - fee
```

### `monthly_price` (1개월 정가) 출처

정책 7번 **"2개월 이상 구독도 1개월 정가 기준"** 구현:

| 상품 주기 | monthly_price |
|---|---|
| 1개월권 (`cycle_months == 1`) | `monthly_price = total_paid` (= 상품 가격) |
| 2개월 이상 | 같은 `productPageId` 그룹의 `paymentPeriod == ONE_MONTH` 옵션의 `price` |

**API 경로**:
```
myProducts[].product.groupCode  (= productPageId)
  → GET /v1/product/group/{productPageId}
  → ProductListData[]
  → filter paymentPeriod == "ONE_MONTH"
  → .price  ✅ 이게 1개월 정가
```

**Fallback** (product_group 조회 실패 또는 ONE_MONTH 옵션 없음):
`monthly_price = total_paid / cycle_months` + 경고 로그. CS팀이 이상치 감지 시 수동 검수.

**근거**: `openspec/admin-ui-page-map.md:179` (`groupCode`), `:305-313` (`/v1/product/group/` API + `paymentPeriod` enum).

### 실데이터 역산 검증 (2026-04-05)

`refund_test_cases_enriched.json` 18건 중 매니저 답변에 "환불 금액 X원" 명시된 케이스 분석:

| 케이스 패턴 | 건수 | 총결제 | 매니저 환불 | 공식 검증 |
|---|---:|---:|---:|---|
| 1개월권 50k + 1/3 경과 전 + 열람 | 11 | 50,000 | 30,000 | `(50k - 50k×1/3) × 0.9 = 30,000` ✅ |
| 1개월권 40k + 1/3 경과 전 + 열람 | 1 | 40,000 | 24,000 | `(40k - 40k×1/3) × 0.9 = 24,000` ✅ |
| 1개월권 30k + 1/3 경과 전 + 열람 | 1 | 30,000 | 18,000 | `(30k - 30k×1/3) × 0.9 = 18,000` ✅ |
| 6개월권 500k + 1개월 경과 | 1 | 500,000 | 360,000 | `(500k - 100k×1/3) × 0.9 = 438,000`으로 계산됨. 매니저는 `(500k - 100k×1) × 0.9 = 360,000` 적용 (재량) |
| 1개월권 50k + 7일 이내 열람 (수수료만) | 2 | 50,000 | 45,000 | `50k × 0.9 = 45,000` — 수수료만 케이스 (정책 해석 필요) |

**결론**:
- 1개월권 패턴 = 정책 공식과 **정확히 일치** (단, 소수 정밀도 `0.333` vs `1/3` 로 ±15원 오차 있음 → `Fraction(1,3)` 사용)
- 6개월권 실답변은 매니저 재량 경로 (우리 agent는 정책 본문대로 438k 답하고 CS팀이 필요 시 수정)
- `50k → 45k` 케이스는 수수료만 차감하는 별도 규칙 존재 가능성 — 추후 확인 필요

### 현재 코드와의 gap (2026-04-05 Option A 발견)

| # | 위치 | 현재 | 정책에 맞게 수정 |
|---|---|---|---|
| **B1** | `src/workflow.py:267` | `payment_cycle_days=30` 하드코딩 | `payment_cycle_days = cycle_months × 30` |
| **B2** | `src/workflow.py:244` | `monthly_price = tx_amount / cycle_months` | 2개월 이상: product_group API로 ONE_MONTH 옵션 price 조회 |
| **B3** | `config/refund_rules.json` EP-002 | `deduct_fraction: 0.333` (소수) | `1/3` (Fraction 정확도) |
| **B4** | `config/refund_rules.json` EP-004 | 1/2 경과 후 → `method: none` (환불 불가) | 1/2 경과 후도 1/2 차감 규칙 적용 (EP-004 삭제 or max 제거) |
| **B5** | `config/refund_rules.json` EP-005 | `months_deduct` 공식 존재 | 정책 본문에 없음 — 삭제 또는 비활성화 |
| **B6** | `src/admin_api.py` | `get_product_group()` 없음 | 추가 필요: `GET /v1/product/group/{productPageId}` |
| **B7** | `data/test_cases/refund_test_cases_enriched.json` | `conversation_time=""` 전부 누락 | 평가셋에서 이 소스 대신 수동 mock (`data/mock_scenarios/golden/v2/`) 사용 |

### Phase 5-B 평가셋 스키마 (확정)

`data/mock_scenarios/golden/v2/*.json`:
```json
{
  "scenario": "설명",
  "source_chat_id": "역산 소스 (옵션)",
  "conversation_time": "YYYY-MM-DDTHH:MM:SSZ",
  "user_messages": [...],
  "admin_data": {
    "products": [{..., "groupCode": "xxx"}],
    "transactions": [...],
    "usage": {"accessed": true/false},
    "product_group": [  // ← 신규: product_group API 응답 mock
      {"paymentPeriod": "ONE_MONTH", "price": 100000},
      {"paymentPeriod": "SIX_MONTH", "price": 500000}
    ]
  },
  "expected": {
    "template_id": "T2_환불_규정_금액",
    "refund_amount_policy": 438000,       // 정책 공식 적용 결과
    "refund_amount_manager": 360000,       // 실제 매니저 답 (참고용)
    "manager_discretion_note": "6개월권 재량 처리 사례"
  }
}
```

---

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

## 현재 상태 (2026-04-02)

- [x] 정책 확정 (케이스 맵 + 비교 자료)
- [x] 워크플로우 구현 (T2 전액/부분 + RefundEngine 연동)
- [x] 유닛 테스트 15/15, e2e 189/189 (100%)
- [x] admin API 토큰 자동 갱신 (refresh token)
- [x] mock 데이터 기반 데모 UI (`app_refund_v2.py`)

## 다음 단계

### Next 1: mock 데이터 실제 API 응답 검증
- 실제 API 응답 형식/값과 mock이 일치하는지 검증
- enriched 데이터(343건)에서 각 분기에 해당하는 실제 응답을 뽑아 mock 교체
- 검증 방법: 토큰 넣고 실제 유저 조회 → mock과 구조 비교

### Next 2: 다양한 시나리오 테스트
- 현재 8개 mock 외에 실제 데이터에서 발견되는 엣지 케이스
  - 복수 구독 (상품 2개+)
  - 프로모션 가격 적용 유저
  - 에셋 상품 (제외 처리 확인)
  - 결제 금액이 0원인 케이스
  - 결제 방법이 계좌이체(VA)인 경우

### Next 3: 멀티턴 처리
- 환불/해지 문의가 여러 턴에 걸쳐 오는 케이스 분석
  - BQ에서 매니저 2턴+ 대화 패턴 추출
  - 턴별 트리거 시점: 유저가 추가 메시지 보낼 때? 매니저가 응답 후?
  - 이전 턴 T2 → 유저 동의 → T3 (현재 구현됨, 실제 데이터로 검증 필요)
- 트리거 방식:
  - 명령어 기반 (현재): 상담사가 명령어 입력 시 실행
  - 자동 감지 (미래): 새 메시지 웹훅 수신 시 자동 판단

### Next 4: 모호한 문의 대응
- 옵시디언 `채널톡 모호한 문의 패턴 분석` 기반
- 5가지 모호 패턴: 환불/해지 정보부족, 결제 맥락불명, 기능 추상적, 맥락없음, CS범위밖
- 매니저 응대 패턴 (A~D): 본인확인, 상품특정, 증상구체화, 오픈질문
- `src/ambiguous_classifier.py` (초기 구현 있음, 고도화 필요)
