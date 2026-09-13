"""
매매창·자동매매 공통 세션 (모의/실전).

deskTradeMode(settings) 기준으로 주문·WS에 쓸 host/token 을 고른다.
전략 paperOnly=True 이면 실전 모드에서도 주문을 막는다.
"""

from __future__ import annotations

from config import runtimeState
from config.config import (
    paper_host_url,
    paper_socket_url,
    real_host_url,
    real_socket_url,
)
from utils.termLog import termLog


def getDeskTradeMode():
    """설정상 매매 모드. paper | real."""
    try:
        from config.settingsStore import getDeskTradeMode as _get

        return _get()
    except Exception:
        return "paper"


def setDeskTradeMode(mode):
    """모드 저장. 반환: paper | real."""
    from config.settingsStore import setDeskTradeMode as _set

    return _set(mode)


def modeLabel(mode=None):
    """표시용 한글."""
    m = str(mode or getDeskTradeMode()).strip().lower()
    return "실전투자" if m == "real" else "모의투자"


def isModeLoggedIn(mode=None):
    """해당 모드 토큰이 있는지."""
    m = str(mode or getDeskTradeMode()).strip().lower()
    if m == "real":
        return runtimeState.isRealLoggedIn()
    return runtimeState.isPaperLoggedIn()


def getOrdSession(paperOnly=False):
    """
    주문용 (hostUrl, token, tokenType) 또는 None.
    paperOnly=True 이고 모드가 real 이면 None (실전 주문 금지).
    """
    mode = getDeskTradeMode()
    if paperOnly and mode == "real":
        return None
    if mode == "real":
        if not runtimeState.isRealLoggedIn():
            return None
        return (
            real_host_url,
            runtimeState.getToken("real"),
            runtimeState.realTokenType or "bearer",
        )
    if not runtimeState.isPaperLoggedIn():
        return None
    return (
        paper_host_url,
        runtimeState.getToken("paper"),
        runtimeState.paperTokenType or "bearer",
    )


def getWsTradeSession():
    """
    WS 허브용 dict. 설정 모드를 우선하고, 미로그인이면 다른 모드로 폴백.
    """
    mode = getDeskTradeMode()
    if mode == "real" and runtimeState.isRealLoggedIn():
        return {
            "mode": "real",
            "socketUrl": real_socket_url,
            "token": runtimeState.getToken("real"),
            "tokenType": runtimeState.realTokenType or "bearer",
        }
    if mode == "paper" and runtimeState.isPaperLoggedIn():
        return {
            "mode": "paper",
            "socketUrl": paper_socket_url,
            "token": runtimeState.getToken("paper"),
            "tokenType": runtimeState.paperTokenType or "bearer",
        }
    # 설정과 다른 쪽이라도 로그인돼 있으면 연결 유지 (전환 직전 등)
    if runtimeState.isPaperLoggedIn():
        return {
            "mode": "paper",
            "socketUrl": paper_socket_url,
            "token": runtimeState.getToken("paper"),
            "tokenType": runtimeState.paperTokenType or "bearer",
        }
    if runtimeState.isRealLoggedIn():
        return {
            "mode": "real",
            "socketUrl": real_socket_url,
            "token": runtimeState.getToken("real"),
            "tokenType": runtimeState.realTokenType or "bearer",
        }
    return None


def allowAutoOrders(paperOnly=False):
    """자동매매 주문을 내도 되는지."""
    return getOrdSession(paperOnly=paperOnly) is not None


def applyRealPaperOnlyOff():
    """
    실전 전환 시 전략 paperOnly 를 풀어 주문이 나가게 한다.
    settings.maTouchLive + stratLive.MSE01 만 건드린다 (spec 파일은 유지).
    """
    try:
        from config.settingsStore import loadSettings, saveSettings

        settings = loadSettings() or {}
        mt = settings.get("maTouchLive") or {}
        if not isinstance(mt, dict):
            mt = {}
        mt["paperOnly"] = False
        settings["maTouchLive"] = mt
        live = settings.get("stratLive") or {}
        if not isinstance(live, dict):
            live = {}
        mse = live.get("MSE01") or {}
        if not isinstance(mse, dict):
            mse = {}
        mse["paperOnly"] = False
        live["MSE01"] = mse
        settings["stratLive"] = live
        saveSettings(settings)
        termLog("[tradeSession] 실전 전환 — paperOnly=false (MTS01/MSE01 settings)")
    except Exception as err:
        termLog(f"[tradeSession] paperOnly 해제 실패: {err}")
