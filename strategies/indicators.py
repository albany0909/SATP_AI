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


def rsiCrossMarks(rsiValues, signalValues):
    """골든/데드 크로스. 현재 봉이 시그널을 넘어간 경우만.

    골든: 직전 RSI <= 직전 시그널 이고 현재 RSI > 현재 시그널.
    데드: 직전 RSI >= 직전 시그널 이고 현재 RSI < 현재 시그널.
    닿기만 하고 넘지 않으면 None. 값이 없으면 None.
    """
    rsi = list(rsiValues or [])
    sig = list(signalValues or [])
    n = max(len(rsi), len(sig))
    out = [None] * n
    for i in range(1, n):
        prevR = rsi[i - 1] if i - 1 < len(rsi) else None
        curR = rsi[i] if i < len(rsi) else None
        prevS = sig[i - 1] if i - 1 < len(sig) else None
        curS = sig[i] if i < len(sig) else None
        if prevR is None or curR is None or prevS is None or curS is None:
            continue
        if prevR <= prevS and curR > curS:
            out[i] = "golden"
        elif prevR >= prevS and curR < curS:
            out[i] = "dead"
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
