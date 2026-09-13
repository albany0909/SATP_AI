"""단순이동평균(SMA) 계산 유틸"""

# 이동평균 기간 (일)
SMA_PERIODS = (5, 10, 20, 60, 120, 240)


def calcSma(closePrices, period):
    """
    최근 period개 종가의 단순이동평균.
    closePrices: 최신→과거 또는 과거→최신 모두 가능하나,
                 이 함수는 '앞쪽 period개'를 사용하므로
                 호출부에서 최신→과거 순으로 넘기는 것을 전제로 한다.
    데이터 부족 시 None.
    """
    if period <= 0:
        return None
    if not closePrices or len(closePrices) < period:
        return None
    window = closePrices[:period]
    return sum(window) / float(period)


def calcSmaSet(closePrices, periods=None):
    """
    여러 기간 SMA를 dict로 반환.
    예: {5: 70100.0, 10: 69850.0, ..., 240: None}
    키는 int period, 값은 float 또는 None.
    """
    usePeriods = periods if periods is not None else SMA_PERIODS
    result = {}
    for n in usePeriods:
        result[int(n)] = calcSma(closePrices, int(n))
    return result


def barsToClosePrices(bars):
    """
    fetchDayBars 결과 bars([{curPrc, dt, ...}, ...]) → 종가 리스트.
    최신→과거 순을 유지한다.
    """
    prices = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        prc = bar.get("curPrc")
        if prc is None:
            continue
        try:
            prices.append(float(prc))
        except (TypeError, ValueError):
            continue
    return prices


def formatSmaVal(val, digits=2):
    """표시용. None이면 '-'."""
    if val is None:
        return "-"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "-"
    # 원 단위는 정수에 가깝면 콤마, 아니면 소수
    if abs(num - round(num)) < 1e-6:
        return f"{int(round(num)):,}"
    return f"{num:,.{digits}f}"
