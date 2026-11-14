# 실계좌관리 (RAM01)

- 전략ID: `realAcntMgr_v1`
- 버전: 1
- 상태: live
- 명세 enabled: True
- 주기: intraday

## 목적

자동 진입 신호는 없다. 매매 등록된 보유만 이후 규칙의 주문 대상이다.

## 대상 종목

```
{
  "source": "kt00004",
  "note": "잔량>0 은 관심(WATCH). 매매 버튼 종목만 TRADE."
}
```

## 진입

- watch: 계좌 보유는 관심종목에만 두고 주문하지 않는다

## 청산·손절

불명확. 스펙 rules 에 해당 키가 없다.

## 수량·금액

```
{
  "paperOnly": false,
  "autoEntry": false,
  "tradeNote": "TRADE",
  "watchNote": "WATCH"
}
```

## 시간

불명확. 시간 키가 params 에 없다.

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "source": "kt00004",
  "note": "잔량>0 은 관심(WATCH). 매매 버튼 종목만 TRADE."
}
```

## 버전 이력

문서·DB 이력이 없다.

보유를 중단 버튼으로 자동 매도하지 않는다.
