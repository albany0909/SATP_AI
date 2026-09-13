"""
토큰당 웹소켓 세션 1개를 유지하는 공유 허브.
주문체결(00), STLS(04/0B) 등이 동일 연결을 사용한다.
LOGIN 토큰 무효(8005 등) 시 자동 재발급 후 재연결한다.
"""

import threading
import time

from config.config import paper_socket_url, real_socket_url
from config import runtimeState
from realtime.wsClient import KiwoomWsClient
from utils.termLog import termLog

_hubLock = threading.RLock()
_isRunning = False
_stopEvent = threading.Event()
_hubThread = None
_wsClient = None
_session = None  # {mode, socketUrl, token, tokenType}

# name → callback(msg dict)
_msgHandlers = {}
# name → callback(ws client) — LOGIN 성공 직후 1회
_loginHandlers = {}

# LOGIN 실패 상태 (허브 루프에서 재발급 판단)
_authFailFlag = False
_lastLoginFailMsg = None
_lastAuthRenewAt = 0.0
# 토큰 재발급 최소 간격 — 무효 토큰으로 연속 REG 방지
_AUTH_RENEW_COOLDOWN_SEC = 60
# 재발급 실패 시 재연결 대기(초)
_AUTH_FAIL_BACKOFF_SEC = 30
# 재발급 성공 후 짧은 대기
_RECONNECT_WAIT_SEC = 3


def _pickTradeSession():
    """settings deskTradeMode 우선. 미로그인이면 다른 모드로 폴백."""
    try:
        from utils.tradeSession import getWsTradeSession

        return getWsTradeSession()
    except Exception:
        pass
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


def registerMsgHandler(name, callback):
    """REAL 등 WS 메시지 수신 콜백 등록."""
    with _hubLock:
        _msgHandlers[name] = callback


def unregisterMsgHandler(name):
    with _hubLock:
        _msgHandlers.pop(name, None)


def registerLoginHandler(name, callback):
    """LOGIN 성공 시 ws 클라이언트로 REG 등 후처리."""
    with _hubLock:
        _loginHandlers[name] = callback


def unregisterLoginHandler(name):
    with _hubLock:
        _loginHandlers.pop(name, None)


def getWsClient():
    """현재 연결된 WS 클라이언트(없으면 None)."""
    return _wsClient


def isHubRunning():
    return _isRunning


def _isTokenAuthFail(msg):
    """
    LOGIN 실패가 토큰 무효인지 판별.
    CODE=8005 또는 메시지에 Token/토큰 인증 실패 포함.
    """
    if not isinstance(msg, dict):
        return False
    try:
        code = int(msg.get("return_code", -1))
    except (TypeError, ValueError):
        code = -1
    if code == 8005:
        return True
    text = str(msg.get("return_msg") or "")
    lower = text.lower()
    if "8005" in text:
        return True
    if "token" in lower and ("유효" in text or "invalid" in lower or "인증" in text):
        return True
    if "토큰 인증에 실패" in text:
        return True
    return False


def _tryRenewAfterAuthFail(mode):
    """
    WS LOGIN 토큰 무효 시 au10001 재발급.
    쿨다운 적용. 반환: True=재발급 성공(또는 쿨다운으로 스킵 후 세션 갱신 대기)
    """
    global _lastAuthRenewAt, _session

    if not mode:
        return False

    now = time.time()
    elapsed = now - _lastAuthRenewAt
    if elapsed < _AUTH_RENEW_COOLDOWN_SEC:
        remain = int(_AUTH_RENEW_COOLDOWN_SEC - elapsed)
        termLog(f"[WS허브] 토큰 재발급 쿨다운 {remain}초 남음 — 대기")
        return False

    _lastAuthRenewAt = now
    termLog(f"[WS허브] LOGIN 토큰 무효 — 자동 재발급 시도 ({mode})")

    # 순환 import 방지 — 호출 시점에만 로드
    from utils.tokenRenewal import renewAccToken

    ok = renewAccToken(modes=[mode], reportTelegram=True)
    if ok:
        # renewOneMode → restartWsHub 가 _session 갱신
        fresh = _pickTradeSession()
        if fresh is not None:
            with _hubLock:
                _session = fresh
        termLog("[WS허브] 토큰 재발급 성공 — 재연결 예정")
        return True

    termLog("[WS허브] 토큰 재발급 실패 — 백오프 후 재시도")
    return False


def _dispatchLogin(msg):
    global _authFailFlag, _lastLoginFailMsg

    try:
        codeOk = int(msg.get("return_code", 1)) == 0
    except (TypeError, ValueError):
        codeOk = False

    if not codeOk:
        # 허브 루프가 재발급·재연결을 처리하도록 플래그만 세움
        _authFailFlag = True
        _lastLoginFailMsg = msg
        return

    _authFailFlag = False
    _lastLoginFailMsg = None
    if _wsClient is None:
        return
    handlers = list(_loginHandlers.values())
    for cb in handlers:
        try:
            cb(_wsClient)
        except Exception as err:
            termLog(f"[WS허브] LOGIN 콜백 오류: {err}")


def _dispatchMessage(msg):
    trnm = str(msg.get("trnm") or "")
    if trnm == "LOGIN":
        _dispatchLogin(msg)
    handlers = list(_msgHandlers.values())
    for cb in handlers:
        try:
            cb(msg)
        except Exception as err:
            termLog(f"[WS허브] 메시지 콜백 오류: {err}")


def _hubLoop():
    """로그인 세션이 있는 동안 WS 유지·재연결. 토큰 무효 시 자동 재발급."""
    global _isRunning, _wsClient, _session, _authFailFlag

    while _isRunning and not _stopEvent.is_set():
        session = _session
        if session is None:
            time.sleep(1)
            continue

        _authFailFlag = False
        client = KiwoomWsClient(
            session["socketUrl"],
            session["token"],
            onMessage=_dispatchMessage,
        )
        _wsClient = client
        client.start()
        modeLabel = "모의" if session["mode"] == "paper" else "실투자"
        termLog(f"[WS허브] 연결 시작 ({modeLabel})")

        # LOGIN 성공/실패 또는 연결 종료까지 대기 (최대 15초)
        loginWaited = 0
        while _isRunning and not _stopEvent.is_set() and loginWaited < 15:
            if client.isLoggedIn:
                break
            if _authFailFlag:
                break
            if client._thread and not client._thread.is_alive():
                break
            time.sleep(0.5)
            loginWaited += 0.5

        # LOGIN 실패(토큰) → 재발급 후 재연결
        if _authFailFlag and not client.isLoggedIn:
            failMsg = _lastLoginFailMsg
            mode = session.get("mode")
            if _isTokenAuthFail(failMsg):
                renewOk = _tryRenewAfterAuthFail(mode)
                client.stop()
                _wsClient = None
                if not _isRunning or _stopEvent.is_set():
                    break
                time.sleep(_RECONNECT_WAIT_SEC if renewOk else _AUTH_FAIL_BACKOFF_SEC)
                continue
            termLog("[WS허브] LOGIN 실패(토큰 외) — 재연결 대기")
            client.stop()
            _wsClient = None
            time.sleep(_AUTH_FAIL_BACKOFF_SEC)
            continue

        # LOGIN 성공 — 연결 유지
        while _isRunning and not _stopEvent.is_set():
            cur = _session
            if cur is None or cur.get("mode") != session.get("mode") or cur.get(
                "token"
            ) != session.get("token"):
                termLog("[WS허브] 세션 변경 — 재연결")
                break
            if _authFailFlag:
                # 유지 중 드물게 재인증 실패가 오면 루프 탈출 후 재발급
                termLog("[WS허브] 유지 중 인증 실패 감지 — 재연결")
                break
            if client._thread and not client._thread.is_alive():
                termLog("[WS허브] WS 스레드 종료 — 재연결 대기")
                break
            time.sleep(1)

        client.stop()
        _wsClient = None

        if not _isRunning or _stopEvent.is_set():
            break
        time.sleep(_RECONNECT_WAIT_SEC)

    _wsClient = None
    _isRunning = False
    termLog("[WS허브] 루프 종료")


def startWsHub():
    """
    로그인된 모드 기준으로 WS 허브를 기동한다.
    이미 실행 중이면 세션만 갱신 후 재연결한다.
    """
    global _isRunning, _hubThread, _session

    session = _pickTradeSession()
    if session is None:
        termLog("[WS허브] 미로그인 — 기동 건너뜀")
        return False

    with _hubLock:
        _session = session
        _stopEvent.clear()
        if _isRunning:
            return True
        _isRunning = True
        _hubThread = threading.Thread(target=_hubLoop, name="wsHub", daemon=True)
        _hubThread.start()
    termLog("[WS허브] 백그라운드 기동")
    return True


def stopWsHub():
    """WS 허브 중지."""
    global _isRunning, _wsClient, _session

    with _hubLock:
        if not _isRunning:
            return
        _isRunning = False
        _session = None
        _stopEvent.set()
        if _wsClient is not None:
            _wsClient.stop()

    termLog("[WS허브] 중지 요청")


def restartWsHub():
    """
    토큰 갱신·재로그인 후 호출 — 세션 갱신 및 재연결.
    허브가 아직 기동되지 않았으면 세션만 갱신하고 False 반환
    (main에서 startWsHub로 기동하도록).
    """
    global _session

    session = _pickTradeSession()
    if session is None:
        if _isRunning:
            stopWsHub()
        return False
    with _hubLock:
        prev = _session
        _session = session
        if not _isRunning:
            return False
        # 토큰·모드가 같으면 재연결 불필요
        if (
            prev
            and prev.get("mode") == session.get("mode")
            and prev.get("token") == session.get("token")
        ):
            return True
        if _wsClient is not None:
            _wsClient.stop()
    return True
