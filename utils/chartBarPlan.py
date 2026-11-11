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
)


def chartSignalDefaults():
    """네 표시는 기본으로 켜 둔다."""
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


def filterPriceSignalMarks(rows, flags):
    """이평 삼각과 돌파 꺾쇠만 숨긴다. 입력 목록은 바꾸지 않는다."""
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
        elif row:
            kept.append(row)
    return kept


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
