# 관리자센터 API 인터페이스

> 소스: apidog-admin OAS + `apps/us-admin/src/entities/user/api/`, `apps/us-admin/src/entities/payment/api/`
> Base URL: `https://dev-api-admin.us-insight.com`

## search_user — `GET /v3/users`

유저 검색. 연락처/닉네임으로 계정 ID를 찾는 첫 단계.

**Request**
```typescript
interface UsersSearchQueryParams {
  offset: number;
  limit: number;
  phoneNumber?: string;
  nickName?: string;
  deleted?: boolean;
  startAt?: Date | string;
  endAt?: Date | string;
  countryCode?: string;
  isShadowBanned?: boolean;
}
```

**Response**
```typescript
interface UsersResponse {
  users: {
    id: string;
    subId: string;
    phoneNumber: string;
    nickName: string;
    signUpMethod: 'direct' | 'kakao' | 'naver' | 'google' | 'apple';
    profileImage: string;
    isShadowBanned: boolean;
    lastAccessedAt: string;
    deleted: boolean;
    createdAt: string;
    countryCode: string;
  }[];
}
```

## get_profile — `GET /v1/users/{id}`

계정 상세 프로필.

**Response**
```typescript
interface UserInfoResponse {
  profile: {
    id: string;
    countryCode: number;
    phoneNumber: string;
    nickName: string;
    signUpState: 'ACTIVE' | 'DORMANT' | 'SUSPENDED' | 'SUBSCRIBE';
    signUpMethod: 'direct' | 'kakao' | 'naver' | 'google' | 'apple';
    signPath: 'APP' | 'WEB' | 'UNKNOWN';
    contentView: number;
    role: 'all' | 'master' | 'tester' | 'admin' | 'user' | null;
    lastAccessedAt: string;
    createdAt: string;
    profileImageURL: string;
    deletedAt?: string;
  };
  memo: {
    totalCount: number;
    list: { id: string; author: string; content: string; createdAt: string; updatedAt: string }[];
  };
  masters: {
    totalCount: number;
    followedList: { profileImageURL: string; masterName: string }[];
  };
  blockedUsers: {
    id: string; nickname: string; blockedNickname: string; blockedAt: string;
  }[];
}
```

## get_subscriptions — `GET /users/{id}/my-products`

유저 보유 구독 상품 목록.

**Request**: `?status=active&offsetId=&limit=`

## get_following_masters — `GET /users/{id}/masters/metadata`

유저가 팔로우 중인 마스터 목록.

**Response**
```typescript
interface UserFollowingMastersResponse {
  masters: {
    cmsId: string;
    hasMembership: boolean;
    id: string;
    isFollowed: boolean;
    name: string;
  }[];
}
```

## get_refund_products — `GET /cs/refund-user/{userId}/products`

환불 대상 멤버십 상품 + 거래 내역.

**Response**
```typescript
type RefundProductsResponse = MyProduct[];

interface MyProduct {
  _id: string;
  owner: string;
  type: string;
  status: 'active' | 'inactive';
  product: { _id: string; name: string };
  transactions: {
    _id: string;
    provider: string;
    state: string;
    token: string;
    method: string;        // 'card' | 'CA' | 'VA' 등
    methodInfo: string;
    amount: string;
    createdAt: string;
    updatedAt: string;
    data: {
      cardNm: string;      // 카드사명
      cardNo: string;      // 카드번호 (마스킹)
      trdAmt: string;      // 거래금액
      trdDtm: string;      // 거래일시
      trdNo: string;       // 거래번호
      instmtMon: string;   // 할부개월
    };
  }[];
  currentTransactionToken: string;
  createdAt: string;
  updatedAt: string;
  expiredAt: string;
}
```

## get_membership_history — `GET /v1/users/{id}/membership-history`

멤버십 이용 이력. 콘텐츠 열람 여부 판단용.

**Response**
```typescript
interface UserFindMembershipHistoryResponse {
  memberships: {
    productName: string;
    paymentCycle: number;
    expiration: boolean;
    memberShipType: 'card' | 'CA' | 'VA' | 'corp' | 'PZ' | 'unknown';
    transactionHistories: {
      createdAt: string;
      state: string;
      purchasedAmount?: string;
      purchasedMethod?: string;
      cardNumber?: string;
      expiredAt?: string;
      changedDisplayName?: string;
      method: string;
      easyPayCode?: 'KKP' | 'NVP' | 'SSP';
    }[];
  }[];
}
```

## get_refund_history — `GET /v1/users/{id}/membership-refund-history`

기존 환불 이력 확인.

**Request**: `?offset=0&limit=10`

**Response**
```typescript
interface UserFindMembershipRefundHistoryResponse {
  refunds: {
    productName: string;
    createdAt: string;
    paymentHistory: {
      amount: number;
      cardType: string;
      cardNo: string;
      key: string;
      createdAt: string;
    };
    refundHistory: {
      refundAmount: number;
      refundAt: string;
    };
  }[];
}
```
