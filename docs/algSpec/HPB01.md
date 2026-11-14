# 신고가 눌림목매매1 (HPB01)

- 전략ID: `highPullback_v1`
- 버전: 1
- 상태: test
- 명세 enabled: True
- 주기: 1

## 목적

status=test. Live OFF. Validate via val/bt/ntl first.

## 대상 종목

```
{
  "type": "first52wHigh",
  "markets": [
    "KOSPI",
    "KOSDAQ"
  ],
  "requireFirst52w": true,
  "minFluRt": 10,
  "volAboveAvg": true,
  "requireFrgnOrOrgnBuy": true,
  "note": "val 2 watch 시 ntl first로 유니버스 선별 후 BT (관심종목 미사용)."
}
```

## 진입

- entry: after setup and buyAfterTm, touch maFast (low<=ema)

## 청산·손절

- stop: after exitAfterTm, bearish break exitStopMa, leave leaveQty
- takeProfit: after exitAfterTm, profit and bearish break maFast, leave leaveQty

## 수량·금액

```
{
  "maType": "ema",
  "maFast": 20,
  "maMid": 50,
  "maSlow": 100,
  "exitStopMa": 60,
  "buyAfterTm": "151500",
  "exitAfterTm": "151800",
  "buyAmtWon": 10000000,
  "leaveQty": 1,
  "requireCross": true,
  "maxEntriesPerDay": 1,
  "useBullFilter": false,
  "setupMinFluRt": 10,
  "lookbackHigh": 250,
  "volMaPeriod": 20,
  "volMult": 1.5,
  "universePrefiltered": true,
  "monitorDays": 20
}
```

## 시간

```
{
  "buyAfterTm": "151500",
  "exitAfterTm": "151800"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "first52wHigh",
  "markets": [
    "KOSPI",
    "KOSDAQ"
  ],
  "requireFirst52w": true,
  "minFluRt": 10,
  "volAboveAvg": true,
  "requireFrgnOrOrgnBuy": true,
  "note": "val 2 watch 시 ntl first로 유니버스 선별 후 BT (관심종목 미사용)."
}
```

## 버전 이력

- 2026-08-08 v1 v1 명세·스펙·구현·BT/val 연동

보유를 중단 버튼으로 자동 매도하지 않는다.
