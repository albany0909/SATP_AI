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
_rankStarted = False
_refresh = {"state": "IDLE", "reason": "", "updatedAt": "", "orders": 0, "label": ""}
RANK_POLL_SEC = 25


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


def paperRankTransport(session):
    """모의 호스트의 ka00198 당일누적만 호출하는 함수. 실호스트는 만들지 않는다."""
    from trading.surgeTradeGate import evaluateMarketRead

    read = evaluateMarketRead(dict(session or {}, purpose="market_read"))
    if not read.get("ok"):
        def _blocked():
            raise RuntimeError(read.get("reason") or "PAPER_SESSION_REQUIRED")

        return _blocked

    def _call():
        from api.getInqRank import QRY_TP_DAY, fetchInqRank
        from config.config import paper_host_url
        from config import runtimeState

        if str(session.get("host") or "") != str(paper_host_url):
            raise RuntimeError("HOST_NOT_PAPER")
        token = runtimeState.getToken("paper")
        if not token:
            raise RuntimeError("TOKEN_MISSING")
        fetched = fetchInqRank(
            paper_host_url,
            token,
            qryTp=QRY_TP_DAY,
            tokenType=runtimeState.paperTokenType or "bearer",
        )
        if not fetched.get("ok"):
            raise RuntimeError(fetched.get("errMsg") or "RANK_FAIL")
        return {"item_inq_rank": fetched.get("rows") or [], "return_code": 0}

    return _call


def _readSession(session):
    if not isinstance(session, dict):
        return session
    out = dict(session)
    out["purpose"] = "market_read"
    return out


def _finalCollected(store, ymd):
    snap = store.slot(ymd, "1600")
    return bool(snap and snap.get("state") == "COLLECTED" and snap.get("observedAt"))


def pullPaperRank(session, now, store, isTrading, transport=None):
    """슬롯이 도래했을 때만 조회한다. 실패 시 순위를 만들지 않는다. 주문은 만들지 않는다."""
    from trading.surgeTradeData import parseKa00198Body, rankCollectSlot
    from trading.surgeTradeGate import evaluateMarketRead

    read = evaluateMarketRead(_readSession(session))
    if not read.get("ok"):
        return {"ok": False, "reason": read.get("reason"), "called": False, "orders": 0}
    ymd = now.strftime("%Y%m%d")
    if _finalCollected(store, ymd):
        return {"ok": True, "reason": "FINAL_DONE", "called": False, "orders": 0}
    slot = rankCollectSlot(now, isTrading, hasFinalCollected=False)
    if slot is None:
        return {"ok": True, "reason": "NOT_DUE", "called": False, "orders": 0}
    existing = store.slot(ymd, slot)
    if existing and existing.get("state") == "COLLECTED" and existing.get("observedAt"):
        return {"ok": True, "reason": "ALREADY", "called": False, "orders": 0, "slot": slot}
    if transport is None:
        return {"ok": False, "reason": "NO_TRANSPORT", "called": False, "orders": 0}
    try:
        body = transport()
    except Exception as err:
        return {"ok": False, "reason": str(err), "called": True, "orders": 0}
    observed = now.strftime("%Y-%m-%dT%H:%M:%S")
    snap = parseKa00198Body(body, observed, slot, "paper_read")
    if not snap.get("ok"):
        return {"ok": False, "reason": snap.get("errMsg") or "PARSE", "called": True, "orders": 0}
    store.save(snap)
    return {"ok": True, "reason": "SAVED", "called": True, "slot": slot, "orders": 0, "observedAt": observed}


def runManualRankSave(session, store, now, transport):
    """
    수동 갱신. 장중 슬롯과 무관하게 당일누적 1회를 ADHOC 으로 저장한다.
    주문·설정 파일은 건드리지 않는다.
    """
    from trading.surgeTradeData import parseKa00198Body
    from trading.surgeTradeGate import evaluateMarketRead

    read = evaluateMarketRead(_readSession(session))
    if not read.get("ok"):
        reason = read.get("reason") or "PAPER_SESSION_REQUIRED"
        return {
            "ok": False,
            "state": "FAIL",
            "reason": reason,
            "label": f"실패 {reason}",
            "called": False,
            "orders": 0,
        }
    if transport is None:
        return {
            "ok": False,
            "state": "FAIL",
            "reason": "NO_TRANSPORT",
            "label": "실패 NO_TRANSPORT",
            "called": False,
            "orders": 0,
        }
    try:
        body = transport()
    except Exception as err:
        return {
            "ok": False,
            "state": "FAIL",
            "reason": str(err),
            "label": f"실패 {err}",
            "called": True,
            "orders": 0,
        }
    observed = now.strftime("%Y-%m-%dT%H:%M:%S")
    snap = parseKa00198Body(body, observed, "ADHOC", "manual_paper_read")
    if not snap.get("ok"):
        reason = snap.get("errMsg") or "PARSE"
        return {
            "ok": False,
            "state": "FAIL",
            "reason": reason,
            "label": f"실패 {reason}",
            "called": True,
            "orders": 0,
        }
    store.save(snap)
    return {
        "ok": True,
        "state": "DONE",
        "reason": "",
        "label": f"완료 {now.strftime('%H:%M')}",
        "called": True,
        "orders": 0,
        "observedAt": observed,
        "slot": "ADHOC",
    }


def beginManualRankRefresh(session, store, now, transport):
    """UI 스레드를 막지 않는다. 상태는 조회중 으로 두고 끝난 뒤 완료·실패를 남긴다."""
    global _refresh
    with _lock:
        if _refresh.get("state") == "BUSY":
            return dict(_refresh)
        _refresh = {"state": "BUSY", "reason": "", "orders": 0, "label": "조회중"}

    def _run():
        global _refresh
        try:
            result = runManualRankSave(session, store, now, transport)
        except Exception as err:
            result = {
                "ok": False,
                "state": "FAIL",
                "reason": str(err),
                "label": f"실패 {err}",
                "orders": 0,
            }
        with _lock:
            _refresh = result

    threading.Thread(target=_run, name="surgeManualRank", daemon=True).start()
    return {"state": "BUSY", "label": "조회중", "orders": 0}


def enqueueManualRefresh(session=None, transport=None, nowText="", store=None, now=None):
    """
    수동 갱신. 모의 조회가 아니면 네트워크를 열지 않는다.
    transport 와 store 가 있으면 ADHOC 으로 저장한다. 주문은 만들지 않는다.
    """
    global _refresh
    if store is not None and now is not None:
        state = runManualRankSave(session, store, now, transport)
        state["updatedAt"] = nowText or state.get("observedAt") or ""
        with _lock:
            _refresh = state
        return dict(state)
    read = evaluateMarketRead(_readSession(session) if session else {"purpose": "market_read"})
    if not read.get("ok"):
        reason = read.get("reason") or "PAPER_SESSION_REQUIRED"
        state = {"state": "FAIL", "reason": reason, "label": f"실패 {reason}", "orders": 0}
    else:
        state = {
            "state": "FAIL",
            "reason": "NO_TRANSPORT",
            "label": "실패 NO_TRANSPORT",
            "orders": 0,
        }
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
            # ka00198 은 조회 수집기가 저장한다. 여기서 다시 호출하지 않는다.
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


def surgeInquiryVisible(settings=None):
    """급등주 탭이 조회 목록에 있을 때만 수집한다."""
    from trading.deskLiveAlgs import visibleMonitorTabs

    return any(row.get("key") == "surge" for row in visibleMonitorTabs(settings))


def _rankStore():
    from trading.surgeTradeData import SnapStore
    from utils.appPaths import configDataDir

    return SnapStore(configDataDir() / "surgeTrade")


def collectRankTick(now, store, isTrading, session, transport):
    """조회 1회. 주문 함수를 호출하지 않는다."""
    result = pullPaperRank(session, now, store, isTrading, transport)
    result["orders"] = 0
    return result


def _rankLoop():
    """SRG01 매매와 별개. 급등주 탭이 보이는 동안만 모의 조회를 저장한다."""
    import time
    from datetime import datetime

    while True:
        try:
            if not surgeInquiryVisible():
                time.sleep(RANK_POLL_SEC)
                continue
            from config.krHoliday import isTradingDay

            now = datetime.now()
            if not isTradingDay(now):
                time.sleep(RANK_POLL_SEC)
                continue
            session = loadPaperOrderSession()
            if session is None:
                time.sleep(RANK_POLL_SEC)
                continue
            session = dict(session)
            session["purpose"] = "market_read"
            collectRankTick(
                now,
                _rankStore(),
                True,
                session,
                paperRankTransport(session),
            )
        except Exception:
            pass
        time.sleep(RANK_POLL_SEC)


def startSurgeRankCollector():
    """
    급등주 탭이 보이면 조회 스레드를 연다.
    매매 enabled 와 무관하다. 설정 파일은 쓰지 않는다.
    """
    global _rankStarted
    if not surgeInquiryVisible():
        return {"started": False, "reason": "TAB_HIDDEN", "orders": 0}
    with _lock:
        if _rankStarted:
            return {"started": True, "reason": "ALREADY", "orders": 0}
        _rankStarted = True
        threading.Thread(target=_rankLoop, name="surgeRankCollect", daemon=True).start()
    try:
        from utils.termLog import termLog

        termLog("[급등주] 조회 수집 시작 — 매매와 분리, 주문 없음")
    except Exception:
        pass
    return {"started": True, "reason": "RANK_ONLY", "orders": 0}


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
