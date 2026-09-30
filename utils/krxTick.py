"""KRX 주식 호가단위. 지정가 주문을 이평값에 맞출 때 사용."""

import math


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


_TICK_EPS = 1e-9


def floorToKrxTick(px):
    """호가단위로 내림 (지정가 매수용 — 의도가보다 비싸게 사지 않는다). 0 이하는 0."""
    try:
        raw = float(px)
    except (TypeError, ValueError):
        return 0
    if raw <= 0:
        return 0
    tick = krxTickSize(raw)
    n = int(math.floor(raw / float(tick) + _TICK_EPS)) * tick
    return max(1, int(n))


def ceilToKrxTick(px):
    """호가단위로 올림 (지정가 매도용 — 목표 수익률 이상 유지). 0 이하는 0."""
    try:
        raw = float(px)
    except (TypeError, ValueError):
        return 0
    if raw <= 0:
        return 0
    tick = krxTickSize(raw)
    n = int(math.ceil(raw / float(tick) - _TICK_EPS)) * tick
    # 구간 경계(1,999.5→2,000 등)는 상위 구간 호가의 배수라 그대로 유효
    return max(1, int(n))


def limitOrdPx(px, side):
    """
    지정가 주문단가를 유효 호가로 맞춘다.
    side: "BUY"(매수, 내림) / "SELL"(매도, 올림). 0 이하·파싱 불가면 0.
    """
    s = str(side or "").strip().upper()
    if s in ("SELL", "S", "매도"):
        return ceilToKrxTick(px)
    return floorToKrxTick(px)


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
