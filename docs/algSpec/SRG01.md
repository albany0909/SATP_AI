# 급등주매매 (SRG01)

- 전략ID: `surgeTrade_v1`
- 버전: 36
- 상태: draft
- 명세 enabled: False
- 주기: D

## 목적

v36. Day-chart ATR stop and target lines are chart series, so they stay on the candles. The axis includes only the lines in view. Entry still requires >= 10. Trading stays disabled. Live DB is not updated by this file.

## 대상 종목

```
{
  "type": "ka00198DayCumulativeInquiry",
  "qryTp": "4",
  "markets": [],
  "notGainersList": "ka10027",
  "minDailyPct": 10.0,
  "tabMinDailyPct": 0.0,
  "dailyBasis": "ka00198.base_comp_chgr"
}
```

## 진입

- entry: Verified first surge of the same confirmed event, tradable, and base_comp_chgr >= 10%. The tab's 0% list is not the entry universe. One initial 1,000,000 KRW from 15:18 until regular close. Freshness requires a snapshot observed at or after 15:18, never a future 16:00 snapshot.
- scope: PAPER ONLY. Real mode, missing mode, unverified paper account, or non-paper host fails closed at the final order boundary.

## 청산·손절

- stops: Return <= -5% sells all but 1 share. Return <= -10% closes the position and wins over every other sell. After a completed add, return strictly below 0% sells all but 1 share.
- profit: From 15:15 until the regular close, a positive holding whose developing candle is bearish and whose price crosses below the causal 20-day average exits in full.

## 수량·금액

```
{
  "paperOnly": true,
  "tabMinDailyPct": 0.0,
  "enabled": false,
  "initialBudgetKrw": 1000000,
  "addBudgetKrw": 1000000,
  "maxAddStages": 2,
  "maxAddStagePerDay": 1,
  "feeRate": 0.0015,
  "add1Pct": 5.0,
  "add2Pct": 10.0,
  "firstStopPct": -5.0,
  "fullStopPct": -10.0,
  "reentryAbovePct": 0.0,
  "addLossBelowPct": 0.0,
  "profitExitAbovePct": 0.0,
  "firstBuyStartTm": "151800",
  "regularCloseTm": "153000",
  "closingAuctionStartTm": "152000",
  "profitExitStartTm": "151500",
  "openTm": "090000",
  "finalSnapTm": "160000",
  "pollIntervalMin": 15,
  "quoteMaxAgeSec": 20,
  "decisionSnapMaxAgeSec": 120,
  "returnBasis": "weightedAverageActualFillPrice",
  "maPeriod": 20,
  "rsiPeriod": 14,
  "exemptLossSellTelegramApproval": true
}
```

## 시간

```
{
  "firstBuyStartTm": "151800",
  "regularCloseTm": "153000",
  "closingAuctionStartTm": "152000",
  "profitExitStartTm": "151500",
  "openTm": "090000",
  "finalSnapTm": "160000"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "ka00198DayCumulativeInquiry",
  "qryTp": "4",
  "markets": [],
  "notGainersList": "ka10027",
  "minDailyPct": 10.0,
  "tabMinDailyPct": 0.0,
  "dailyBasis": "ka00198.base_comp_chgr"
}
```

## 버전 이력

- 2026-11-08 v1 v1 급등주매매(Surge Trade, SRG01) 초안. 모의 전용, 기본 비활성. 실주문·실DB 없음.
- 2026-11-09 v2 v2 급등주 탭 조회는 매매가 꺼져 있어도 15분·16:00 수집. 수동 갱신은 ADHOC. 주문은 계속 비활성.
- 2026-11-09 v3 v3 매수는 날짜 있는 KOSPI200 명부·확인 이슈·신선한 시세가 있을 때만. KOSDAQ150 은 ka20002 명세에 코드가 없어 비운다. 보유 청산은 시세만 사용. 주문은 계속 비활성.
- 2026-11-09 v4 v4 KOSPI200 은 모의 ka20002 전수이고 190~210종목일 때만. KOSDAQ150 은 KRX에서 지수명 '코스닥 150' 을 찾은 뒤 MDCSTAT00601. DART 키는 config 로만 읽고 출력하지 않는다. 주문은 계속 비활성.
- 2026-11-09 v5 v5 명부 필터와 명부 조회를 뺀다. 탭과 후보는 ka00198 base_comp_chgr 10% 이상만. 잘린 스냅샷 파일은 다시 받는다. 주문은 계속 비활성.
- 2026-11-09 v6 v6 탭은 5% 이상. 원순위·1년저점대비 칸을 빼고 첫급등대비를 넣는다. 매수 조건은 10% 그대로. 주문은 계속 비활성.
- 2026-11-10 v7 v7 장 시작 전 탭은 전 거래일 최종 목록을 보여주고 상단에 조회일시를 적는다. 매수 조건은 바꾸지 않는다. 주문은 계속 비활성.
- 2026-11-10 v8 v8 탭의 시장·거래대금·이슈·수급·RSI·첫급등대비를 조회 캐시로 채운다. 없는 값은 빈 칸. 매수 조건은 바꾸지 않는다. 주문은 계속 비활성.
- 2026-11-10 v9 v9 같은 이슈 단어는 같은 옅은 배경. 헤드라인은 네이버 모바일 종목뉴스. 실패는 한 줄로만 남긴다. 주문은 계속 비활성.
- 2026-11-10 v10 v10 급등주 탭에서 시장 칸을 뺀다. 시장 값은 캐시에 남긴다. 주문은 계속 비활성.
- 2026-11-10 v11 v11 RSI14 칸을 거래대금 바로 앞으로 옮긴다. 주문은 계속 비활성.
- 2026-11-10 v12 v12 RSI 30 이하는 옅은 파랑, 70 이상은 옅은 빨강. 그 사이와 빈 값은 배경 없음. 주문은 계속 비활성.
- 2026-11-10 v13 v13 이전 250거래일 고가 이상이면 종목명 앞에 신 배지. 셀 글자는 종목명만. 주문은 계속 비활성.
- 2026-11-10 v14 v14 최근 20거래일에만 첫 급등이 있으면 뉴 배지. 신 다음에 그린다. 주문은 계속 비활성.
- 2026-11-10 v15 v15 실패한 이슈 캐시는 다시 받고, 단어는 주도주 탭과 같은 표를 쓴다. 주문은 계속 비활성.
- 2026-11-10 v16 v16 이슈는 종목명이 들어간 헤드라인에서 이유 태그를 우선한다. 주문은 계속 비활성.
- 2026-11-10 v17 v17 종목명 칸 바탕색을 없애고 신·뉴 네모 안에만 연한 색을 넣는다. 주문은 계속 비활성.
- 2026-11-10 v18 v18 현재가·거래대금은 콤마. 순매수는 ka10059 금액(백만원)을 억원으로 보여 준다. 주문은 계속 비활성.
- 2026-11-10 v19 v19 외국인·기관·연기금 칸. 연기금은 penfnd_etc. 이슈 색은 한 화면에서 겹치지 않는다. 주문은 계속 비활성.
- 2026-11-10 v20 v20 첫급등대비 칸을 최근1년으로 바꾼다. 기준은 일봉 저가. 첫급등 기록은 남긴다. 주문은 계속 비활성.
- 2026-11-11 v21 v21 일간% 20% 이상은 연빨강, 10% 이상은 연노랑. 다른 칸은 그대로. 주문은 계속 비활성.
- 2026-11-11 v22 v22 종목명 안의 항공은 우주항공이 아니다. 통합·유가는 이유 단어. 주문은 계속 비활성.
- 2026-11-11 v23 v23 급등주 탭 종목의 일·주·월봉을 차트 적재 대상에 넣는다. 주문은 계속 비활성.
- 2026-11-11 v24 v24 탭 표시는 0% 이상(보합 포함). 매수 진입은 10% 그대로. 주문은 계속 비활성.
- 2026-11-11 v25 v25 장중에도 급등주 일·주·월봉을 받고, 차트 조회는 시간 초과를 남긴다. 주문은 계속 비활성.
- 2026-11-11 v26 v26 관심종목을 클릭하면 차트 탭이 열리고 그 종목 차트를 보여 준다. 표 갱신은 탭을 열지 않는다. 주문은 계속 비활성.
- 2026-11-11 v27 v27 관심종목과 차트 사이를 드래그해 차트를 넓힐 수 있다. 주문은 계속 비활성.
- 2026-11-11 v28 v28 차트 금액축을 오른쪽에 둔다. 일·주·월봉 모두 같다. 주문은 계속 비활성.
- 2026-11-11 v29 v29 차트는 통합 봉과 RSI14, 최대 30년 확대. 매매 일봉은 그대로. 주문은 계속 비활성.
- 2026-11-11 v30 v30 차트 RSI 칸에 시그널 9과 골든·데드 크로스를 그린다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-11 v31 v31 차트에 5·20 이평 크로스, MACD, 20봉 고저 돌파를 그린다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-11 v32 v32 종목명 옆에서 이평·돌파·RSI·MACD 시그널 표시를 켜고 끈다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-14 v33 v33 차트에 눌림 표시를 넣는다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-14 v34 v34 일봉 차트에 ATR 손절·익절선을 그린다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-14 v35 v35 차트 시그널에 전체 체크를 둔다. 매수 조건은 그대로. 주문은 계속 비활성.
- 2026-11-14 v36 v36 ATR 손절·익절선을 차트 시리즈로 고쳐 일봉 위에 보이게 한다. 매수 조건은 그대로. 주문은 계속 비활성.

보유를 중단 버튼으로 자동 매도하지 않는다.
