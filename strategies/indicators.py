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
