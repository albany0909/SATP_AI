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
    """저장본이 있으면 그 후보·슬롯을 보여 준다. 주문은 하지 않는다."""
    from strategies.impl.pullbackTwoStep import formatCandidateLog, formatSlotLog

    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(path or pbkPath("pbk01_state.jsonl"), "state") or {}
    except Exception:
        row = {}
    cand = formatCandidateLog(row.get("candidates") or [])
    slots = formatSlotLog(row.get("positions") or [])
    if not row:
        return "PBK01 중지 · 매수 후보 0건"
    return cand + "\n" + slots


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
    if changed and sender is not None:
        sender(text)
    appendRow(filePath, {
        "kind": "state",
        "announcedYmd": str(ymd),
        "candidateKey": candidateSignature(rows),
        "candidates": list(rows or []),
        "positions": list(positions or []),
    })
    return {"sent": bool(changed and sender is not None), "changed": changed, "text": text}


def _barsForSignal(bars):
    """저장 일봉의 qty 를 거래량으로 맞춘다."""
    out = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        row = dict(bar)
        if row.get("volume") is None and row.get("qty") is not None:
            row["volume"] = row.get("qty")
        if row.get("amount") is None:
            try:
                row["amount"] = float(row.get("close") or 0) * float(row.get("volume") or 0)
            except (TypeError, ValueError):
                row["amount"] = None
        out.append(row)
    return out


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


def _safeLog(log, text):
    if log is not None:
        log(text)
        return
    try:
        from utils.termLog import termLog

        termLog(text)
    except Exception:
        pass


def quotesFromCache(codes):
    """후보 종목의 캐시 현재가만 읽는다. 목록 밖은 보지 않는다."""
    out = {}
    try:
        from trading.watchRtMonitor import peekWatchLastPx
    except Exception:
        return out
    for code in codes or []:
        info = peekWatchLastPx(code) or {}
        raw = info.get("curPrc") or info.get("krxPrc") or info.get("nxtPrc")
        try:
            px = float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if px > 0:
            out[str(code)] = {"price": px}
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
        _announcedYmd = ymd
        if dueStartup:
            _startupDone = True
        if dueDaily:
            _scanYmd = ymd
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
    owned = []
    for pos in positions or []:
        if int((pos or {}).get("qty") or 0) > 0 or (pos or {}).get("leg1"):
            owned.append(pos.get("stkCd"))
    touches = []
    if enabled:
        if not quotes:
            quotes = quotesFromCache([row.get("stkCd") for row in rows])
        touches = touchesForQuotes(rows, quotes or {}, owned=owned)
    return {
        "announced": _announcedYmd,
        "candidates": rows,
        "touches": touches,
        "enabled": bool(enabled),
    }


def _loop():
    first = True
    while True:
        try:
            def _send(text):
                from telegram.telSend import sendTelegramMsg

                sendTelegramMsg(text)

            runPbkCycle(force=first, sender=_send)
        except Exception:
            _safeLog(None, "[PBK01] 루프 오류")
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
    _safeLog(None, "[PBK01] 후보 루프 기동. 주문은 모의·설정 스위치가 켜질 때만")
    return {"started": True, "reason": "CANDIDATE_LOOP"}
