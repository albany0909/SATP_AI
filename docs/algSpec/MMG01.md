# 수동정찰 종가 피라미딩 (MMG01)

## 쉽게 말하면

사람이 먼저 1주 이상 사 둔 종목만 대상으로, 15:15 무렵 종가 쪽으로 피라미딩합니다. 자동으로 아무 종목이나 고르지는 않습니다. 손절 퍼센트는 이 쉬운 문장에 단정하지 않습니다. 스펙 rules 에 stop 키가 없어 불명확합니다. 이미 관심 있는 종목을 마감 전에 나누어 담는 용도입니다.

중지해도 이미 가진 종목의 손절·익절은 계속 보고, 새 매수는 하지 않습니다.

- 전략ID: `manualMgr_v1`
- 버전: 1
- 상태: paper
- 명세 enabled: True
- 주기: 1d (EOD decision) + intraday protective stop only

## 목적

DRAFT rev3 — uncommitted, enabled=false; screener/autoScout design only

## 대상 종목

```
{
  "source": "userPool",
  "scout": "user buys 1 share manually (HTS/MTS)",
  "adopt": "pool stock + non-bot BUY fill (qty>=1) after enable; pre-held only via 'mmg adopt'",
  "basisFromAcnt": true
}
```

## 진입

- prereq: MTS01 v64: onBrokerFill 체결선착 대기복구는 봇 주문번호/MTS01 관리종목만

## 청산·손절

불명확. 스펙 rules 에 해당 키가 없다.

## 수량·금액

```
{
  "schedule": {
    "evalAt": "15:15",
    "orderWindowStart": "15:15",
    "orderWindowEnd": "15:28",
    "session": "15:15-15:20 continuous (limit, marketable) then 15:20-15:30 closing call auction (trde_tp 0/3 accepted; no dedicated close code)",
    "fallbackAfterHoursClose": {
      "enabled": false,
      "trdeTp": "81",
      "window": "15:40-16:00",
      "note": "장마감후 시간외 종가, verify in paper"
    },
    "noOrderAfter": "15:29",
    "intradayAdds": false
  },
  "levels": {
    "darvasBox": {
      "lookbackD": 20,
      "topConfirmD": 3,
      "bottomConfirmD": 3
    },
    "pivotLeftD": 3,
    "pivotRightD": 3,
    "pivotLookbackD": 120,
    "baseHighD": [
      20,
      50
    ],
    "ma": [
      10,
      20,
      50,
      150,
      200
    ],
    "wk30Proxy": "SMA150",
    "atrPeriod": 14,
    "clusterAtrMult": 0.25,
    "clusterMinPct": 0.4
  },
  "trendTemplate": {
    "pxAboveSma50": true,
    "sma50AboveSma150": true,
    "sma150AboveSma200": true,
    "sma200RisingD": 20,
    "pxAboveLow52wPct": 30,
    "pxWithinHigh52wPct": 25,
    "weinsteinStage2": "SMA150 rising (vs 20D ago) and close > SMA150",
    "rsVsKospiD": 63,
    "rsMin": "stock 63D return > index 63D return"
  },
  "setups": {
    "pivotBreakout": {
      "pivot": "base/box high (20-50D)",
      "closeAbovePivot": true,
      "volMult": 1.5,
      "volAvgD": 50,
      "maxExtPct": 5.0
    },
    "vcp": {
      "contractions": [
        2,
        4
      ],
      "eachTighterRatio": 0.7,
      "lastRangePctMax": 10,
      "volDryRatio": 0.7,
      "trigger": "close > last contraction high on vol>=1.4x"
    },
    "darvasBreak": {
      "trigger": "close > box top, vol>=1.5x",
      "stop": "box bottom - 0.5 tick... use bottom*0.99"
    },
    "pullbackMa": {
      "ma": [
        10,
        20
      ],
      "touchBandPct": 1.5,
      "trigger": "bull close, close > prior high, vol < 50D avg on pullback",
      "requireStage2": true
    }
  },
  "pyramid": {
    "targetPosAmt": 3000000,
    "maxPosAmt": 3000000,
    "tranchePct": [
      0.4,
      0.35,
      0.25
    ],
    "maxAdds": 3,
    "addOnlyIfProfitPct": 1.0,
    "addOnlyIfAboveLastAddPx": true,
    "minDaysBetweenAdds": 2,
    "riskPerAddPctEquity": 0.5,
    "qty": "min(trancheAmt/px, riskAmt/(px-stop), cap remaining) floor, >=1",
    "orderType": "limit",
    "pxRound": "floorToKrxTick",
    "limitOffsetTicks": 2
  },
  "exits": {
    "catastrophicStopPct": -8.0,
    "initialStopRule": "max(-8% from avg, setup stop(box bottom / pivot low / MA50))",
    "tightenAfterAdd": "stop = max(prev stop, last add setup low*0.99, avg*0.97 after 2nd add)",
    "breakevenAfterPct": 10.0,
    "partialTp": [
      {
        "atPct": 20,
        "ratio": 0.33,
        "rule": "O'Neil 20-25%"
      },
      {
        "climax": "3-day gain>=25% or gap+wide range on record vol",
        "ratio": 0.33
      }
    ],
    "trail": "close < SMA10 (if gain>=20%) else close < SMA20 -> sell half; close < SMA50 -> exit",
    "partialSl": {
      "trigger": "close < nearest daily support/box bottom",
      "ratio": 0.5
    },
    "exitAll": [
      "close < SMA50",
      "close < SMA150 (stage 3/4)",
      "intraday <= catastrophic stop"
    ],
    "scoutOnlyRule": "1-share scout never stopped intraday; EOD EXIT if trend template fails 2 closes",
    "orderType": "limit (EOD, ceilToKrxTick, -2 ticks from last) ; market for catastrophic intraday",
    "runnerProfile": {
      "recommended": true,
      "partialTp": "none (optional 1/4 at +30%)",
      "exitAll": "close<SMA50 or close < highestHigh-3*ATR14",
      "note": "backtest best: C|runA"
    }
  },
  "risk": {
    "equitySource": "kt00004 추정예탁자산",
    "maxTotalExposurePct": 60,
    "maxStocks": 6,
    "maxRiskPerStockPctEquity": 1.25,
    "dailyLossLimitPct": -2.0,
    "dailyLossAction": "no ADD today",
    "monthlyDdHaltPct": -6.0
  },
  "exclusive": {
    "blockOtherAlgsOnPool": true,
    "blockStls": true
  },
  "screener": {
    "enabled": false,
    "dryRunOnly": true,
    "universe": "KOSPI+KOSDAQ, close>=1000, 20D avg value>=5bn KRW, exclude ETF/SPAC/managed",
    "signals": {
      "E1": "close crosses above SMA150 + vol>=1.5x50D + SMA150 slope20>=-2%",
      "E2": "close>20D high, vol>=1.5x, close>=85% 52wHigh, close>SMA50, 20D box range<=20%",
      "E3": "SMA20 crosses above SMA60 + vol5>=1.2x vol50 + close>SMA20",
      "E4": "close>=95% 52wHigh and 20D-high breakout with vol>=1.5x",
      "NEAR": "trend template + close within 3% of 20D high (watch only)"
    },
    "schedule": [
      "16:10 after-close full scan (next-day watch)",
      "15:10 intraday rescan of watch for 15:15 signal"
    ],
    "maxNames": 10,
    "score": "2*signals + TT + near + min(volx,3)/3 + close/hi52",
    "watchWrite": {
      "store": "utils.watchStkStore",
      "tag": "SRC=MMG01_SCR, NOTE=signal/score",
      "dedupe": "skip if exists (update NOTE only)",
      "expiryDays": 10,
      "manualPinned": "never auto-remove user-added"
    }
  },
  "autoScout": {
    "enabled": false,
    "rule": "screener pick + E1-E4 on day close -> buy 1 share at 15:15-15:20 (limit floorToKrxTick? use marketable limit), max 2 new/day, same caps",
    "scoutMaxAmt": 300000,
    "note": "if 1 share > scoutMaxAmt skip (e.g. 1.78M stock)"
  },
  "paperOnly": true
}
```

## 시간

```
{
  "schedule": {
    "evalAt": "15:15",
    "orderWindowStart": "15:15",
    "orderWindowEnd": "15:28",
    "session": "15:15-15:20 continuous (limit, marketable) then 15:20-15:30 closing call auction (trde_tp 0/3 accepted; no dedicated close code)",
    "fallbackAfterHoursClose": {
      "enabled": false,
      "trdeTp": "81",
      "window": "15:40-16:00",
      "note": "장마감후 시간외 종가, verify in paper"
    },
    "noOrderAfter": "15:29",
    "intradayAdds": false
  }
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "source": "userPool",
  "scout": "user buys 1 share manually (HTS/MTS)",
  "adopt": "pool stock + non-bot BUY fill (qty>=1) after enable; pre-held only via 'mmg adopt'",
  "basisFromAcnt": true
}
```

## 버전 이력

- 2026-10-12 v1 v1 라이브(모의) 첫 구현 — 최종 설정 F60|earn + 래칫 R, 종목당 최대 매수금액 설정 연동, paperOnly.

보유를 중단 버튼으로 자동 매도하지 않는다.
