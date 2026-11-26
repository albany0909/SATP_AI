"""
MMG01(수동정찰 종가 피라미딩) 지표·판단 — 순수 함수 (백테스트 bt4/bt5 와 같은 규칙).

bars: 오래된→최신 순 [{dt, open, high, low, close, vol}] (마지막 행 = 판단일, 장중이면 현재가 봉)
"""

from __future__ import annotations

import math

# 최종 설정 (F60|earn + 래칫 R) 기본값 — spec params 로 덮어쓴다
DEFAULTS = {
    "catStopPct": 8.0,          # 장중 재앙손절 (평단 대비 %)
    "atrK": 2.0,                # 추가 후 손절 = 평단 − k·ATR
    "chandAtr": 3.0,            # EOD 전량: 종가 < 고점 − 3·ATR
    "ratchetGainPct": 50.0,     # +50% 이후
    "ratchetKeepPct": 75.0,     # 손절 = max(평단, 고점×0.75)
    "breakevenGainPct": 10.0,   # +10% 이후 손절 ≥ 평단
    "addMinGainPct": 5.0,       # 추가: 평단 대비 +5% 이상
    "addGapDays": 2,            # 추가 간격 (거래일)
    "maxAdds": 3,
    "tranchePct": [40, 35, 25],
    "riskPerAddPct": 0.5,       # 추가 1회 위험 (자산 대비 %)
    "reentryBanDays": 60,
    "scoutMaxPx": 300000,
    "minVal20": 5e9,            # 20일 평균 거래대금
    "minPx": 1000,
}


def _sma(xs, n, end=None):
    end = len(xs) if end is None else end
    if end < n or n <= 0:
        return None
    seg = xs[end - n:end]
    return sum(seg) / float(n)


def _maxv(xs, a, b):
    seg = xs[max(0, a):b]
    return max(seg) if seg else None


def _minv(xs, a, b):
    seg = xs[max(0, a):b]
    return min(seg) if seg else None


def calcInd(bars):
    """
    마지막 봉 기준 지표. 데이터 부족이면 None.
    필요한 봉: 최소 201개 (SMA200 + 직전값). 52주 고가는 200봉 이상이면 계산.
    """
    if not bars or len(bars) < 201:
        return None
    c = [float(b["close"]) for b in bars]
    h = [float(b["high"]) for b in bars]
    lo = [float(b["low"]) for b in bars]
    o = [float(b["open"]) for b in bars]
    v = [float(b.get("vol") or 0) for b in bars]
    n = len(bars)
    i = n - 1
    tr = [h[0] - lo[0]]
    for k in range(1, n):
        tr.append(max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1])))
    ind = {
        "dt": bars[-1].get("dt"),
        "close": c[i], "open": o[i], "high": h[i], "low": lo[i], "vol": v[i],
        "prevClose": c[i - 1], "prevHigh": h[i - 1],
    }
    for m in (10, 20, 50, 60, 150, 200):
        ind[f"s{m}"] = _sma(c, m)
        ind[f"s{m}Prev"] = _sma(c, m, end=n - 1)
    ind["s50Ago5"] = _sma(c, 50, end=n - 5)
    ind["s150Ago20"] = _sma(c, 150, end=n - 20)
    ind["s200Ago20"] = _sma(c, 200, end=n - 20)
    ind["atr"] = sum(tr[-14:]) / 14.0
    ind["v50"] = _sma(v, 50, end=n - 1)           # 직전 50일 평균 (당일 제외)
    v50prev = _sma(v, 50, end=n - 1)
    ind["v5"] = _sma(v, 5)
    ind["hh20"] = _maxv(h, n - 21, n - 1)          # 직전 20일 고가
    ind["ll20"] = _minv(lo, n - 21, n - 1)
    lb = min(250, n - 1)
    ind["hi52"] = _maxv(h, n - 1 - lb, n - 1)
    ind["lo52"] = _minv(lo, n - 1 - lb, n - 1)
    vals = [c[k] * v[k] for k in range(n - 20, n)]
    ind["val20"] = sum(vals) / 20.0
    vr = (v[i] / v50prev) if v50prev else 0.0
    ind["vr"] = vr
    C = c[i]
    hh20, ll20 = ind["hh20"], ind["ll20"]
    s10, s20, s50, s60, s150 = ind["s10"], ind["s20"], ind["s50"], ind["s60"], ind["s150"]
    brk = bool(hh20 and C > hh20 and vr >= 1.5 and C <= hh20 * 1.05)
    ind["brk"] = brk
    ind["pb"] = bool(
        ((lo[i] <= s20 * 1.015) or (lo[i] <= s10 * 1.015))
        and C > o[i] and C > h[i - 1] and C > s20
    )
    ind["stage2"] = bool(C > s150 and ind["s150Ago20"] and s150 > ind["s150Ago20"] and s50 > s150)
    e1 = bool(C > s150 and c[i - 1] <= ind["s150Prev"] and vr >= 1.5
              and ind["s150Ago20"] and s150 >= ind["s150Ago20"] * 0.98)
    e2 = bool(brk and ind["hi52"] and C >= ind["hi52"] * 0.85 and C > s50
              and ll20 and (hh20 / ll20 - 1) <= 0.20)
    e3 = bool(s20 > s60 and ind["s20Prev"] <= ind["s60Prev"]
              and ind["v50"] and ind["v5"] >= 1.2 * ind["v50"] and C > s20)
    e4 = bool(ind["hi52"] and C >= ind["hi52"] * 0.95 and brk)
    ind["e1"], ind["e2"], ind["e3"], ind["e4"] = e1, e2, e3, e4
    ind["sigTags"] = [k for k, f in (("E1", e1), ("E2", e2), ("E3", e3), ("E4", e4)) if f]
    ind["liq"] = bool(ind["val20"] >= DEFAULTS["minVal20"] and C >= DEFAULTS["minPx"])
    ind["entrySig"] = bool(ind["sigTags"]) and ind["liq"]
    return ind


def baseStop(avg, p=None):
    p = p or DEFAULTS
    return float(avg) * (1.0 - float(p["catStopPct"]) / 100.0)


def updateStops(pos, ind, p=None):
    """EOD 손절 갱신 (본전·래칫). pos 는 dict 그대로 수정."""
    p = p or DEFAULTS
    avg = float(pos["avg"])
    pos["hi"] = max(float(pos.get("hi") or 0), float(ind["high"]))
    g = ind["close"] / avg - 1.0
    stop = float(pos.get("stop") or baseStop(avg, p))
    if g * 100.0 >= float(p["breakevenGainPct"]):
        stop = max(stop, avg)
    if g * 100.0 >= float(p["ratchetGainPct"]):
        stop = max(stop, avg, pos["hi"] * float(p["ratchetKeepPct"]) / 100.0)
    pos["stop"] = stop
    if ind.get("stage2"):
        pos["s2"] = True
    return pos


def bt4SameCloseFill(signalTiming="afterClose", fillMode="close"):
    """bt4/bt5 문서의 결정일 종가 체결.

    신호가 그날 종가를 본 뒤에만 성립하면 같은 종가 체결은 거부한다.
    15:15 장중 판단이면 signalTiming='intraday' 로 15:18 분봉을 고를 수 있다.
    기본 체결은 다음 거래일 시가다. 외부 mmgbt 스크립트는 이 저장소에 없다.
    """
    if str(fillMode or "") == "close" and str(signalTiming or "") != "intraday":
        return {
            "ok": False,
            "flag": "LOOKAHEAD",
            "reason": "결정일 종가 체결은 장마감 뒤에야 아는 신호에 쓸 수 없다",
        }
    return {"ok": True, "flag": "", "reason": ""}


def eodExitReason(pos, ind, p=None):
    """EOD 전량청산 사유 또는 None. (runA: 종가<SMA50 또는 SMA150, 고점−3ATR)"""
    p = p or DEFAULTS
    C = ind["close"]
    if C < ind["s50"] or C < ind["s150"]:
        return "EXIT_MA50"
    hi = max(float(pos.get("hi") or 0), float(ind["high"]))
    if C < hi - float(p["chandAtr"]) * ind["atr"]:
        return "EXIT_CHAND"
    return None


def intradayStopHit(pos, lastPx):
    """장중 손절 (추가매수 1회 이상 포지션만 — 백테스트와 동일: 1주 정찰은 장중 손절 제외)."""
    if int(pos.get("nAdds") or 0) <= 0:
        return False
    stop = float(pos.get("stop") or 0)
    return stop > 0 and float(lastPx) <= stop


def addPlan(pos, ind, ctx, p=None):
    """
    추가매수 계획. 반환 {qty, setup, stopAfter} 또는 None(+사유 문자열).
    ctx: {maxBuyAmt, equity, exposure, maxExposurePct, haltAdds, barsSinceAdd}
    """
    p = p or DEFAULTS
    n = int(pos.get("nAdds") or 0)
    if n >= int(p["maxAdds"]):
        return None, "MAX_ADDS"
    if ctx.get("haltAdds"):
        return None, "DAILY_LOSS_HALT"
    if int(ctx.get("barsSinceAdd") or 0) < int(p["addGapDays"]):
        return None, "GAP"
    C = ind["close"]
    avg = float(pos["avg"])
    if C < avg * (1.0 + float(p["addMinGainPct"]) / 100.0):
        return None, "GAIN_LT_MIN"
    if C <= float(pos.get("lastAddPx") or pos.get("entryPx") or 0):
        return None, "NOT_ABOVE_LAST"
    if not (pos.get("s2") or ind.get("stage2")):
        return None, "NOT_STAGE2"
    if not (C > ind["s50"] and ind["s50Ago5"] and ind["s50"] > ind["s50Ago5"]):
        return None, "MA50_GATE"
    setup = "BRK" if ind["brk"] else ("PB" if ind["pb"] else None)
    if not setup:
        return None, "NO_SETUP"
    maxAmt = float(ctx["maxBuyAmt"])
    amt = float(p["tranchePct"][n]) / 100.0 * maxAmt
    st = C - float(p["atrK"]) * ind["atr"]
    st = max(st, C * (1.0 - float(p["catStopPct"]) / 100.0))
    riskQ = (float(p["riskPerAddPct"]) / 100.0 * float(ctx["equity"])) / max(C - st, 1.0)
    room = maxAmt - float(pos.get("cost") or 0)
    expoRoom = float(ctx["equity"]) * float(ctx.get("maxExposurePct", 60)) / 100.0 - float(ctx.get("exposure") or 0)
    qty = int(math.floor(min(amt / C, riskQ, room / C, expoRoom / C)))
    if qty < 1:
        return None, "QTY_ZERO"
    newCost = float(pos.get("cost") or 0) + C * qty
    newQty = int(pos["qty"]) + qty
    newAvg = newCost / newQty
    stopAfter = max(float(pos.get("stop") or 0), newAvg * (1.0 - float(p["catStopPct"]) / 100.0),
                    newAvg - float(p["atrK"]) * ind["atr"])
    return {"qty": qty, "setup": setup, "stopAfter": stopAfter, "px": C}, ""


def entryAllowed(cd, ind, ctx, p=None):
    """자동정찰 진입 가능 여부 (신호·유동성·가격상한·실적·지수·재진입금지)."""
    p = p or DEFAULTS
    if not ind or not ind.get("entrySig"):
        return False, "NO_SIGNAL"
    if ind["close"] > float(p["scoutMaxPx"]):
        return False, "PX_GT_CAP"
    if not ctx.get("regimeOk"):
        return False, "REGIME"
    if not ctx.get("earnOk"):
        return False, "EARN"
    days = ctx.get("daysSinceExit")
    if days is not None and days < int(p["reentryBanDays"]):
        return False, "REENTRY_BAN"
    return True, ""
