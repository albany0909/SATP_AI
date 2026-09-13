# 정배열 이평선 돌파매매1

- **stratId**: `alignMaBreak_v1`
- **상태**: test
- **스펙**: `strategies/specs/alignMaBreak_v1.json`
- **권장 타임프레임**: 1분봉
- **작성일**: 2026-08-06
- **실주문**: 기본 OFF (백테스트·`val` 검증용, `USE_TRADE` 자동 ON 없음)

## 1. 개요

실시간 조회순위(또는 그로부터 적재된 관심종목) 종목 중 **양봉·이평 정배열** 후보에 대해,
장 마감 직전(**15:15 이후**) **20이평 터치 후 양봉 상향 돌파** 시 고정 금액 진입.
이후 (**15:18 이후**) 60/20이평 **음봉 하향 돌파**로 손절·익절(1주 잔여 원칙).

## 2. 대상종목 (Universe)

1. **실시간순위** 검색(적재) 종목 — 운영상 `AI_WATCH_STK`(RANK 동기화, `USE_BT=Y`) 또는
   장마감 `AI_DAY_INQ_RANK`(당일누적)와 동일 유니버스로 검증
2. **양봉만**: `close > open` (후보 필터 및 진입 봉 공통)
3. **정배열**: `MA20 > MA50 > MA100`  
   - 기본 이평 종류: **EMA** (`params.maType=ema`, 지표 모듈과 동일)

> 단일 종목 `bt` 시에는 유니버스 필터를 엔진 밖에서 종목선택으로 대체한다.
> 배치 `val … watch` 는 관심종목(순위 기반) 대상.

## 3. 필요 지표

| 지표 | 기본 | 용도 |
|------|------|------|
| MA/EMA 20 | maFast | 진입 터치·돌파 / 익절 이탈 |
| MA/EMA 50 | maMid | 정배열 |
| MA/EMA 100 | maSlow | 정배열 |
| MA/EMA 60 | exitStopMa | 손절 이탈 |

## 4. 매수 시점

**모두 AND** (1분봉 종가 기준):

1. **시각** ≥ `buyAfterTm` (기본 **15:15:00** → `151500`)
2. **정배열** 유지: `emaFast > emaMid > emaSlow`
3. **양봉**: `close > open`
4. **20이평 터치**: `low <= emaFast` (해당 봉 저가가 20선 이하 접촉)
5. **양봉 상향 돌파**: `close > emaFast`  
   - 권장 보강: 직전 종가가 20선 아래·근접했던 경우(직전 `close <= prevEmaFast`)를 돌파로 인정 — 구현은
     `requireCross=true` 시 직전 대비 상향 교차 강제, 기본 `true`

**주문 규모**: `buyAmtWon` (기본 **10,000,000원** = 1,000만원)  
수량 ≈ `floor(buyAmtWon / 종가)` (백테스트 통계는 수익률 기반 단순 왕복)

**제한**: 일 1회 진입(`maxEntriesPerDay=1`). 잔여 1주 보유 중 재진입 없음.

## 5. 손절 시점

**모두 AND** (보유 중):

1. **시각** ≥ `exitAfterTm` (기본 **15:18:00** → `151800`)
2. **음봉**: `close < open`
3. **60이평 음봉 하향 돌파**: `close < emaStop`  
   - 터치 보강: `high >= emaStop` (위에서 아래로 돌파) 또는 직전 종가 ≥ 직전 60이평

→ **한 주 남기고 전량 매도** (`leaveQty=1`)  
→ BT 단순화: 체결 이벤트 1건 SELL, P&L은 **전량 청산 가정**(잔여 1주 슬리피지 무시). 실주문 시 leaveQty 적용.

## 6. 익절 시점

**모두 AND** (보유 중, 미손절):

1. **시각** ≥ `exitAfterTm` (기본 15:18)
2. **수익 중**: `close > entryPrice` (또는 평가손익 > 0)
3. **음봉**: `close < open`
4. **20이평 음봉 하향 돌파**: `close < emaFast` (+ 직전 상회 교차 권장)

→ 손절과 동일하게 **1주 남기고 전량 매도**.  
→ 동일 봉에 손절·익절 동시 성립 시 **손절 우선**.

## 7. 파라미터

| 변수 | 기본 | 설명 |
|------|------|------|
| maType | ema | ema \| sma (sma는 추후) |
| maFast | 20 | |
| maMid | 50 | |
| maSlow | 100 | |
| exitStopMa | 60 | 손절 이평 |
| buyAfterTm | "151500" | 매수 허용 시각 |
| exitAfterTm | "151800" | 청산 허용 시각 |
| buyAmtWon | 10000000 | 매수 금액(원) |
| leaveQty | 1 | 청산 시 잔여 주수 |
| requireCross | true | 이평 상·하향 교차 강제 |
| maxEntriesPerDay | 1 | |
| useBullFilter | true | 진입 봉 양봉 필수 |

## 8. 백테스트 가정

- 데이터: `AI_MIN_STK_PRC` 1분봉, 워밍업 최소 100봉+
- 체결가: 조건 충족 봉 **종가**
- 수수료·세금: 1단계 0
- 15:15~15:20 구간 봉이 비면 신호 없음 (데이터 공백)
- 강제청산: 시뮬 기간 종료 시 잔여 포지션 종가 청산 (`force_close_eod`)

## 9. 검증 (`val` / Cursor Skill)

```text
val static alignMaBreak_v1
val alignMaBreak_v1 watch {시작} {종료} [투자금만원]
bt 2 삼성전자 20260701 20260731 1000
```

- Static: docs / specs / impl / engine 연결
- BT 게이트: `utils/stratValidate.py` 기본 임계

## 10. 리스크·주의

- 초단 구간(약 5분) → 신호 희소·표본 작음. `CONDITIONAL` 판정 가능
- 순위 종목·양봉은 **선별 단계**이고, 진입은 분봉 조건이 추가로 필요
- 실주문 연동 전 모의·`val` 통과 권장. `status=live` / 자동매매는 컨펌 후

## 11. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-08-06 | v1 명세·스펙·구현·BT/`val` 연동 |
