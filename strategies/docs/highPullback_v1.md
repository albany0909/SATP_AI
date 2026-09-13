# 신고가 눌림목매매1

- **stratId**: `highPullback_v1`
- **상태**: test
- **스펙**: `strategies/specs/highPullback_v1.json`
- **권장 타임프레임**: 1분봉
- **작성일**: 2026-08-08
- **실주문**: 기본 OFF (`USE_TRADE` 자동 ON 없음)
- **관련**: `ntl first`(ka10016+일봉), `AI_DAY_STK_PRC` 수급

## 1. 개요

코스피·코스닥 종목 중 **최초 52주 신고가**를 **10% 이상 장대 양봉·거래량 급증·외국인/기관 매수**와 함께 기록한 뒤,
이후 **15:15 이후 20이평 터치(눌림)** 시 고정 금액 매수.
15:18 이후 60/20이평 음봉 하향 돌파로 손절·익절(1주 잔여).

## 2. 대상종목 (Universe)

| # | 조건 | 구현/운영 |
|---|------|-----------|
| 1 | 국내 **코스피·코스닥** | 시장 필터 (ETF/ETN/신주인수권 등은 제외 권장) |
| 2 | **최초 52주 신고가 도달** | `ntl first` / `utils.ntlFirstEntry` (당일 신고 시작일) |
| 3 | **등락 ≥ 10% 장대 양봉** | 일봉 `fluRt≥setupMinFluRt` 및 양봉 |
| 4 | **거래량 평균 대비 과다** | 일 거래량 ≥ `volMaPeriod` 평균 × `volMult` |
| 5 | **기관 또는 외국인 매수** | 일봉 수급 `ORGN_NETPRPS>0` 또는 `FOR_NETPRPS`/`FRGN_VAL>0` (적재분) |

선별은 `val … watch` 실행 시 **자동**: `ntl first`(ka10016+일봉 최초 52주) + 등락≥10% + 코스피/코스닥
(+ 일봉 있으면 거래량·수급 필터, 없으면 완화 통과).  
**분봉 BT** 는 그 선정 종목에 대해 기간 내 눌림 진입을 검증한다.  
※ ka10016 은 현재 시점 스냅샷이므로, “과거 N개월 전수 최초신고일” 재구성은 아니다.

## 3. 필요 지표

| 지표 | 기본 | 용도 |
|------|------|------|
| EMA 20 | maFast | 눌림 터치·익절 |
| EMA 60 | exitStopMa | 손절 |
| (참고) EMA 50/100 | | 유니버스 문서용(필수 아님) |

## 4. 매수

**전제**: 해당 종목이 유니버스 셋업 성립 이후 모니터링 중 (`setup` 일자 ≤ 당일).

**AND**:

1. 시각 ≥ `buyAfterTm` (**15:15:00**)
2. **20이평 터치**: `low ≤ emaFast`
3. (옵션) 터치 봉 양봉 `useBullFilter`

**규모**: `buyAmtWon` 기본 **10,000,000원**  
**제한**: 일 1회 (`maxEntriesPerDay`), 포지션 1슬롯

## 5. 손절

시각 ≥ `exitAfterTm`(15:18) AND 음봉 AND **60이평 하향 돌파**  
→ leaveQty=1 (BT는 전량 청산 가정)

## 6. 익절

시각 ≥ 15:18 AND **수익 중** AND 음봉 AND **20이평 하향 돌파**  
→ 동일 leaveQty. 손절·익절 동시 → **손절 우선**

## 7. 파라미터

| 변수 | 기본 | 설명 |
|------|------|------|
| maFast | 20 | 눌림·익절 |
| exitStopMa | 60 | 손절 |
| buyAfterTm | 151500 | |
| exitAfterTm | 151800 | |
| buyAmtWon | 10000000 | |
| leaveQty | 1 | |
| setupMinFluRt | 10 | 셋업 장대 양봉 등락% |
| lookbackHigh | 250 | 52주(거래일) 근사 |
| volMaPeriod | 20 | 거래량 평균 일수 |
| volMult | 1.5 | 평균 대비 배수 |
| requireCross | true | 청산 시 이평 교차 강제 |
| universePrefiltered | true | BT에서 봉 셋업 불충분 시 외부 유니버스 가정 무장 |
| maxEntriesPerDay | 1 | |
| useBullFilter | false | 진입 터치 봉 양봉 강제(기본 off—명세는 터치 중심) |
| monitorDays | 20 | 셋업 후 모니터링 일수 상한(0=무제한) |

## 8. 백테스트 가정

- 1분봉 OHLC, 워밍업 60~100봉+
- 셋업 근사: 분봉을 **일 단위로 집계**해 최초 고점·등락·거래량 조건 검사  
  (수급·정확한 코스피필터는 DB/선별 단계)
- `universePrefiltered=true` 면 일봉 셋업이 안 잡혀도 모니터링 무장(단일 bt용)
- 체결: 종가 / 수수료 0 / EOD force_close

## 9. 검증

```text
val static highPullback_v1
val highPullback_v1 watch {시작} {종료} [투자금]
bt highPullback_v1 {종목} {시작} {종료} 1000
ntl first
```

- Static: docs / specs / impl / engine
- 런타임 게이트: `utils/stratValidate.py`
- 유니버스 정합: 먼저 `ntl first`·일봉 수급 확인 권장

## 10. 리스크

- 분봉만으로는 수급·정확한 250일 고점이 왜곡될 수 있음 → 선별 분리
- 15:15~ 구간 신호 희소
- 실시간 운용 전 모의·`val` 통과 권장

## 11. 변경 이력

| 일자 | 내용 |
|------|------|
| 2026-08-08 | v1 명세·스펙·구현·BT/val 연동 |
