# 눌림 ATR (ATR01)

## 쉽게 말하면

차트가 그리는 눌림이 20일선에서 나오면, 다음 봉 시가에 산다고 가정합니다. 손절은 진입가에서 ATR14의 2배 아래, 익절은 2배 위입니다. 보유는 10거래일을 넘기지 않습니다. 차트 오른쪽 글자는 ATR01 로 시작합니다. 1년 참고 성적은 비용 없이 잰 값이라, 수수료·세금·슬리피지를 넣고 다시 확인해야 합니다. 예를 들어 10,000원에 사고 ATR이 200원이면 손절은 9,600원, 익절은 10,400원입니다. 주문 코드는 아직 없습니다.

중지해도 이미 가진 종목의 손절·익절은 계속 보고, 새 매수는 하지 않습니다.

- 전략ID: `atrPullback_v1`
- 버전: 1
- 상태: unimplemented
- 명세 enabled: False
- 주기: day

## 목적

스펙과 등록만 있다. 실전·모의 주문 코드는 없다. 차트 눌림 ATR 선의 오른쪽 글자와 ATR선 체크 설명은 ATR01 로 시작한다. SMA20 눌림의 손절·익절은 2×ATR14, 보유는 10거래일이다. 1년 비용 없는 참고: 거래당 +2.79%, PF 1.52, 153건. 이 수치는 수수료·매도세·슬리피지 없이 잰 것이므로 v40 비용 모델로 다시 확인해야 한다.

## 대상 종목

```
{
  "type": "chart",
  "note": "차트에 눌림이 찍힌 일봉. 주문 유니버스는 아직 없다."
}
```

## 진입

- entry: chart 눌림 on SMA20: touch within 1.5%, at least 2 lower closes after the swing high, lower volume, bullish candle or lower wick >= body, retrace of the rise no more than 61.8%. Fill assumption is the next bar open.
- trend: close > SMA20 and SMA20 is higher than 5 bars ago; 20-bar high >= prior-leg low * 1.15

## 청산·손절

- stop: entry - 2 * ATR14
- exit: stop, target, or 10 trading days from the entry bar, whichever comes first
- takeProfit: entry + 2 * ATR14

## 수량·금액

```
{
  "paperOnly": true,
  "smaPeriod": 20,
  "atrPeriod": 14,
  "stopAtr": 2,
  "targetAtr": 2,
  "holdBars": 10,
  "maxRetrace": 0.618,
  "touchPct": 0.015,
  "legMult": 1.15
}
```

## 시간

불명확. 시간 키가 params 에 없다.

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "chart",
  "note": "차트에 눌림이 찍힌 일봉. 주문 유니버스는 아직 없다."
}
```

## 설정

```
{
  "stratLive.ATR01.enabled": false,
  "stratLive.ATR01.paperOnly": true,
  "chartLabel": "ATR01",
  "orders": "none"
}
```

## 버전 이력

- 2026-11-14 v1 스펙 등록. 주문 없음. 1년 비용 없는 참고 +2.79%/거래, PF 1.52, 153건.

보유를 중단 버튼으로 자동 매도하지 않는다.
