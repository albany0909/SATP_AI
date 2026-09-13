"""KRX 주식 호가단위. 지정가 주문을 이평값에 맞출 때 사용."""


def krxTickSize(px):
    """
    국내주식 호가단위(원).
    2천 미만 1, 2천~5천 미만 5, 5천~2만 미만 10, 2만~5만 미만 50,
    5만~20만 미만 100, 20만~50만 미만 500, 50만 이상 1000.
    """
    try:
        p = abs(float(px))
    except (TypeError, ValueError):
        return 1
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def roundToKrxTick(px):
    """가격을 가장 가까운 호가단위로 맞춘다. 1원 미만은 1."""
    try:
        raw = float(px)
    except (TypeError, ValueError):
        return 0
    if raw <= 0:
        return 0
    tick = krxTickSize(raw)
    if tick <= 1:
        return max(1, int(round(raw)))
    n = int(round(raw / float(tick))) * tick
    return max(tick, int(n))
