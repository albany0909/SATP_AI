# 시장 국면 배지

메인 화면 상단에만 보여 준다. 주문, 전략 스위치, 알고리즘 버전과는 연결되어 있지 않다.

입력은 KODEX200(`069500`) 일봉이다. 판정은 그 봉과 그 앞 봉만 사용한다. 장 마감(15:30) 이후 마지막 봉을 그날의 확정 판정으로 한 번 남긴다. 장중에는 전일까지 확정된 판정을 보여주고, 현재가는 `장중참고`로만 붙인다.

## 우선순위

1. 200일선, 20·60·120일선, 5거래일 전 60일선, ADX, +DI, -DI 중 하나라도 없으면 **판정불가**.
2. **ADX < 20 이면 횡보장.** 종가가 200일선 아래이고 60일선이 하락해도 하락장이 아니다. -DI가 +DI보다 큰 것만으로는 하락장을 만들지 않는다.
3. ADX가 20 이상일 때 **상승장**: 종가 > 200일선, 그리고 (20일선 > 60일선 > 120일선 또는 60일선 상승), 그리고 +DI > -DI.
4. ADX가 20 이상일 때 **하락장**: 종가 < 200일선, 그리고 (60일선 하락 또는 -DI > +DI).
5. 나머지는 **횡보장**.

60일선 상승은 오늘 60일선이 `chartMarketRegime.smaSlopeBars`(기본 5)거래일 전 60일선보다 높을 때다. 낮으면 하락이다. 같으면 둘 다 아니다.

이평은 단순이평이다. ADX와 DI는 Wilder 14다. ADX 기준 20은 `chartMarketRegime.adxMin` 으로 바꿀 수 있다. 이 코드는 `settings.json` 을 쓰지 않으며, 키가 없으면 기본값을 쓴다.

`chartMarketRegime.symbol`(기본 `069500`)은 장마감 일봉 적재 대상에 포함된다. 그 종목은 `AI_DAY_STK_PRC`에 없는 날짜만 INSERT 하고, 이미 있는 행은 고치지 않는다. `chartMarketRegime.autoBackfill`(기본 false)이 true이고 봉이 200개 미만이면 데스크를 열 때 한 번만 최근 12개월을 같은 방식으로 채운다.

판정이 안 되면 `[marketRegime] 판정 판정불가 <날짜> <사유> (069500 봉 N개)` 한 줄을 남긴다. 이 경우는 DB와 jsonl에 쓰지 않는다. 판정이 안 되어도 REG01 값이 있으면 보조줄에 `데이터 부족 · REG01 OFF`처럼 붙인다.

## 코스피 일봉

REG01 입력은 코스피 종합지수다. 키움 업종일봉 `ka20006`, 업종코드 `001`이다. 받은 봉은 기존 `AI_DAY_INDS_PRC`(`MKT_TP=KR`)에 없는 날짜만 `INSERT IGNORE` 한다. 있는 행은 수정하거나 삭제하지 않는다. 테이블이 없으면 테이블을 만들지 않고 `config/data/kospiDay.jsonl`에 없는 날짜만 붙인다.

장마감 후 관심·실계좌 일봉 적재가 돌 때 그 날짜 한 건만 추가한다. 봉이 부족하면 아래를 사람이 한 번 실행한다. 데스크가 자동으로 실행하지 않는다.

```python
from utils.kospiIdxLoad import loadKospiIdxDays
loadKospiIdxDays(months=24)
```

키움 HTTP 429는 일봉 조회와 같은 재시도(1.5초, 3초, 6초, 최대 3회)를 탄다. 조회 TR만 사용한다.

## 보조 표시

- REG01: 판정일 이전 달까지 확정된 마지막 월말(그 달의 마지막 거래일) 코스피 종가가, 그 월말을 포함한 최근 10개 월말 종가 평균보다 크면 ON, 아니면 OFF. 같으면 OFF. 10개가 없거나 진행 중인 달만 있으면 비운다. 판정일 이후 봉과 진행 중인 달은 쓰지 않는다. 매매로 이어지지 않는다. 배지 보조줄, `AI_MKT_REGIME.REG01`, jsonl에 같은 값을 넣는다. 이미 저장된 날짜 행은 고치지 않는다.
- ADX, +DI, -DI
- 최근 20일 수익률의 표본표준편차 × sqrt(252)
- 지금까지 고점(고가, 없으면 종가) 대비 마지막 종가
- 종가의 200일선 대비 거리(%)

## 11/16 장중 참고

KODEX200 79,625, 200일선 101,457, 20/60/120일선 86,631/99,493/109,866, ADX 27.8, +DI 17.4, -DI 31.5, 20일 변동성 59%, 고점 2026-06-22 147,847 대비 약 -46%, REG01 OFF. ADX가 20 이상이고 종가가 200일선 아래이며 60일선이 하락하므로 **하락장**이다.

## 저장

`sql/marketRegime.sql` 은 사람이 실행할 DDL이다. 앱은 테이블을 만들지 않는다. 테이블이 없으면 `config/data/marketRegime.jsonl` 에 날짜 한 줄씩 붙이고, DB는 건너뛴 뒤 `[marketRegime] DB 테이블 없음 — jsonl에만 기록` 한 줄을 남긴다. 테이블이 있으면 같은 내용을 INSERT 한다. 이미 있는 날짜는 고치거나 지우지 않는다.

## 로그

- 하루 판정: `[marketRegime] 판정 하락장 20261116 종가<200일선 · 60일선 하락 · ADX 27.8`
- 국면이 바뀔 때: `[marketRegime] 횡보장→하락장 20261116 종가<200일선 · 60일선 하락 · ADX 27.8`
- 바뀐 줄만 텔레그램으로 보낸다.

## 함수

```python
from utils.marketRegime import classifySnapshot, judgeBars, presentRegime, reg01Side, reg01Signal

classifySnapshot({
    "close": 79625, "sma200": 101457,
    "sma20": 86631, "sma60": 99493, "sma120": 109866, "sma60Prev": 100000,
    "adx": 27.8, "plusDi": 17.4, "minusDi": 31.5, "reg01": "OFF",
})
judgeBars(dailyBars)          # 마지막 봉까지
presentRegime(bars, today, nowHm, closeHm="153000", livePx=None)
reg01Side(monthEndCloses)     # 길이 10 미만이면 None
reg01Signal(kospiBars, asOf)  # 진행 중인 달은 빼고, 10개 미만이면 None
```
