# us-admin 상담사 자동조회 에이전트 — API 맵

> **목적**: 상담사 업무를 자동화하는 AI 에이전트가 직접 호출할 GET API 레퍼런스.
> **원칙**: UI 렌더링 관련 트리거/모달/enabled 플래그는 무시. 에이전트는 axios로 직접 호출.
> **범위**: GET 요청만. 환불/구독/멤버십 관련 조회 시나리오.

---

## 📌 빠른 인덱스

| 시나리오                      | 엔드포인트                                                                                                                              |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| userId만으로 종합 조회 (권장) | `/v1/users/{id}` + `/v1/users/{id}/membership-history` + `/v1/users/{id}/membership-refund-history`                                     |
| 휴대폰 → userId               | `GET /v3/users?phoneNumber=...`                                                                                                         |
| 환불 액션용 drill-down        | `/v2/users` → `/cs/refund-user/{userId}/products` → `/my-products/{id}/transaction-round-history` → `/v1/refund/membership/{paymentId}` |
| 가상계좌 조회                 | `/cs/transactions/virtual-accounts`                                                                                                     |
| 환불 정책 (금액 계산)         | S3 `policy/{STAGE}/policy.json`                                                                                                         |
| 보유상품 조회                 | `/users/{id}/my-products?status=active`                                                                                                 |
| 읽은 콘텐츠                   | `/users/{id}/contents`                                                                                                                  |
| 상품 정보 조회                | `/v1/masters/product-group`, `/v1/product-group/{id}`, `/v1/product/group/{id}`                                                         |

---

## 1. 회원 검색/식별

### `GET /v3/users`

**파라미터**: `phoneNumber`, `nickName`, `startAt`, `endAt`, `deleted`, `offset`, `limit`
**출처**: `apps/us-admin/src/entities/user/api/queries/useGetUsers.ts`

**응답 필드**:
| 필드 | 타입 | 의미 |
|---|---|---|
| `users[].id` | string | userId (다른 API의 key) |
| `users[].nickName` | string | 별칭 |
| `users[].phoneNumber` | string | 휴대폰번호 |
| `users[].countryCode` | string | 국가코드 |
| `users[].subId` | string | 보조 ID |
| `users[].createdAt` | ISO string | 가입일 |
| `users[].lastAccessedAt` | ISO string | 최종접속일 |
| `users[].deleted` | boolean | true=탈퇴, false=활성 |
| `users[].signUpMethod` | enum | `direct` / `kakao` / `naver` / `google` / `apple` |
| `users[].isAgreementOnCollection` | boolean | 정보수집 동의 여부 |

### `GET /v2/users` (CSRefund 페이지용)

같은 용도지만 다른 버전. 휴대폰번호로 조회 결과 반환. 필드는 `/v3/users`와 유사.

---

## 2. 회원 상세 조회 (권장 3종 세트)

### ⭐ `GET /v1/users/{id}` — 프로필

**출처**: `apps/us-admin/src/entities/user/api/queries/useGetUserInfo.ts`

| 필드                              | 타입       | 의미                                              |
| --------------------------------- | ---------- | ------------------------------------------------- |
| `profile.createdAt`               | ISO string | 가입일                                            |
| `profile.lastAccessedAt`          | ISO string | 최종접속일                                        |
| `profile.isAgreementOnCollection` | boolean    | 정보수집 동의                                     |
| `profile.signPath`                | enum       | `APP` / `WEB` / `UNKNOWN` (가입 플랫폼)           |
| `profile.signUpMethod`            | enum       | `direct` / `kakao` / `naver` / `google` / `apple` |
| `profile.role`                    | enum       | `user` / `master` / `admin` / `tester` / `all`    |
| `profile.contentView`             | number     | 콘텐츠 뷰 수                                      |
| `profile.nickName`                | string     | 별칭                                              |
| `profile.phoneNumber`             | string     | 휴대폰                                            |
| `memo.totalCount`, `memo.list[]`  | -          | 상담 메모                                         |
| `masters.followedList[]`          | array      | 팔로우한 마스터                                   |
| `blockedUsers[]`                  | array      | 차단한 사용자                                     |

### ⭐⭐ `GET /v1/users/{id}/membership-history` — 멤버십 구매 이력

**출처**: `apps/us-admin/src/entities/user/ui/MembershipHistoryAccordion/index.tsx`

| 필드                                   | 타입    | 의미                                                                                |
| -------------------------------------- | ------- | ----------------------------------------------------------------------------------- |
| `memberships[].productName`            | string  | 상품명                                                                              |
| `memberships[].paymentCycle`           | number  | **결제 회차** (1, 2, 3, ...). 회차 카운트지 개월 수 아님 ⚠️ 네이밍 함정 — 아래 참조 |
| `memberships[].expiration`             | boolean | 만료 여부                                                                           |
| `memberships[].memberShipType`         | enum    | `subscription` / `onetimepurchase` (단건이면 paymentCycle 무시하고 "단건"으로 표시) |
| `memberships[].transactionHistories[]` | array   | 아래 ⬇️                                                                             |

#### ⚠️ `paymentCycle` 백엔드 네이밍 함정 (2026-04-05 확정)

**필드명만 보면 "주기(개월)"처럼 읽히지만 실제로는 결제 회차 카운트**. 확인 경로:

- 컴포넌트: `apps/us-admin/src/entities/user/ui/MembershipHistoryAccordion/index.tsx:37`
  - UI 라벨 하드코딩: `결제회차: {getPaymentCycleLabel(membership)}`
- 포매터: 같은 파일 L10-22

```ts
const getPaymentCycleLabel = (membership: UserMembership) => {
  const cycle = membership.paymentCycle;
  const type = membership.memberShipType;

  if (type === PAYMENT_METHOD_TYPE.ONETIMEPURCHASE) {
    return '단건';
  }
  return cycle;   // ← 단위 없음. 숫자 그대로 반환.
};
```

**실제 화면 출력**:
- `미라클모닝 3개월 / 결제회차: 2` ← 상품명에 "3개월", paymentCycle 값은 "2"
- `선물박스 / 결제회차: 단건` ← onetimepurchase

**혼동 원인**:
- 필드명 `paymentCycle` → "주기(cycle)" 연상
- 별도 enum `PaymentCycleType` (`ONE_MONTH` / `SIX_MONTH` / ...)은 **진짜 주기**인데, 이건 `ProductListData.paymentPeriod`(상품 옵션 상세)에만 쓰이고 `UserMembership`과는 무관
- 즉 백엔드에 **이름만 비슷한 필드 2개**가 있고 의미가 다름

**에이전트가 "주기(개월)"를 알고 싶을 때**:
- `membership-history.paymentCycle`에서 얻을 수 없음
- 옵션 1: `ProductListData.paymentPeriod` (상품 상세 API, productPageId join 경로는 불명확)
- 옵션 2: `productName` 문자열 파싱("6개월", "1년" 등) ← 현재 에이전트가 사용 중

**transactionHistories[] 필드**:
| 필드 | 타입 | 의미 |
|---|---|---|
| `state` | enum | TransactionState (아래 enum 표 참조) |
| `purchasedAmount` | string/number | 결제금액 |
| `method` | enum | `CA` (신용카드) / `PZ` (간편결제) |
| `easyPayCode` | enum | `KKP` (카카오페이) / `NVP` (네이버페이) / `SSP` (삼성페이) — `method === 'PZ'`일 때만 |
| `cardNumber` | string | 카드번호 (마스킹되지 않은 raw) |
| `createdAt` | ISO string | 결제 시간 |
| `changedDisplayName` | string | 변경된 상품명 (state=SUBSCRIPTION_CHANGED 시) |
| `expiredAt` | ISO string | 구독 해지 예정일 (state=SUBSCRIPTION_CANCELLED 시) |

### ⭐⭐ `GET /v1/users/{id}/membership-refund-history` — 멤버십 환불 이력

**파라미터**: `offset`, `limit` (무한 스크롤)
**출처**: `apps/us-admin/src/widgets/user/ui/RefundTab/columns.tsx:5-69`

| 필드                         | 타입       | 의미           |
| ---------------------------- | ---------- | -------------- |
| `productName`                | string     | 상품명         |
| `createdAt`                  | ISO string | 환불 요청일    |
| `paymentHistory.amount`      | number     | 원 결제 금액   |
| `paymentHistory.cardType`    | string     | 카드 종류      |
| `paymentHistory.cardNo`      | string     | 카드번호       |
| `paymentHistory.createdAt`   | ISO string | 원 결제 시간   |
| `paymentHistory.key`         | string     | 결제 키        |
| `refundHistory.refundAmount` | number     | 환불 금액      |
| `refundHistory.refundAt`     | ISO string | 환불 처리 시간 |

### `GET /v1/users/{id}/shop-refund-history` — 샵 환불 이력

**파라미터**: `offset`, `limit`
같은 구조에 `optionName`, `orderQuantity` 추가.

### `GET /users/{id}/contents` — 읽은 콘텐츠

**응답**:

```ts
{
  pick:   { title: string; masterName: string; createdAt: string }[],
  secret: { title: string; masterName: string; createdAt: string }[]
}
```

- `pick` = 일반 콘텐츠, `secret` = 시크릿 콘텐츠

---

## 3. 보유 상품 조회

### `GET /users/{id}/my-products?status=active`

**파라미터**: `id`, `offsetId`, `limit`, `status`
**출처**: `apps/us-admin/src/entities/user/api/queries/useGetUserProducts.ts`

| 필드                                 | 타입       | 의미                                                 |
| ------------------------------------ | ---------- | ---------------------------------------------------- |
| `myProducts[].id`                    | string     | myProductId                                          |
| `myProducts[].product.masterName`    | string     | 마스터명                                             |
| `myProducts[].product.id`            | string     | 상품 ID                                              |
| `myProducts[].product.groupCode`     | number     | 페이지 코드                                          |
| `myProducts[].product.name`          | string     | 상품명                                               |
| `myProducts[].type`                  | enum       | `SUBSCRIPTION` / `ONE_TIME_PURCHASE` / `INTEGRATION` |
| `myProducts[].price`                 | number     | 금액                                                 |
| `myProducts[].purchasedSuccessCount` | number     | 성공 결제 횟수                                       |
| `myProducts[].activatedAt`           | ISO string | 사용 시작일                                          |
| `myProducts[].expiredAt`             | ISO string | 사용 종료일                                          |
| `myProducts[].lastTransactionState`  | enum       | `PURCHASED_SUCCESS` 등 TransactionState              |
| `myProducts[].usagePeriodType`       | enum       | `DURATION` / `DATE_RANGE`                            |

---

## 4. 환불 액션용 Drill-down (CSRefund 페이지)

### `GET /cs/refund-user/{userId}/products` — 멤버십 목록

**출처**: `apps/us-admin/src/entities/payment/api/queries/useGetUserProductByUserId.ts`

| 필드                        | 타입       | 의미                                        |
| --------------------------- | ---------- | ------------------------------------------- |
| `myProducts[]._id`          | string     | 다음 단계 `transaction-round-history`의 key |
| `myProducts[].product.name` | string     | 상품명                                      |
| `myProducts[].expiredAt`    | ISO string | 만료일                                      |

### ⭐ `GET /my-products/{myProductId}/transaction-round-history` — 결제 회차별 내역

**출처**: `apps/us-admin/src/entities/payment/ui/MembershipPaymentHistoryList/index.tsx`

| 필드                | 타입       | 의미                                                                  |
| ------------------- | ---------- | --------------------------------------------------------------------- |
| `paymentId`         | string     | 환불 API용 key                                                        |
| `paymentRound`      | number     | 결제 회차                                                             |
| `status`            | enum       | `PAYMENT_COMPLETED` / `PARTIAL_REFUND_COMPLETED` / `REFUND_COMPLETED` |
| `paymentMethod`     | enum       | `CA` (신용카드) / `VA` (가상계좌) / `PZ` (간편결제)                   |
| `cardNumber`        | string     | 카드번호 (raw)                                                        |
| `paymentAmount`     | number     | 결제금액                                                              |
| `refundAmount`      | number     | 환불금액                                                              |
| `refundRound`       | number     | 환불 회차                                                             |
| `lastTransactionAt` | ISO string | 내역 생성일                                                           |

**환불 가능 판단**: `status !== 'REFUND_COMPLETED'`

### `GET /v1/refund/membership/{paymentId}` — 환불 상세 내역

| 필드        | 타입       | 의미   |
| ----------- | ---------- | ------ |
| `amount`    | number     | 금액   |
| `round`     | number     | 회차   |
| `createdAt` | ISO string | 생성일 |

---

## 5. 가상계좌

### `GET /cs/transactions/virtual-accounts`

**파라미터**: `offset`, `limit`, `phoneNumber`, `state` (`ALL` / `completed` / `refund` / `waiting` / `expired`)

| 필드                                | 타입       | 의미                                                                                 |
| ----------------------------------- | ---------- | ------------------------------------------------------------------------------------ |
| `transactions[].token`              | string     | 상세 조회 key                                                                        |
| `transactions[].userPhoneNumber`    | string     | 휴대폰                                                                               |
| `transactions[].userNickName`       | string     | 닉네임                                                                               |
| `transactions[].vBankName`          | string     | 은행명                                                                               |
| `transactions[].vBankAccountNumber` | string     | 계좌번호                                                                             |
| `transactions[].amount`             | number     | 금액                                                                                 |
| `transactions[].state`              | enum       | `purchased_success` / `waiting_for_deposit` / `purchased_refund` / `deposit_expired` |
| `transactions[].createdAt`          | ISO string | 완료일                                                                               |
| `transactions[].expiredAt`          | ISO string | 만료일 (state=deposit_expired일 때 사용)                                             |

### `GET /cs/transactions/virtual-accounts/{token}` — 상세

동일 필드 + `transactions[]` 히스토리 (state 변화 내역).

---

## 6. 환불 정책 (금액 계산용)

### `GET https://{bucketName}.s3.ap-northeast-2.amazonaws.com/policy/{STAGE}/policy.json`

**출처**: `apps/us-admin/src/shared/model/hooks/usePolicy.ts`
**참고**: 일반 API 아님. S3 직접 fetch. STAGE는 `dev` / `prod` 등.

**타입**:

```ts
{
  policies: { name: string; description: string[] }[],
  lastUpdatedBy?: string,
  updatedAt?: string
}
```

에이전트가 환불 금액 계산 시 이 JSON 파싱해서 정책 매칭 로직 구현 필요.

---

## 7. 상품 정보

### `GET /v1/masters/product-group` — 마스터 목록

| 필드                                         | 의미                               |
| -------------------------------------------- | ---------------------------------- |
| `masterId`, `masterName`                     | 마스터 식별/이름                   |
| `publicType`                                 | `PENDING` / `PUBLIC` / `PRIVATE`   |
| `productGroupType`                           | `US_PLUS` / `US_CAMPUS`            |
| `productGroupViewStatus`                     | `ACTIVE` / `INACTIVE` / `EXCLUDED` |
| `productGroupWebLink`, `productGroupAppLink` | 링크                               |

### `GET /v1/product-group/{productPageId}` — 상품 페이지 상세

**응답 타입**: `ProductPageData` (`apps/us-admin/src/entities/product/model/types/index.ts:322-343`)

| 필드                              | 의미                                 |
| --------------------------------- | ------------------------------------ |
| `title`                           | 페이지명                             |
| `status`                          | `ACTIVE` / `INACTIVE`                |
| `type`                            | `SUBSCRIPTION` / `ONE_TIME_PURCHASE` |
| `startAt`, `endAt`                | 판매 기간                            |
| `applyStartAt`, `applyEndAt`      | 신청 기간                            |
| `isAlwaysPublic`, `isAlwaysApply` | 항상 공개/신청 가능                  |
| `discountRate`                    | 할인율                               |
| `contents[]`                      | 이미지 목록                          |
| `mainContents[]`                  | 메인 배너                            |
| `notices[]`                       | 유의사항 (string 배열)               |

### `GET /v1/product/group/{productPageId}` — 상품 옵션 목록

**응답 타입**: `ProductListData[]`

| 필드                                    | 의미                                                                                |
| --------------------------------------- | ----------------------------------------------------------------------------------- |
| `productId`, `name`, `price`            | 상품 기본                                                                           |
| `type`                                  | `SUBSCRIPTION` / `ONE_TIME_PURCHASE`                                                |
| `paymentPeriod`                         | `ONE_MONTH` / `TWO_MONTH` / `THREE_MONTH` / `FOUR_MONTH` / `SIX_MONTH` / `ONE_YEAR` |
| `defaultPaymentCount`                   | 기본 결제 횟수                                                                      |
| `useStartAt`, `useEndAt`, `useDuration` | 사용 기간                                                                           |
| `usagePeriodType`                       | `DURATION` / `DATE_RANGE`                                                           |
| `isDisplay`                             | 공개 여부                                                                           |
| `viewSequence`                          | 노출 순서                                                                           |

---

## 🗂️ Enum 매핑 마스터 표

### TransactionState (`apps/us-admin/src/entities/product/model/types/index.ts:126-141`)

```
purchased_success            → 결제성공
purchased_fail               → 결제실패
purchased_refund             → 환불완료
subscription_expired         → 구독만료
subscription_cancelled       → 구독해지 신청
subscription_changed         → 상품변경
subscription_renewed         → 구독 갱신
subscription_cancel_withdraw → 해지 철회
waiting_for_deposit          → 입금대기
paymentmethod_change         → 결제수단 변경
hecto_network_fail           → 헥토 네트워크 실패
virtual_account_refund_fail  → 가상계좌 환불 실패
card_cancel_fail             → 카드 취소 실패
unknown                      → 알 수 없음
```

### TRANSACTION_ROUND_HISTORY_STATUS

```
PAYMENT_COMPLETED          → 결제완료
PARTIAL_REFUND_COMPLETED   → 부분환불
REFUND_COMPLETED           → 환불완료
```

### PaymentMethod (카드/간편결제 구분)

```
CA → 신용카드
VA → 가상계좌
PZ → 간편결제 (easyPayCode로 세부 구분)
```

### EasyPaymentCode (method=PZ일 때만)

```
KKP → 카카오페이
NVP → 네이버페이
SSP → 삼성페이
```

### SignUpMethod

```
direct → 휴대폰 번호
kakao  → Kakao
naver  → Naver
google → Google
apple  → Apple
```

### SignPath (가입 플랫폼)

```
APP / WEB / UNKNOWN
```

### Role (권한)

```
user / master / admin / tester / all
```

### VirtualAccount State

```
purchased_success   → 입금 완료
waiting_for_deposit → 입금 대기
purchased_refund    → 환불 완료
deposit_expired     → 입금 만료
```

### ProductType

```
SUBSCRIPTION      → 구독
ONE_TIME_PURCHASE → 단건
INTEGRATION       → 연동
```

### PRODUCT_PUBLISH_STATUS

```
ACTIVE   → 공개
INACTIVE → 비공개
```

### PublicType (마스터 상품)

```
PENDING → 준비중
PUBLIC  → 공개
PRIVATE → 비공개
```

### PaymentCycle (상품 옵션)

```
ONE_MONTH   → 1개월
TWO_MONTH   → 2개월
THREE_MONTH → 3개월
FOUR_MONTH  → 4개월
SIX_MONTH   → 6개월
ONE_YEAR    → 1년
```

### UsagePeriodType

```
DURATION   → 일수 기반 (useDuration 사용)
DATE_RANGE → 날짜 범위 (useStartAt ~ useEndAt)
```

### MemberShipType

```
subscription    → 구독형
onetimepurchase → 단건 (이 경우 paymentCycle 대신 "단건"으로 표기)
```

---

## 🎯 에이전트 시나리오별 추천 호출 패턴

### 시나리오 A: "이 고객 상황 요약해줘" (휴대폰번호 주어짐)

```
1. GET /v3/users?phoneNumber=010xxxx → userId 추출
2. 병렬:
   - GET /v1/users/{userId}
   - GET /v1/users/{userId}/membership-history
   - GET /v1/users/{userId}/membership-refund-history
3. (필요시) GET /users/{userId}/my-products?status=active
```

### 시나리오 B: "환불 이력만 확인"

```
1. GET /v3/users?phoneNumber=... → userId
2. GET /v1/users/{userId}/membership-refund-history
```

### 시나리오 C: "환불 금액이 얼마인지 계산"

```
1. 고객 결제 건 식별 (시나리오 A)
2. GET S3 policy.json → 환불 정책 파싱
3. transaction-round-history의 paymentRound/paymentAmount와 정책 매칭
```

### 시나리오 D: "가상계좌 입금/환불 상태"

```
1. GET /cs/transactions/virtual-accounts?phoneNumber=...&state=ALL
2. (상세 필요시) GET /cs/transactions/virtual-accounts/{token}
```

### 시나리오 E: "상품 기본 정보가 필요"

```
1. GET /v1/masters/product-group (마스터 목록)
2. GET /v1/product-group/{productPageId} (상품 페이지 상세)
3. GET /v1/product/group/{productPageId} (상품 옵션들)
```

---

## ⚠️ 주의사항 (에이전트 구현 시)

1. **`paymentCycle`은 회차 카운트** (개월 수 아님). `memberShipType === 'onetimepurchase'`면 "단건"으로 치환. 증거: `MembershipHistoryAccordion/index.tsx:10-22,37` 포매터 원문 — §2 하위 "네이밍 함정" 섹션 참조.
2. **`method === 'PZ'`일 때만 `easyPayCode` 존재**. method=CA면 바로 "신용카드".
3. **회원상세의 멤버십 이력 vs CSRefund 플로우의 transaction-round-history**는 다른 데이터:
   - `membership-history`: 읽기 전용 요약 (빠른 조회)
   - `transaction-round-history`: 환불 액션에 필요한 paymentId 포함
4. **환불 정책은 S3 직접 fetch**. API 아님. STAGE 변수 주의.
5. **카드번호 마스킹은 UI 레이어 로직**. API는 raw cardNumber 반환. 에이전트는 표시할 때만 마스킹.
6. **무한스크롤 API (`offset`, `limit`)**: 환불 이력, my-products 등. 필요한 만큼만 페이지네이션.
