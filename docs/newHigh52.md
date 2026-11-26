# 52주 신고가 최초봉

차트 표시용이다. 매매 주문에 연결하지 않는다. 검증 봇과 재무 봇의 백테스트도 이 함수를 그대로 호출한다.

## 함수

```python
from utils.newHigh52 import firstNewHighIndices, newHighLookback, loadNewHigh52Options

idx = firstNewHighIndices(bars, lookback=250, gapBars=20, basis="close")
```

- `firstNewHighIndices(bars, lookback=250, gapBars=20, basis="close") -> list[int]`
- `bars`: 오래된 봉이 앞. 숫자면 종가. dict 면 `close` 또는 `high`.
- `newHighLookback("day")` 는 250, `"week"` 는 52, `"month"` 는 `None`(표시 안 함).
- `loadNewHigh52Options()` 는 설정이 있으면 읽고, 없으면 기본값만 돌려준다. 파일을 만들지 않는다.

## 판정

그 봉의 가격이 **그 봉을 뺀 직전 lookback개** 가격의 최고보다 크면 신고가다. 기본 가격은 종가다. `basis="high"` 이면 고가다. 고가가 없거나 0이면 그 봉의 종가를 고가 대신 쓴다.

직전 봉이 lookback개보다 적거나, 그 구간에 가격이 없는 봉이 있으면 그 봉은 표시하지 않는다. 일봉 lookback 은 250, 주봉은 52다.

연속 신고가, 또는 직전 신고가와의 사이에 신고가가 아닌 봉이 `gapBars`개보다 적으면 같은 구간이다. **그 구간의 첫 봉만** 최초봉이다.

끊김: 마지막 신고가 봉 다음부터 신고가가 아닌 봉이 `gapBars`개 이상 이어진 뒤의 다음 신고가는 새 최초봉이다. 기본 `gapBars` 는 20이다. 그 사이에 신고가가 한 번이라도 나오면 그 봉이 새 마지막 신고가가 되고, 20봉 카운트는 다시 시작한다.

예. 마지막 신고가가 100번 봉이고 `gapBars` 가 20이면, 101번부터 120번까지 신고가가 없어야 한다. 121번 이후의 첫 신고가가 새 최초봉이다. 110번이 다시 신고가면 최초봉이 아니고, 그 봉부터 20봉을 다시 센다.

미래 봉은 보지 않는다. i번 봉은 `bars[i]` 와 `bars[i-lookback:i]` 만 비교한다.

## 설정 키

`settings.json` 을 이 기능이 고치지 않는다. 키가 없을 때의 기본값은 아래와 같다.

- `chartNewHigh52.gapBars`: 정수, 기본 20
- `chartNewHigh52.basis`: `close`(기본) 또는 `high`

일봉 250, 주봉 52는 주기로 고정한다. 월봉은 표시하지 않는다.
