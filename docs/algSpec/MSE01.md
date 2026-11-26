# 이평선지지 매매 (MSE01)

## 쉽게 말하면

이평선 위로 한 번 올라간 뒤, 약한 음봉이나 도지가 5일선에 받치면 장 마감 무렵 삽니다. 매수 시간은 스펙의 15:18~15:20 구간입니다. 청산 가격 규칙은 rules.exit 에 있고, 이 쉬운 설명에 숫자를 더 넣지 않습니다. 오르막에서 쉬는 눌림에 가깝고, 하락장 신규 매수 조건은 불명확합니다.

중지해도 이미 가진 종목의 손절·익절은 계속 보고, 새 매수는 하지 않습니다.

- 전략ID: `maSupportEod_v1`
- 버전: 9
- 상태: test
- 명세 enabled: True
- 주기: D

## 목적

status=test. Live: maSupportEodLive. BUY_SKIP 복기(종목당 1일 1건). 실전 당일 봉 = 1분봉 없으면 ka10001 시/고/저/현재가+누적거래량(구독 종목은 0B 현재가로 종가·고저 보정). 매수 창은 연속거래 마지막 2분(15:18~15:20) — 동시호가 직전까지. ka10001 은 폴링당 20종씩 나눠 조회(회전)하고 한도는 utils/trRateLimit 공용 게이트로 지킨다. 창 안에서 평가 가능한 종목은 약 110종.

## 대상 종목

```
{
  "type": "ka10016",
  "requireBull": false,
  "pickHm": "1600",
  "pickTr": "ka10016",
  "pickDt": "250",
  "pickNtlTp": "1",
  "pickHighLowCloseTp": "1",
  "pickMkt": [
    "kospi",
    "kosdaq"
  ],
  "pickMrktTp": [
    "001",
    "101"
  ],
  "stexTp": "1",
  "maxStk": 500,
  "fullReplace": false,
  "keepSmaLen": 20,
  "dropIfCloseAtOrBelowSma20": true,
  "note": "장마감 후 16:00 ka10016 52주 신고가(코스피 001+코스닥 101, 고저기준 dt=250) 편입. 종가≤전일확정 SMA20 이면 관심 제외. 종가>SMA20 이면 당일 신고가 목록에 없어도 유지. 원장 미청산 유지. 최초진입 필터 없음. 매수 규칙은 SMA5 돌파+약음봉/십자(이평터치 MTS01과 분리)."
}
```

## 진입

- entry: after breakout history, weak bear or doji/small body + SMA5 support + today volume < min(last volCmpBullDays bullish-day volumes) → buy (live: 15:18~15:20 exclusive — continuous session only, stops right before the closing auction; BT: day close)
- breakout: daily close crosses above SMA5 (prior closes only); same-day breakout bar does not buy

## 청산·손절

- exit: while long, close < SMA5 → sell (live: 15:18~15:20 market, BT: day close)

## 수량·금액

```
{
  "maType": "sma",
  "smaLen": 5,
  "weakBearMaxPct": 2.5,
  "dojiMaxPct": 0.5,
  "buyAmtWon": 2000000,
  "banSameDayBreakoutBuy": true,
  "volCmpBullDays": 3,
  "buyStartTm": "151800",
  "buyEndTm": "152000",
  "sellStartTm": "151800",
  "sellEndTm": "152000",
  "paperOnly": true
}
```

## 시간

```
{
  "buyStartTm": "151800",
  "buyEndTm": "152000",
  "sellStartTm": "151800",
  "sellEndTm": "152000"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "ka10016",
  "requireBull": false,
  "pickHm": "1600",
  "pickTr": "ka10016",
  "pickDt": "250",
  "pickNtlTp": "1",
  "pickHighLowCloseTp": "1",
  "pickMkt": [
    "kospi",
    "kosdaq"
  ],
  "pickMrktTp": [
    "001",
    "101"
  ],
  "stexTp": "1",
  "maxStk": 500,
  "fullReplace": false,
  "keepSmaLen": 20,
  "dropIfCloseAtOrBelowSma20": true,
  "note": "장마감 후 16:00 ka10016 52주 신고가(코스피 001+코스닥 101, 고저기준 dt=250) 편입. 종가≤전일확정 SMA20 이면 관심 제외. 종가>SMA20 이면 당일 신고가 목록에 없어도 유지. 원장 미청산 유지. 최초진입 필터 없음. 매수 규칙은 SMA5 돌파+약음봉/십자(이평터치 MTS01과 분리)."
}
```

## 버전 이력

- 2026-08-22 v1 v1 최초 — 8/1~8/21 검증 규칙 반영, 전략 등록
- 2026-08-22 v? 실전 매수 15:15~15:20 · SMA5 이탈 매도 15:18~15:20 장중
- 2026-08-28 v2 v2 — 매수 15:20~15:28, 약음봉 2.5%+십자 0.5%, SMA5 유니버스 분리, BUY_SKIP 복기, 매수 OHLC는 분봉 종가 유지
- 2026-08-29 v3 v3 — 관심: 순위20∪대금30 중 52주 신고가 편입, 종가≤SMA20 이면 제외. 매수 조건(SMA5 돌파·약음봉)은 유지
- 2026-08-30 v4 v4 — 관심: 16:00 ka10016 52주 신고가(코스피+코스닥)로 전량 교체. 기존 목록 삭제. SMA20 유지 없음
- 2026-08-30 v5 v5 — 관심 제외는 종가≤SMA20 만. ka10016 은 편입, 종가>SMA20 이면 유지
- 2026-08-30 v6 v6 — 매수: 당일 거래량 < 최근 3개 양봉 거래량(최솟값)
- 2026-09-03 v7 v7 — 실전 매수 15:18~15:31(미만, 15:30분 포함) (이전 15:20~15:28)
- 2026-09-03 v8 v8 — 장중 당일 봉을 ka10001(시/고/저/현재가+누적거래량)로 만든다. 이전에는 1분봉이 매수 창 뒤(15:34~)에 적재돼 대상 대부분이 NO_OHLC 로 스킵되어 매수가 아예 나오지 않았다. 0B 구독 종목은 실시간 현재가로 종가·고저 보정. 돌파이력 없음·보유 중은 조회 생략
- 2026-09-04 v9 v9 — 실전 매수 15:18~15:20(미만) (이전 15:18~15:31). 장마감 동시호가 직전까지만 매수 — 봉 모양·현재가가 연속거래 가격일 때만 판정한다. 창이 닫히면 폴링 중간에도 즉시 중단, 폴링 1회 최대 40초

보유를 중단 버튼으로 자동 매도하지 않는다.
