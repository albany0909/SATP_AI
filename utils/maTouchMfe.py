"""
이평터치 매수 후 청산 전 최대수익률(MFE).

1차 절반익절(halfTpPct) 후보를 고르기 위해 고가/평단을 남긴다.
"""


def pnlPctFromAvg(px, avgPx):
    """현재가의 평단 대비 수익률%."""
    try:
        avg = float(avgPx or 0)
        p = float(px or 0)
    except (TypeError, ValueError):
        return None
    if avg <= 0 or p <= 0:
        return None
    return (p / avg - 1.0) * 100.0


def noteMaxPnl(rec, px, tm=""):
    """
    보유 중 고점 수익률을 rec 에 갱신한다.
    매수 직후는 0% 로 시작한다. 갱신되면 True.
    """
    if not rec:
        return False
    avgPx = rec.get("avgPx")
    pct = pnlPctFromAvg(px, avgPx)
    if pct is None:
        return False
    try:
        p = float(px)
    except (TypeError, ValueError):
        return False
    prev = rec.get("maxPnlPct")
    try:
        prevN = float(prev) if prev is not None else None
    except (TypeError, ValueError):
        prevN = None
    if prevN is not None and pct <= prevN + 1e-9:
        return False
    rec["maxPnlPct"] = pct
    rec["maxPx"] = p
    if tm:
        rec["maxPnlTm"] = str(tm)
    return True


def initMaxPnl(rec, fillPx, tm=""):
    """매수 체결 직후 고점을 평단(체결가) 0% 로 둔다."""
    if not rec:
        return
    try:
        p = float(fillPx or rec.get("avgPx") or 0)
    except (TypeError, ValueError):
        p = 0.0
    rec["maxPnlPct"] = 0.0
    rec["maxPx"] = p if p > 0 else rec.get("maxPx")
    if tm:
        rec["maxPnlTm"] = str(tm)
