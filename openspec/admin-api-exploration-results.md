# 관리자센터 API 탐색 결과

> 탐색일: 2026-03-31

---

## 페이지별 실제 API 정리

### 1. 회원 상세 (/users/{id}) — 기본정보, 환불이력

| Endpoint | 용도 |
|----------|------|
| `GET /v1/users/{id}` | 프로필, 가입방법, 마스터, 메모 등 기본정보 |
| `GET /v1/users/{id}/membership-history` | 멤버십 구독 이력 |
| `GET /v1/users/{id}/membership-refund-history` | 멤버십 환불 이력 (환불 탭) |
| `GET /v1/users/{id}/shop-refund-history` | 샵 환불 이력 (환불 탭) |
| `GET /users/{id}/contents` | 콘텐츠 열람 이력 (모달) |

### 2. CS 상담 (/cs/support) — 보유 상품

| Endpoint | 용도 |
|----------|------|
| `GET /v3/users` | 전화번호/닉네임으로 유저 검색 |
| `GET /users/{id}/my-products` | 보유중인 상품 목록 |
| `GET /users/{id}/masters/metadata` | 팔로잉 마스터 목록 |
| `PATCH /my-products/{id}/subscribe-cancel` | 구독 취소 |
| `PATCH /my-products/{id}/subscribe-cancel-withdraw` | 구독 취소 철회 |

### 3. CS 환불 (/cs/refund) — 결제 내역 & 환불

| Endpoint | 용도 |
|----------|------|
| `GET /v2/users` | 전화번호로 유저 검색 |
| `GET /cs/refund-user/{userId}/products` | 멤버십 상품 (결제 정보 포함) |
| `GET /cs/refund-user/{userId}/shop-product` | 샵 상품 결제 내역 |
| `GET /cs/refund-user/{userId}/gift-product` | 선물 상품 결제 내역 |
| `GET /cs/refund-user/ordererPhoneNumber/{phone}/shop-product` | 비회원 결제 내역 |
| `GET /my-products/{myProductId}/transaction-round-history` | 빌링 사이클 이력 |
| `GET /v1/refund/membership/{paymentId}` | 환불 이력 상세 |
| `POST /v1/refund/membership` | 멤버십 환불 실행 |
| `POST /v1/refund/shop-product` | 샵 환불 실행 |
| `POST /v1/refund/gift-product` | 선물 환불 실행 |

---

## CS AI 에이전트가 호출해야 할 핵심 API (조회만)

| # | Endpoint | 용도 | tools.py 매핑 |
|---|----------|------|---------------|
| 1 | `GET /v3/users?phoneNumber=xxx` | 유저 찾기 | `lookup_account` |
| 2 | `GET /v1/users/{id}` | 기본정보 (이름, 연락처, 가입방법) | `lookup_account` |
| 3 | `GET /users/{id}/my-products` | 보유 상품 (구독 상태, 등급) | `lookup_subscription` |
| 4 | `GET /cs/refund-user/{userId}/products` | 결제 정보 (금액, 카드, 주기) | `lookup_payment` |
| 5 | `GET /cs/refund-user/{userId}/shop-product` | 샵 결제 내역 | `lookup_payment` |
| 6 | `GET /users/{id}/contents` | 콘텐츠 열람 여부 (환불 규정 판단) | `lookup_usage` |
| 7 | `GET /v1/users/{id}/membership-history` | 구독 이력 | `lookup_subscription` |

### 유저 식별 흐름

```
전화번호 → GET /v3/users?phoneNumber=xxx → userId 획득
  → userId로 나머지 API 호출
```

### 환불 계산에 필요한 API 조합

```
1. GET /v3/users?phoneNumber=xxx          → userId
2. GET /cs/refund-user/{userId}/products  → 결제금액, 결제일, 결제주기, 상품정가
3. GET /users/{id}/contents               → 열람 여부, 열람 일시
4. 환불 규정 테이블 적용                   → 환불 가능 여부 + 금액 산출
5. 답변 템플릿에 {환불금액} 채움           → 안내 답변 생성
```

---

## 추가 확인 필요

- 채널톡 memberId ↔ 관리자센터 userId 매칭 방법 (채널톡 초기화 시 userId 주입 여부)
- API 인증 방식 (Bearer token? API key? 세션?)
- Rate limit
- 어스캠퍼스 API가 별도 base URL인지 동일한지
