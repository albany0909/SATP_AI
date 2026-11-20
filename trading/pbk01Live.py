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
        if quotes is None:
            try:
                refreshPbkQuotes(rows, positions, reason="PBK시세")
            except Exception as err:
                _safeLog(log, f"[PBK01] 시세 구독 실패 {type(err).__name__}")
            quotes = quotesFromCache([row.get("stkCd") for row in rows] + list(owned))
        touches = touchesForQuotes(rows, quotes or {}, owned=owned)
    return {
        "announced": _announcedYmd,
        "candidates": rows,
        "touches": touches,
        "enabled": bool(enabled),
    }


_lastSummaryAt = None
_lastQuoteSummary = {}
_pollCursor = 0


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
    return {"price": body.get("cur_prc"), "tm": datetime.now().strftime("%H%M%S")}


def refreshPbkQuotes(rows, positions, reason="PBK시세", fetch=None, pollLimit=None):
    """후보·보유를 0B 에 추가하고, 한도 밖은 ka10001 로 받는다."""
    global _pollCursor, _lastQuoteSummary
    from trading.pbkQuotePlan import POLL_PER_CYCLE, planPbkQuotes, summarizeQuotes, takePollBatch
    from trading.watchRtMonitor import _rtItemsOf, notePolledPx, pbkQuoteBudget, syncPbkTouch

    built = _attachPrices(_universeRows(rows, positions))
    budget = pbkQuoteBudget()

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
    limit = POLL_PER_CYCLE if pollLimit is None else int(pollLimit)
    batch, _pollCursor = takePollBatch(plan["poll"], _pollCursor, limit)
    getter = fetch or _fetchKa10001
    polled = 0
    for code in batch:
        try:
            got = getter(code) or {}
        except Exception:
            got = {}
        if notePolledPx(code, got.get("price"), got.get("tm") or ""):
            polled += 1
    summary = summarizeQuotes(built, {row["stkCd"]: row for row in _attachPrices(built)})
    _lastQuoteSummary = summary
    _safeLog(
        None,
        f"[PBK01] 0B 대상 {len(plan['subscribe'])}종 · 폴링 {len(plan['poll'])}종 "
        f"이번 {polled}종 ({reason})",
    )
    return {"plan": plan, "rows": built, "summary": summary}


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
    while True:
        started = time.time()
        _safeLog(None, "[PBK01] 사이클 시작")
        result = {}
        try:
            def _send(text):
                from telegram.telSend import sendTelegramMsg

                sendTelegramMsg(text)

            result = runPbkCycle(force=first, sender=_send) or {}
        except Exception as err:
            _safeLog(None, f"[PBK01] 루프 오류 {type(err).__name__}")
        elapsed = time.time() - started
        touches = result.get("touches") or []
        _safeLog(None, f"[PBK01] 사이클 끝 {elapsed:.0f}s 터치 {len(touches)}")
        if shouldBeat(_lastSummaryAt, time.time(), 300):
            _safeLog(None, formatSummary(_lastQuoteSummary))
            _lastSummaryAt = time.time()
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
