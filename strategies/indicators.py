"""지표 계산 헬퍼 (EMA, SMA, RSI, Williams Fractal)"""


def calcEmaSeries(closes, period):
    """
    종가 리스트 → EMA 리스트 (동일 길이).
    초기는 SMA로 시드 후 EMA. period 미만은 None.
    """
    n = len(closes)
    out = [None] * n
    if period <= 0 or n == 0:
        return out
    k = 2.0 / (period + 1.0)
    # 첫 period개 SMA
    if n < period:
        return out
    seed = sum(closes[:period]) / float(period)
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = closes[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def calcSmaSeries(closes, period):
    """
    종가 리스트 → 단순이동평균 SMA (동일 길이).
    period 미만 구간은 None.
    """
    n = len(closes)
    out = [None] * n
    if period <= 0 or n == 0 or n < period:
        return out
    # 누적합으로 O(n)
    run = 0.0
    for i in range(n):
        run += float(closes[i] or 0)
        if i >= period:
            run -= float(closes[i - period] or 0)
        if i >= period - 1:
            out[i] = run / float(period)
    return out


def calcRsiSeries(closes, period=14):
    """
    종가 리스트 → RSI 리스트 (동일 길이). Wilder 평활 — 웹 차트 RSI와 동일 계산.
    첫 period개는 단순평균으로 시드하며 그 이전 구간은 None.
    """
    n = len(closes)
    out = [None] * n
    p = max(2, int(period or 14))
    if n <= p:
        return out

    sumGain = 0.0
    sumLoss = 0.0
    for i in range(1, p + 1):
        diff = float(closes[i] or 0) - float(closes[i - 1] or 0)
        if diff >= 0:
            sumGain += diff
        else:
            sumLoss -= diff
    avgGain = sumGain / p
    avgLoss = sumLoss / p
    out[p] = 100.0 if avgLoss == 0 else 100.0 - 100.0 / (1.0 + avgGain / avgLoss)

    for i in range(p + 1, n):
        diff = float(closes[i] or 0) - float(closes[i - 1] or 0)
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avgGain = (avgGain * (p - 1) + gain) / p
        avgLoss = (avgLoss * (p - 1) + loss) / p
        out[i] = 100.0 if avgLoss == 0 else 100.0 - 100.0 / (1.0 + avgGain / avgLoss)
    return out


def rsiSignalSeries(rsiValues, period=9):
    """RSI 시그널. 키움 eavg(지수이동평균): 첫 period 개 단순평균 시드, 이후 승수 2/(period+1).

    앞쪽 None 은 건너뛰고, 그 다음 연속된 RSI 값에만 calcEmaSeries 를 적용한다.
    유효 RSI 가 period 미만이면 전부 None. 봉을 만들지 않는다.
    """
    values = list(rsiValues or [])
    n = len(values)
    out = [None] * n
    p = int(period or 0)
    if p <= 0 or n == 0:
        return out
    start = 0
    while start < n and values[start] is None:
        start += 1
    tail = []
    for raw in values[start:]:
        if raw is None:
            break
        tail.append(float(raw))
    ema = calcEmaSeries(tail, p)
    for i, val in enumerate(ema):
        out[start + i] = val
    return out


def seriesCrossMarks(fastValues, slowValues):
    """빠른 선이 느린 선을 넘은 봉만. 닿기만 하면 None.

    골든: 직전 빠른 값 <= 직전 느린 값 이고 현재 빠른 값 > 현재 느린 값.
    데드: 직전 빠른 값 >= 직전 느린 값 이고 현재 빠른 값 < 현재 느린 값.
    """
    fast = list(fastValues or [])
    slow = list(slowValues or [])
    n = max(len(fast), len(slow))
    out = [None] * n
    for i in range(1, n):
        prevF = fast[i - 1] if i - 1 < len(fast) else None
        curF = fast[i] if i < len(fast) else None
        prevS = slow[i - 1] if i - 1 < len(slow) else None
        curS = slow[i] if i < len(slow) else None
        if prevF is None or curF is None or prevS is None or curS is None:
            continue
        if prevF <= prevS and curF > curS:
            out[i] = "golden"
        elif prevF >= prevS and curF < curS:
            out[i] = "dead"
    return out


def rsiCrossMarks(rsiValues, signalValues):
    """RSI 가 시그널을 넘은 봉. seriesCrossMarks 와 같다."""
    return seriesCrossMarks(rsiValues, signalValues)


def macdSeries(closes, fast=12, slow=26, signal=9):
    """MACD 12·26·9. 선은 EMA(fast)-EMA(slow), 시그널은 그 선의 eavg.

    느린 EMA 가 생기기 전에는 None. 봉을 만들지 않는다.
    """
    vals = []
    for raw in closes or []:
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            vals.append(0.0)
    fastE = calcEmaSeries(vals, int(fast or 12))
    slowE = calcEmaSeries(vals, int(slow or 26))
    macd = [None] * len(vals)
    for i in range(len(vals)):
        if fastE[i] is None or slowE[i] is None:
            continue
        macd[i] = fastE[i] - slowE[i]
    return macd, rsiSignalSeries(macd, int(signal or 9))


def breakoutMarks(highs, lows, lookback=20):
    """현재 봉을 뺀 직전 구간 고가·저가 돌파. 직전이 없으면 None.

    고가 돌파: 현재 고가 > 직전 고가의 최대. 저가 돌파: 현재 저가 < 직전 저가의 최소.
    직전이 lookback 보다 짧으면 있는 봉만 본다. 같으면 돌파가 아니다.
    """
    hi = list(highs or [])
    lo = list(lows or [])
    n = max(len(hi), len(lo))
    out = [None] * n
    span = int(lookback or 0)
    if span < 1 or n == 0:
        return out
    for i in range(n):
        start = max(0, i - span)
        prevH = []
        prevL = []
        for j in range(start, i):
            if j < len(hi) and hi[j] is not None:
                prevH.append(float(hi[j]))
            if j < len(lo) and lo[j] is not None:
                prevL.append(float(lo[j]))
        highBrk = False
        lowBrk = False
        if i < len(hi) and hi[i] is not None and prevH and float(hi[i]) > max(prevH):
            highBrk = True
        if i < len(lo) and lo[i] is not None and prevL and float(lo[i]) < min(prevL):
            lowBrk = True
        if highBrk and lowBrk:
            out[i] = "both"
        elif highBrk:
            out[i] = "high"
        elif lowBrk:
            out[i] = "low"
    return out


def isDownFractal(lows, idx, left=2, right=2):
    """
    idx 시점이 다운 프랙탈(매수 신호)인지.
    확정은 idx+right 봉까지 본 뒤에만 True로 쓸 것 — 호출측에서 확인.
    lows[idx]가 좌우 대비 최저.
    """
    if idx < left or idx + right >= len(lows):
        return False
    pivot = lows[idx]
    if pivot is None:
        return False
    for k in range(1, left + 1):
        if lows[idx - k] is None or lows[idx - k] <= pivot:
            return False
    for k in range(1, right + 1):
        if lows[idx + k] is None or lows[idx + k] <= pivot:
            return False
    return True
