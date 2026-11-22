"""PBK01 KODEX200 병행. REG01 상승장만 여유 현금을 069500에 둔다.

하락장은 전량 현금이다. CD금리 ETF는 쓰지 않는다.
주문 함수는 없고, 모의 사이클이 이 계획만 받아 paper 호스트로 낸다.
"""

from __future__ import annotations

KODEX = "069500"
SWITCH_COST = 0.001
PAUSE_RET = 0.03
PAUSE_DAYS = 60


def _ymd(bar):
    raw = (bar or {}).get("tradeDt")
    if raw is None:
        raw = (bar or {}).get("TRADE_DT")
    text = str(raw or "").replace("-", "")
    return text[:8]


def _close(bar):
    try:
        num = float((bar or {}).get("close") if (bar or {}).get("close") is not None else (bar or {}).get("CLOSE_PRC"))
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _open(bar):
    try:
        num = float((bar or {}).get("open") if (bar or {}).get("open") is not None else (bar or {}).get("OPEN_PRC"))
    except (TypeError, ValueError):
        return None
    if num is None or num <= 0:
        return _close(bar)
    return num


def _sorted(bars):
    rows = [bar for bar in (bars or []) if len(_ymd(bar)) == 8]
    rows.sort(key=_ymd)
    return rows


def regimeToday(kospiBars, asOf):
    """오늘 적용 REG01. ON이면 상승, OFF이면 하락. 월말 10개가 없으면 빈 문자열."""
    from utils.marketRegime import reg01Signal

    side = reg01Signal(kospiBars, asOf)
    if side == "ON":
        return "상승"
    if side == "OFF":
        return "하락"
    return ""


def priorCloses(bars, asOf):
    """asOf 당일 전 종가. 당일 봉은 넣지 않는다."""
    day = str(asOf or "")[:8]
    out = []
    for bar in _sorted(bars):
        ymd = _ymd(bar)
        if day and ymd >= day:
            continue
        px = _close(bar)
        if px is not None:
            out.append(px)
    return out


def kodexReturn(bars, asOf, days=PAUSE_DAYS):
    """전일까지 days 거래일 수익률. 봉이 부족하면 None."""
    closes = priorCloses(bars, asOf)
    n = int(days)
    if n <= 0 or len(closes) <= n:
        return None
    base = closes[-1 - n]
    last = closes[-1]
    if base <= 0:
        return None
    return last / base - 1.0


def pauseNewEntries(ret, regime):
    """상승장이고 전일까지 60일 수익률이 +3% 이상이면 신규 1차를 쉰다."""
    if regime != "상승" or ret is None:
        return False
    return float(ret) >= PAUSE_RET


def sessionPx(bars, asOf, field):
    """당일 봉의 시가 또는 종가. 없으면 None."""
    day = str(asOf or "")[:8]
    found = None
    for bar in _sorted(bars):
        if _ymd(bar) == day:
            found = bar
    if found is None:
        return None
    return _open(found) if field == "open" else _close(found)


def lastClose(bars, asOf):
    closes = priorCloses(bars, asOf)
    if closes:
        return closes[-1]
    day = str(asOf or "")[:8]
    for bar in reversed(_sorted(bars)):
        if not day or _ymd(bar) <= day:
            px = _close(bar)
            if px:
                return px
    return None


def sharesForCash(cash, px, buy=True):
    """전환비용 0.1%를 넣은 수량. 매수는 비용만큼 덜 사고, 매도는 필요 대금을 맞춘다."""
    try:
        money = float(cash)
        price = float(px)
    except (TypeError, ValueError):
        return 0
    if money <= 0 or price <= 0:
        return 0
    if buy:
        return int(money // (price * (1.0 + SWITCH_COST)))
    return int((money / (price * (1.0 - SWITCH_COST))) + 0.999999)


def tradeValue(qty, px, buy=True):
    gross = int(qty) * float(px)
    if buy:
        return int(gross * (1.0 + SWITCH_COST) + 0.999999)
    return int(gross * (1.0 - SWITCH_COST))


def reservedLeg2(positions, slotKrw=6_000_000):
    """아직 안 산 2차 배정. 1.5배 한도에서 뺀다."""
    from strategies.impl.pullbackTwoStep import legMoney, scaledSlot

    total = 0
    for pos in positions or []:
        if not isinstance(pos, dict) or not pos.get("leg1"):
            continue
        leg2 = pos.get("leg2") if isinstance(pos.get("leg2"), dict) else {}
        filled = int(leg2.get("filledQty") or 0)
        remain = int(leg2.get("remainQty") or 0)
        if leg2.get("settled") and filled > 0 and remain <= 0:
            continue
        scale = pos.get("slotBoost") if pos.get("slotBoost") not in (None, "") else 1
        total += legMoney(scaledSlot(slotKrw, scale), 40)
    return total


def parkRoom(cash, kodexQty, kodexPx, reserved):
    """현금 + KODEX 평가×(1-0.1%) - 미체결 2차. 음수는 0."""
    try:
        liquid = float(cash or 0) + int(kodexQty or 0) * float(kodexPx or 0) * (1.0 - SWITCH_COST)
        liquid -= float(reserved or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, int(liquid))


def _order(side, qty, px, when, method, reason):
    return {
        "stkCd": KODEX,
        "stkNm": "KODEX 200",
        "side": side,
        "qty": int(qty),
        "limitPx": int(px),
        "fillPx": float(px),
        "when": when,
        "method": method,
        "reason": reason,
        "trdeTp": "0",
    }


def planParkOrders(park, regime, cash, openPx, closePx, pbkBuys=None, pbkSells=None):
    """하루 KODEX 계획. 하락장이면 보유를 시가에 모두 판다.

    PBK 매수는 현금 먼저, 부족분만 시가에 KODEX를 판다.
    PBK 매도 대금은 상승장일 때만 종가에 다시 산다.
    """
    state = dict(park or {})
    qty = max(0, int(state.get("kodexQty") or 0))
    left = max(0, int(cash or 0))
    orders = []
    bull = regime == "상승"
    bear = regime == "하락"
    label = "상승" if bull else ("하락" if bear else "")
    prev = str(state.get("regime") or "")
    if not state.get("started"):
        state["started"] = True
        if bull and openPx and left > 0:
            q = sharesForCash(left, openPx, buy=True)
            if q > 0:
                orders.append(_order("BUY", q, openPx, "시가", "시작일 시가", "최초KODEX"))
                left -= tradeValue(q, openPx, buy=True)
                qty += q
    elif label and prev and prev != label and openPx:
        if bear and qty > 0:
            orders.append(_order("SELL", qty, openPx, "시가", "전환 첫 거래일 시가", "REG01하락"))
            left += tradeValue(qty, openPx, buy=False)
            qty = 0
        elif bull and left > 0:
            q = sharesForCash(left, openPx, buy=True)
            if q > 0:
                orders.append(_order("BUY", q, openPx, "시가", "전환 첫 거래일 시가", "REG01상승"))
                left -= tradeValue(q, openPx, buy=True)
                qty += q
    if label:
        state["regime"] = label
    for buy in pbkBuys or []:
        need = max(0, int(buy.get("spend") or 0))
        use = min(left, need)
        left -= use
        short = need - use
        if short <= 0 or not openPx or qty <= 0:
            continue
        sellQty = min(qty, sharesForCash(short, openPx, buy=False))
        if sellQty <= 0:
            continue
        orders.append(_order("SELL", sellQty, openPx, "시가", "지정가체결직후 KODEX 시가매도", "PBK매수자금"))
        left += tradeValue(sellQty, openPx, buy=False)
        qty -= sellQty
    if bull and closePx:
        for sell in pbkSells or []:
            proceeds = max(0, int(sell.get("proceeds") or 0))
            q = sharesForCash(proceeds, closePx, buy=True)
            if q <= 0:
                continue
            orders.append(_order("BUY", q, closePx, "종가", "매도대금 종가 재매수", "PBK매도재매수"))
            qty += q
    state["kodexQty"] = qty
    return {"orders": orders, "park": state, "cash": left}


def describePark(kodexBars, kospiBars, asOf, park=None, markPx=None):
    """화면 세 줄. 주문은 하지 않는다."""
    regime = regimeToday(kospiBars, asOf) or "미정"
    ret = kodexReturn(kodexBars, asOf)
    pause = pauseNewEntries(ret, regime)
    qty = max(0, int((park or {}).get("kodexQty") or 0))
    px = markPx if markPx else lastClose(kodexBars, asOf)
    try:
        value = int(qty * float(px)) if px else 0
    except (TypeError, ValueError):
        value = 0
    retTxt = "-" if ret is None else f"{ret * 100.0:+.1f}%"
    pauseTxt = "쉬기" if pause else "매수"
    regimeTxt = regime if regime in ("상승", "하락") else "미정"
    return (
        f"REG01 {regimeTxt}\n"
        f"KODEX 60일 {retTxt} · {pauseTxt}\n"
        f"KODEX {qty:,}주 · {value:,}원"
    )


def prepareParkContext(now=None, kospiBars=None, kodexBars=None, park=None, positions=None, cash=None):
    """사이클이 읽는 레짐·쉬기·1.5배 한도. 시세가 없으면 쉬지 않고 한도도 두지 않는다."""
    from datetime import datetime

    moment = now or datetime.now()
    day = moment.strftime("%Y%m%d")
    state = dict(park or {})
    if kospiBars is None or kodexBars is None:
        kospiBars, kodexBars, state = _loadBars(day, state, kospiBars, kodexBars)
    regime = regimeToday(kospiBars, day)
    ret = kodexReturn(kodexBars, day)
    pause = pauseNewEntries(ret, regime)
    px = sessionPx(kodexBars, day, "open") or lastClose(kodexBars, day)
    room = None
    if cash is not None and px:
        room = parkRoom(cash, state.get("kodexQty"), px, reservedLeg2(positions))
    return {
        "regime": regime,
        "ret": ret,
        "pause": pause,
        "skipped": list(state.get("skipped") or []),
        "room": room,
        "open": sessionPx(kodexBars, day, "open"),
        "close": sessionPx(kodexBars, day, "close"),
        "park": state,
        "kodexBars": kodexBars,
        "kospiBars": kospiBars,
    }


def _loadBars(day, state, kospiBars, kodexBars):
    if kospiBars is None:
        try:
            from utils.kospiIdxLoad import loadStoredKospiBars

            kospiBars = loadStoredKospiBars(day) or []
        except Exception:
            kospiBars = []
    if kodexBars is None:
        try:
            from utils.chartBarLoad import loadStoredDayBars

            kodexBars = loadStoredDayBars(KODEX) or []
        except Exception:
            kodexBars = []
    if not state:
        try:
            from utils.pbk01Store import latestByKind, pbkPath

            saved = latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}
            state = dict(saved.get("park") or {})
        except Exception:
            state = {}
    return kospiBars or [], kodexBars or [], state
