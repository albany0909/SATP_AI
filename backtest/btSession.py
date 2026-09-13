"""백테스트 실행 세션 상태 (단일 작업)"""

_state = {
    "status": "idle",  # idle | pending_login | running
    "phase": "",
    "stratId": "",
    "stkCd": "",
    "startDt": "",
    "endDt": "",
    "initCash": 0,
    "abort": False,
    "lastMsg": "",
}


def getBtStatus():
    """현재 상태 스냅샷 반환."""
    return dict(_state)


def isBtRunning():
    return _state["status"] == "running"


def isPendingLogin():
    return _state["status"] == "pending_login"


def isBtBusy():
    """실행 중 또는 로그인 확인 대기."""
    return _state["status"] in ("running", "pending_login")


def requestBtAbort():
    """
    running → 중단 플래그.
    pending_login → 대기 취소.
    반환: 처리 여부.
    """
    if _state["status"] == "pending_login":
        endBtRun()
        return True
    if _state["status"] != "running":
        return False
    _state["abort"] = True
    return True


def shouldAbortBt():
    return bool(_state.get("abort"))


def setPendingLogin(stratId, stkCd, startDt, endDt, initCash):
    """로그인 여부 확인 대기. 이미 busy면 False."""
    if isBtBusy():
        return False
    _state.update(
        {
            "status": "pending_login",
            "phase": "ask_login",
            "stratId": stratId,
            "stkCd": stkCd,
            "startDt": startDt,
            "endDt": endDt,
            "initCash": initCash,
            "abort": False,
            "lastMsg": "로그인 진행 여부 대기",
        }
    )
    return True


def getPendingJob():
    """pending_login 일 때 작업 파라미터 dict, 아니면 None."""
    if _state["status"] != "pending_login":
        return None
    return {
        "stratId": _state["stratId"],
        "stkCd": _state["stkCd"],
        "startDt": _state["startDt"],
        "endDt": _state["endDt"],
        "initCash": _state["initCash"],
    }


def beginBtRun(stratId, stkCd, startDt, endDt, initCash):
    """실행 시작 — busy면 False. pending에서 이어갈 때도 호출."""
    if _state["status"] == "running":
        return False
    _state.update(
        {
            "status": "running",
            "phase": "start",
            "stratId": stratId,
            "stkCd": stkCd,
            "startDt": startDt,
            "endDt": endDt,
            "initCash": initCash,
            "abort": False,
            "lastMsg": "",
        }
    )
    return True


def setBtPhase(phase, lastMsg=""):
    _state["phase"] = phase or ""
    if lastMsg:
        _state["lastMsg"] = lastMsg


def endBtRun():
    _state["status"] = "idle"
    _state["phase"] = ""
    _state["abort"] = False
    _state["lastMsg"] = ""


def formatBtStatusMsg():
    st = getBtStatus()
    if st["status"] == "idle":
        return "[BT 상태] idle (대기 중)"

    # 투자금은 원 보관 — 표시는 만원+원
    wonVal = float(st.get("initCash") or 0)
    cashText = f"{wonVal / 10000:,.0f}만원 ({wonVal:,.0f}원)"

    if st["status"] == "pending_login":
        return (
            "[BT 상태] pending_login (로그인 확인 대기)\n"
            f"전략: {st.get('stratId')}\n"
            f"종목: {st.get('stkCd')}\n"
            f"기간: {st.get('startDt')} ~ {st.get('endDt')}\n"
            f"투자금: {cashText}\n"
            "→ bt yes paper | bt yes real | bt no"
        )
    return (
        "[BT 상태] running\n"
        f"단계: {st.get('phase') or '-'}\n"
        f"전략: {st.get('stratId')}\n"
        f"종목: {st.get('stkCd')}\n"
        f"기간: {st.get('startDt')} ~ {st.get('endDt')}\n"
        f"투자금: {cashText}\n"
        f"최근: {st.get('lastMsg') or '-'}\n"
        "중단: bt stop"
    )
