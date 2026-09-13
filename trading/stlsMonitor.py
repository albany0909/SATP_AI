"""
스탑로스(STLS) 감시.
kt00004로 보유 동기화 → WS(04 잔고, 0B 체결) 수신 → 익절/손절 시 시장가 전량매도.
"""

import threading

from api.getAcntEvlt import fetchAcntEvlt, parsePadNum
from api.sellStk import placeSellOrd
from config.config import paper_host_url, paper_socket_url, real_host_url, real_socket_url
from config import runtimeState
from config.settingsStore import getSlrPct, getTprPct, isWithinMktHours
from realtime.wsHub import (
    getWsClient,
    registerLoginHandler,
    registerMsgHandler,
    unregisterLoginHandler,
    unregisterMsgHandler,
)
from telegram.telSend import sendTelegramMsg
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 매매구분 — 시장가
TRDE_TP_MARKET = "3"

# 전역 상태 (camelCase)
isStlsRunning = False
_session = None  # {mode, hostUrl, socketUrl, token, tokenType}
_STLS_MSG_HANDLER = "stlsMonitor"
_STLS_LOGIN_HANDLER = "stlsMonitorLogin"
_watchLock = threading.RLock()
# Watchlist: stkCd → {rmndQty, avgPrc, locked}
_watchlist = {}


def _pickTradeSession():
    """모의 우선, 없으면 실투자. 없으면 None."""
    if runtimeState.isPaperLoggedIn():
        return {
            "mode": "paper",
            "hostUrl": paper_host_url,
            "socketUrl": paper_socket_url,
            "token": runtimeState.getToken("paper"),
            "tokenType": runtimeState.paperTokenType or "bearer",
        }
    if runtimeState.isRealLoggedIn():
        return {
            "mode": "real",
            "hostUrl": real_host_url,
            "socketUrl": real_socket_url,
            "token": runtimeState.getToken("real"),
            "tokenType": runtimeState.realTokenType or "bearer",
        }
    return None


def _absPrice(raw):
    """부호 포함 가격 문자열 → 양수 int. 실패 시 None."""
    num = parsePadNum(raw)
    if num is None:
        return None
    return abs(int(num))


def _notify(msg):
    """터미널 + 텔레그램 알림."""
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="stls")
    except Exception as err:
        termLog(f"[STLS] 텔레그램 전송 실패: {err}")


def _loadWatchlistFromAcnt(hostUrl, token, tokenType):
    """
    kt00004로 보유종목을 Watchlist에 로드한다.
    반환: (성공여부, 종목수 또는 에러메시지)
    """
    global _watchlist
    result = fetchAcntEvlt(
        hostUrl, token, qryTp="1", dmstStexTp="KRX", tokenType=tokenType
    )
    if not result.get("ok"):
        return False, result.get("errMsg") or "계좌평가 조회 실패"

    body = result.get("body") or {}
    holdings = body.get("stk_acnt_evlt_prst") or []
    newMap = {}
    if isinstance(holdings, list):
        for item in holdings:
            if not isinstance(item, dict):
                continue
            stkCd = normStkCd(item.get("stk_cd"))
            qty = parsePadNum(item.get("rmnd_qty")) or 0
            avgPrc = _absPrice(item.get("avg_prc")) or 0
            if not stkCd or qty <= 0 or avgPrc <= 0:
                continue
            newMap[stkCd] = {
                "rmndQty": int(qty),
                "avgPrc": int(avgPrc),
                "locked": False,
            }

    with _watchLock:
        _watchlist = newMap
    return True, len(newMap)


def _watchStkCodes():
    with _watchLock:
        return list(_watchlist.keys())


def _regBalance(ws):
    """04 잔고 실시간 등록 — item 빈 문자열."""
    ws.sendReg(["04"], [""], grpNo="1", refresh="1")


def _regQuotes(ws, stkCodes, refresh="1"):
    """0B 주식체결 등록."""
    codes = [c for c in (stkCodes or []) if c]
    if not codes:
        return
    ws.sendReg(["0B"], codes, grpNo="1", refresh=refresh)


def _removeQuote(ws, stkCd):
    """단일 종목 0B 해지."""
    if not stkCd:
        return
    ws.sendRemove(["0B"], [stkCd], grpNo="1")


def _calcPlRt(curPrc, avgPrc):
    """손익률(%) = (현재가 - 평단) / 평단 * 100"""
    if not avgPrc:
        return None
    return (float(curPrc) - float(avgPrc)) / float(avgPrc) * 100.0


def _trySellStk(stkCd, reason, plRt, curPrc):
    """
    익절/손절 시장가 전량매도.
    호출 전 locked 처리 권장. 장 시간 밖이면 스킵.
    """
    global _session
    if _session is None:
        return

    if not isWithinMktHours():
        termLog(f"[STLS] 장시간 외 — 매도 보류 {stkCd} ({reason} {plRt:.2f}%)")
        # 장 시작 후 다시 감시되도록 lock 해제
        with _watchLock:
            item = _watchlist.get(stkCd)
            if item:
                item["locked"] = False
        return

    with _watchLock:
        item = _watchlist.get(stkCd)
        if not item:
            return
        ordQty = int(item.get("rmndQty") or 0)
        avgPrc = int(item.get("avgPrc") or 0)

    if ordQty <= 0:
        return

    hostUrl = _session["hostUrl"]
    token = _session["token"]
    tokenType = _session["tokenType"]
    modeLabel = "모의투자" if _session["mode"] == "paper" else "실투자"

    result = placeSellOrd(
        hostUrl,
        token,
        "KRX",
        stkCd,
        str(ordQty),
        TRDE_TP_MARKET,
        ordUv="",
        tokenType=tokenType,
    )

    if result.get("ok"):
        ordNo = result.get("ordNo") or "-"
        _notify(
            f"[STLS][{modeLabel}] {reason} 시장가 매도\n"
            f"종목: {stkCd} / 수량: {ordQty}주\n"
            f"평단: {avgPrc:,} / 현재가: {curPrc:,} / 손익률: {plRt:.2f}%\n"
            f"주문번호: {ordNo}"
        )
        # 체결 전 중복 방지 — Watchlist에서 제거 (04로 확정되면 정리됨)
        with _watchLock:
            _watchlist.pop(stkCd, None)
        ws = _wsClient()
        if ws is not None:
            _removeQuote(ws, stkCd)
        # 전량 청산이면 관심종목 제거 + 최종 수익률 저장 (알고리즘 검증)
        try:
            from trading.maTouchLive import onExternalFullClose

            onExternalFullClose(
                stkCd, curPrc, reason, qty=ordQty, avgPx=avgPrc
            )
        except Exception as err:
            termLog(f"[STLS] 청산후처리 실패 {stkCd}: {err}")
    else:
        err = result.get("errMsg") or "매도 실패"
        _notify(f"[STLS] 매도 실패 ({stkCd}): {err}")
        with _watchLock:
            item = _watchlist.get(stkCd)
            if item:
                item["locked"] = False


def _onTick0B(stkCd, values):
    """0B 체결 — 현재가(10)로 손익률 판단 후 매도."""
    stkCd = normStkCd(stkCd)
    if not stkCd:
        return

    curPrc = _absPrice((values or {}).get("10"))
    if curPrc is None or curPrc <= 0:
        return

    with _watchLock:
        item = _watchlist.get(stkCd)
        if not item or item.get("locked"):
            return
        avgPrc = int(item.get("avgPrc") or 0)
        if avgPrc <= 0:
            return
        # 중복 주문 방지 — 조건 충족 즉시 Lock
        plRt = _calcPlRt(curPrc, avgPrc)
        if plRt is None:
            return
        tprPct = float(getTprPct())
        slrPct = float(getSlrPct())
        hitTp = plRt >= tprPct
        hitSl = plRt <= slrPct
        if not hitTp and not hitSl:
            return
        item["locked"] = True
        reason = "익절" if hitTp else "손절"

    _trySellStk(stkCd, reason, plRt, curPrc)


def _wsClient():
    """공유 WS 허브의 클라이언트."""
    return getWsClient()


def _onBalance04(stkCd, values):
    """
    04 잔고 — 수량(930)·매입단가(931) 갱신.
    수량 0이면 Watchlist 제거 + 0B 해지.
    신규 보유면 Watchlist 추가 + 0B 등록.
    """
    vals = values or {}
    stkCd = normStkCd(stkCd or vals.get("9001"))
    if not stkCd:
        return

    qty = parsePadNum(vals.get("930"))
    if qty is None:
        qty = 0
    qty = int(qty)
    avgPrc = _absPrice(vals.get("931"))

    if qty <= 0:
        with _watchLock:
            existed = stkCd in _watchlist
            _watchlist.pop(stkCd, None)
        ws = _wsClient()
        if existed and ws is not None:
            _removeQuote(ws, stkCd)
            termLog(f"[STLS] 잔고 0 — 감시 해제 {stkCd}")
        return

    # 평단 없으면 기존 값 유지
    with _watchLock:
        prev = _watchlist.get(stkCd)
        if avgPrc is None or avgPrc <= 0:
            avgPrc = int(prev["avgPrc"]) if prev else 0
        if avgPrc <= 0:
            return
        isNew = prev is None
        locked = bool(prev.get("locked")) if prev else False
        _watchlist[stkCd] = {
            "rmndQty": qty,
            "avgPrc": int(avgPrc),
            "locked": locked,
        }

    ws = _wsClient()
    if isNew and ws is not None:
        termLog(f"[STLS] 신규 보유 감시 등록 {stkCd} qty={qty} avg={avgPrc}")
        _regQuotes(ws, [stkCd], refresh="1")


def _regStlsRealtime(ws):
    """STLS용 04·0B 실시간 등록."""
    if ws is None or not isStlsRunning:
        return
    _regBalance(ws)
    codes = _watchStkCodes()
    if codes:
        _regQuotes(ws, codes, refresh="1")
    termLog(f"[STLS] 실시간 등록 — 잔고04 + 체결0B {len(codes)}종목")


def _onStlsLogin(ws):
    _regStlsRealtime(ws)


def _handleWsMessage(msg):
    """WS 수신 메시지 분기."""
    if not isStlsRunning:
        return

    if str(msg.get("trnm") or "") != "REAL":
        return

    dataList = msg.get("data") or []
    if not isinstance(dataList, list):
        return

    for row in dataList:
        if not isinstance(row, dict):
            continue
        typ = str(row.get("type") or "")
        item = row.get("item")
        values = row.get("values") or {}
        if not isinstance(values, dict):
            values = {}

        if typ == "0B":
            _onTick0B(item, values)
        elif typ == "04":
            _onBalance04(item, values)


def startStlsMonitor():
    """
    STLS 감시 시작.
    반환: (성공여부, 메시지)
    """
    global isStlsRunning, _session

    if isStlsRunning:
        return False, "[STLS] 이미 실행 중입니다. stop stls 로 중지하세요."

    session = _pickTradeSession()
    if session is None:
        return False, "[STLS] 시작 전 login paper 또는 login real이 필요합니다."

    ok, info = _loadWatchlistFromAcnt(
        session["hostUrl"], session["token"], session["tokenType"]
    )
    if not ok:
        return False, f"[STLS] 보유종목 동기화 실패: {info}"

    _session = session
    isStlsRunning = True
    registerMsgHandler(_STLS_MSG_HANDLER, _handleWsMessage)
    registerLoginHandler(_STLS_LOGIN_HANDLER, _onStlsLogin)
    ws = _wsClient()
    if ws is not None and ws.isLoggedIn:
        _regStlsRealtime(ws)

    termLog(
        f"[STLS] 감시 시작 ({'모의' if session['mode'] == 'paper' else '실투자'}) "
        f"익절={getTprPct()}% / 손절={getSlrPct()}%"
    )

    modeLabel = "모의투자" if session["mode"] == "paper" else "실투자"
    msg = (
        f"[STLS] 시작 ({modeLabel})\n"
        f"감시종목: {info}개\n"
        f"익절: {getTprPct()}% / 손절: {getSlrPct()}%\n"
        f"매도는 장시간(mkhr) 내에서만 실행"
    )
    return True, msg


def stopStlsMonitor():
    """
    STLS 감시 중지.
    반환: (성공여부, 메시지)
    """
    global isStlsRunning

    if not isStlsRunning:
        return False, "[STLS] 실행 중이 아닙니다."

    isStlsRunning = False
    unregisterMsgHandler(_STLS_MSG_HANDLER)
    unregisterLoginHandler(_STLS_LOGIN_HANDLER)

    ws = _wsClient()
    if ws is not None:
        codes = _watchStkCodes()
        for stkCd in codes:
            _removeQuote(ws, stkCd)
        try:
            ws.sendRemove(["04"], [""], grpNo="1")
        except Exception:
            pass

    return True, "[STLS] 중지 완료 — 실시간 감시를 해제했습니다."


def getStlsStatusText():
    """현재 STLS 상태 요약."""
    if not isStlsRunning:
        return "중지됨"
    with _watchLock:
        n = len(_watchlist)
    return f"실행중 (감시 {n}종목)"
