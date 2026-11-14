"""차트 일·주·월봉 적재 계획. DB 와 키움 호출은 하지 않는다."""

from __future__ import annotations

from datetime import datetime, timedelta

from utils.stkCdUtil import normStkCd

CHART_PERIODS = ("day", "week", "month")
POLE_API = {
    "week": "ka10082",
    "month": "ka10083",
}
# 차트 조회·최신화가 이 시간을 넘기면 헤더를 실패로 바꾼다.
CHART_FETCH_TIMEOUT_SEC = 120
INTRADAY_CHART_REASONS = ("startup", "slot", "manual")


def chartFetchStartLine(code, kind):
    """메인 스레드에서 바로 남길 시작 로그."""
    return f"[deskChart] {kind} 시작 {code}"


def chartFetchExpired(startedMono, nowMono, timeoutSec=CHART_FETCH_TIMEOUT_SEC):
    try:
        return float(nowMono) - float(startedMono) >= float(timeoutSec)
    except (TypeError, ValueError):
        return False


def unifiedStkCd(code):
    """키움 SOR 통합 코드. 명세 예: 039490_AL. ka00001 이 아니다."""
    from utils.stkCdUtil import normStkCd

    text = normStkCd(code).upper()
    if text.endswith("_AL"):
        text = text[:-3]
    if text.endswith("_NX"):
        text = text[:-3]
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            break
    if len(digits) < 6:
        return ""
    return digits[-6:] + UNIFIED_STK_SUFFIX


def chartTailNeedsRefresh(maxDt, today):
    """마지막 봉이 없거나 오늘 이하면 꼬리를 다시 받는다. 장중 미완성 봉을 덮어쓴다."""
    day = str(today or "")[:8]
    last = str(maxDt or "")[:8]
    if len(day) != 8:
        return False
    if len(last) != 8:
        return True
    return last <= day


def applyChartLive(bars, liveYmd, px, tickYmd):
    """통합 현재가로 오늘 봉의 종가·고가·저가만 갱신한다. 시가는 유지한다.

    tick 날짜가 오늘이 아니거나 오늘 봉이 없으면 None. 가짜 도지는 만들지 않는다.
    """
    day = str(liveYmd or "")[:8]
    tick = str(tickYmd or "")[:8]
    if len(day) != 8 or tick != day or not bars:
        return None
    try:
        price = abs(float(px))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    out = [dict(bar) for bar in bars]
    last = out[-1]
    if str(last.get("tradeDt") or "")[:8] != day:
        return None
    last["close"] = price
    try:
        high = float(last.get("high") or 0)
    except (TypeError, ValueError):
        high = 0.0
    last["high"] = price if high <= 0 else max(high, price)
    try:
        low = float(last.get("low") or 0)
    except (TypeError, ValueError):
        low = 0.0
    last["low"] = price if low <= 0 else min(low, price)
    try:
        opened = float(last.get("open") or 0)
    except (TypeError, ValueError):
        opened = 0.0
    if opened <= 0:
        last["open"] = price
    return out


def unifiedLivePx(info, today):
    """오늘 0B 만. KRX·NXT 중 시각이 늦은 가격을 통합 현재가로 본다."""
    row = info or {}
    if str(row.get("tickYmd") or "")[:8] != str(today or "")[:8]:
        return None

    def _px(raw):
        try:
            val = abs(float(str(raw).replace(",", "").replace("+", "")))
        except (TypeError, ValueError):
            return None
        return val if val > 0 else None

    krxTm = str(row.get("krxTm") or "")
    nxtTm = str(row.get("nxtTm") or "")
    nxtPx = _px(row.get("nxtPrc"))
    krxPx = _px(row.get("krxPrc"))
    if nxtPx is not None and nxtTm and (not krxTm or nxtTm >= krxTm):
        return nxtPx
    if krxPx is not None:
        return krxPx
    if nxtPx is not None:
        return nxtPx
    return _px(row.get("curPrc"))


def nextShowCount(showN, total, zoomOut, step=20, minShow=20):
    """zoomOut 이면 더 긴 과거. 상한은 저장된 봉 수이며 없는 봉은 만들지 않는다."""
    try:
        shown = int(showN)
        bars = int(total)
        jump = max(int(step), max(1, shown // 10))
        floor = int(minShow)
    except (TypeError, ValueError):
        return 20
    if bars <= 0:
        return max(floor, 0)
    if zoomOut:
        return min(bars, shown + jump)
    return max(min(floor, bars), shown - jump)


def intradayChartWanted(reason):
    return str(reason or "") in INTRADAY_CHART_REASONS


# 차트 금액축. 국내 시세 화면처럼 오른쪽에 둔다.
CHART_PRICE_AXIS_RIGHT = True
# 차트 통합봉 조회·표시 상한. 상장이 짧거나 원천에 없으면 있는 봉만 쓴다.
CHART_LOOKBACK_DAYS = 365 * 30
UNIFIED_STK_SUFFIX = "_AL"
DESK_SPLIT_MIN_LEFT = 360
DESK_SPLIT_MIN_RIGHT = 300
DESK_SPLIT_MIN_CHART = 380
# 차트 시그널 표시. 세션 안에서만 켜고 끈다. 선과 칸은 남긴다.
CHART_SIGNAL_GROUPS = (
    ("ma", "이평크로스"),
    ("brk", "돌파"),
    ("rsi", "RSI크로스"),
    ("macd", "MACD크로스"),
    ("pull", "눌림"),
    ("atr", "ATR선"),
)
# 눌림 표시 기준. 매매 조건이 아니다.
PULLBACK_LOOKBACK = 20
PULLBACK_LEG_MULT = 1.15
PULLBACK_SMA_RISE = 5
PULLBACK_TOUCH_PCT = 0.015
PULLBACK_MIN_DOWN = 2
PULLBACK_MAX_RETRACE = 0.618
# 눌림 다음 봉 시가 기준 ATR 손절·익절. 차트 표시만. 일봉만.
ATR_PERIOD = 14
ATR_HOLD_BARS = 10
ATR_STOP_MULT = {5: 1.5, 10: 2.0, 20: 2.0}
ATR_TARGET_MULT = {5: 2.0, 10: 3.0, 20: 2.0}


def chartSignalDefaults():
    """시그널 표시는 기본으로 켜 둔다."""
    return {key: True for key, _label in CHART_SIGNAL_GROUPS}


def signalFlags(flags):
    """없는 키는 기본 켜짐. 모르는 키는 넣지 않는다."""
    out = chartSignalDefaults()
    if isinstance(flags, dict):
        for key, _label in CHART_SIGNAL_GROUPS:
            if key in flags:
                out[key] = bool(flags[key])
    return out


def setChartSignal(flags, key, checked):
    """한 그룹만 바꾼다. 나머지는 유지한다."""
    out = signalFlags(flags)
    if key in out:
        out[key] = bool(checked)
    return out


def chartSignalAllState(flags):
    """전체 체크. 여섯이 다 켜지면 checked, 다 꺼지면 unchecked, 섞이면 partial."""
    on = signalFlags(flags)
    values = [bool(on[key]) for key, _label in CHART_SIGNAL_GROUPS]
    if all(values):
        return "checked"
    if not any(values):
        return "unchecked"
    return "partial"


def applyAllChartSignals(turnOn):
    """여섯 표시를 같이 켜거나 끈다."""
    return {key: bool(turnOn) for key, _label in CHART_SIGNAL_GROUPS}


def nextAllChartSignals(flags):
    """전체 클릭. 전부 켜져 있으면 끄고, 꺼짐·섞임이면 전부 켠다."""
    return applyAllChartSignals(chartSignalAllState(flags) != "checked")


def filterPriceSignalMarks(rows, flags):
    """이평 삼각, 돌파 꺾쇠, 눌림만 숨긴다. 입력 목록은 바꾸지 않는다."""
    on = signalFlags(flags)
    kept = []
    for row in rows or []:
        kind = str((row or {}).get("kind") or "")
        if kind in ("maGolden", "maDead"):
            if on["ma"]:
                kept.append(row)
        elif kind in ("brkHigh", "brkLow"):
            if on["brk"]:
                kept.append(row)
        elif kind == "pullback":
            if on.get("pull"):
                kept.append(row)
        elif kind == "atr":
            if on.get("atr") and on.get("pull"):
                kept.append(row)
        elif row:
            kept.append(row)
    return kept


def _barNum(bar, key, fallback=0.0):
    try:
        return float((bar or {}).get(key) if (bar or {}).get(key) is not None else fallback)
    except (TypeError, ValueError):
        return float(fallback)


def pullbackMarks(bars):
    """차트 눌림. 봉마다 None 이거나 {"line": 5|10|20}.

    상승: 종가 > SMA20, SMA20 이 5봉 전보다 높고, 최근 20봉 최고가가
    그 고점 이전 저가의 1.15배 이상.
    터치: 저가가 SMA5·10·20 의 ±1.5% 안이고 종가는 그 선 이상. 가장 가까운 선을 적는다.
    눌림: 최근 스윙 고점 뒤, 표시 봉 전에 종가가 내린 봉이 2개 이상.
    거래량: 그 하락 봉 평균이 상승 다리 평균보다 작거나, 표시 봉 거래량이 20봉 평균 미만.
    확인: 양봉(종가>시가) 이거나 아래꼬리 >= 몸통.
    되돌림이 상승 폭의 61.8% 를 넘으면 제외. 같은 스윙 고점에는 한 번만.
    """
    from strategies.indicators import calcSmaSeries

    rows = list(bars or [])
    n = len(rows)
    out = [None] * n
    if n == 0:
        return out
    opens, highs, lows, closes, qtys = [], [], [], [], []
    for bar in rows:
        close = _barNum(bar, "close", 0.0)
        openPx = _barNum(bar, "open", close)
        high = _barNum(bar, "high", max(openPx, close))
        low = _barNum(bar, "low", min(openPx, close))
        opens.append(openPx)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        qtys.append(_barNum(bar, "qty", 0.0))
    sma = {
        5: calcSmaSeries(closes, 5),
        10: calcSmaSeries(closes, 10),
        20: calcSmaSeries(closes, 20),
    }
    vol20 = calcSmaSeries(qtys, 20)
    lastSwing = None
    look = PULLBACK_LOOKBACK
    rise = PULLBACK_SMA_RISE
    band = PULLBACK_TOUCH_PCT
    for i in range(n):
        if sma[20][i] is None or i < rise or sma[20][i - rise] is None:
            continue
        if closes[i] <= sma[20][i] or sma[20][i] <= sma[20][i - rise]:
            continue
        start = max(0, i - look)
        if i <= start:
            continue
        swingAt = start
        for j in range(start + 1, i):
            if highs[j] >= highs[swingAt]:
                swingAt = j
        down = 0
        for j in range(swingAt + 1, i):
            if closes[j] < closes[j - 1]:
                down += 1
        if down < PULLBACK_MIN_DOWN:
            continue
        legLow = min(lows[start:swingAt + 1])
        swing = highs[swingAt]
        if legLow <= 0 or swing < legLow * PULLBACK_LEG_MULT:
            continue
        span = swing - legLow
        if span <= 0 or (swing - lows[i]) / span > PULLBACK_MAX_RETRACE:
            continue
        best = None
        bestDist = None
        for period in (5, 10, 20):
            level = sma[period][i]
            if level is None or level <= 0:
                continue
            if lows[i] > level * (1.0 + band) or lows[i] < level * (1.0 - band):
                continue
            if closes[i] < level:
                continue
            dist = abs(lows[i] - level) / level
            if bestDist is None or dist < bestDist:
                bestDist = dist
                best = period
        if best is None:
            continue
        dipped = qtys[swingAt + 1:i]
        climbed = qtys[start:swingAt + 1]
        dipAvg = (sum(dipped) / len(dipped)) if dipped else qtys[i]
        climbAvg = (sum(climbed) / len(climbed)) if climbed else 0.0
        dry = dipAvg < climbAvg or (vol20[i] is not None and qtys[i] < vol20[i])
        if not dry:
            continue
        body = abs(closes[i] - opens[i])
        wick = min(opens[i], closes[i]) - lows[i]
        if not (closes[i] > opens[i] or wick >= body):
            continue
        if lastSwing is not None and swingAt == lastSwing:
            continue
        out[i] = {"line": best}
        lastSwing = swingAt
    return out


def atrWilderSeries(bars, period=ATR_PERIOD):
    """Wilder ATR. 전일 종가가 있는 봉부터 TR. 첫 14개 평균은 인덱스 period, 이후 RMA."""
    rows = list(bars or [])
    n = len(rows)
    out = [None] * n
    p = int(period or 0)
    if p < 1 or n <= p:
        return out
    trs = [None] * n
    prevClose = _barNum(rows[0], "close", 0.0)
    for i in range(1, n):
        high = _barNum(rows[i], "high", _barNum(rows[i], "close", 0.0))
        low = _barNum(rows[i], "low", _barNum(rows[i], "close", 0.0))
        close = _barNum(rows[i], "close", 0.0)
        trs[i] = max(high - low, abs(high - prevClose), abs(low - prevClose))
        prevClose = close
    seedAt = p
    if seedAt >= n or any(trs[j] is None for j in range(1, seedAt + 1)):
        return out
    atr = sum(trs[1:seedAt + 1]) / float(p)
    out[seedAt] = atr
    for i in range(seedAt + 1, n):
        atr = (atr * (p - 1) + trs[i]) / float(p)
        out[i] = atr
    return out


def _atrBarHit(bar, stop, target):
    """같은 봉에서 손절과 익절이 같이 닿으면 손절. 시가 갭도 체결로 본다."""
    close = _barNum(bar, "close", 0.0)
    opened = _barNum(bar, "open", close)
    high = _barNum(bar, "high", max(opened, close))
    low = _barNum(bar, "low", min(opened, close))
    stopGap = opened <= stop
    targetGap = opened >= target
    if stopGap and targetGap:
        return "stop"
    if stopGap:
        return "stop"
    if targetGap:
        return "target"
    stopTouch = low <= stop
    targetTouch = high >= target
    if stopTouch and targetTouch:
        return "stop"
    if stopTouch:
        return "stop"
    if targetTouch:
        return "target"
    return None


def atrStopPlans(bars, marks, barTp="day"):
    """눌림 봉의 ATR14 로 손절·익절. 주·월은 빈 목록.

    진입은 다음 봉 시가. 눌림5 는 1.5/2, 눌림10 은 2/3, 눌림20 은 2/2 배.
    보유는 진입 봉 포함 10봉. 마지막 봉이 신호면 종가를 예상 진입으로 둔다.
    """
    if str(barTp or "day") != "day":
        return []
    rows = list(bars or [])
    flags = list(marks or [])
    n = len(rows)
    if n == 0:
        return []
    atr = atrWilderSeries(rows, ATR_PERIOD)
    plans = []
    for i, mark in enumerate(flags):
        if not mark or i >= n:
            continue
        try:
            line = int(mark.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if line not in ATR_STOP_MULT or atr[i] is None:
            continue
        width = float(atr[i])
        if width <= 0:
            continue
        stopMult = ATR_STOP_MULT[line]
        targetMult = ATR_TARGET_MULT[line]
        if i == n - 1:
            entry = _barNum(rows[i], "close", 0.0)
            if entry <= 0:
                continue
            plans.append({
                "signal": i,
                "line": line,
                "atr": width,
                "entry": entry,
                "stop": entry - stopMult * width,
                "target": entry + targetMult * width,
                "entryBar": None,
                "endBar": i,
                "exitBar": None,
                "outcome": None,
                "provisional": True,
                "extend": False,
                "label": False,
            })
            continue
        entryBar = i + 1
        entry = _barNum(rows[entryBar], "open", _barNum(rows[entryBar], "close", 0.0))
        if entry <= 0:
            continue
        stop = entry - stopMult * width
        target = entry + targetMult * width
        lastHold = min(n - 1, entryBar + ATR_HOLD_BARS - 1)
        outcome = None
        exitBar = None
        for j in range(entryBar, lastHold + 1):
            hit = _atrBarHit(rows[j], stop, target)
            if hit:
                outcome = hit
                exitBar = j
                break
        plans.append({
            "signal": i,
            "line": line,
            "atr": width,
            "entry": entry,
            "stop": stop,
            "target": target,
            "entryBar": entryBar,
            "endBar": exitBar if exitBar is not None else lastHold,
            "exitBar": exitBar,
            "outcome": outcome,
            "provisional": False,
            "extend": False,
            "label": False,
        })
    focus = None
    for plan in reversed(plans):
        if plan["provisional"]:
            focus = plan
            break
        if plan["outcome"] is None and plan["entryBar"] is not None:
            if (n - 1) - int(plan["entryBar"]) < ATR_HOLD_BARS:
                focus = plan
                break
    if focus is not None:
        focus["extend"] = True
        focus["label"] = not focus["provisional"]
    return plans


def filterAtrPlans(plans, flags):
    """ATR선 또는 눌림이 꺼지면 선을 숨긴다. 키가 없으면 기본은 켜짐."""
    on = signalFlags(flags)
    if not on.get("atr") or not on.get("pull"):
        return []
    return list(plans or [])


def atrLineSpan(plan, visStart, visCount):
    """화면에 걸치는 ATR 선의 x 구간. 화면 밖이고 연장도 아니면 None.

    x 는 보이는 봉의 값축. 왼쪽 끝은 -0.5, 오른쪽 끝은 봉 수 - 0.5.
    """
    try:
        count = int(visCount)
        origin = int(visStart)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    start = (plan or {}).get("entryBar")
    if start is None:
        start = (plan or {}).get("signal")
    end = (plan or {}).get("endBar")
    if start is None or end is None:
        return None
    try:
        start = int(start)
        end = int(end)
    except (TypeError, ValueError):
        return None
    view1 = origin + count - 1
    right = float(count) - 0.5
    if (plan or {}).get("extend"):
        x2 = right
    else:
        if end < origin or start > view1:
            return None
        x2 = min(right, float(end - origin))
    x1 = -0.5 if start < origin else float(start - origin)
    if x2 < x1:
        return None
    return x1, x2


def priceSpanForPlans(lo, hi, plans, visStart, visCount):
    """화면에 그리는 ATR 가격만 축에 넣는다. 과거 손절이 차트를 납작하게 만들지 않는다."""
    try:
        low = None if lo is None else float(lo)
        high = None if hi is None else float(hi)
    except (TypeError, ValueError):
        low, high = None, None
    for plan in plans or []:
        if atrLineSpan(plan, visStart, visCount) is None:
            continue
        for key in ("stop", "target", "entry"):
            try:
                val = float(plan.get(key))
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            low = val if low is None else min(low, val)
            high = val if high is None else max(high, val)
    return low, high


def filterCrossMarks(marks, shown):
    """끄면 자리 수는 유지하고 표시만 지운다."""
    seq = list(marks or [])
    if shown:
        return seq
    return [None] * len(seq)


def deskSideSplitSizes(total, saved=None, chartOpen=False):
    """관심종목(왼쪽)과 차트(오른쪽) 너비. 저장값이 있으면 그 비율을 유지한다."""
    try:
        width = int(total or 0)
    except (TypeError, ValueError):
        width = 0
    minRight = DESK_SPLIT_MIN_CHART if chartOpen else DESK_SPLIT_MIN_RIGHT
    minLeft = DESK_SPLIT_MIN_LEFT
    if width <= 0:
        return [minLeft, minRight]
    if width <= minLeft:
        return [width, 0]
    if width < minLeft + minRight:
        return [minLeft, width - minLeft]

    pair = None
    if isinstance(saved, (list, tuple)) and len(saved) == 2:
        try:
            left0 = int(saved[0])
            right0 = int(saved[1])
        except (TypeError, ValueError):
            left0 = right0 = 0
        if left0 >= minLeft and right0 >= minRight:
            pair = (left0, right0)
    if pair:
        span = pair[0] + pair[1]
        left = int(round(width * pair[0] / span))
    elif chartOpen:
        right = max(minRight, int(width * 0.62))
        left = width - min(right, width - minLeft)
    else:
        right = min(minRight + 120, width - minLeft)
        left = width - max(minRight, right)
    left = max(minLeft, min(left, width - minRight))
    return [left, width - left]


def chartActionForWatchPick(userPick, code, currentCode="", refreshBusy=False, syncBusy=False, chartOpen=False):
    """관심종목 행 선택 때 차트 탭을 열지 결정한다.

    반환: open(탭을 연다), reload(이미 열린 차트만 바꾼다), ignore.
    표 갱신·동기 재선택은 userPick 이 아니거나 busy 이므로 탭을 열지 않는다.
    """
    if refreshBusy or syncBusy or not userPick:
        return "ignore"
    if not str(code or "").strip():
        return "ignore"
    if chartOpen:
        if str(code) == str(currentCode or ""):
            return "ignore"
        return "reload"
    return "open"


def mergeLoadCodes(*groups):
    """순위·관심·급등 코드를 한 목록으로. 먼저 들어온 순서를 유지한다."""
    out = []
    seen = set()
    for group in groups:
        for raw in group or []:
            if isinstance(raw, dict):
                raw = raw.get("stkCd") or raw.get("STK_CD") or raw.get("code")
            cd = normStkCd(raw)
            if cd and cd not in seen:
                seen.add(cd)
                out.append(cd)
    return out


def codesFromSurgeRows(rows):
    """급등주 탭에 남는 종목만. 탭 필터(+5%)를 다시 쓰지 않고 이미 걸러진 행도 받는다."""
    from trading.surgeTradeData import tabRankRows

    kept = tabRankRows(rows or [])
    return mergeLoadCodes(kept.get("rows") or [])


def seriesFor(bundle, period):
    """차트 주기에 해당하는 봉만."""
    key = str(period or "day")
    if key not in CHART_PERIODS:
        key = "day"
    data = bundle or {}
    rows = data.get(key)
    return list(rows or [])


def needsAutoFetch(bundle, period):
    """그 주기 봉이 없으면 일·주·월을 함께 받는다."""
    key = str(period or "day")
    if key not in CHART_PERIODS:
        return False
    return len(seriesFor(bundle, key)) == 0


def liveTickRedraws(period):
    """1초 현재가 갱신은 일봉 화면만 다시 그린다."""
    return str(period or "day") == "day"


def poleFetchDecision(stat, today, barTp, refetch=False):
    """이미 최신이면 skip, 아니면 fetch. 실패로 보지 않는다."""
    if refetch:
        return "fetch"
    info = stat or {}
    maxDt = str(info.get("maxDt") or "")[:8]
    cnt = int(info.get("cnt") or 0)
    if cnt <= 0 or len(maxDt) != 8:
        return "fetch"
    day = str(today or "")[:8]
    try:
        latest = datetime.strptime(maxDt, "%Y%m%d")
        now = datetime.strptime(day, "%Y%m%d") if len(day) == 8 else datetime.now()
    except ValueError:
        return "fetch"
    gap = (now - latest).days
    if barTp == "week" and cnt >= 8 and gap <= 10:
        return "skip"
    if barTp == "month" and cnt >= 6 and gap <= 40:
        return "skip"
    return "fetch"


def collectSurgeChartCds(now=None, directory=None):
    """급등주 탭에 보이는 종목. 장 시작 전에는 전일 목록. 폴더가 없으면 빈 목록."""
    from pathlib import Path

    now = now or datetime.now()
    if directory is None:
        try:
            from utils.appPaths import configDataDir

            directory = configDataDir() / "surgeTrade"
        except Exception:
            return []
    root = Path(directory)
    if not root.is_dir():
        return []
    try:
        from trading.surgeTradeData import SnapStore, selectTabDisplay

        picked = selectTabDisplay(SnapStore(root), now)
        snap = (picked or {}).get("snap") or {}
        return codesFromSurgeRows(snap.get("rows") or [])
    except Exception:
        return []
