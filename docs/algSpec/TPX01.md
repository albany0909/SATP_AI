# 시간별 분할익절·청산 (TPX01)

## 쉽게 말하면

시간대에 따라 보유 종목을 나누어 익절하거나 청산합니다. 손실이 난 매도는 텔레그램 확인을 기다립니다. 몇 시에 몇 퍼센트인지는 rules.tp 문장을 그대로 봐야 하고, 여기서 숫자를 만들지 않습니다. 장중 보유를 시간에 맞춰 줄이는 용도입니다.

- 전략ID: `timeTpExit_v1`
- 버전: 4
- 상태: draft
- 명세 enabled: False
- 주기: intraday

## 목적

v4 draft. t212u: 손실 매도는 자동 주문 금지, 텔레그램 확인 후 진행. 라이브·주문/재시작 없음.

## 대상 종목

```
{
  "type": "tradeRegisteredHoldings",
  "note": "매매등록(trade-on) 보유종목(qty>0)만. 전체 보유/LDR01 스카우트 목록 아님"
}
```

## 진입

불명확. 스펙 rules 에 해당 키가 없다.

## 청산·손절

- tp: 15:15~15:20 직전 창 안의 현재 수익률만 판단(장중 고점 무시). >=10/20/30% 시 단계별 1회, 체크 시점 남은 수량의 10/20/30% 매도. 계산<1주면 1주
- negCut: 1~3차 중 하나라도 익절 후 수익률<0%면 남은 수량 50% 청산(<1주면 1주) 먼저, 그 다음 플래그 초기화
- maExit: 15:19~15:20 가격/수익률만 사용: 현재가<SMA20 음봉 이탈 & 수익률>=10%면 1주만 남기고 매도

## 수량·금액

```
{
  "tpStartTm": "151500",
  "maExitTm": "151900",
  "tpLevels": [
    {
      "step": 1,
      "minPnlPct": 10.0,
      "sellRatio": 0.1
    },
    {
      "step": 2,
      "minPnlPct": 20.0,
      "sellRatio": 0.2
    },
    {
      "step": 3,
      "minPnlPct": 30.0,
      "sellRatio": 0.3
    }
  ],
  "sellRatioBase": "remainingQtyAtCheck",
  "resetPnlPct": 0.0,
  "negAfterTpCutRatio": 0.5,
  "maPeriod": 20,
  "maExitMinPnlPct": 10.0,
  "maExitKeepQty": 1,
  "qtyRound": "floor_min1_whenQtyGe2",
  "pnlBase": "avgBuyPrice_feeExcluded",
  "onePerDayPerStep": true,
  "paperOnly": true,
  "tpWindowStartTm": "151500",
  "tpWindowEndTm": "152000",
  "tpWindowEndExclusive": true,
  "maExitWindowStartTm": "151900",
  "maExitWindowEndTm": "152000",
  "pnlSource": "currentInWindow_notIntradayPeak",
  "negAfterTpOrder": "cutThenReset",
  "minHoldQty": 2,
  "excludeQtyEq1": true
}
```

## 시간

```
{
  "tpStartTm": "151500",
  "maExitTm": "151900",
  "maExitMinPnlPct": 10.0,
  "tpWindowStartTm": "151500",
  "tpWindowEndTm": "152000",
  "maExitWindowStartTm": "151900",
  "maExitWindowEndTm": "152000"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "tradeRegisteredHoldings",
  "note": "매매등록(trade-on) 보유종목(qty>0)만. 전체 보유/LDR01 스카우트 목록 아님"
}
```

## 버전 이력

문서·DB 이력이 없다.

보유를 중단 버튼으로 자동 매도하지 않는다.
