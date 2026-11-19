"""PBK01 모의 연결. 기본 비활성. 실전 호스트로는 주문 함수가 없다.

켜져도 paperOnly 와 모의 호스트가 아니면 주문을 던지고 로그만 남긴다.
후보는 확정 일봉만 본다. 장대양봉 당일에는 사지 않는다.
"""

from __future__ import annotations

import threading

_started = False
_lock = threading.Lock()
_announcedYmd = ""


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


def isPbkEnabled():
    from strategies.impl.pullbackTwoStep import loadPbkOptions

    opt = loadPbkOptions()
    if not opt.get("paperOnly"):
        return False
    try:
        from config.settingsStore import loadSettings

        settings = loadSettings() or {}
    except Exception:
        settings = {}
    row = (settings.get("stratLive") or {}).get("PBK01")
    if not isinstance(row, dict) or row.get("enabled") is not True:
        return False
    if row.get("paperOnly") is not True:
        return False
    return bool(opt.get("enabled"))


def deskBoardText():
    """저장본이 있으면 그 후보·슬롯을 보여 준다. 주문은 하지 않는다."""
    from strategies.impl.pullbackTwoStep import formatCandidateLog, formatSlotLog

    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(pbkPath("pbk01_state.jsonl"), "state") or {}
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
    """스펙·설정이 꺼져 있으면 스레드를 만들지 않는다."""
    global _started
    if not isPbkEnabled() and heldPositionCount() <= 0:
        try:
            from utils.termLog import termLog

            termLog("[PBK01] 비활성 — 기동 생략")
        except Exception:
            pass
        return {"started": False, "reason": "DISABLED"}
    with _lock:
        if _started:
            return {"started": True, "reason": "ALREADY"}
        _started = True
    try:
        from utils.termLog import termLog

        termLog("[PBK01] 모의 루프 기동. 실전 주문 경로 없음")
    except Exception:
        pass
    return {"started": True, "reason": "PAPER_LOOP"}
