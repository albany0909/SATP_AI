"""PBK01 모의 연결. 기본 비활성. 실전 호스트로는 주문 함수가 없다.

켜고 끄는 것은 settings.stratLive.PBK01.enabled 이다.
후보는 기동 시 한 번, 그리고 거래일 장 시작 전에 계산한다.
장중 터치는 그 후보 목록만 본다.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

_started = False
_lock = threading.Lock()
_announcedYmd = ""
_startupDone = False
_scanYmd = ""
SCAN_HM = "08:40"


def heldPositionCount():
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}
        n = 0
        for pos in row.get("positions") or []:
            if int((pos or {}).get("qty") or 0) > 0:
                n += 1
        return n
    except Exception:
        return 0


def _settingsRow():
    try:
        from config.settingsStore import loadSettings

        row = ((loadSettings() or {}).get("stratLive") or {}).get("PBK01")
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}


def scanHm():
    """장 시작 전 후보 시각. settings 가 우선, 없으면 08:40."""
    raw = str(_settingsRow().get("scanHm") or "").strip()
    if not raw:
        try:
            from strategies.impl.pullbackTwoStep import loadPbkOptions

            raw = str(loadPbkOptions().get("scanHm") or SCAN_HM)
        except Exception:
            raw = SCAN_HM
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 4:
        return digits + "00"
    if len(digits) >= 6:
        return digits[:6]
    return "084000"


def isPbkEnabled():
    """설정 스위치. 스펙 enabled 는 보지 않는다. paperOnly 가 아니면 꺼진 것이다."""
    from strategies.impl.pullbackTwoStep import loadPbkOptions

    if not loadPbkOptions().get("paperOnly"):
        return False
    row = _settingsRow()
    if row.get("paperOnly") is False:
        return False
    return row.get("enabled") is True


def deskBoardText(path=None):
    """상단 한 줄. 로그·텔레그램용 후보 나열은 formatCandidateLog 를 쓴다."""
    from strategies.impl.pullbackTwoStep import formatBoardLine

    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(path or pbkPath("pbk01_state.jsonl"), "state") or {}
    except Exception:
        row = {}
    return formatBoardLine(
        row.get("candidates") or [],
        row.get("positions") or [],
        row.get("scannedAt") or "",
    )


def candidateSignature(rows):
    """종목·신호일·만료가 같으면 같은 목록이다."""
    parts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        parts.append("|".join([
            str(row.get("stkCd") or ""),
            str(row.get("signalDt") or ""),
            str(row.get("expireDt") or ""),
        ]))
    return "\n".join(sorted(parts))


def announceCandidates(rows, positions, ymd, sender=None, log=None, path=None):
    """로그는 매번. 텔레그램은 후보 목록이 바뀔 때만."""
    from strategies.impl.pullbackTwoStep import formatCandidateLog, formatSlotLog
    from utils.pbk01Store import appendRow, latestByKind, pbkPath

    filePath = path or pbkPath("pbk01_state.jsonl")
    prev = latestByKind(filePath, "state") or {}
    text = formatCandidateLog(rows)
    slotLine = formatSlotLog(positions)
    if log is not None:
        log(text)
        log(slotLine)
    else:
        from utils.termLog import termLog

        termLog(text)
        termLog(slotLine)
    changed = candidateSignature(rows) != str(prev.get("candidateKey") or "")
    sentOk = False
    if changed and sender is not None:
        try:
            sender(text)
            sentOk = True
            _safeLog(log, "[PBK01] 텔레그램 전송 성공")
        except Exception as err:
            _safeLog(log, f"[PBK01] 텔레그램 전송 실패 {type(err).__name__}")
    elif sender is not None:
        _safeLog(log, "[PBK01] 텔레그램 생략 목록 동일")
    appendRow(filePath, {
        "kind": "state",
        "announcedYmd": str(ymd),
        "scannedAt": datetime.now().strftime("%H:%M"),
        "candidateKey": candidateSignature(rows),
        "candidates": list(rows or []),
        "positions": list(positions or []),
    })
    return {"sent": sentOk, "changed": changed, "text": text}


def _barsForSignal(bars):
    """저장 일봉의 qty 를 거래량으로 맞춘다. 후보 필터와 같은 함수다."""
    from strategies.impl.pullbackTwoStep import normalizeDayBars

    return normalizeDayBars(bars)


def barAsOf(now):
    """확정된 마지막 거래일. 장중·장전에는 직전 거래일, 장마감 후면 그날."""
    from config.krHoliday import isTradingDay, prevTradingDay

    ymd = now.strftime("%Y%m%d")
    if isTradingDay(now) and now.strftime("%H%M%S") >= "153000":
        return ymd
    return prevTradingDay(now) or ymd


def sessionDay(now):
    """매수 창을 재는 세션. 휴일이면 다음 거래일."""
    from config.krHoliday import isTradingDay, nextTradingDay

    if isTradingDay(now):
        return now.strftime("%Y%m%d")
    return nextTradingDay(now) or now.strftime("%Y%m%d")


def _loadMarketDates():
    """코스피 지수 jsonl, 없으면 069500 저장 일봉. 없으면 빈 목록."""
    dates = []
    try:
        from utils.kospiIdxLoad import _readJsonl, kospiJsonlPath

        for row in _readJsonl(kospiJsonlPath()) or []:
            day = str((row or {}).get("TRADE_DT") or "")[:8]
            if len(day) == 8:
                dates.append(day)
    except Exception:
        dates = []
    if len(set(dates)) >= 40:
        return sorted(set(dates))
    try:
        from utils.chartBarLoad import loadStoredDayBars

        for bar in loadStoredDayBars("069500") or []:
            day = str((bar or {}).get("tradeDt") or "")[:8]
            if len(day) == 8:
                dates.append(day)
    except Exception:
        pass
    return sorted(set(dates))


def _mergeDisclosures(disclosuresByCode, surprises):
    out = {}
    for code, items in (disclosuresByCode or {}).items():
        out.setdefault(str(code), []).extend(list(items or []))
    for code, items in (surprises or {}).items():
        out.setdefault(str(code), []).extend(list(items or []))
    return out


def buildScan(barsByCode, disclosuresByCode, today, positions=None, asOf=None, calendar=None, surprises=None, log=None):
    """공시마다 장대양봉을 찾고, 창이 열린 가장 최근 이벤트를 후보로 고른다.

    asOf(직전 거래일)까지 봉이 없으면 후보에서 빼고 '봉 부족 제외 N건'을 남긴다.
    """
    from datetime import datetime, timedelta

    from strategies.impl.pullbackTwoStep import (
        barsCoverAsOf,
        candidateRows,
        findCandleEvents,
        holidayCalendar,
    )

    session = str(today or "")[:8]
    if surprises is None:
        try:
            from utils.pbkFinStore import surprisesByCode

            surprises = surprisesByCode()
        except Exception:
            surprises = {}
    discs = _mergeDisclosures(disclosuresByCode, surprises)
    if calendar is None and session:
        marks = [session]
        if asOf:
            marks.append(str(asOf)[:8])
        for items in discs.values():
            for item in items or []:
                day = str((item or {}).get("date") or (item or {}).get("rceptDt") or "")[:8]
                if len(day) == 8:
                    marks.append(day)
        start = min(marks)
        end = (datetime.strptime(max(marks), "%Y%m%d") + timedelta(days=120)).strftime("%Y%m%d")
        calendar = holidayCalendar(start, end, _loadMarketDates())
    events = []
    normalized = {}
    excluded = 0
    codes = sorted(set(list((barsByCode or {}).keys()) + list(discs.keys())))
    for code in codes:
        rows = _barsForSignal((barsByCode or {}).get(code))
        normalized[code] = rows
        if code not in discs:
            continue
        if asOf and not barsCoverAsOf(rows, asOf):
            excluded += 1
            continue
        events.extend(findCandleEvents(
            rows,
            discs.get(code) or [],
            code,
            asOf=asOf or session,
            calendar=calendar,
        ))
    if excluded:
        _safeLog(log, f"[PBK01] 봉 부족 제외 {excluded}건")
    picked = candidateRows(events, positions or [], normalized, session, calendar=calendar)
    return events, picked


def refreshStaleBars(barsByCode, codes, asOf, fetcher=None):
    """직전 거래일 봉이 없는 종목만 먼저 받는다. 읽기 전용 스캔 전에 호출한다."""
    from strategies.impl.pullbackTwoStep import barsCoverAsOf

    stale = [code for code in codes or [] if not barsCoverAsOf((barsByCode or {}).get(code), asOf)]
    if not stale:
        return barsByCode or {}
    if fetcher is None:
        def fetcher(want, end):
            from utils.pbkUniverseLoad import _loadWithKiwoom

            return _loadWithKiwoom(want, end)
    try:
        fetcher(stale, asOf)
    except Exception as err:
        _safeLog(None, f"[PBK01] 봉 우선 조회 실패: {err}")
    try:
        from utils.chartBarLoad import loadStoredDayBars

        for code in stale:
            got = loadStoredDayBars(code, endYmd=asOf) or []
            if got:
                barsByCode[code] = got
    except Exception as err:
        _safeLog(None, f"[PBK01] 봉 재읽기 실패: {err}")
    return barsByCode


def touchesForQuotes(candidates, quotes, owned=None):
    """저장된 후보만 장중 터치 대상으로 쓴다."""
    from strategies.impl.pullbackTwoStep import touchesFromCandidates

    return touchesFromCandidates(candidates, quotes, owned=owned)


def _regularSession(now):
    """터치·폴링·0B 판정은 정규장에서만 한다."""
    try:
        from utils.pbkWatchScreen import screenUsesLiveQuote

        return bool(screenUsesLiveQuote(now))
    except Exception:
        return False


def _liveQuoteSrc(quote):
    return str((quote or {}).get("src") or "") in ("0B", "poll")


def _safeLog(log, text):
    if log is not None:
        log(text)
        return
    try:
        from utils.termLog import termLog

        termLog(text)
    except Exception:
        pass


def quotesFromCache(codes, now=None):
    """후보 종목의 캐시 현재가만 읽는다. 등락 부호는 절대값으로 푼다."""
    from datetime import datetime

    from trading.pbkOrderPlan import absPx
    from trading.pbkQuotePlan import quoteIsFresh

    moment = now or datetime.now()
    out = {}
    try:
        from trading.watchRtMonitor import peekWatchLastPx
    except Exception:
        return out
    for code in codes or []:
        info = peekWatchLastPx(code) or {}
        px = absPx(info.get("curPrc") or info.get("krxPrc") or info.get("nxtPrc"))
        if px is None:
            continue
        fresh = quoteIsFresh(info, moment)
        out[str(code)] = {
            "price": px,
            "low": absPx(info.get("lowPrc")),
            "open": absPx(info.get("openPrc")),
            "src": info.get("src") or "",
            "tm": info.get("tm") or "",
            "tickYmd": info.get("tickYmd") or "",
            "fresh": fresh,
            "curPrc": info.get("curPrc"),
        }
    return out


def defaultScanInputs(today):
    """전 종목 중 거래대금 조건을 통과한 종목의 저장 일봉과 저장된 실적 공시."""
    discs = {}
    try:
        from utils.pbkDartStore import disclosuresByCode

        discs = disclosuresByCode()
    except Exception:
        _safeLog(None, "[PBK01] 공시 파일을 읽지 못함")
        discs = {}
    codes = sorted(discs.keys())
    try:
        from utils.pbkUniverseLoad import listKospiKosdaq

        listed = listKospiKosdaq()
        if listed:
            want = set(listed)
            codes = [code for code in codes if code in want] or codes
    except Exception:
        _safeLog(None, "[PBK01] 종목 마스터를 읽지 못함")
    bars = {}
    try:
        from utils.chartBarLoad import loadStoredDayBars

        for code in codes:
            try:
                bars[code] = loadStoredDayBars(code) or []
            except Exception:
                bars[code] = []
    except Exception:
        _safeLog(None, "[PBK01] 일봉을 읽지 못함")
    positions = []
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        positions = (latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}).get("positions") or []
    except Exception:
        positions = []
    return {"bars": bars, "disclosures": discs, "positions": positions, "quotes": {}}


def runPbkCycle(now=None, loader=None, sender=None, log=None, path=None, quotes=None, force=False, always=False, enabled=None):
    """기동 1회, 매 거래일 scanHm, 또는 적재 직후(always)에 후보를 다시 계산한다.

    텔레그램은 목록이 바뀔 때만. 장중 터치는 저장된 후보만 사용한다.
    """
    global _announcedYmd, _startupDone, _scanYmd
    now = now or datetime.now()
    ymd = now.strftime("%Y%m%d")
    hm = now.strftime("%H%M%S")
    trading = True
    try:
        from config.krHoliday import isTradingDay

        trading = bool(isTradingDay(now))
    except Exception:
        trading = True
    dueStartup = bool(force) and not _startupDone
    dueDaily = bool(trading) and hm >= scanHm() and _scanYmd != ymd
    due = bool(always) or dueStartup or dueDaily
    if enabled is None:
        enabled = isPbkEnabled()
    positions = []
    rows = []
    if due:
        pack = {}
        try:
            pack = (loader or defaultScanInputs)(ymd) or {}
        except Exception:
            _safeLog(log, "[PBK01] 후보 계산 실패")
            pack = {}
        positions = pack.get("positions") or []
        asOf = barAsOf(now)
        session = sessionDay(now)
        bars = dict(pack.get("bars") or {})
        discs = pack.get("disclosures") or {}
        surprises = pack.get("surprises")
        if surprises is None:
            try:
                from utils.pbkFinStore import surprisesByCode

                surprises = surprisesByCode()
            except Exception:
                surprises = {}
        codes = sorted(set(list(bars.keys()) + list(discs.keys()) + list(surprises.keys())))
        if pack.get("refreshBars", True):
            bars = refreshStaleBars(bars, codes, asOf, fetcher=pack.get("barFetcher"))
        _events, rows = buildScan(
            bars,
            discs,
            session,
            positions,
            asOf=asOf,
            surprises=surprises,
            log=log,
        )
        announceCandidates(
            rows,
            positions,
            ymd,
            sender=sender,
            log=log,
            path=path,
        )
        try:
            from utils.pbkWatchScreen import noteScreenRefresh

            noteScreenRefresh("PBK스캔")
        except Exception:
            pass
        _announcedYmd = ymd
        if dueStartup:
            _startupDone = True
        if dueDaily:
            _scanYmd = ymd
        positions = _completeHeld(
            positions, rows, discs, bars, asOf=asOf, history=_signalHistory(path),
        )
        rows, _blocked = _markHeldAmount(rows, positions, bars)
        if quotes is None:
            quotes = pack.get("quotes") or {}
    else:
        try:
            from utils.pbk01Store import latestByKind, pbkPath

            saved = latestByKind(path or pbkPath("pbk01_state.jsonl"), "state") or {}
            rows = saved.get("candidates") or []
            positions = saved.get("positions") or []
        except Exception:
            rows = []
            positions = []
        heldBars = _barsForHeld(rows, positions)
        positions = _completeHeld(
            positions, rows, None, heldBars, history=_signalHistory(path),
        )
        rows, _blocked = _markHeldAmount(rows, positions, heldBars)
    owned = []
    for pos in positions or []:
        if int((pos or {}).get("qty") or 0) > 0 or (pos or {}).get("leg1"):
            owned.append(pos.get("stkCd"))
    touches = []
    if enabled:
        if not _regularSession(now):
            if quotes is None:
                seen = quotesFromCache([row.get("stkCd") for row in rows] + list(owned), now=now)
            else:
                seen = quotes
            ignored = [code for code, quote in (seen or {}).items() if _liveQuoteSrc(quote)]
            if ignored:
                _safeLog(log, f"[PBK01] 장외 시세 무시 {len(ignored)}종")
            else:
                _safeLog(log, "[PBK01] 장외 시세 무시 터치·폴링 안 함")
            try:
                noteTouchWait([])
            except Exception:
                pass
            kept = {}
            for code, quote in (quotes or {}).items():
                if quotes is None or _liveQuoteSrc(quote):
                    continue
                kept[code] = quote
            quotes = kept
        else:
            if quotes is None:
                try:
                    refreshPbkQuotes(rows, positions, reason="PBK시세", now=now)
                except Exception as err:
                    _safeLog(log, f"[PBK01] 시세 구독 실패 {type(err).__name__}")
                quotes = quotesFromCache([row.get("stkCd") for row in rows] + list(owned), now=now)
        usable = {}
        for code, quote in (quotes or {}).items():
            if isinstance(quote, dict) and quote.get("fresh") is False:
                continue
            usable[code] = quote
        touches = touchesForQuotes(rows, usable, owned=owned)
    return {
        "announced": _announcedYmd,
        "candidates": rows,
        "positions": positions,
        "quotes": quotes or {},
        "ymd": ymd,
        "hm": hm,
        "touches": touches,
        "enabled": bool(enabled),
    }


_lastSummaryAt = None
_lastQuoteSummary = {}
_pollCursor = 0


def _barsForHeld(candidates, positions):
    """보유 종목의 저장 일봉. 테스트와 조회 실패는 빈 목록이다."""
    import os

    if os.environ.get("SATP_TEST") == "1":
        return {}
    codes = []
    for pos in positions or []:
        code = str((pos or {}).get("stkCd") or "")
        try:
            qty = int((pos or {}).get("qty") or (pos or {}).get("brokerQty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if code and (qty > 0 or (pos or {}).get("leg1") or (pos or {}).get("leg2")):
            codes.append(code)
    out = {}
    if not codes:
        return out
    try:
        from utils.chartBarLoad import loadStoredDayBars
    except Exception:
        return out
    for code in codes:
        try:
            out[code] = loadStoredDayBars(code) or []
        except Exception:
            out[code] = []
    return out


_bootAmtNoted = set()


def _logAmtHold(code, log=None, boot=False):
    """대금 미달 추가매수 보류. 부팅 로그는 종목당 한 번, 사이클 로그는 매번."""
    global _bootAmtNoted
    text = f"[PBK01] 대금미달 {code} 추가매수 보류"
    if boot:
        if code in _bootAmtNoted:
            return
        _bootAmtNoted.add(code)
        _safeLog(None, text)
        return
    _safeLog(log, text)


def _markHeldAmount(candidates, positions, barsByCode):
    """신호일을 모르거나 대금을 계산할 수 없으면 추가 매수를 막는다."""
    from strategies.impl.pullbackTwoStep import signalAmt20

    rows = [dict(row) for row in (candidates or []) if isinstance(row, dict)]
    by = {str(row.get("stkCd") or ""): row for row in rows if row.get("stkCd")}
    blocked = []
    for pos in positions or []:
        if not isinstance(pos, dict) or pos.get("released") or pos.get("closed"):
            continue
        code = str(pos.get("stkCd") or "")
        try:
            qty = int(pos.get("qty") or pos.get("brokerQty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not code or not (qty > 0 or pos.get("leg1") or pos.get("leg2")):
            continue
        sig = str(pos.get("signalDt") or (by.get(code) or {}).get("signalDt") or "")
        bars = (barsByCode or {}).get(code)
        row = by.get(code)
        if not sig:
            if row is None:
                row = {
                    "stkCd": code,
                    "stkNm": pos.get("stkNm") or "",
                    "signalDt": "",
                    "ma5": pos.get("ma5"),
                    "ma20": pos.get("ma20"),
                }
                rows.append(row)
                by[code] = row
            row["amtOk"] = False
            blocked.append(code)
            continue
        if not bars:
            continue
        got = signalAmt20(bars, sig)
        if not got:
            continue
        if got.get("ok"):
            if row is not None:
                row["amtOk"] = True
            continue
        if row is None:
            row = {
                "stkCd": code,
                "stkNm": pos.get("stkNm") or "",
                "signalDt": sig,
                "ma5": pos.get("ma5"),
                "ma20": pos.get("ma20"),
            }
            rows.append(row)
            by[code] = row
        row["amtOk"] = False
        blocked.append(code)
    return rows, blocked


def _signalHistory(path):
    """이전 상태와 체결 기록. 최신 후보에 신호일이 없어도 여기서 찾는다."""
    import os
    from pathlib import Path

    from utils.pbk01Store import readRows

    if path is None and os.environ.get("SATP_TEST") == "1":
        return []
    filePath = Path(path) if path else None
    if filePath is None:
        try:
            from utils.pbk01Store import pbkPath

            filePath = pbkPath("pbk01_state.jsonl")
        except Exception:
            return []
    rows = list(readRows(filePath))
    try:
        fills = sorted(filePath.parent.glob("pbk01_fills_*.jsonl"))
    except Exception:
        fills = []
    for fill in fills:
        rows.extend(readRows(fill))
    return rows


def _completeHeld(positions, candidates, disclosures, barsByCode, asOf="", names=None, history=None):
    from trading.pbkOrderPlan import completeHoldRecords

    return completeHoldRecords(
        positions,
        candidates,
        disclosures=disclosures,
        barsByCode=barsByCode,
        names=names,
        asOf=asOf,
        history=history,
    )


def _holdContext(positions, disclosures=None, barsByCode=None):
    """복원 때 공시·일봉. 테스트에서는 주입된 값만 쓰고 운영 파일은 열지 않는다."""
    import os

    if disclosures is not None or barsByCode is not None:
        return disclosures or {}, barsByCode or {}
    if os.environ.get("SATP_TEST") == "1":
        return {}, {}
    codes = []
    for pos in positions or []:
        code = str((pos or {}).get("stkCd") or "")
        if code:
            codes.append(code)
    discs = {}
    bars = {}
    try:
        from utils.pbkDartStore import disclosuresByCode

        discs = disclosuresByCode() or {}
    except Exception:
        discs = {}
    try:
        from utils.chartBarLoad import loadStoredDayBars

        for code in codes:
            try:
                bars[code] = loadStoredDayBars(code) or []
            except Exception:
                bars[code] = []
    except Exception:
        bars = {}
    return discs, bars


def _namesFor(positions, candidates, accountRows=None, names=None):
    import os

    from trading.pbkOrderPlan import _knownName

    out = {}
    for row in list(accountRows or []) + list(candidates or []) + list(positions or []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        name = _knownName(row.get("stkNm") or row.get("name"), code)
        if code and name:
            out[code] = name
    if isinstance(names, dict):
        for code, name in names.items():
            text = _knownName(name, code)
            if text:
                out[str(code)] = text
    missing = []
    for pos in positions or []:
        code = str((pos or {}).get("stkCd") or "")
        if code and code not in out and code not in missing:
            missing.append(code)
    if missing and os.environ.get("SATP_TEST") != "1":
        try:
            from utils.pbkWatchScreen import lookupStkNames

            found = lookupStkNames(missing) or {}
        except Exception:
            found = {}
        for code, name in found.items():
            text = _knownName(name, code)
            if text:
                out[str(code)] = text
    return out


def _universeRows(rows, positions):
    """공식 후보와 모의 보유. 표시용 체결 수량도 보유로 본다."""
    held = set()
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        code = str(pos.get("stkCd") or "").strip()
        try:
            qty = int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if pos.get("released"):
            continue
        if code and (qty > 0 or pos.get("leg1") or pos.get("leg2")):
            held.add(code)
    try:
        from utils.pbkWatchScreen import screenFillSnapshot

        for code, qty in (screenFillSnapshot() or {}).items():
            if int(qty or 0) > 0 and code:
                held.add(str(code))
    except Exception:
        pass
    out = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        item = dict(row)
        item["holding"] = code in held
        out.append(item)
    for code in sorted(held):
        if code in seen:
            continue
        out.append({"stkCd": code, "holding": True, "ma5": None, "ma20": None})
    return out


def _attachPrices(rows):
    try:
        from trading.watchRtMonitor import peekWatchLastPx
    except Exception:
        peekWatchLastPx = lambda _cd: {}
    out = []
    for row in rows or []:
        item = dict(row)
        info = peekWatchLastPx(item.get("stkCd")) or {}
        item["price"] = info.get("curPrc") or info.get("krxPrc") or info.get("nxtPrc")
        item["tm"] = info.get("tm") or ""
        item["src"] = info.get("src") or ""
        item["tickYmd"] = info.get("tickYmd") or ""
        item["curPrc"] = info.get("curPrc") or item["price"]
        out.append(item)
    return out


def _fetchKa10001(code):
    """한도 밖 종목 현재가. 토큰은 로그에 남기지 않는다."""
    from utils.trRateLimit import acquireTrSlot

    acquireTrSlot("ka10001", mode="paper")
    try:
        from api.getStkInfo import fetchStkInfo
        from utils.dayPrcLoadService import acquireKiwoomSession
    except Exception:
        return None
    session = acquireKiwoomSession()
    if not session:
        return None
    _mode, host, token, tokenType = session
    got = fetchStkInfo(host, token, code, tokenType=tokenType) or {}
    if not got.get("ok"):
        return None
    body = got.get("body") or {}
    return {
        "price": body.get("cur_prc"),
        "low": body.get("low_pric"),
        "open": body.get("open_pric"),
        "tm": datetime.now().strftime("%H%M%S"),
    }


def refreshPbkQuotes(rows, positions, reason="PBK시세", fetch=None, pollLimit=None, now=None):
    """후보·보유를 0B 에 추가하고, 오늘 틱이 없거나 오래된 종목은 ka10001 로 받는다."""
    global _lastQuoteSummary
    from datetime import datetime

    from trading.pbkQuotePlan import (
        POLL_PER_CYCLE,
        planPbkQuotes,
        pollBatch,
        stalePollEntries,
        summarizeQuotes,
    )
    from trading.watchRtMonitor import (
        _rtItemsOf,
        notePolledPx,
        pbkQuoteBudget,
        syncPbkTouch,
        unmatchedTickCount,
    )

    moment = now or datetime.now()
    if not _regularSession(moment):
        try:
            noteTouchWait([])
        except Exception:
            pass
        _safeLog(None, "[PBK01] 장외 시세 무시 폴링 안 함")
        return {
            "plan": {"subscribe": []},
            "rows": list(rows or []),
            "summary": {},
            "stale": [],
            "skipped": "offsession",
        }
    built = _attachPrices(_universeRows(rows, positions))
    budget = pbkQuoteBudget()
    registered = set(budget.get("registered") or [])

    def _size(code):
        return len(_rtItemsOf(code) or [code])

    plan = planPbkQuotes(
        built,
        budget.get("kept"),
        _size,
        usedItems=budget.get("usedItems") or 0,
        usedStocks=budget.get("usedStocks") or 0,
    )
    syncPbkTouch(plan["subscribe"], reason)
    ticks = {row["stkCd"]: row for row in built}
    stale = stalePollEntries(built, ticks, moment, registered=registered)
    limit = POLL_PER_CYCLE if pollLimit is None else int(pollLimit)
    batch = pollBatch(
        built, ticks, moment, registered=registered, limit=limit, priorityCodes=touchWaitCodes(),
    )
    getter = fetch or _fetchKa10001
    polled = 0
    for code in batch:
        try:
            got = getter(code) or {}
        except Exception:
            got = {}
        if notePolledPx(code, got.get("price"), got.get("tm") or "", got.get("low")):
            polled += 1
    summary = summarizeQuotes(built, {row["stkCd"]: row for row in _attachPrices(built)})
    summary["stale"] = len(stale)
    _lastQuoteSummary = summary
    reasons = {}
    for item in stale:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    why = " ".join(f"{key} {cnt}" for key, cnt in sorted(reasons.items())) or "없음"
    dropped = unmatchedTickCount()
    _safeLog(
        None,
        f"[PBK01] 0B 대상 {len(plan['subscribe'])}종 · stale 폴링 {len(stale)}종 "
        f"이번 {polled}종 ({why}) 등록밖틱 {dropped} ({reason})",
    )
    return {"plan": plan, "rows": built, "summary": summary, "stale": stale}


def _paperSession():
    from config.config import paper_host_url

    mode = "paper"
    try:
        from utils.tradeSession import getDeskTradeMode

        mode = getDeskTradeMode()
    except Exception:
        mode = "paper"
    return {"mode": mode, "host": paper_host_url}


def _saveBook(path, ymd, candidates, positions):
    from trading.pbkOrderPlan import fillBookNames
    from utils.pbk01Store import appendRow

    positions = fillBookNames(positions, candidates)
    appendRow(path, {
        "kind": "state",
        "announcedYmd": str(ymd or ""),
        "scannedAt": datetime.now().strftime("%H:%M"),
        "candidates": list(candidates or []),
        "positions": list(positions or []),
    })


def _missedFile(ymd):
    from trading.pbkOrderPlan import missedPath
    from utils.pbk01Store import pbkPath

    return missedPath(pbkPath(".").parent, ymd)


_touchWaitCds = []


def noteTouchWait(codes):
    """직전 사이클에서 시세가 낡아 건너뛴 종목. 다음 폴링에서 먼저 받는다."""
    global _touchWaitCds
    _touchWaitCds = [str(code) for code in (codes or []) if str(code or "").strip()]


def touchWaitCodes():
    return list(_touchWaitCds)


def placePaperCycle(result, broker=None, session=None, log=None, sender=None, path=None, now=None, chaseMaxPct=None, requireBroker=False, askFetch=None, cash=None):
    """신선한 터치만 모의 주문. 놓친 터치는 파일에만 남긴다."""
    from strategies.impl.pullbackTwoStep import PaperOnlyError
    from trading.pbkOrderPlan import applyPaperResult, buildLiveActions, pbkChaseMaxPct
    from utils.pbk01Store import appendRow, latestByKind, pbkPath

    if not (result or {}).get("enabled"):
        return {"actions": [], "missed": [], "skips": []}
    filePath = path or pbkPath("pbk01_state.jsonl")
    saved = latestByKind(filePath, "state") or {}
    positions = list((result or {}).get("positions") or saved.get("positions") or [])
    candidates = list((result or {}).get("candidates") or saved.get("candidates") or [])
    ymd = str((result or {}).get("ymd") or "")
    hm = str((result or {}).get("hm") or "")
    pct = pbkChaseMaxPct() if chaseMaxPct is None else chaseMaxPct
    quotes = dict((result or {}).get("quotes") or {})
    if askFetch is not None:
        for row in candidates:
            code = str((row or {}).get("stkCd") or "")
            if not code:
                continue
            quote = dict(quotes.get(code) or {})
            if quote.get("ask") not in (None, ""):
                quotes[code] = quote
                continue
            try:
                got = askFetch(code)
            except Exception:
                got = None
            if got not in (None, ""):
                quote["ask"] = got
            quotes[code] = quote
    plan = buildLiveActions(
        candidates,
        quotes,
        positions,
        hm,
        ymd,
        chaseMaxPct=pct,
        now=now,
        requireBroker=requireBroker,
        cash=cash,
    )
    for row in plan["missed"]:
        _safeLog(log, f"[PBK01] 당일 터치 놓침 {row.get('stkCd')} {row.get('lineName')} 괴리 {row.get('gapPct')}%")
        try:
            appendRow(_missedFile(row.get("ymd") or ymd), row)
        except Exception as err:
            _safeLog(log, f"[PBK01] 놓침 기록 실패 {type(err).__name__}")
    waited = []
    for row in plan["skips"]:
        if row.get("reason") == "대금미달":
            _logAmtHold(row.get("stkCd"), log=log)
        elif row.get("reason") in ("시세 대기", "슬롯한도", "시간외", "매도대기", "잔고불일치"):
            _safeLog(log, f"[PBK01] 스킵 {row.get('stkCd')} {row.get('reason')}")
        if row.get("reason") == "시세 대기" and row.get("stkCd"):
            waited.append(row.get("stkCd"))
    noteTouchWait(waited)
    useBroker = broker or paperBroker
    sess = session if session is not None else _paperSession()
    failed = set()
    for action in plan["actions"]:
        code = str(action.get("stkCd") or "")
        if action.get("side") == "BUY" and code in failed:
            continue
        positions = applyPaperResult(positions, action, "pending", ymd=ymd)
        _saveBook(filePath, ymd, candidates, positions)
        try:
            got = useBroker(action, sess) or {}
        except PaperOnlyError:
            failed.add(code)
            positions = applyPaperResult(positions, action, "fail", ymd=ymd)
            _saveBook(filePath, ymd, candidates, positions)
            _safeLog(log, f"[PBK01] 거절 {action.get('stkCd')} 실전가드")
            continue
        except Exception as err:
            failed.add(code)
            positions = applyPaperResult(positions, action, "fail", ymd=ymd)
            _saveBook(filePath, ymd, candidates, positions)
            _safeLog(log, f"[PBK01] 거절 {action.get('stkCd')} {type(err).__name__}")
            continue
        if not got.get("ok"):
            failed.add(code)
            positions = applyPaperResult(positions, action, "fail", ymd=ymd)
            _saveBook(filePath, ymd, candidates, positions)
            _safeLog(log, f"[PBK01] 거절 {action.get('stkCd')} {got.get('reason') or got.get('errMsg') or '주문실패'}")
            continue
        positions = applyPaperResult(
            positions, action, "filled", ordNo=got.get("ordNo") or "", ymd=ymd,
        )
        _saveBook(filePath, ymd, candidates, positions)
        _safeLog(
            log,
            f"[PBK01] 체결 {action.get('side')} {action.get('stkCd')} "
            f"{action.get('reason')} {action.get('qty')}주 {action.get('fillPx')}",
        )
        if sender is not None:
            try:
                sender(
                    f"[PBK01] 체결 {action.get('side')} {action.get('stkCd')} "
                    f"{action.get('reason')} {action.get('qty')}주 {action.get('fillPx')}"
                )
            except Exception:
                _safeLog(log, "[PBK01] 텔레그램 전송 실패 RuntimeError")
        if action.get("side") == "BUY":
            try:
                from utils.pbkWatchScreen import applyScreenFill

                applyScreenFill("매수", action.get("stkCd"), action.get("qty"))
            except Exception:
                pass
        elif action.get("side") == "SELL":
            try:
                from utils.pbkWatchScreen import applyScreenFill

                applyScreenFill("매도", action.get("stkCd"), action.get("qty"))
            except Exception:
                pass
    plan["positions"] = positions
    return plan


def notePbkBrokerFill(sideLabel, stkCd, qty, px, ordNo="", cumQty=None):
    """00 체결로 실제 수량·평균만 고친다. 전략 기록가는 그대로다."""
    from trading.pbkOrderPlan import noteBrokerFill
    from utils.pbk01Store import latestByKind, pbkPath

    path = pbkPath("pbk01_state.jsonl")
    saved = latestByKind(path, "state") or {}
    positions = list(saved.get("positions") or [])
    updated, matched = noteBrokerFill(positions, stkCd, sideLabel, qty, px, ordNo, cumQty=cumQty)
    if not matched:
        return {"ok": True, "matched": False}
    _saveBook(path, saved.get("announcedYmd") or "", saved.get("candidates") or [], updated)
    _safeLog(None, f"[PBK01] 실체결 {stkCd} {sideLabel} {qty}주 {px}")
    return {"ok": True, "matched": True}


def _settleBook(filePath, saved, positions, includeToday=False, today="", orderFills=None):
    """실체결로 확정하고 소멸 잔량을 날짜 파일에 남긴다. 재주문은 하지 않는다."""
    from trading.pbkOrderPlan import fillsPath, settlePositions
    from utils.pbk01Store import appendRow

    today = str(today or datetime.now().strftime("%Y%m%d"))
    updated, logs = settlePositions(
        positions,
        today,
        includeToday=includeToday,
        accountKnown=False,
        orderFills=orderFills,
    )
    names = {}
    for item in list(saved.get("candidates") or []) + list(updated or []):
        code = str((item or {}).get("stkCd") or "")
        name = str((item or {}).get("stkNm") or "").strip()
        if code and name and name != code:
            names[code] = name
    for row in logs:
        code = str(row.get("stkCd") or "")
        cur = str(row.get("stkNm") or "").strip()
        if code and (not cur or cur == code) and names.get(code):
            row["stkNm"] = names[code]
    if logs:
        _saveBook(filePath, saved.get("announcedYmd") or today, saved.get("candidates") or [], updated)
        for row in logs:
            try:
                appendRow(fillsPath(filePath.parent, row.get("ymd") or today), row)
            except Exception as err:
                _safeLog(None, f"[PBK01] 체결기록 실패 {type(err).__name__}")
        _safeLog(None, f"[PBK01] 미체결 소멸 {len(logs)}건")
    return updated


def settleIfClosed(path=None, hm="", ymd=""):
    """15:30 이후에는 오늘 주문까지, 그 전에는 전날만 확정한다. 주문은 내지 않는다."""
    from utils.pbk01Store import latestByKind, pbkPath

    textHm = "".join(ch for ch in str(hm or "") if ch.isdigit()).zfill(6)[-6:]
    filePath = path or pbkPath("pbk01_state.jsonl")
    saved = latestByKind(filePath, "state") or {}
    positions = list(saved.get("positions") or [])
    if not positions:
        return []
    return _settleBook(filePath, saved, positions, includeToday=textHm >= "153000")


def restorePaperBook(path=None, accountRows=None, openOrders=None, accountKnown=False, now=None, orderFills=None, disclosures=None, barsByCode=None, names=None):
    """구 상태 파일을 읽어 보유를 유지하고, 잔고·미체결이 있으면 실제 수량만 맞춘다.

    신호일과 청산일이 비어 있으면 후보·공시와 첫 체결일로 채운다.
    지난 장의 미체결은 상태에서만 지우고 취소 주문은 내지 않는다.
    """
    from trading.pbkOrderPlan import reconcilePositions
    from utils.pbk01Store import latestByKind, pbkPath

    filePath = path or pbkPath("pbk01_state.jsonl")
    saved = latestByKind(filePath, "state") or {}
    positions = list(saved.get("positions") or [])
    candidates = list(saved.get("candidates") or [])
    updated = reconcilePositions(
        positions,
        accountRows=accountRows,
        openOrders=openOrders,
        accountKnown=accountKnown,
    )
    mismatches = []
    if accountKnown:
        from trading.pbkOrderPlan import balanceMismatches

        mismatches = balanceMismatches(updated, accountRows)
        bad = {row["stkCd"] for row in mismatches}
        for pos in updated:
            if str(pos.get("stkCd") or "") in bad:
                pos["buyHold"] = True
                pos["buyHoldReason"] = "잔고불일치"
        for row in mismatches:
            _safeLog(
                None,
                f"[PBK01] 잔고 불일치 {row['stkCd']} 상태 {row['stateQty']} 계좌 {row['acctQty']} 신규매수 보류",
            )
    moment = now or datetime.now()
    today = moment.strftime("%Y%m%d")
    includeToday = moment.strftime("%H%M%S") >= "153000"
    discs, bars = _holdContext(updated, disclosures, barsByCode)
    nameMap = _namesFor(updated, candidates, accountRows, names)
    history = _signalHistory(filePath)
    updated = _completeHeld(
        updated, candidates, discs, bars, asOf=today, names=nameMap, history=history,
    )
    for pos in updated:
        code = str(pos.get("stkCd") or "")
        if code and nameMap.get(code):
            pos["stkNm"] = nameMap[code]
    for row in candidates:
        code = str((row or {}).get("stkCd") or "")
        if code and nameMap.get(code) and str(row.get("stkNm") or "") in ("", code):
            row["stkNm"] = nameMap[code]
    saved = dict(saved)
    saved["candidates"] = candidates
    updated = _settleBook(
        filePath,
        saved,
        updated,
        includeToday=includeToday,
        today=today,
        orderFills=orderFills,
    )
    from trading.pbkOrderPlan import _dropExpiredOpenOrders

    for pos in updated:
        _dropExpiredOpenOrders(pos, today, includeToday)
    updated = _completeHeld(
        updated, candidates, discs, bars, asOf=today, names=nameMap, history=history,
    )
    candidates, blocked = _markHeldAmount(candidates, updated, bars)
    for code in blocked:
        _logAmtHold(code, boot=True)
    _saveBook(filePath, saved.get("announcedYmd") or today, candidates, updated)
    held = 0
    for pos in updated:
        if pos.get("released"):
            continue
        try:
            plan = int(pos.get("brokerQty") if pos.get("brokerQty") is not None else pos.get("qty") or 0)
        except (TypeError, ValueError):
            plan = 0
        if plan > 0 or ((pos.get("leg1") or pos.get("leg2")) and not pos.get("settled")):
            held += 1
    _safeLog(None, f"[PBK01] 재기동 보유 {held}종 복원")
    return {"ok": True, "positions": updated, "held": held, "mismatches": mismatches}


def fetchPaperAsk(stkCd, session=None):
    """모의 호가의 매도 1호가. 없거나 실전이면 None. 현재가로 대체하지 않는다."""
    from config.config import paper_host_url, real_host_url
    from trading.pbkOrderPlan import parseAsk

    sess = session if session is not None else _paperSession()
    host = str((sess or {}).get("host") or "")
    mode = str((sess or {}).get("mode") or "")
    if mode != "paper" or host != str(paper_host_url) or host == str(real_host_url) or "mockapi.kiwoom.com" not in host:
        return None
    try:
        from config import runtimeState
        from api.getHoga import fetchHoga
        from utils.trRateLimit import acquireTrSlot

        token = runtimeState.getToken("paper")
        if not token:
            return None
        acquireTrSlot("ka10004", mode="paper")
        got = fetchHoga(paper_host_url, token, stkCd, tokenType=runtimeState.paperTokenType or "bearer")
    except Exception:
        return None
    if not got or not got.get("ok"):
        return None
    return parseAsk(got.get("body") or {})


def fetchPaperBook(session=None, accountFetch=None, openFetch=None, cntrFetch=None):
    """모의 호스트의 잔고·미체결만 읽는다. 실전 호스트는 호출하지 않는다."""
    from config.config import paper_host_url, real_host_url
    from trading.pbkOrderPlan import parseAccountHolds, parseOpenOrders

    sess = session if session is not None else _paperSession()
    host = str((sess or {}).get("host") or "")
    mode = str((sess or {}).get("mode") or "")
    if mode != "paper" or host != str(paper_host_url) or host == str(real_host_url) or "mockapi.kiwoom.com" not in host:
        return {"ok": False, "reason": "PAPER_ONLY", "account": [], "orders": []}
    if accountFetch is None or openFetch is None:
        from config import runtimeState

        token = runtimeState.getToken("paper")
        tokenType = runtimeState.paperTokenType or "bearer"
        if not token:
            return {"ok": False, "reason": "NO_TOKEN", "account": [], "orders": []}

        def accountFetch():
            from api.getAcntEvlt import fetchAcntEvlt
            from utils.trRateLimit import acquireTrSlot

            acquireTrSlot("kt00004", mode="paper")
            return fetchAcntEvlt(paper_host_url, token, tokenType=tokenType)

        def openFetch():
            from api.getOpenOrd import fetchOpenOrd
            from utils.trRateLimit import acquireTrSlot

            acquireTrSlot("ka10075", mode="paper")
            return fetchOpenOrd(paper_host_url, token, tokenType=tokenType)

        if cntrFetch is None:
            def cntrFetch():
                from api.getCntr import fetchCntr
                from utils.trRateLimit import acquireTrSlot

                acquireTrSlot("ka10076", mode="paper")
                return fetchCntr(paper_host_url, token, tokenType=tokenType)

    try:
        acct = accountFetch() or {}
    except Exception as err:
        return {"ok": False, "reason": type(err).__name__, "errMsg": str(err), "account": [], "orders": []}
    if not acct.get("ok"):
        return {
            "ok": False,
            "reason": "ACCOUNT",
            "errMsg": str(acct.get("errMsg") or ""),
            "account": [],
            "orders": [],
        }
    try:
        opened = openFetch() or {}
    except Exception as err:
        return {"ok": False, "reason": type(err).__name__, "errMsg": str(err), "account": [], "orders": []}
    if not opened.get("ok"):
        return {
            "ok": False,
            "reason": "OPEN",
            "errMsg": str(opened.get("errMsg") or ""),
            "account": [],
            "orders": [],
        }
    fills = {}
    if cntrFetch is not None:
        try:
            booked = cntrFetch() or {}
        except Exception:
            booked = {}
        if booked.get("ok"):
            from trading.pbkOrderPlan import parseFillQty

            fills = parseFillQty(booked.get("body") or {})
    return {
        "ok": True,
        "account": parseAccountHolds(acct.get("body") or {}),
        "orders": parseOpenOrders(opened.get("body") or {}),
        "fills": fills,
    }


def _bookAuthRetry(snap):
    """토큰이 아직 없거나 8005면 잠시 뒤 같은 조회를 다시 한다."""
    from utils.tokenAuth import isTokenAuthFailText

    reason = str((snap or {}).get("reason") or "")
    err = str((snap or {}).get("errMsg") or "")
    if reason == "NO_TOKEN":
        return True
    if reason in ("ACCOUNT", "OPEN") and isTokenAuthFailText(err):
        return True
    return False


def _logBookDone(priorPositions, accountRows):
    """대조 전 상태 수량과 계좌 수량. 아직 없던 수량은 불일치로 세지 않는다."""
    acct = {}
    for row in accountRows or []:
        code = str((row or {}).get("stkCd") or "")
        if not code:
            continue
        try:
            acct[code] = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            acct[code] = 0
    bad = []
    for pos in priorPositions or []:
        if not isinstance(pos, dict) or pos.get("brokerQty") is None:
            continue
        code = str(pos.get("stkCd") or "")
        try:
            stateQty = int(pos.get("brokerQty") or 0)
        except (TypeError, ValueError):
            stateQty = 0
        acctQty = int(acct.get(code) or 0)
        if code and stateQty != acctQty and (stateQty > 0 or acctQty > 0):
            bad.append((code, stateQty, acctQty))
    if bad:
        bits = " ".join(f"{code} 상태{state} 계좌{held}" for code, state, held in bad)
        _safeLog(None, f"[PBK01] 잔고 대조 완료 불일치 {len(bad)}종 {bits}")
        return
    bits = " ".join(f"{code} {qty}" for code, qty in sorted(acct.items()))
    _safeLog(None, f"[PBK01] 잔고 대조 완료 일치 {len(acct)}종 {bits}".rstrip())


def bootPaperBook(path=None, session=None, accountFetch=None, openFetch=None, now=None, retryPause=1.05, maxAttempts=8):
    """재기동 직후. 토큰 8005면 준비될 때까지 다시 읽고, 끝나면 수량을 남긴다."""
    import time

    tries = max(1, int(maxAttempts or 1))
    snap = {"ok": False, "reason": "NO_TOKEN", "errMsg": ""}
    for attempt in range(tries):
        snap = fetchPaperBook(session=session, accountFetch=accountFetch, openFetch=openFetch)
        if snap.get("ok") or not _bookAuthRetry(snap) or attempt + 1 >= tries:
            break
        _safeLog(None, f"[PBK01] 잔고 대조 재시도 {attempt + 1} {snap.get('reason') or ''}")
        if retryPause:
            time.sleep(float(retryPause))
    if not snap.get("ok"):
        err = str(snap.get("errMsg") or "").strip()
        detail = f" {err}" if err else ""
        _safeLog(None, f"[PBK01] 잔고 대조 보류 {snap.get('reason') or ''}{detail}".rstrip())
        restored = restorePaperBook(path=path, accountKnown=False, now=now)
        restored["reconciled"] = False
        return restored
    from utils.pbk01Store import latestByKind, pbkPath

    prior = (latestByKind(path or pbkPath("pbk01_state.jsonl"), "state") or {}).get("positions") or []
    restored = restorePaperBook(
        path=path,
        accountRows=snap.get("account") or [],
        openOrders=snap.get("orders") or [],
        accountKnown=True,
        now=now,
        orderFills=snap.get("fills") or None,
    )
    restored["reconciled"] = True
    _logBookDone(prior, snap.get("account") or [])
    for row in restored.get("mismatches") or []:
        text = (
            f"[PBK01] 잔고 불일치 {row['stkCd']} 상태 {row['stateQty']} "
            f"계좌 {row['acctQty']} 신규매수 보류"
        )
        try:
            from telegram.telSend import sendTelegramMsg

            sendTelegramMsg(text)
        except Exception:
            _safeLog(None, "[PBK01] 텔레그램 전송 실패 RuntimeError")
    return restored


def notePbkAccountQty(accountRows):
    """잔고 조회의 실제 수량을 PBK 포지션에 붙인다."""
    from trading.pbkOrderPlan import applyAccountQty
    from utils.pbk01Store import latestByKind, pbkPath

    path = pbkPath("pbk01_state.jsonl")
    saved = latestByKind(path, "state") or {}
    positions = list(saved.get("positions") or [])
    if not positions:
        return {"ok": True, "changed": False}
    updated, changed = applyAccountQty(positions, accountRows)
    if changed:
        _saveBook(path, saved.get("announcedYmd") or "", saved.get("candidates") or [], updated)
    return {"ok": True, "changed": changed}


_unfilledLoggedYmd = ""


def logUnfilledRemainder(path=None, hm="", ymd=""):
    """장 마감 미체결 잔량. 취소 주문은 내지 않고 하루 한 번만 남긴다."""
    global _unfilledLoggedYmd
    from trading.pbkOrderPlan import unfilledRemainder
    from utils.pbk01Store import latestByKind, pbkPath

    textHm = "".join(ch for ch in str(hm or "") if ch.isdigit())
    textHm = textHm.zfill(6)[-6:]
    day = str(ymd or datetime.now().strftime("%Y%m%d"))
    if textHm < "153000" or _unfilledLoggedYmd == day:
        return []
    filePath = path or pbkPath("pbk01_state.jsonl")
    saved = latestByKind(filePath, "state") or {}
    rows = unfilledRemainder(saved.get("positions") or [])
    for row in rows:
        _safeLog(
            None,
            f"[PBK01] 미체결 잔량 {row['stkCd']} 주문 {row['planQty']} 실체결 {row['brokerQty']} "
            f"잔량 {row['leftQty']}",
        )
    _unfilledLoggedYmd = day
    return rows


def _beatLoop():
    """스캔이 오래 걸려도 1분마다 생존 로그를 남긴다."""
    while True:
        time.sleep(60)
        _safeLog(None, "[PBK01] 살아있음")


def _loop():
    global _lastSummaryAt
    from trading.pbkQuotePlan import formatSummary, shouldBeat

    first = True
    _safeLog(None, "[PBK01] 루프 시작")
    try:
        bootPaperBook()
    except Exception as err:
        _safeLog(None, f"[PBK01] 재기동 복원 실패 {type(err).__name__}")
    while True:
        started = time.time()
        _safeLog(None, "[PBK01] 사이클 시작")
        result = {}
        try:
            def _send(text):
                from telegram.telSend import sendTelegramMsg

                sendTelegramMsg(text)

            result = runPbkCycle(force=first, sender=_send) or {}
            from trading.pbkOrderPlan import paperCash

            placed = placePaperCycle(
                result, sender=_send, requireBroker=True, askFetch=fetchPaperAsk, cash=paperCash(),
            ) or {}
        except Exception as err:
            _safeLog(None, f"[PBK01] 루프 오류 {type(err).__name__}")
            placed = {}
        elapsed = time.time() - started
        touches = result.get("touches") or []
        _safeLog(
            None,
            f"[PBK01] 사이클 끝 {elapsed:.0f}s 터치 {len(touches)} "
            f"주문 {len(placed.get('actions') or [])} "
            f"놓침 {len(placed.get('missed') or [])}",
        )
        if shouldBeat(_lastSummaryAt, time.time(), 300):
            _safeLog(None, formatSummary(_lastQuoteSummary))
            _lastSummaryAt = time.time()
        try:
            hmNow = datetime.now().strftime("%H%M%S")
            logUnfilledRemainder(hm=hmNow)
            settleIfClosed(hm=hmNow)
        except Exception:
            pass
        first = False
        time.sleep(30)


def paperBroker(action, session):
    """모의 호스트로만 kt10000. 실전 URL 은 주문 인자로 쓰지 않는다."""
    from strategies.impl.pullbackTwoStep import submitPbkOrder

    def _send(act, sess):
        from config.config import paper_host_url
        from config import runtimeState

        token = runtimeState.getToken("paper")
        tokenType = runtimeState.paperTokenType or "bearer"
        if not token or str(sess.get("host") or "") != str(paper_host_url):
            return {"ok": False, "sent": False, "reason": "HOST_NOT_PAPER"}
        code = str(act.get("stkCd") or "")
        qty = str(int(act.get("qty") or 0))
        trdeTp = str(act.get("trdeTp") or "0")
        limit = act.get("limitPx")
        ordUv = "" if limit in (None, "") else str(int(limit))
        if act.get("side") == "BUY":
            from api.buyStk import placeBuyOrd

            result = placeBuyOrd(
                paper_host_url, token, "KRX", code, qty, trdeTp, ordUv=ordUv, tokenType=tokenType,
            )
        elif act.get("side") == "SELL":
            from api.sellStk import placeSellOrd

            result = placeSellOrd(
                paper_host_url, token, "KRX", code, qty, trdeTp, ordUv=ordUv, tokenType=tokenType,
            )
        else:
            return {"ok": False, "sent": False, "reason": "NO_ORDER"}
        result = dict(result or {})
        result["sent"] = bool(result.get("ok"))
        return result

    return submitPbkOrder(action, session, _send, paperOnly=True)


def startPbk01Live():
    """데스크 기동 시 후보 루프를 연다. 주문은 설정이 켜지고 paperOnly 일 때만."""
    global _started
    with _lock:
        if _started:
            return {"started": True, "reason": "ALREADY"}
        _started = True
        threading.Thread(target=_loop, name="pbk01Live", daemon=True).start()
        threading.Thread(target=_beatLoop, name="pbk01Beat", daemon=True).start()
    _safeLog(None, "[PBK01] 후보 루프 기동. 주문은 모의·설정 스위치가 켜질 때만")
    return {"started": True, "reason": "CANDIDATE_LOOP"}
