# 정배열 이평선 돌파매매1 (AMB01)

## 쉽게 말하면

이동평균이 정배열인 종목이 그 선을 위로 돌파하면 삽니다. 손절·익절 문장은 rules.stop·takeProfit 에 있습니다. 예시 가격은 스펙에 금액 예시가 없어 불명확합니다. 오르막이 정렬된 종목에 맞습니다.

- 전략ID: `alignMaBreak_v1`
- 버전: 1
- 상태: test
- 명세 enabled: True
- 주기: 1

## 목적

status=test. Live auto-trade OFF. Validate via val/bt.

## 대상 종목

```
{
  "type": "rankWatch",
  "requireBull": true,
  "note": "실시간순위/AI_WATCH_STK 기반 + 양봉 필터(종목선정). 단일 bt는 종목 지정."
}
```

## 진입

- entry: after buyAfterTm, touch maFast (low<=ma), bull break close>maFast
- trend: maFast > maMid > maSlow

## 청산·손절

- stop: after exitAfterTm, bearish break below exitStopMa, leave leaveQty
- takeProfit: after exitAfterTm, profit and bearish break below maFast, leave leaveQty

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
  "useBullFilter": true
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
  "type": "rankWatch",
  "requireBull": true,
  "note": "실시간순위/AI_WATCH_STK 기반 + 양봉 필터(종목선정). 단일 bt는 종목 지정."
}
```

## 버전 이력

- 2026-08-06 v1 v1 명세·스펙·구현·BT/val 연동

보유를 중단 버튼으로 자동 매도하지 않는다.
