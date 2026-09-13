# 장대양봉 2050이평매매

- **stratId**: `bullBodyMa2050_v1`
- **상태**: test
- **스펙**: `strategies/specs/bullBodyMa2050_v1.json`
- **구현**: `strategies/impl/bullBodyMa2050.py`
- **원천 봉**: 1분 (BT에서 일/5/15분 리샘플)
- **실주문**: 기본 OFF
- **검증**: `val static bullBodyMa2050_v1` / `bt list` 번호 확인 후 `val {n} watch …`

## 1. 개요

키움 조건식으로 **장대·대금·거래량·20/50 골든** 종목을 고른 뒤,  
**일봉 20·60 단순이평 터치** 확인 후 **5분 첫 양봉**에 고정 금액 매수.  
**매수 당일 15:18 전량매도** (기본 익절/청산).

## 2. 대상종목 (Universe)

조건식: **P and J and R and (S or T)**

| 기호 | 조건 (키움식) | BT 근사 |
|------|----------------|---------|
| P | 1일전 거래대금(백만) 10000~999999999 | 분→일 합산 `qty×close/1e6` ≥ `minAmtMn` |
| J | [일]1봉전 양봉 몸통 | 전일 close > open |
| R | [일]1봉전 = 20봉 중 신고거래량 | 전일 qty = max(직전 20봉 qty) |
| S | [일]0봉전 (종가1)×(종가20) 골든크로스 | 당일 종가 SMA20 상향 돌파 |
| T | [일]1봉전 (종가1)×(종가50) 골든크로스 | 전일 종가 SMA50 상향 돌파 |

- 실전: HTS 조건검색/실시간 조건으로 선정 → 관심·워치 적재 후 본 전략 모니터 권장  
- BT: 분봉 집계 일봉으로 위 조건 근사 (`universePrefiltered=true` 시 셋업 0건이어도 워치 가정 가능)

## 3. 매수

1. 셋업 성립 후 `monitorDays`(기본 10) 이내  
2. **일봉 단순이평 SMA20 또는 SMA60 터치**  
   - 우선순위: SMA20 터치 우선 (`preferSmaA`)  
   - 터치 시 레벨 고정 → 5분 모니터  
3. **5분봉**에서 해당 이평을 포함하는 **첫 양봉** 종가 매수  
4. 금액: `buyAmtWon` 기본 **1,000,000원** (변경 가능)  
5. 진입 시각 ≥ 15:18 이면 신규 금지  

수량: `floor(buyAmtWon / price)`, 1주 미만이면 스킵.

## 4. 익절 (당일 전량)

- 매수 **당일** `exitEodTm` (**15:18:00**) 이후 **전량 매도**  
- reason: `eod_1518_flat`  
- 1주 잔여 없음 (전량)

## 5. (옵션) 15분 이평 익절

- `use15mTp=true` 일 때만: 15분 음봉 종가 < 터치 이평 → 전량  
- **기본 false** (현재 명세 미사용)

## 6. 파라미터

| 변수 | 기본 | 설명 |
|------|------|------|
| smaTouchA | 20 | 일 터치 이평 A |
| smaTouchB | 60 | 일 터치 이평 B |
| smaFast / smaMid | 20 / 50 | 조건 S / T |
| minAmtMn | 10000 | P 최소 대금(백만) |
| volLookback | 20 | R 거래량 창 |
| entryBarMin | 5 | 매수 봉 |
| exitBarMin | 15 | (옵션) 15분 익절 봉 |
| buyAmtWon | 1000000 | 매수 금액(원) |
| exitEodTm | 151800 | 매수당일 전량 익절 |
| use15mTp | false | 15분 이평 하회 익절 ON/OFF |
| monitorDays | 10 | 셋업 후 모니터 일수 |
| universePrefiltered | true | 외부 유니버스 시 셋업 완화 |
| preferSmaA | true | 20선 터치 우선 |

## 7. 상태 머신

```
IDLE → ARMED(셋업) → WAIT_5M(일 터치) → IN_POSITION
  IN_POSITION → IDLE (매수당일 15:18 전량)
```
## 8. 검증 방법

```text
bt list
val static bullBodyMa2050_v1
# 관심종목 유니버스 (USE_BT=Y) — 번호는 bt list 기준
val bullBodyMa2050_v1 watch 20260701 20260731 1000
# 단일 종목 (1분 적재 필요, 워밍업 권장 60거래일+)
bt bullBodyMa2050_v1 005930 20260701 20260731 1000
```

- Static: 명세·스펙·impl·엔진 등록  
- Verdict: `utils/stratValidate` 게이트  
- 데이터: 1분봉 충분 + 일 60봉 이상 기간 권장  

## 9. 리스크·주의

- 조건식 **대금**은 분봉 근사 — 실전은 HTS 대금과 차이 가능  
- 당일 SMA 터치: 장중 레인지 vs 전일 SMA 위주 (룩어헤드 최소화)  
- 매수 당일 15:18 전량 청산 → 오버나잇 없음  
- 5분·15분 리샘플 경계는 키움 분봉과 1~수 분 어긋날 수 있음  
- **실주문 연동 전** 모의·VAL 게이트 PASS 권장  

## 10. 실매매 확장 (후속)

1. 조건검색 결과 → `AI_WATCH` / 전용 테이블 적재  
2. 실시간 5·15분 또는 1분 집계 + 주문 모듈  
3. `buyAmtWon` 설정 JSON 연동  

현재 `enabled=true`, `status=test`, 자동 실주문 없음.
