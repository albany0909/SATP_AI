# 이평선터치 매매 (MTS01)

## 쉽게 말하면

전날 순위·거래대금으로 고른 종목이 20일선 위로 올라오면 삽니다. 스펙 메모에는 1·2차 매수 전 하드 손절이 -1.5%로 적혀 있습니다. 1차 익절 비율은 버전마다 바뀌어 현재 params 의 tp1 을 봅니다. 장중에 새 종목을 집어넣지 않고, 이미 고른 목록만 봅니다. 예를 들어 10,000원에 샀고 하드 손절이 1.5%이면 9,850원 근처에서 팝니다.

중지해도 이미 가진 종목의 손절·익절은 계속 보고, 새 매수는 하지 않습니다.

- 전략ID: `maTouchScale_v1`
- 버전: 64
- 상태: test
- 명세 enabled: True
- 주기: 1

## 목적

v64: 대기 없는 매수 체결(체결선착 대기복구)은 이평터치 주문번호·최근 이평터치 매수주문(180초)·이미 이평터치 보유 종목만 반영 — 수동(HTS/MTS)·다른 전략 체결은 편입하지 않음. MMG01 풀·보유 종목은 신규매수 스킵(MMG01_POOL). v63: 모든 지정가 주문을 유효 호가로 — 매수 내림·매도 올림(1차 +1.4% 지정가 29,710→29,750 등, 571557 거부 방지). 규칙·파라미터 변경 없음. v62: 하드손절 hardStopPct 2→1.5 (1·2차 전). v61: 1차 전 +1%면 +1.4% 지정가 선주문 · −1%↓ 취소·재무장(시간취소 없음) · 1차 체결완료 후만 본전. v60: 1차익절 지정가(트리거가)·10분 미체결 취소 후 재도달 대기 · 1차 체결완료 전 본전청산 금지 · 완료 후 0%↓ 시장가. v59: 주문워커 TRAIL 매도 누락 수정(큐만 넣고 _placeSell 미호출). v58: 3차 미사용·잔량≥2주여도 되돌림(트레일) 검사. v57: 2차 +4%/50% 재도입 · 1·2차 시 1주면 전량. v56: tp1 1.4%/50%, trail 4/-3 after peak, tp2-4 off, 15:15 flat 0~4%. v55: weakMfeExit. v54: tp1 1.2→1.4. v55: weakMfeExit 90m + peak<1% + pnl<=-0.5% + 현행 tp1Pct 미도달 시 전량. v54: tp1Pct 1.2→1.4. v53: 관심 제외

## 대상 종목

```
{
  "type": "rank16h",
  "requireBull": false,
  "pickHm": "1600",
  "pickRankTr": "ka00198",
  "pickRankQryTp": "1",
  "pickMkt": [
    "kospi",
    "kosdaq"
  ],
  "minQtyPerStep": 1,
  "maxStk": 50,
  "rankMax": 20,
  "pickTrdePrcaTr": "ka10032",
  "maxTrdePrcaRank": 30,
  "replaceDaily": true,
  "keepOpenPos": true,
  "note": "거래일 16:00 ka00198 당일누적 상위 20 ∪ ka10032 거래대금 상위 30 (중복 제거), 코스피/코스닥만. 매차 최소금액으로 1주 이상. 다음 거래일 관리종목(MTS01)·관심 USE_TRADE=Y. 매일 전량 교체, 원장 미청산 유지. 기존 관심은 현재가>SMA20 유지, 이틀 연속 음봉+종가≤SMA20 일 때만 제외(하루 이탈은 유예). 신규 편입은 현재가>SMA20. 양봉 필터 없음. 장중 신규 편입 없음.",
  "watchDrop": "sma20_2bear"
}
```

## 진입

- entry: from above touch SMA20 once; do not buy on touch; same-day only: wait for a 3-min bar that closes bullish above SMA20 (close>open and close>SMA20); place a limit buy at SMA20 rounded DOWN to a valid KRX tick; if unfilled after 10 minutes cancel remainder (no market follow-up); wait expires same day; retouch resets; 14:00 cutoff only if next calendar day is closed; after full exit ban rebuy that stock same day

## 청산·손절

- stop: before any TP: hard -1.5% (hardStopPct); after 1st or 2nd TP: avg 0% (breakeven) flatten remaining; same-day entry flatten after 15:18 if px<=avg; prior-day SMA20 after 15:18 | weak MFE flat: hold>=weakHoldMin, never reached current tp1Pct (tp1Taken or maxPnl>=tp1Pct), maxPnl<weakPeakMaxPct, pnl<=weakExitMaxPnlPct
- takeProfit: intraday: 1st +1.4% sell 50%; 2nd +4% sell 50% of remaining; no 3rd/4th; residual trail 4/-3; 15:15 flat 0~4%; qty=1 flattens at TP1/TP2

## 수량·금액

```
{
  "maType": "sma",
  "maSteps": [
    20
  ],
  "buyAmtByStep": {
    "20": 1000000
  },
  "exitAfterTm": "151800",
  "buyCutoffTm": "140000",
  "buyCutoffIfNextClosed": true,
  "stopMa": 20,
  "takeMa": 10,
  "confirmMode": "bar3BreakLimit",
  "confirmBarMin": 3,
  "limitWaitSec": 600,
  "tp1Pct": 1.4,
  "tp1QtyRatio": 0.5,
  "tp2Pct": 4,
  "tp2QtyRatio": 0.5,
  "halfTpPct": 3,
  "halfTpFromStat": false,
  "nextClosedHalfTm": "151500",
  "nextClosedHalfMinPct": 0,
  "nextClosedHalfMaxPct": 4,
  "hardStopPct": 1.5,
  "afterHalfStopPct": 0,
  "breakevenAfterTp1": true,
  "buyBanUntilTm": "000000",
  "reclaimMinPct": 0,
  "banBuyIfOpenBelowSma": false,
  "halfAddNextDay": false,
  "halfAddMinPnlPct": 5,
  "halfAddQtyRatio": 0.5,
  "eodFlatIfNoProfit": true,
  "rsiTpPeriod": 14,
  "rsiTpMin": 70,
  "tp3Pct": 0,
  "tp3QtyRatio": 0.5,
  "tp4Pct": 0,
  "afterHalfTrailMinPct": 4,
  "afterHalfTrailTpPct": 3,
  "afterHalfRsiMin": 0,
  "banRebuySameDay": true,
  "paperOnly": true,
  "weakMfeExitEnabled": true,
  "weakHoldMin": 90,
  "weakPeakMaxPct": 1.0,
  "weakExitMaxPnlPct": -0.5,
  "tp1UseLimit": true,
  "tp1LimitSec": 0,
  "tp1PreArmPct": 1.0,
  "tp1CancelPct": -1.0
}
```

## 시간

```
{
  "exitAfterTm": "151800",
  "buyCutoffTm": "140000",
  "nextClosedHalfTm": "151500",
  "buyBanUntilTm": "000000",
  "weakExitMaxPnlPct": -0.5
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "rank16h",
  "requireBull": false,
  "pickHm": "1600",
  "pickRankTr": "ka00198",
  "pickRankQryTp": "1",
  "pickMkt": [
    "kospi",
    "kosdaq"
  ],
  "minQtyPerStep": 1,
  "maxStk": 50,
  "rankMax": 20,
  "pickTrdePrcaTr": "ka10032",
  "maxTrdePrcaRank": 30,
  "replaceDaily": true,
  "keepOpenPos": true,
  "note": "거래일 16:00 ka00198 당일누적 상위 20 ∪ ka10032 거래대금 상위 30 (중복 제거), 코스피/코스닥만. 매차 최소금액으로 1주 이상. 다음 거래일 관리종목(MTS01)·관심 USE_TRADE=Y. 매일 전량 교체, 원장 미청산 유지. 기존 관심은 현재가>SMA20 유지, 이틀 연속 음봉+종가≤SMA20 일 때만 제외(하루 이탈은 유예). 신규 편입은 현재가>SMA20. 양봉 필터 없음. 장중 신규 편입 없음.",
  "watchDrop": "sma20_2bear"
}
```

## 버전 이력

- 2026-10-12 v64 v64 체결선착 대기복구 제한: 이평터치 주문번호·최근 이평터치 매수주문(180초)·기존 이평터치 보유만 포지션 반영, 수동(HTS/MTS)·타전략 체결은 무시. MMG01 풀/보유 종목 신규매수 스킵. 매매규칙·파라미터 불변.
- 2026-09-30 v63 v63 지정가 주문단가를 유효 호가로: 매수 내림(SMA20 인근 → SMA20 이하 호가) · 매도 올림(1차 +1.4% 지정가, 예 29,710→29,750). 호가 오류(571557) 거부 반복 수정. 수치 파라미터 변경 없음.
- 2026-09-22 v62 v62 하드손절 −2% → −1.5% (1·2차 전 hardStopPct). 익절후본전·트레일 유지.
- 2026-09-21 v61 v61 1차 전 +1% 도달 시 +1.4% 지정가 선주문 · −1%↓ 취소 후 재무장(10분 취소 폐지) · 1차 체결완료 후만 본전.
- 2026-09-21 v60 v60 1차익절 지정가(트리거가) · 10분 미체결 취소 후 재도달 대기 · 1차 체결완료 전 본전(0%) 금지 · 완료 후 0%↓ 시장가.
- 2026-09-21 v59 v59 잔량 트레일: 주문워커에 TRAIL→_placeSell 누락 수정 (조건충족인데 미주문).
- 2026-09-21 v58 v58 잔량 트레일: 3차 끔+수량≥2 에서도 고점 대비 −N%p 되돌림 검사 (비에이치 잔량 미청산 수정).
- 2026-09-17 v57 v57 2차 익절 +4%/잔량 50% 추가 · 1·2차 도달 시 1주면 전량. (1차 1.4%/50% · 트레일 4/−3 · 15:15 0~4% 유지)
- 2026-09-17 v56 v56 1차 +1.4%/50% · 2·3·4차 끔 · 잔량 트레일 고점+4% 후 −3%p · 15:15 0~4% 전량 (본전·약세조기 유지).
- 2026-08-15 v1 v1 최초
- 2026-08-21 v? 매차 7/13/20. 터치 즉시 매수 폐지 → 3분봉 2연속 확인 후 매수
- 2026-08-21 v? 14:00 매수금지는 다음날 장이 안 열리는 날만. 휴장 전날 15:15 0~5% 절반
- 2026-09-16 v55 v55 약세조기청산: 보유≥90분·고점<+1%·평가≤−0.5%·당시 tp1Pct 미도달이면 전량 (하드손절 전).
- 2026-09-16 v54 v54 1차 익절 +1.2% → +1.4% (2·3·4차·트레일 유지).
- 2026-09-16 v53 v53 관심 제외: SMA20 하루 이탈 → 이틀 연속 음봉+종가≤SMA20 만 제외(유예 유지).
- 2026-08-21 v11 v11 매수 SMA20만 · 1차 +3% 절반 · 하드 -2% · 15:18 수익 중 SMA10 이탈 잔량 청산 · 당일 재매수 금지
- 2026-08-25 v? 잔량 익절 — 매수 후 10이평 장중 돌파 있으면 SMA10, 없으면 SMA20 이탈
- 2026-08-26 v12 v12 3분봉 2연속 확인 폐지 → 당일 터치 후 현재가 > SMA20 회복 시 매수
- 2026-08-28 v13 v13 당일 매수가 15:18에 미수익이면 전량 청산 (오버나잇 시초 갭 방지). 수익 중 당일 매수는 기존 익절·익일 SMA20
- 2026-08-29 v14 v14 절반 후 잔량 손절 평단−0.3%. 시초 09:00~09:10·시가≤SMA20·회복 +0.3% 필터. 1차 익절 +2.5% (통계). 절반 다음날 +3%이면 잔량 절반 추가
- 2026-08-29 v15 v15 절반 후 잔량 손절 평단−1%. 시초 회복 금지 09:00~09:05. 절반 다음날 +5% 이상이면 잔량 절반 추가
- 2026-08-29 v16 v16 v13 복귀 + 절반 후 잔량 손절만 평단−0.3%. v14/v15 시초필터·회복+0.3%·1차 2.5%·다음날 추가 제외
- 2026-08-29 v17 v17 운영 확정. 규칙은 v16과 동일 (v13 + 절반 후 평단−0.3%만)
- 2026-08-30 v18 v18 매차 금액 300만원. 매매 규칙은 v17과 동일
- 2026-08-31 v19 v19 실매매 1차 익절 = 청산전 최대수익률(MFE) 통계. 표본은 +% 1차 익절 첫 사용일(2026-08-24)~현재. 매일 16:05 재계산·반영. 전략탭 1차 수익률 (통계치) 빨강
- 2026-08-31 v20 v20 다음날 휴장일 15:15부터 평단 0% 이상 3% 미만 전량 청산 (이전 0~5% 절반). 3% 이상은 보유
- 2026-09-01 v21 v21 절반 전 하드스탑 −1.7% (이전 −2%). 8/24~ 1차익절 MAE 하한을 넘기지 않게 조임
- 2026-09-01 v22 v22 절반 전 하드스탑 −2% 로 복귀 (v21 −1.7% 철회)
- 2026-09-01 v23 v23 수익 중 RSI14 ≥ 70 이면 잔량 전량 청산 (장중, 1차 절반보다 앞)
- 2026-09-01 v24 v24 절반 후 잔량 손절 평단 0%(본전) (이전 −0.3%)
- 2026-09-02 v? 실매매 1차 통계 = 최근 10거래일 매수 · 1차 전 -2% 손절 건의 손절 전 고점 평균. 16:05 반영
- 2026-09-02 v25 v25 틱 회복 매수 폐지 → 터치 후 3분봉 종가>SMA20 돌파 + 바로 다음 3분 양봉이면 매수
- 2026-09-02 v26 v26 매일 15:15부터 평단 0% 이상 3% 이하 전량 (이전: 다음날 휴장일만, 3% 미만)
- 2026-09-02 v27 v27 15:15 전량에 손실(0% 이하) 도 포함. 평단 3% 이하면 전량, 3% 초과만 보유
- 2026-09-02 v28 v28 15:15 전량에서 손실 제외 → 다시 0% 이상 3% 이하만 전량
- 2026-09-02 v29 v29 1차 절반 후 잔량 +3% 이상이면 +20% 또는 RSI14≥65 전량
- 2026-09-02 v30 v30 2차(절반 후 잔량) 익절 +20% → +15%
- 2026-09-02 v31 v31 매수: 3분봉 양봉 돌파 1봉이면 SMA20 호가 지정가. 3분 미체결 시 취소 후 현재가>SMA20 일 때만 시장가
- 2026-09-02 v32 v32 1차 +1.5% 보유 30%, 2차 +2% 보유 30%, 예전 2차(+15%/RSI65)는 3차. 1차 MFE 통계 오버레이 끄기
- 2026-09-02 v33 v33 1차 또는 2차 익절 후 잔량이 본전(0%) 이하면 전량. 익절 전에만 하드 −2%
- 2026-09-03 v34 v34 매수: 3분봉 양봉 돌파 마감이면 시장가(그 봉 종가). SMA20 지정가·3분 미체결 전환 폐지
- 2026-09-03 v35 v35 매수: 3분봉 양봉 돌파 마감 → SMA20 인근 호가 지정가. 5분 미체결이면 취소만(시장가 전환 없음). 취소는 거래로그에 표시
- 2026-09-03 v36 v36 3차 +5% 보유 50%, 4차 +10% 전량. 구 3차(+15% / RSI14≥65 전량) 삭제
- 2026-09-03 v37 v37 3차 +5% → +4% 보유 50% (4차 +10% 전량은 유지)
- 2026-09-03 v38 v38 4차 +10% → +7% 전량 (3차 +4% 50% 유지)
- 2026-09-04 v39 v39 15:18 이후 수익 중 SMA10/SMA20 이탈 익절 삭제. 당일 미수익 전량·익일 SMA20 손절은 유지
- 2026-09-09 v40 v40 1차 +1.5% → +1.2%, 2차 +2%(유지), 3차 +4% → +3% (4차 +7% 유지)
- 2026-09-10 v41 v41 1차 +1.2% → +1.5% (2차 +2% · 3차 +3% · 4차 +7% 유지)
- 2026-09-10 v42 v42 3차 +3% → +4% (1차 +1.5% · 2차 +2% · 4차 +7% 유지)
- 2026-09-12 v43 v43 지정가 미체결 대기 5분→3분, 미체결 시 취소 후 시장가 전환 (v35 취소만 폐지)
- 2026-09-13 v44 v44 1차 +1.5% → +1.2% (2차 +2% · 3차 +4% · 4차 +7% · 하드 −2% 유지)
- 2026-09-13 v45 v45 잔량 트레일: 고점 +5% 이후 −3%p 전량. 4차 +7%와 병행·먼저 충족
- 2026-09-14 v46 v46 지정가 미체결 대기 3분→10분, 미체결 시 취소만 (시장가 전환 없음)

보유를 중단 버튼으로 자동 매도하지 않는다.
