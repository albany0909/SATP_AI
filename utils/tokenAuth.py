# -*- coding: utf-8 -*-
"""
토큰 인증 실패(8005) 판별 · 유효 여부(메모리) · 재발급 쿨다운.
만료시각은 runtimeState 가 내부 관리하고, UI 는 유효/무효만 본다.
"""

from __future__ import annotations

import threading
import time

from utils.termLog import termLog

# mode -> True(유효) / False(무효) / None(아직 확인 전)
_tokenAlive = {}
_aliveLock = threading.Lock()

_AUTH_RENEW_COOLDOWN_SEC = 60
_lastRenewAt = 0.0
_renewLock = threading.Lock()


def isTokenAuthFailText(errMsg):
    """REST/WS 오류 문구가 토큰 무효(8005)인지."""
    text = str(errMsg or "")
    if not text:
        return False
    lower = text.lower()
    if "8005" in text:
        return True
    if "token" in lower and (
        "유효" in text or "invalid" in lower or "인증" in text
    ):
        return True
    if "토큰 인증에 실패" in text:
        return True
    if "Token이 유효하지" in text:
        return True
    return False


def getTokenAlive(mode=None):
    """
    deskTradeMode 기준 토큰 생존 여부.
    True / False / None(미확인·미로그인).
    """
    m = _normMode(mode)
    if not m:
        return None
    with _aliveLock:
        return _tokenAlive.get(m)


def setTokenAlive(mode, alive):
    """생존 여부 기록. alive: True|False|None."""
    m = _normMode(mode)
    if not m:
        return
    with _aliveLock:
        if alive is None:
            _tokenAlive.pop(m, None)
        else:
            _tokenAlive[m] = bool(alive)


def tokenAliveLabel(mode=None):
    """UI용 짧은 문구: 토큰유효 / 토큰무효 / 토큰확인중 / ''."""
    m = _normMode(mode)
    if not m:
        return ""
    try:
        from config import runtimeState

        logged = (
            runtimeState.isRealLoggedIn()
            if m == "real"
            else runtimeState.isPaperLoggedIn()
        )
    except Exception:
        logged = False
    if not logged:
        return ""
    alive = getTokenAlive(m)
    if alive is True:
        return "토큰유효"
    if alive is False:
        return "토큰무효"
    return "토큰확인중"


def tryRenewOnAuthFail(mode, reportTelegram=False):
    """
    8005 등 인증 실패 시 au10001 재발급 (쿨다운 60초).
    성공 시 setTokenAlive(True). 반환: 재발급 성공 여부.
    """
    global _lastRenewAt
    m = _normMode(mode)
    if not m:
        return False
    with _renewLock:
        now = time.time()
        elapsed = now - _lastRenewAt
        if elapsed < _AUTH_RENEW_COOLDOWN_SEC:
            remain = int(_AUTH_RENEW_COOLDOWN_SEC - elapsed)
            termLog(f"[토큰] 재발급 쿨다운 {remain}초 — 대기")
            return False
        _lastRenewAt = now

    termLog(f"[토큰] 인증실패 — 자동 재발급 시도 ({m})")
    try:
        from utils.tokenRenewal import renewAccToken

        ok = renewAccToken(modes=[m], reportTelegram=bool(reportTelegram))
    except Exception as err:
        termLog(f"[토큰] 재발급 예외: {err}")
        setTokenAlive(m, False)
        return False
    if ok:
        setTokenAlive(m, True)
        termLog(f"[토큰] 재발급 성공 ({m})")
        return True
    setTokenAlive(m, False)
    termLog(f"[토큰] 재발급 실패 ({m})")
    return False


def _normMode(mode):
    if mode in ("paper", "real"):
        return mode
    try:
        from utils.tradeSession import getDeskTradeMode

        m = getDeskTradeMode()
        return m if m in ("paper", "real") else None
    except Exception:
        return None
