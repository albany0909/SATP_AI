"""
국내주식 조건검색 목록조회 (ka10171 / WebSocket CNSRLST).
토큰당 WS 세션 1개 제약 — 공유 wsHub 연결을 재사용한다.
"""

import threading
import time

from config import runtimeState
from realtime.wsHub import (
    getWsClient,
    isHubRunning,
    registerMsgHandler,
    startWsHub,
    unregisterMsgHandler,
)
from utils.termLog import termLog

TRNM_CNSRLST = "CNSRLST"
_HANDLER_NAME = "cnsrListWait"
_REQ_LOCK = threading.Lock()

# 최근 조회 결과 캐시 — 이후 조건검색 요청(seq)용
_lastCnsrItems = []
_lastCnsrAt = 0.0


def getLastCnsrItems():
    """마지막 성공 조회 목록 복사본."""
    return list(_lastCnsrItems)


def parseCnsrListData(data):
    """
    CNSRLST data 파싱.
    예: [["0","조건1"], ...] 또는 [{"seq":"0","name":"조건1"}, ...]
    """
    items = []
    if not isinstance(data, list):
        return items

    for row in data:
        seq = ""
        name = ""
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            seq = str(row[0]).strip()
            name = str(row[1]).strip()
        elif isinstance(row, dict):
            seq = str(row.get("seq") or "").strip()
            name = str(row.get("name") or "").strip()
        else:
            continue
        if seq == "" and name == "":
            continue
        items.append({"seq": seq, "name": name})
    return items


def waitWsLoggedIn(timeoutSec):
    """공유 허브 LOGIN 완료까지 대기. 성공 시 ws 클라이언트."""
    if not isHubRunning():
        if not startWsHub():
            return None
    deadline = time.time() + float(timeoutSec)
    while time.time() < deadline:
        ws = getWsClient()
        if ws is not None and ws.isLoggedIn:
            return ws
        time.sleep(0.2)
    return None


def findCnsrItemBySeq(seq):
    """캐시에서 seq에 해당하는 {seq, name} 반환. 없으면 None."""
    target = str(seq or "").strip()
    for item in _lastCnsrItems:
        if str(item.get("seq")) == target:
            return dict(item)
    return None


def fetchCnsrList(timeoutSec=15):
    """
    WebSocket으로 CNSRLST를 보내 조건식 목록을 받는다.
    반환:
      성공: ok=True, items=[{seq, name}, ...], itemCnt
      실패: ok=False, errMsg
    """
    global _lastCnsrItems, _lastCnsrAt

    if not runtimeState.isLoggedIn():
        return {
            "ok": False,
            "errMsg": "조건식 조회 전 login paper 또는 login real이 필요합니다.",
        }

    # 동시 요청 방지 — 응답 핸들러가 하나뿐
    if not _REQ_LOCK.acquire(blocking=False):
        return {"ok": False, "errMsg": "조건식 목록 조회가 이미 진행 중입니다."}

    doneEvent = threading.Event()
    resultBox = {"msg": None}

    def _onMsg(msg):
        if not isinstance(msg, dict):
            return
        if str(msg.get("trnm") or "") != TRNM_CNSRLST:
            return
        resultBox["msg"] = msg
        doneEvent.set()

    try:
        registerMsgHandler(_HANDLER_NAME, _onMsg)

        ws = waitWsLoggedIn(min(10, timeoutSec))
        if ws is None:
            return {
                "ok": False,
                "errMsg": "웹소켓 LOGIN 대기 시간 초과 — 연결/토큰을 확인하세요.",
            }

        # 요청 전송 (명세: body trnm=CNSRLST 고정)
        sent = ws.sendJson({"trnm": TRNM_CNSRLST})
        if not sent:
            return {"ok": False, "errMsg": "CNSRLST 전송 실패"}

        termLog("[CNSRLST] 조건검색 목록 요청 전송")
        if not doneEvent.wait(timeout=float(timeoutSec)):
            return {"ok": False, "errMsg": f"CNSRLST 응답 대기 시간 초과 ({timeoutSec}초)"}

        msg = resultBox["msg"] or {}
        try:
            codeOk = int(msg.get("return_code", 1)) == 0
        except (TypeError, ValueError):
            # return_code 없으면 data 유무로 판단
            codeOk = isinstance(msg.get("data"), list)

        if not codeOk:
            err = msg.get("return_msg") or "조건검색 목록 조회 실패"
            return {
                "ok": False,
                "errMsg": f"[{msg.get('return_code')}] {err}",
                "raw": msg,
            }

        items = parseCnsrListData(msg.get("data"))
        _lastCnsrItems = items
        _lastCnsrAt = time.time()
        termLog(f"[CNSRLST] 조건식 {len(items)}건 수신")
        return {
            "ok": True,
            "items": items,
            "itemCnt": len(items),
            "raw": msg,
        }
    finally:
        unregisterMsgHandler(_HANDLER_NAME)
        _REQ_LOCK.release()
