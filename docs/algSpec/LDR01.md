# 주도주 피라미딩 (LDR01)

- 전략ID: `leaderScale_v1`
- 버전: 2
- 상태: paper
- 명세 enabled: True
- 주기: D

## 목적

v2. 모의 전용(paperOnly). 실주문 엔진은 당분간 MMG01(manualMgrLive) 모의 기동으로 피라미딩 실행. LDR01은 스펙·탭·복기 기준.

## 대상 종목

```
{
  "type": "leaderEvent",
  "markets": [
    "KOSPI",
    "KOSDAQ"
  ],
  "minFluRt": 7,
  "volVsPrev": true,
  "volMult": 1.5,
  "eventWindowDays": 3,
  "themeMinUpCnt": 3,
  "themeMinUpFlu": 2,
  "themeUpRatio": 0.3,
  "rsVsIndex": true,
  "rsLookbackD": 20
}
```

## 진입

- watch: theme breadth + flu/vol spike + RS>index; WATCH only until TRADE unlock

## 청산·손절

불명확. 스펙 rules 에 해당 키가 없다.

## 수량·금액

```
{
  "scoutQty": 1,
  "addQtys": [
    2,
    3,
    4
  ],
  "maxAdds": 3,
  "setupValidDays": 15,
  "gapSkipPct": 5.0,
  "setupMinFluPct": 7.0,
  "pullbackLow": 0.38,
  "pullbackHigh": 0.62,
  "smaFast": 5,
  "smaMid": 10,
  "smaSlow": 20,
  "softStopMa": 10,
  "hardStopMa": 20,
  "hardStopPct": 8.0,
  "partialTpPct": 15.0,
  "partialTpRatio": 0.25,
  "trailFromPeakPct": 12.0,
  "riskPerTradePct": 1.0,
  "maxRiskPerStkPct": 2.0,
  "maxNewScoutPerDay": 2,
  "maxTradePositions": 6,
  "indexFilterSma": 20,
  "indexBlockDownPct": 2.0,
  "buyStartTm": "091000",
  "buyEndTm": "143000",
  "eodDecideTm": "151500",
  "paperOnly": true
}
```

## 시간

```
{
  "buyStartTm": "091000",
  "buyEndTm": "143000",
  "eodDecideTm": "151500"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "leaderEvent",
  "markets": [
    "KOSPI",
    "KOSDAQ"
  ],
  "minFluRt": 7,
  "volVsPrev": true,
  "volMult": 1.5,
  "eventWindowDays": 3,
  "themeMinUpCnt": 3,
  "themeMinUpFlu": 2,
  "themeUpRatio": 0.3,
  "rsVsIndex": true,
  "rsLookbackD": 20
}
```

## 버전 이력

- 날짜 v? 내용
- 2026-10-11 v1 v1 draft 초안 (자동주문 없음)
- 2026-10-31 v2 v2 LDR-PYR 규칙 확정, paper 상태, 모의 운용(MMG01 엔진 연동)

보유를 중단 버튼으로 자동 매도하지 않는다.
