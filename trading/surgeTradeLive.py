"""SRG01 라이브 연결. 기본 비활성. 켜져도 모의 게이트를 통과해야 주문이 나간다.

15분 순위 조회와 보유·후보 시세 폴링은 분리한다. 웹소켓 관심종목을 늘리지 않는다.
텔레그램을 보내지 않는다.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from strategies.impl.surgeTrade import ALG_CD, ALG_VER, STRAT_ID, SurgeBook, buildAlgVerEvent
from strategies.specLoader import loadStratSpec
from trading.surgeTradeGate import evaluateMarketRead, submitSurgeOrder

_lock = threading.Lock()
_started = False
_refresh = {"state": "IDLE", "reason": "", "updatedAt": ""}


def isSurgeEnabled():
    """스펙 enabled 와 settings.stratLive.SRG01.enabled 가 둘 다 True 일 때만."""
    spec = loadStratSpec(STRAT_ID) or {}
    if spec.get("enabled") is not True:
        return False
    if spec.get("algCd") != ALG_CD or int(spec.get("version") or 0) != ALG_VER:
        return False
    params = spec.get("params") or {}
    if params.get("paperOnly") is not True:
        return False
    try:
        from config.settingsStore import loadSettings

        settings = loadSettings() or {}
        row = (settings.get("stratLive") or {}).get(ALG_CD) or {}
        return row.get("enabled") is True
    except Exception:
        return False


def loadBook(path):
    book = SurgeBook()
    filePath = Path(path)
    if filePath.is_file():
        try:
            book.restore(json.loads(filePath.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            book.frozen = "STATE_UNREADABLE"
            return book
    return book


def saveBook(book, path):
    filePath = Path(path)
    filePath.parent.mkdir(parents=True, exist_ok=True)
    filePath.write_text(json.dumps(book.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(filePath)


def pollQuotes(codes, fetchQuote):
    """후보·보유 시세만 조회한다. WS REG 를 호출하지 않는다."""
    out = {}
    for code in codes or []:
        try:
            out[str(code)] = fetchQuote(str(code))
        except Exception as err:
            out[str(code)] = {"stale": True, "errMsg": str(err)}
    return out


def runDecisionCycle(
    book,
    now,
    quotes,
    candidates,
    cash,
    ownOk,
    session,
    broker,
    enabled=None,
):
    """판단 후 게이트. enabled 가 아니면 브로커를 호출하지 않는다."""
    if enabled is None:
        enabled = isSurgeEnabled()
    if not enabled:
        return {"ran": False, "reason": "DISABLED", "actions": [], "orders": []}
    if getattr(book, "frozen", ""):
        return {"ran": False, "reason": book.frozen, "actions": [], "orders": []}
    decided = book.decide(now, quotes, candidates, cash, ownOk)
    orders = []
    events = []
    if not decided.get("ok"):
        return {"ran": True, "reason": decided.get("reason"), "actions": [], "orders": []}
    for action in decided.get("actions") or []:
        if action.get("side") not in ("BUY", "SELL"):
            continue
        result = submitSurgeOrder(action, session, broker)
        orders.append(result)
        events.append(
            buildAlgVerEvent(
                action.get("side"),
                action.get("stkCd"),
                action.get("reason"),
                decided.get("ymd"),
                decided.get("hms"),
                action.get("limitPx"),
            )
        )
        if not result.get("ok"):
            book.onReject(action.get("stkCd"), action.get("intentId"))
    return {
        "ran": True,
        "reason": "OK",
        "actions": decided.get("actions") or [],
        "orders": orders,
        "events": events,
        "cash": decided.get("cash"),
    }


def enqueueManualRefresh(session=None, transport=None, nowText=""):
    """
    수동 갱신. 모의 조회 세션이 없으면 네트워크를 열지 않는다.
    이 경로는 주문을 만들지 않는다.
    """
    global _refresh
    read = evaluateMarketRead(session or {"purpose": "market_read"})
    if not read.get("ok"):
        state = {"state": "BLOCKED", "reason": read.get("reason") or "PAPER_SESSION_REQUIRED", "orders": 0}
    elif transport is None:
        state = {"state": "BLOCKED", "reason": "NO_TRANSPORT", "orders": 0}
    else:
        try:
            body = transport()
            state = {
                "state": "READY" if isinstance(body, dict) else "ERROR",
                "reason": "",
                "orders": 0,
                "body": body if isinstance(body, dict) else None,
            }
        except Exception as err:
            state = {"state": "ERROR", "reason": str(err), "orders": 0}
    state["updatedAt"] = nowText
    with _lock:
        _refresh = state
    return dict(state)


def refreshStatus():
    with _lock:
        return dict(_refresh)


def loadPaperOrderSession():
    """
    모의 모드 + 모의 토큰 + 모의 호스트가 맞을 때만 세션을 만든다.
    계좌가 모의인지 따로 알려 주지 않으면 이 세 가지가 불일치할 때 None.
    토큰 문자열은 반환하지 않는다.
    """
    try:
        from config.config import paper_host_url
        from config import runtimeState
        from utils.tradeSession import getDeskTradeMode
    except Exception:
        return None
    try:
        if str(getDeskTradeMode() or "") != "paper":
            return None
        if not runtimeState.isPaperLoggedIn():
            return None
        if not paper_host_url or "mockapi.kiwoom.com" not in str(paper_host_url):
            return None
    except Exception:
        return None
    return {
        "mode": "paper",
        "host": paper_host_url,
        "accountEnv": "paper",
        "accountVerified": True,
        "tokenPresent": True,
        "purpose": "order",
    }


def paperBroker(action, session):
    """게이트를 다시 통과한 모의 호스트로만 kt10000 을 호출한다."""
    from trading.surgeTradeGate import evaluateOrderSession

    gate = evaluateOrderSession(session)
    if not gate.get("ok"):
        return {"ok": False, "sent": False, "reason": gate.get("reason")}
    from config.config import paper_host_url
    from config import runtimeState

    token = runtimeState.getToken("paper")
    tokenType = runtimeState.paperTokenType or "bearer"
    if not token or str(session.get("host") or "") != str(paper_host_url):
        return {"ok": False, "sent": False, "reason": "HOST_NOT_PAPER"}
    code = str(action.get("stkCd") or "")
    qty = str(int(action.get("qty") or 0))
    trdeTp = str(action.get("trdeTp") or "")
    ordUv = "" if action.get("limitPx") in (None, "") else str(int(float(action["limitPx"])))
    if action.get("side") == "BUY":
        from api.buyStk import placeBuyOrd

        result = placeBuyOrd(
            paper_host_url, token, "KRX", code, qty, trdeTp, ordUv=ordUv, tokenType=tokenType
        )
    elif action.get("side") == "SELL":
        from api.sellStk import placeSellOrd

        result = placeSellOrd(
            paper_host_url, token, "KRX", code, qty, trdeTp, ordUv=ordUv, tokenType=tokenType
        )
    else:
        return {"ok": False, "sent": False, "reason": "NO_ORDER"}
    result = dict(result or {})
    result["sent"] = bool(result.get("ok"))
    return result


def liveTick(book, now, quotes, candidates, cash, ownOk, session=None, broker=None, enabled=None):
    """한 번 판단. 세션이 없거나 비활성이면 주문을 보내지 않는다."""
    if enabled is None:
        enabled = isSurgeEnabled()
    if not enabled:
        return {"ran": False, "reason": "DISABLED", "orders": []}
    sess = session if session is not None else loadPaperOrderSession()
    if not sess:
        return {"ran": False, "reason": "PAPER_SESSION_REQUIRED", "orders": []}
    useBroker = broker or paperBroker
    return runDecisionCycle(
        book, now, quotes, candidates, cash, ownOk, sess, useBroker, enabled=True
    )


def _statePath():
    from utils.appPaths import configDataDir

    return configDataDir() / "surgeTradeState.json"


def _loop():
    """활성화된 뒤에만 돈다. 시세가 없으면 주문을 만들지 않는다."""
    import time
    from datetime import datetime

    path = _statePath()
    book = loadBook(path)
    while True:
        try:
            if not isSurgeEnabled():
                time.sleep(5)
                continue
            session = loadPaperOrderSession()
            if session is None:
                time.sleep(5)
                continue
            liveTick(
                book,
                datetime.now(),
                {},
                [],
                0,
                {},
                session=session,
                enabled=True,
            )
            saveBook(book, path)
        except Exception:
            pass
        time.sleep(5)


def startSurgeTradeLive():
    """비활성이면 스레드를 만들지 않는다. 설정 파일을 바꾸지 않는다."""
    global _started
    if not isSurgeEnabled():
        try:
            from utils.termLog import termLog

            termLog("[SRG01] 비활성 — 기동 생략")
        except Exception:
            pass
        return {"started": False, "reason": "DISABLED"}
    with _lock:
        if _started:
            return {"started": True, "reason": "ALREADY"}
        _started = True
        threading.Thread(target=_loop, name="surgeTradeSrg01", daemon=True).start()
    return {"started": True, "reason": "PAPER_LOOP"}
