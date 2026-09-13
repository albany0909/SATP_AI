"""
코스피·코스닥·코스피200 업종지수 실시간(0J).
매매창 상단 표시용. 주문·전략과 무관하다.
종목 0B(grp 2)와 겹치지 않게 grp_no=3.
명세: tr_docs/0J.json , 업종코드는 ka20006.json 과 동일.
"""

import threading

from realtime.wsHub import (
    getWsClient,
    registerLoginHandler,
    registerMsgHandler,
)
from utils.termLog import termLog

_HANDLER_NAME = "indsRt0J"
_LOGIN_HANDLER_NAME = "indsRt0JLogin"
_GRP_NO = "3"
# 코스피 / 코스닥 / 코스피200(선물지수 표시)
_INDS_CDS = ("001", "101", "201")

_lock = threading.Lock()
_lastInds = {}
_started = False
# 일봉 종가 시드는 한 번만. 없는 코드(201 미적재 등)를 1초마다 재조회하지 않는다.
_seedTried = False


def _normIndsCd(raw):
    """0J item → 3자리 업종코드. 짧은 숫자도 001 형태로 맞춘다."""
    text = str(raw or "").strip().upper()
    if text.endswith("_NX") or text.endswith("_AL"):
        text = text[:-3]
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return digits.zfill(3)[-3:]
    return text


def parseIndsPrc(raw):
    """
    0J FID 10. 소수점이 있으면 그대로, 없으면 절대값 10000 이상을 100배값으로 본다.
    ka20006 일봉과 같은 스케일 규칙.
    """
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if not text or text in ("-", "--"):
        return None
    if text[:1] in "+-":
        text = text[1:]
    try:
        if "." in text:
            return abs(float(text))
        num = abs(float(text))
    except (TypeError, ValueError):
        return None
    if num >= 10000:
        return num / 100.0
    return num


def parseIndsFlu(raw):
    """0J FID 12 등락율(%)."""
    if raw is None:
        return None
    text = str(raw).replace("%", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def getIndsLastMap():
    """매매창용. {indsCd: {prc, fluRt, tm, src, tradeDt?}}. 0J 없으면 일봉 종가."""
    _seedFromDayPrc()
    with _lock:
        return {cd: dict(info) for cd, info in _lastInds.items()}


def _seedFromDayPrc():
    """0J 오기 전·장후 실시간 없을 때 마지막 일봉 종가로 칸을 채운다."""
    global _seedTried
    with _lock:
        missing = [cd for cd in _INDS_CDS if cd not in _lastInds]
    if not missing or _seedTried:
        return
    try:
        from utils.dayIndsPrcStore import loadIndsLastCloseMap
        closeMap = loadIndsLastCloseMap(_INDS_CDS) or {}
    except Exception as err:
        # DB/환경이 아직 안 뜬 경우 다음 1초 페인트에서 재시도
        termLog(f"[indsRt] 전일지수 시드 실패: {err}")
        return
    _seedTried = True
    with _lock:
        for cd in _INDS_CDS:
            if cd in _lastInds:
                continue
            info = closeMap.get(cd)
            if not info or info.get("prc") is None:
                continue
            _lastInds[cd] = {
                "prc": info.get("prc"),
                "fluRt": info.get("fluRt"),
                "tm": "",
                "tradeDt": info.get("tradeDt") or "",
                "src": "db",
            }


def _sessionForClose():
    """ka20006 폴백용 세션. 모의 우선."""
    from config import runtimeState
    from config.config import paper_host_url, real_host_url

    if runtimeState.isPaperLoggedIn():
        return {
            "hostUrl": paper_host_url,
            "token": runtimeState.getToken("paper"),
            "tokenType": runtimeState.paperTokenType or "bearer",
            "isPaper": True,
        }
    if runtimeState.isRealLoggedIn():
        return {
            "hostUrl": real_host_url,
            "token": runtimeState.getToken("real"),
            "tokenType": runtimeState.realTokenType or "bearer",
            "isPaper": False,
        }
    return None


def _applyCloseSeed(cd, info, src):
    """0J가 이미 있으면 덮지 않는다."""
    if not info or info.get("prc") is None:
        return False
    with _lock:
        cur = _lastInds.get(cd) or {}
        if str(cur.get("src") or "") == "0J":
            return False
        _lastInds[cd] = {
            "prc": info.get("prc"),
            "fluRt": info.get("fluRt"),
            "tm": "",
            "tradeDt": info.get("tradeDt") or "",
            "src": src,
        }
    return True


def _refreshCloseFromTr():
    """
    장후·0J 미수신 시 ka20006 최근 종가로 보강.
    201은 일봉 테이블에 없을 수 있고, 001/101도 적재일이 밀릴 수 있다.
    """
    import time
    from config import runtimeState

    for _ in range(40):
        if runtimeState.isLoggedIn():
            break
        time.sleep(0.25)
    sess = _sessionForClose()
    if sess is None:
        return
    try:
        from api.getDayIndsPrc import fetchLastIndsClose
    except Exception as err:
        termLog(f"[indsRt] 종가 TR import 실패: {err}")
        return
    waitSec = 1.05 if sess["isPaper"] else 0.3
    first = True
    filled = []
    for cd in _INDS_CDS:
        with _lock:
            srcNow = str((_lastInds.get(cd) or {}).get("src") or "")
        if srcNow == "0J":
            continue
        if not first:
            time.sleep(waitSec)
        first = False
        info = fetchLastIndsClose(
            sess["hostUrl"],
            sess["token"],
            cd,
            tokenType=sess["tokenType"],
        )
        if _applyCloseSeed(cd, info, "tr"):
            filled.append(cd)
    if filled:
        termLog(f"[indsRt] 일봉 종가 보강 {','.join(filled)}")


def _regInds(ws, reason):
    if ws is None or not getattr(ws, "isLoggedIn", False):
        termLog(f"[indsRt] WS 미로그인 — 0J 보류 ({reason})")
        return
    ok = ws.sendReg(["0J"], list(_INDS_CDS), grpNo=_GRP_NO, refresh="1")
    termLog(
        f"[indsRt] 0J 구독 {'OK' if ok else '실패'} "
        f"{','.join(_INDS_CDS)} ({reason}) grp={_GRP_NO}"
    )


def _onLogin(ws):
    _regInds(ws, "LOGIN")


def _onWsMessage(msg):
    if not isinstance(msg, dict) or msg.get("trnm") != "REAL":
        return
    for row in msg.get("data") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "") != "0J":
            continue
        cd = _normIndsCd(row.get("item") or (row.get("values") or {}).get("9001"))
        if cd not in _INDS_CDS:
            continue
        vals = row.get("values") or {}
        prc = parseIndsPrc(vals.get("10"))
        flu = parseIndsFlu(vals.get("12"))
        with _lock:
            info = dict(_lastInds.get(cd) or {})
            if prc is not None:
                info["prc"] = prc
            if flu is not None:
                info["fluRt"] = flu
            info["tm"] = str(vals.get("20") or "")
            info["src"] = "0J"
            _lastInds[cd] = info


def startIndsRtMonitor():
    """main.py 에서 WS 기동 전에 호출. LOGIN 훅으로 0J 등록."""
    global _started
    if _started:
        return
    _started = True
    _seedFromDayPrc()
    registerMsgHandler(_HANDLER_NAME, _onWsMessage)
    registerLoginHandler(_LOGIN_HANDLER_NAME, _onLogin)
    ws = getWsClient()
    if ws is not None and getattr(ws, "isLoggedIn", False):
        _regInds(ws, "기동(이미 LOGIN)")
    threading.Thread(
        target=_refreshCloseFromTr, name="indsCloseSeed", daemon=True
    ).start()
    termLog("[indsRt] 코스피/코스닥/코스피200 0J 감시 시작")
