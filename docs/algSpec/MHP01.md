# 마하세븐 눌림목(정배열+프랙탈) (MHP01)

## 쉽게 말하면

정배열에서 눌린 뒤 프랙탈 매수 신호가 나오면 삽니다. 손절·익절은 rules.stop·takeProfit 입니다. 구체 원 단위 예시는 스펙에 없어 불명확합니다. 상승 추세의 짧은 눌림에 맞습니다.

- 전략ID: `mahaPullback_v1`
- 버전: 1
- 상태: draft
- 명세 enabled: True
- 주기: 1

## 목적

1분봉 원천 적재 후 상위 TF는 리샘플. status=draft — 엔진 미연동.

## 대상 종목

```
{
  "type": "list",
  "stkCds": [
    "005930"
  ]
}
```

## 진입

- entry: closeCrossAbove maFast
- trend: maFast > maMid > maSlow

## 청산·손절

- stop: priceBreakBelow stopMa
- takeProfit: entry + (entry - stopPrice) * rrMult

## 수량·금액

```
{
  "paperOnly": true,
  "maType": "ema",
  "maFast": 20,
  "maMid": 50,
  "maSlow": 100,
  "stopMa": 5,
  "fractalPeriod": 7,
  "fractalLeft": 2,
  "fractalRight": 2,
  "rrMult": 1.5,
  "stopUse": "close"
}
```

## 시간

불명확. 시간 키가 params 에 없다.

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "list",
  "stkCds": [
    "005930"
  ]
}
```

## 버전 이력

- 2026-08-02 v? 초안 (요약 전략 + EMA/1분봉/손익비 1.5 반영)

보유를 중단 버튼으로 자동 매도하지 않는다.
