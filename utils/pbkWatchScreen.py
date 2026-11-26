"""관심종목 화면은 PBK01 후보와 모의 보유만 보여 준다.

수집(일봉·주봉·월봉·국면·지수)은 이 목록을 쓰지 않는다.
AI_WATCH_STK 행은 지우지 않는다. 예전 목록은 json 으로 한 번만 백업한다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

COLLECTION_EXTRAS = ("069500", "001", "101", "201")


def watchScreenOn(settings=None):
    """모의이고 watchScreen 이 false 가 아니면 PBK 화면을 쓴다."""
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    mode = str(settings.get("deskTradeMode") or "paper").strip().lower()
    if mode in ("real", "live", "실전"):
        return False
    row = ((settings.get("stratLive") or {}).get("PBK01") or {})
    if isinstance(row, dict) and row.get("watchScreen") is False:
        return False
    return True


def _code(raw):
    from utils.stkCdUtil import normStkCd

    return normStkCd(raw) or ""


PBK_NEAR_PCT = 3.0
HOLD_BG = "#E8F5E9"
NEAR_BG = "#FFF59D"
TOUCH_BG = "#FFE0B2"
PBK_TONE_SORT_MS = 3000
TONE_FG = "#212121"


def pbkNearPct(settings=None):
    """인접 퍼센트. 키가 없으면 3. 파일을 쓰지 않는다."""
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    raw = settings.get("pbkNearPct") if isinstance(settings, dict) else None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return PBK_NEAR_PCT
    if val <= 0:
        return PBK_NEAR_PCT
    return val


def leg1Filled(row):
    """1차(5이평) 체결. 매매 슬롯의 leg1 과 같다."""
    if not isinstance(row, dict):
        return False
    leg = row.get("leg1")
    if isinstance(leg, dict):
        try:
            if int(leg.get("qty") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        return leg.get("fillPx") is not None or leg.get("px") is not None
    return bool(leg)


def nextBuyLine(row):
    """다음 매수 선. 미체결은 5이평, 1차 뒤에는 20이평."""
    if leg1Filled(row):
        return "ma20", (row or {}).get("ma20")
    return "ma5", (row or {}).get("ma5")


def _toneNum(raw):
    if raw is None or raw == "":
        return None
    try:
        num = abs(float(str(raw).replace(",", "").lstrip("+")))
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def pbkRowTone(row, price=None, low=None, nearPct=None):
    """보유는 연초록, 선 이하는 연주황, 위 0~임계는 연노랑.

    보유가 인접·터치보다 우선이다. 색이 없으면 bg 는 None.
    """
    pct = PBK_NEAR_PCT if nearPct is None else nearPct
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = PBK_NEAR_PCT
    if pct <= 0:
        pct = PBK_NEAR_PCT
    lineKey, line = nextBuyLine(row or {})
    linePx = _toneNum(line)
    px = _toneNum(price)
    lowPx = _toneNum(low)
    holding = bool((row or {}).get("holding")) or str((row or {}).get("role") or "") == "보유"
    if holding:
        tone = "hold"
    elif linePx is None:
        tone = "none"
    elif (px is not None and px <= linePx) or (lowPx is not None and lowPx <= linePx):
        tone = "touch"
    elif px is not None and linePx < px and (px - linePx) / linePx * 100.0 <= pct:
        tone = "near"
    else:
        tone = "none"
    bg = {"hold": HOLD_BG, "near": NEAR_BG, "touch": TOUCH_BG}.get(tone)
    return {
        "tone": tone,
        "bg": bg,
        "fg": TONE_FG if bg else None,
        "lineKey": lineKey,
        "line": linePx,
        "nearPct": pct,
    }


def _held(row):
    if not isinstance(row, dict):
        return False
    try:
        if int(row.get("qty") or row.get("qtyNum") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(row.get("leg1"))


def unionRows(candidates, positions, accountHolds):
    """후보와 보유(슬롯·모의 잔고)의 합집합. 보유면 구분은 보유, 아니면 후보."""
    by = {}

    def slot(code):
        row = by.get(code)
        if row is None:
            row = {
                "stkCd": code,
                "stkNm": code,
                "role": "",
                "candidate": False,
                "holding": False,
                "signalDt": "",
                "expireDt": "",
                "ma5": None,
                "ma20": None,
                "grpCd": "",
            }
            by[code] = row
        return row

    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        code = _code(item.get("stkCd"))
        if not code:
            continue
        row = slot(code)
        row["candidate"] = True
        name = str(item.get("stkNm") or item.get("name") or "").strip()
        if name:
            row["stkNm"] = name
        row["signalDt"] = str(item.get("signalDt") or "")
        row["expireDt"] = str(item.get("expireDt") or "")
        if item.get("ma5") is not None:
            row["ma5"] = item.get("ma5")
        if item.get("ma20") is not None:
            row["ma20"] = item.get("ma20")
        market = str(item.get("market") or item.get("marketName") or "")
        if market:
            row["market"] = market
        row["grpCd"] = row["grpCd"] or "PBK01"
    for item in list(positions or []) + list(accountHolds or []):
        if not _held(item):
            continue
        code = _code(item.get("stkCd") or item.get("STK_CD"))
        if not code:
            continue
        row = slot(code)
        row["holding"] = True
        if item.get("leg1"):
            row["leg1"] = item.get("leg1")
        if item.get("leg2"):
            row["leg2"] = item.get("leg2")
        if item.get("firstBuyDt"):
            row["firstBuyDt"] = str(item.get("firstBuyDt"))
        if item.get("exitDt"):
            row["exitDt"] = str(item.get("exitDt"))
        name = str(item.get("stkNm") or item.get("STK_NM") or item.get("name") or "").strip()
        if name:
            row["stkNm"] = name
        if not row["grpCd"]:
            row["grpCd"] = "PAPER" if item.get("leg1") is None and item.get("signalDt") is None else "PBK01"
    out = []
    for row in by.values():
        if row["holding"]:
            row["role"] = "보유"
        elif row["candidate"]:
            row["role"] = "후보"
        else:
            continue
        row["boost"] = ""
        if row["role"] == "후보":
            from strategies.impl.pullbackTwoStep import marketName, slotScale

            market = row.get("market") or marketName(row.get("stkCd"))
            if slotScale(market, row.get("ma5"), row.get("ma20")) == 1.5:
                row["boost"] = "1.5x"
        out.append(row)
    order = {"보유": 0, "후보": 1}
    out.sort(key=lambda row: (order.get(row["role"], 9), row["stkCd"]))
    return out


_screenFill = {}
_screenCodes = []
_screenLock = threading.Lock()


def screenFillSnapshot():
    """표시용 체결 수량. 0 은 전량 청산으로 기억한 종목."""
    with _screenLock:
        return dict(_screenFill)


def resetScreenFills():
    """테스트용. 표시 체결 기억을 비운다."""
    global _screenCodes
    with _screenLock:
        _screenFill.clear()
        _screenCodes = []


def _sideKind(sideLabel):
    text = str(sideLabel or "")
    if "매수" in text or text.strip().upper() in ("BUY", "B"):
        return "BUY"
    if "매도" in text or text.strip().upper() in ("SELL", "S"):
        return "SELL"
    return ""


def applyScreenFill(sideLabel, stkCd, qty=0, known=None):
    """표시용 체결. 매수는 보유, 매도는 전량 청산(0). 매매 상태 파일은 쓰지 않는다.

    known 이 있으면 그 코드(또는 이미 기억한 코드)만 반영한다.
    """
    code = _code(stkCd)
    kind = _sideKind(sideLabel)
    if not code or not kind:
        return None
    try:
        q = abs(int(qty or 0))
    except (TypeError, ValueError):
        q = 0
    if q <= 0:
        return None
    with _screenLock:
        if known is not None:
            allowed = {_code(item) for item in known if _code(item)}
            if code not in allowed and code not in _screenFill:
                return None
        if kind == "BUY":
            _screenFill[code] = int(_screenFill.get(code) or 0) + q
        else:
            _screenFill[code] = 0
        return int(_screenFill[code])


def mergeScreenFills(rows):
    """체결 기억을 구분에 반영한다.

    전량 청산(수량 0)이면 공식 후보일 때만 후보로 남기고, 아니면 뺀다.
    """
    by = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = _code(row.get("stkCd"))
        if not code:
            continue
        item = dict(row)
        item["stkCd"] = code
        by[code] = item
    with _screenLock:
        for code, qty in list(_screenFill.items()):
            row = by.get(code)
            if int(qty or 0) > 0:
                if row is None:
                    row = {
                        "stkCd": code,
                        "stkNm": code,
                        "role": "보유",
                        "candidate": False,
                        "holding": True,
                        "signalDt": "",
                        "expireDt": "",
                        "ma5": None,
                        "ma20": None,
                        "grpCd": "PBK01",
                    }
                    by[code] = row
                row["holding"] = True
                row["role"] = "보유"
                continue
            if row is not None and row.get("candidate"):
                row["holding"] = False
                row["role"] = "후보"
                continue
            by.pop(code, None)
            _screenFill.pop(code, None)
    out = list(by.values())
    order = {"보유": 0, "후보": 1}
    out.sort(key=lambda row: (order.get(row.get("role"), 9), row.get("stkCd") or ""))
    return out


def _sortValue(value):
    if isinstance(value, bool) or value is None or value == "":
        return (1, 0.0, str(value or ""))
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    text = str(value)
    try:
        return (0, float(text.replace(",", "")), "")
    except (TypeError, ValueError):
        return (1, 0.0, text)


def toneFromBg(bg):
    """칸 배경색을 터치·인접·보유·없음으로 되돌린다."""
    key = str(bg or "").lower()
    if key == TOUCH_BG.lower():
        return "touch"
    if key == NEAR_BG.lower():
        return "near"
    if key == HOLD_BG.lower():
        return "hold"
    return "none"


def _toneRank(tone):
    return {"touch": 0, "near": 1, "none": 2}.get(str(tone or "none"), 2)


def sortRoleGroups(rows, valueOf, descending=False, toneOf=None):
    """보유는 위에 둔다. 관심종목은 연주황, 연노랑, 색 없음 순이다.

    같은 색 안에서는 valueOf 정렬을 그대로 쓴다.
    """
    holds = []
    bands = {0: [], 1: [], 2: []}
    for index, row in enumerate(rows or []):
        if str((row or {}).get("role") or "") == "보유":
            holds.append((index, row))
            continue
        tone = "none"
        if toneOf is not None:
            tone = toneOf(row) or "none"
        bands[_toneRank(tone)].append((index, row))

    def key(pair):
        kind, num, text = _sortValue(valueOf(pair[1]))
        if descending:
            num = -num
            text = tuple(-ord(ch) for ch in text)
        return (kind, num, text, pair[0])

    holds.sort(key=key)
    ordered = list(holds)
    for rank in (0, 1, 2):
        group = bands[rank]
        group.sort(key=key)
        ordered.extend(group)
    return [row for _index, row in ordered]


_nameCache = {}


def _realName(name, code):
    text = str(name or "").strip()
    if not text or text == str(code or ""):
        return ""
    return text


def _needsName(row):
    return _realName(row.get("stkNm"), row.get("stkCd")) == ""


def _masterNames(codes):
    """AI_STK_INFO 종목마스터."""
    from utils.dbConn import openDbConn

    if not codes:
        return {}
    conn = openDbConn()
    try:
        marks = ",".join(["%s"] * len(codes))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT STK_CD, STK_NM FROM AI_STK_INFO WHERE STK_CD IN ({marks})",
                tuple(codes),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    out = {}
    for row in rows:
        code = _code((row or {}).get("STK_CD"))
        if code:
            out[code] = (row or {}).get("STK_NM") or ""
    return out


def _watchNames(codes):
    """AI_WATCH_STK 에 있는 이름."""
    from utils.watchStkStore import listActiveWatch

    want = set(codes or [])
    out = {}
    for row in listActiveWatch(mktTp="KR") or []:
        code = _code((row or {}).get("STK_CD"))
        if code in want:
            out[code] = (row or {}).get("STK_NM") or ""
    return out


def _pbkFileNames(codes):
    """PBK 상태 파일에 적힌 종목명. 마스터에 없는 신규 상장 코드용."""
    want = {_code(code) for code in (codes or []) if _code(code)}
    if not want:
        return {}
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        saved = latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}
    except Exception:
        return {}
    out = {}
    for item in list(saved.get("candidates") or []) + list(saved.get("positions") or []):
        if not isinstance(item, dict):
            continue
        code = _code(item.get("stkCd"))
        if code not in want:
            continue
        name = _realName(item.get("stkNm") or item.get("name"), code)
        if name:
            out[code] = name
    return out


def _kiwoomNames(codes):
    """키움 ka10001 종목명. 앞 단계에서 못 찾은 코드만."""
    from api.getStkInfo import fetchStkInfo
    from utils.dayPrcLoadService import acquireKiwoomSession

    session = acquireKiwoomSession()
    if not session:
        return {}
    _mode, host, token, tokenType = session
    out = {}
    for code in codes or []:
        got = fetchStkInfo(host, token, code, tokenType=tokenType) or {}
        body = got.get("body") or {}
        out[code] = body.get("stk_nm") or ""
    return out


def lookupStkNames(codes, masterFn=None, watchFn=None, kiwoomFn=None, cache=None, fileFn=None):
    """종목마스터, 관심종목, PBK 후보 파일, 키움 순. 없으면 코드."""
    store = _nameCache if cache is None else cache
    pending = []
    out = {}
    for raw in codes or []:
        code = _code(raw)
        if not code or code in out:
            continue
        if code in store and store[code]:
            out[code] = store[code]
            continue
        out[code] = ""
        pending.append(code)
    for source in (
        masterFn or _masterNames,
        watchFn or _watchNames,
        fileFn or _pbkFileNames,
        kiwoomFn or _kiwoomNames,
    ):
        if not pending:
            break
        try:
            found = source(pending) or {}
        except Exception:
            found = {}
        still = []
        for code in pending:
            name = _realName(found.get(code), code)
            if name:
                out[code] = name
                store[code] = name
            else:
                still.append(code)
        pending = still
    for code in pending:
        out[code] = code
        store.setdefault(code, "")
    return out


def applyStkNames(rows, names):
    """이름이 코드와 같으면 조회 결과로 바꾼다. 조회가 없으면 코드를 유지한다."""
    for row in rows or []:
        if not isinstance(row, dict) or not _needsName(row):
            continue
        code = str(row.get("stkCd") or "")
        row["stkNm"] = _realName((names or {}).get(code), code) or code
    return rows


def loadScreenRows(statePath=None, accountHolds=None, names=None):
    """저장된 PBK 후보·슬롯과 넘긴 모의 잔고로 화면 행을 만든다."""
    candidates = []
    positions = []
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(statePath or pbkPath("pbk01_state.jsonl"), "state") or {}
        candidates = row.get("candidates") or []
        positions = row.get("positions") or []
    except Exception:
        candidates = []
        positions = []
    rows = mergeScreenFills(unionRows(candidates, positions, accountHolds))
    global _screenCodes
    with _screenLock:
        _screenCodes = [row["stkCd"] for row in rows if row.get("stkCd")]
    if names is None:
        names = lookupStkNames([item["stkCd"] for item in rows if _needsName(item)])
    return applyStkNames(rows, names)


def _px(raw):
    """키움 현재가. 하락 틱의 마이너스 부호는 떼고, 0·빈값은 없다."""
    if raw is None or raw == "":
        return None
    text = str(raw).replace(",", "").strip()
    if text[:1] in "+-":
        text = text[1:]
    try:
        num = abs(float(text))
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num



def holdingExitDay(row, calendar=None):
    """보유 예상 매도일. 첫 체결일을 0번째로 센 f+10 거래일. 휴장일은 뺀다.

    날짜 계산은 exitDtFromFirst 만 쓴다. 저장된 exitDt 가 있으면 그대로 쓰고,
    없으면 firstBuyDt 또는 다리 중 가장 이른 체결일(_firstFillDay)로 구한다.
    """
    from strategies.impl.pullbackTwoStep import _ymd
    from trading.pbkOrderPlan import _firstFillDay, exitDtFromFirst

    day = _ymd((row or {}).get("exitDt"))
    if day:
        return day
    first = _ymd((row or {}).get("firstBuyDt")) or _firstFillDay(row)
    return exitDtFromFirst(first, calendar) or ""


def holdRoleLabel(row, today="", calendar=None):
    """관리종목 구분 칸 글자. 후보·1.5x 는 그대로, 보유는 매도일까지 남은 거래일.

    남은 날과 지난 날은 기존 buySpan 으로만 센다. 새 날짜 함수는 없다.
    D-0 은 '매도일'(빨간 굵게), 지난 날은 '매도지연' 또는 '매도지연 +n'(빨간 굵게),
    D-1 은 빨간색, 그 외는 D-n. 매도일을 모르면 '보유'.
    """
    from datetime import datetime, timedelta

    from strategies.impl.pullbackTwoStep import _ymd, buySpan, holidayCalendar

    role = str((row or {}).get("role") or "")
    held = role == "보유" or bool((row or {}).get("holding"))
    if not held:
        text = "후보 1.5x" if role == "후보" and (row or {}).get("boost") == "1.5x" else role
        return {"text": text, "alert": False, "bold": False, "remain": None, "late": None}
    exitDt = holdingExitDay(row, calendar)
    if not exitDt:
        return {"text": "보유", "alert": False, "bold": False, "remain": None, "late": None}
    cur = _ymd(today)
    if not cur:
        cur = datetime.now().strftime("%Y%m%d")
    days = list(calendar or [])
    if not days:
        start, end = (cur, exitDt) if cur <= exitDt else (exitDt, cur)
        end = (datetime.strptime(end, "%Y%m%d") + timedelta(days=14)).strftime("%Y%m%d")
        days = holidayCalendar(start, end)
    if cur < exitDt:
        # 주말·휴장일은 다음 거래일 화면과 같게. 다만 다음 날이 매도일이면 아직 D-1 이다.
        if cur in days:
            remain = int(buySpan(cur, exitDt, days) or 0)
        else:
            nxt = next((day for day in days if day > cur), "")
            if not nxt or nxt > exitDt:
                remain = int(buySpan(cur, exitDt, days) or 0)
            elif nxt == exitDt:
                remain = 1
            else:
                remain = int(buySpan(nxt, exitDt, days) or 0)
        return {"text": f"D-{remain}", "alert": remain <= 1, "bold": False, "remain": remain, "late": 0}
    if cur == exitDt:
        return {"text": "매도일", "alert": True, "bold": True, "remain": 0, "late": 0}
    late = int(buySpan(exitDt, cur, days) or 0)
    text = f"매도지연 +{late}" if late > 0 else "매도지연"
    return {"text": text, "alert": True, "bold": True, "remain": 0, "late": late}




def formatPx(value):
    """가격을 천 단위 쉼표로. 없으면 빈칸 대신 대시."""
    num = _px(value)
    if num is None:
        return "-"
    return f"{int(round(num)):,}"


def keepShownPx(prevText, newText):
    """새 값이 비면 이미 보이던 가격을 유지한다. 계산이 바뀐 것은 아니다."""
    new = "-" if newText in (None, "") else str(newText)
    prev = "" if prevText is None else str(prevText)
    if new == "-" and prev not in ("", "-"):
        return prev
    return new


def pbkVisibleWidths(widths, codeCol=1):
    """숨긴 종목코드 칸의 너비를 나머지 칸에 나눈다."""
    cols = [int(w) for w in widths or []]
    if not cols or codeCol < 0 or codeCol >= len(cols):
        return tuple(cols)
    spare = cols[codeCol]
    cols[codeCol] = 0
    visible = [i for i in range(len(cols)) if i != codeCol]
    if not visible or spare <= 0:
        return tuple(cols)
    share, extra = divmod(int(spare), len(visible))
    for n, idx in enumerate(visible):
        cols[idx] += share + (1 if n < extra else 0)
    return tuple(cols)


def screenUsesLiveQuote(now=None):
    """화면 계산에 실시간 틱·폴링·0B를 쓰는 때는 정규장뿐이다."""
    from datetime import datetime

    from config.krHoliday import isTradingDay
    from config.settingsStore import isWithinMktHours

    day = now or datetime.now()
    if not isTradingDay(day):
        return False
    return bool(isWithinMktHours(day.strftime("%H:%M")))


def isLiveScreenQuote(quote):
    """장외에 무시할 실시간 틱·폴링·0B. 공식 종가 시드는 틱이 아니다."""
    quote = quote or {}
    src = str(quote.get("src") or "")
    if src == "dayClose":
        return False
    if src:
        return True
    return _px(quote.get("curPrc") or quote.get("krxPrc") or quote.get("nxtPrc")) is not None


def screenPrice(quote, closePx=None, inSession=False):
    """정규장에는 0B 현재가. 그 밖에는 공식 종가만 쓰고 실시간 틱은 버린다."""
    quote = quote or {}
    live = _px(quote.get("curPrc") or quote.get("krxPrc") or quote.get("nxtPrc"))
    close = _px(closePx)
    src = str(quote.get("src") or "")
    if close is None and src == "dayClose":
        close = live
    if not inSession:
        return close
    if src != "dayClose" and live:
        return live
    if close:
        return close
    return live


def maGapText(ma, price):
    """현재가가 이평보다 낮을 때만 '(-3.2%)'. 그 외에는 빈 문자열."""
    line = _px(ma)
    px = _px(price)
    if line is None or px is None or px >= line:
        return ""
    return f"({(px / line - 1.0) * 100.0:.1f}%)"


def masAndClose(bars, asOf=None):
    """직전 확정일까지의 5·20이평과 그 날 종가. 이평이 부족하면 None."""
    from strategies.impl.pullbackTwoStep import _barDt, _f, _sortBars, _ymd, normalizeDayBars, priorMean

    limit = _ymd(asOf)
    kept = []
    for bar in _sortBars(normalizeDayBars(bars)):
        day = _barDt(bar)
        if not day or (limit and day > limit):
            continue
        kept.append(bar)
    if not kept:
        return None, None, None
    return (
        priorMean(kept, len(kept), 5, "close"),
        priorMean(kept, len(kept), 20, "close"),
        _f(kept[-1], "close", "CLOSE_PRC"),
    )


def annotateScreenBars(rows, barsByCode, asOf=None):
    """비어 있는 이평과 공식 종가를 일봉으로 채운다. 있는 이평은 유지한다."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        ma5, ma20, close = masAndClose((barsByCode or {}).get(code), asOf)
        if row.get("ma5") is None and ma5 is not None:
            row["ma5"] = ma5
        if row.get("ma20") is None and ma20 is not None:
            row["ma20"] = ma20
        if close is not None:
            row["closePx"] = close
    return rows


def scrollBarWanted(overflow, hover):
    """넘칠 때만 칸을 두고, 손잡이는 마우스가 그리드 위에 있을 때만."""
    over = bool(overflow)
    return {"showBar": over, "showHandle": over and bool(hover)}


def screenCodes(rows):
    return [row["stkCd"] for row in rows or [] if row.get("stkCd")]


def pbkStateCodes(path=None):
    """상태 파일의 후보·보유 코드. 시세 시드가 화면 종목을 빠뜨리지 않게."""
    from utils.stkCdUtil import normStkCd

    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(path or pbkPath("pbk01_state.jsonl"), "state") or {}
    except Exception:
        return []
    out = []
    seen = set()
    for item in list(row.get("candidates") or []) + list(row.get("positions") or []):
        if not isinstance(item, dict):
            continue
        code = normStkCd(item.get("stkCd"))
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def backupWatchOnce(rows, folder, now=None):
    """현재 저장 형식 그대로 한 번만 백업한다. 파일이 있으면 덮어쓰지 않는다."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    found = sorted(folder.glob("watchlist_backup_*.json"))
    if found:
        return {"ok": True, "skipped": "exists", "path": str(found[0]), "cnt": 0}
    stamp = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    path = folder / f"watchlist_backup_{stamp}.json"
    payload = list(rows or [])
    from config.settingsStore import _writeJson

    _writeJson(path, payload)
    return {"ok": True, "skipped": "", "path": str(path), "cnt": len(payload)}


def readWatchBackup(folder):
    folder = Path(folder)
    found = sorted(folder.glob("watchlist_backup_*.json"))
    if not found:
        return []
    try:
        data = json.loads(found[0].read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def legacyWatchRows(reader=None):
    """DB 관심종목을 읽기만 한다. 행은 지우지 않는다."""
    if reader is not None:
        return list(reader() or [])
    try:
        from utils.watchStkStore import listActiveWatch

        return list(listActiveWatch(mktTp="KR") or [])
    except Exception:
        return []


def ensureWatchBackup(folder, reader=None):
    """목록을 읽었고 1건 이상일 때만 백업한다. 빈 결과로 파일을 만들지 않는다."""
    folder = Path(folder)
    if list(folder.glob("watchlist_backup_*.json")):
        return {"ok": True, "skipped": "exists"}
    try:
        rows = legacyWatchRows(reader)
    except Exception:
        return {"ok": False, "skipped": "unread"}
    if not rows:
        return {"ok": True, "skipped": "empty"}
    return backupWatchOnce(rows, folder)


def collectionCodes(backupRows, extras=COLLECTION_EXTRAS):
    """화면과 분리된 수집 대상. 백업된 관심종목, 069500, 지수."""
    out = []
    seen = set()
    for row in backupRows or []:
        if isinstance(row, str):
            code = _code(row)
        else:
            code = _code((row or {}).get("stkCd") or (row or {}).get("STK_CD"))
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    for raw in extras or []:
        code = str(raw or "").strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def touchDelta(previous, screen, keep, registered=None):
    """화면 목록에 맞춰 넣을 코드와 뺄 코드. keep 은 다른 수집·탭이 쓰는 구독이다.

    registered 가 있으면 편입은 그 실제 등록 집합에 없는 종목이다.
    화면 목록에만 있고 등록이 안 된 후보도 넣는다.
    """
    prev = {_code(code) for code in (previous or []) if _code(code)}
    want = []
    seen = set()
    for code in screen or []:
        cd = _code(code)
        if cd and cd not in seen:
            seen.add(cd)
            want.append(cd)
    wantSet = set(want)
    held = {_code(code) for code in (keep or []) if _code(code)}
    if registered is None:
        added = [code for code in want if code not in prev and code not in held]
    else:
        reg = {_code(code) for code in registered if _code(code)}
        added = [code for code in want if code not in reg]
    removed = [code for code in prev if code not in wantSet and code not in held]
    missing = [code for code in want if registered is not None and code not in {_code(c) for c in registered if _code(c)}]
    return {"added": added, "removed": removed, "screen": want, "missing": missing}


PBK_TAB = {"key": "pbk", "name": "PBK", "bg": "#C5E1A5", "kind": "monitor", "algCd": "PBK01"}
CLOSED_TAB = {"key": "closed", "name": "청산종목", "bg": "#EEEEEE", "kind": "history", "algCd": ""}


def ensurePbkTab(tabs, settings=None):
    """모의 PBK 화면이면 저장된 탭 목록에 PBK 가 없어도 앞에 넣는다."""
    rows = []
    for item in tabs or []:
        if isinstance(item, dict):
            rows.append(dict(item))
        else:
            rows.append({"key": str(item or ""), "name": str(item or ""), "bg": ""})
    if not watchScreenOn(settings):
        return rows
    if any(str(row.get("key") or "") == "pbk" for row in rows):
        return rows
    return [dict(PBK_TAB)] + rows


def ensureClosedTab(tabs, settings=None):
    """청산 이력 탭. 시세 구독 탭이 아니므로 관심종목 뒤에 붙인다."""
    rows = [dict(row) for row in (tabs or []) if isinstance(row, dict)]
    if not watchScreenOn(settings):
        return rows
    if any(str(row.get("key") or "") == "closed" for row in rows):
        return rows
    return rows + [dict(CLOSED_TAB)]


def bootWatchSelection(current, visibleKeys, settings=None):
    """부팅 때는 저장된 탭보다 PBK 를 먼저 연다."""
    keys = []
    for item in visibleKeys or []:
        key = item.get("key") if isinstance(item, dict) else item
        key = str(key or "").strip()
        if key and key not in keys:
            keys.append(key)
    if watchScreenOn(settings) and "pbk" in keys:
        return "pbk"
    from trading.deskLiveAlgs import resolveMonitorSelection

    return resolveMonitorSelection(current, keys)


def setWatchScreen(on):
    """화면 스위치. DB 관심종목은 건드리지 않는다."""
    from config.settingsStore import loadSettings, patchSettings

    settings = loadSettings() or {}
    live = dict(settings.get("stratLive") or {})
    row = dict(live.get("PBK01") or {})
    row["watchScreen"] = bool(on)
    row.setdefault("paperOnly", True)
    live["PBK01"] = row
    patchSettings({"stratLive": live})
    return {"ok": True, "watchScreen": bool(on)}


def restoreLegacyWatch():
    """관심종목 화면을 백업·DB 목록으로 되돌린다."""
    return setWatchScreen(False)


def noteScreenRefresh(reason, codes=None, accountHolds=None):
    """스캔·백필·체결 뒤 터치 시세만 화면 목록에 맞춘다. 실전 모드에서는 하지 않는다."""
    if not watchScreenOn():
        return {"ok": True, "skipped": "off"}
    if codes is None:
        codes = screenCodes(loadScreenRows(accountHolds=accountHolds))
    try:
        from trading.watchRtMonitor import syncPbkTouch

        return syncPbkTouch(codes, reason or "PBK화면")
    except Exception as err:
        return {"ok": False, "errMsg": str(err), "screen": list(codes or [])}


def notePaperFill(sideLabel, stkCd, qty=0):
    """모의 매수·매도 체결 뒤에 구분과 화면 시세를 갱신한다."""
    if not watchScreenOn():
        return {"ok": True, "skipped": "off"}
    with _screenLock:
        known = list(_screenCodes)
    changed = applyScreenFill(sideLabel, stkCd, qty, known=known)
    if changed is not None:
        try:
            from utils.tradeLogHook import emitWatchRefresh

            emitWatchRefresh()
        except Exception:
            pass
    return noteScreenRefresh(f"체결 {sideLabel} {stkCd}")
