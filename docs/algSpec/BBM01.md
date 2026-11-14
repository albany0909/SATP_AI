# 장대양봉 2050이평매매 (BBM01)

## 쉽게 말하면

긴 양봉이 20일·50일선과 함께 나온 종목을 삽니다. 익절 문장은 rules.takeProfit 에 있습니다. 손절 가격 예시는 스펙에 없어 불명확합니다. 거래가 몰린 상승일에 가깝습니다.

- 전략ID: `bullBodyMa2050_v1`
- 버전: 1
- 상태: test
- 명세 enabled: True
- 주기: 1

## 목적

status=test. Live OFF. 익절=매수당일 15:18 전량.

## 대상 종목

```
{
  "type": "kiwoomCondition",
  "formula": "P and J and R and (S or T)",
  "note": "키움 조건식: 1봉전 대금≥1만백만·양봉·20봉신고거래량 + (당일 SMA1×20 또는 전일 SMA1×50 골든). BT는 일봉 근사. 실전은 조건검색 연동 권장."
}
```

## 진입

- entry: after setup, day SMA20|60 touch → first 5m bull on that ma → buyAmtWon

## 청산·손절

- takeProfit: buy day exitEodTm(151800) full flat

## 수량·금액

```
{
  "paperOnly": true,
  "maType": "sma",
  "smaTouchA": 20,
  "smaTouchB": 60,
  "smaFast": 20,
  "smaMid": 50,
  "minAmtMn": 10000,
  "volLookback": 20,
  "entryBarMin": 5,
  "exitBarMin": 15,
  "buyAmtWon": 1000000,
  "exitEodTm": "151800",
  "use15mTp": false,
  "maxEntriesPerDay": 1,
  "monitorDays": 10,
  "universePrefiltered": true,
  "preferSmaA": true
}
```

## 시간

```
{
  "minAmtMn": 10000,
  "exitEodTm": "151800"
}
```

## 데이터

스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.

```
{
  "type": "kiwoomCondition",
  "formula": "P and J and R and (S or T)",
  "note": "키움 조건식: 1봉전 대금≥1만백만·양봉·20봉신고거래량 + (당일 SMA1×20 또는 전일 SMA1×50 골든). BT는 일봉 근사. 실전은 조건검색 연동 권장."
}
```

## 버전 이력

문서·DB 이력이 없다.

보유를 중단 버튼으로 자동 매도하지 않는다.
