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


def announceCandidates(rows, positions, ymd, sender=None, log=None, path=None):
    """하루에 한 번 로그. sender 가 있으면 텔레그램도 한 번."""
    from strategies.impl.pullbackTwoStep import formatCandidateLog, formatSlotLog
    from utils.pbk01Store import appendRow, latestByKind, pbkPath

    filePath = path or pbkPath("pbk01_state.jsonl")
    prev = latestByKind(filePath, "state") or {}
    if str(prev.get("announcedYmd") or "") == str(ymd):
        return {"sent": False, "reason": "already"}
    text = formatCandidateLog(rows)
    slotLine = formatSlotLog(positions)
    if log is not None:
        log(text)
        log(slotLine)
    else:
        from utils.termLog import termLog

        termLog(text)
        termLog(slotLine)
    if sender is not None:
        sender(text)
    appendRow(filePath, {
        "kind": "state",
        "announcedYmd": str(ymd),
        "candidates": list(rows or []),
        "positions": list(positions or []),
    })
    return {"sent": True, "text": text}


def buildScan(barsByCode, disclosuresByCode, today, positions=None):
    """findCandleEvent 로 종목당 신호를 만들고 candidateRows 로 후보를 고른다."""
    from strategies.impl.pullbackTwoStep import candidateRows, findCandleEvent

    events = []
    for code, bars in (barsByCode or {}).items():
        event = findCandleEvent(
            bars,
            (disclosuresByCode or {}).get(code) or [],
            code,
            asOf=today,
        )
        if event:
            events.append(event)
    rows = candidateRows(events, positions or [], barsByCode, today)
    return events, rows


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


def defaultScanInputs(today):
    """관심·보유 일봉과 공시. 키는 로그에 남기지 않는다. 실패하면 빈 목록."""
    codes = []
    try:
        from utils.watchDayPrcLoad import collectWatchHoldCds

        codes = collectWatchHoldCds() or []
    except Exception:
        _safeLog(None, "[PBK01] 종목 목록을 읽지 못함")
    bars = {}
    discs = {}
    for code in codes:
        try:
            from utils.chartBarLoad import loadStoredDayBars

            bars[code] = loadStoredDayBars(code) or []
        except Exception:
            bars[code] = []
        try:
            from research.dartDiscFetch import fetchDartDiscList

            got = fetchDartDiscList(code, daysBack=60) or {}
            discs[code] = [
                {
                    "title": item.get("reportNm"),
                    "date": item.get("rceptDt"),
                    "rceptNo": item.get("rceptNo"),
                }
                for item in (got.get("items") or [])
                if isinstance(item, dict)
            ]
        except Exception:
            _safeLog(None, f"[PBK01] 공시 조회 실패 {code}")
            discs[code] = []
    positions = []
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        positions = (latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}).get("positions") or []
    except Exception:
        positions = []
    return {"bars": bars, "disclosures": discs, "positions": positions, "quotes": {}}


def runPbkCycle(now=None, loader=None, sender=None, log=None, path=None, quotes=None, force=False, enabled=None):
    """기동 1회(force) 또는 거래일 scanHm 이후에 후보를 한 번 알린다.

    장중 터치는 방금 계산했거나 이미 저장된 후보만 사용한다.
    """
    global _announcedYmd
    now = now or datetime.now()
    ymd = now.strftime("%Y%m%d")
    hm = now.strftime("%H%M%S")
    trading = True
    try:
        from config.krHoliday import isTradingDay

        trading = bool(isTradingDay(now))
    except Exception:
        trading = True
    due = bool(force) or (trading and hm >= scanHm() and _announcedYmd != ymd)
    if enabled is None:
        enabled = isPbkEnabled()
    positions = []
    rows = []
    if due and _announcedYmd != ymd:
        pack = {}
        try:
            pack = (loader or defaultScanInputs)(ymd) or {}
        except Exception:
            _safeLog(log, "[PBK01] 후보 계산 실패")
            pack = {}
        positions = pack.get("positions") or []
        _events, rows = buildScan(
            pack.get("bars") or {},
            pack.get("disclosures") or {},
            ymd,
            positions,
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
