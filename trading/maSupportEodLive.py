"""
이평선지지(MSE01) 모의 실행기.

- 매수: 15:18~15:20(미만) 장중 — SMA5 지지·약음봉/십자 충족 시 시장가.
  장마감 동시호가(15:20) 직전까지, 즉 연속거래 마지막 2분만 본다
- 매도: 15:18~15:20(미만) 장중 — SMA5 이탈 시 시장가
- 대상: AI_ALG_STK(MSE01) ∪ 원장 미청산 (관심 USE_TRADE 전체는 쓰지 않음)
  유니버스 적재는 16:00 maTouchUniv — ka10016 52주 신고가 편입, 종가≤SMA20 이면 제외
- 당일 봉: 1분봉은 장마감 배치(15:34~)에서 들어오므로 매수 창에는 없다.
  장중에는 ka10001(시/고/저/현재가+누적거래량)로 봉을 만들고, 0B 구독 종목은
  실시간 현재가로 종가·고저를 보정한다. 모의 1초 1회라 폴링당 조회 수를 제한한다.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from api.buyStk import placeBuyOrd
from api.sellStk import placeSellOrd
from config import runtimeState
from config.krHoliday import isTradingDay
from strategies.impl.maSupportEod import MaSupportEodStrategy
from strategies.specLoader import loadStratSpec
from telegram.telSend import sendTelegramMsg
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.tradeSession import getOrdSession, modeLabel


def _tradeMode():
    """원장·복기·손익 적재용 deskTradeMode."""
    try:
        from utils.tradeSession import getDeskTradeMode

        return getDeskTradeMode()
    except Exception:
        return "paper"

_STATE_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "maSupportEodState.json"
_STRAT_ID = "maSupportEod_v1"
_ALG_CD = "MSE01"
_TRDE_TP_MARKET = "3"
_PAPER_WAIT_SEC = 1.05
# 매수 허용 구간 15:18:00 ~ 15:19:59 (15:20 미만 — 장마감 동시호가 직전까지)
_BUY_START_TM = "151800"
_BUY_END_TM = "152000"
# SMA5 이탈 매도 — 15:18:00 ~ 15:19:59 (15:20 미만)
_SELL_START_TM = "151800"
_SELL_END_TM = "152000"
_INTRADAY_POLL_SEC = 12
_WARM_DAYS = 40
# 매수 폴링 1회에 조회할 종목 수 (묶음). 모의는 TR 1개당 1초 1회라
# 한 폴링에 다 몰지 않고 나눠 돌린다. 다음 폴링은 이어진 위치부터 본다.
_LIVE_TR_CHUNK = 20
# 매수 폴링 1회 최대 소요(초) — 안전망. 매수 창이 2분뿐이라 이 값으로 잘라서
# 같은 창 안에서 매도 스캔(SMA5 이탈)도 다시 돌 기회를 준다.
_BUY_PASS_MAX_SEC = 40

_lock = threading.Lock()
_params = None
_paramsAt = 0.0
_brokeOut = {}  # stkCd → bool
_lastSellYmd = ""
_workerStarted = False
_lastOrdAt = 0.0
_pendingSkip = {}  # stkCd → {reason, note, px}
_boughtToday = set()
_skipFlushYmd = ""
_wasBuyWin = False
# 매수 스캔 시작 위치. 한 폴링이 전 종목을 못 볼 수 있어 다음 폴링은
# 멈춘 지점부터 돌려 창 안에서 모든 종목이 차례로 평가되게 한다.
_buyCursor = 0
_buyCursorYmd = ""


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _nowHms():
    return datetime.now().strftime("%H%M%S")


def _buyWindow():
    """spec buyStartTm ~ buyEndTm(미포함)."""
    p = _loadParams()
    start = str(p.get("buyStartTm") or _BUY_START_TM)
    end = str(p.get("buyEndTm") or _BUY_END_TM)
    hms = _nowHms()
    return start <= hms < end


def _sellWindow():
    """spec sellStartTm ~ sellEndTm(미포함)."""
    p = _loadParams()
    start = str(p.get("sellStartTm") or _SELL_START_TM)
    end = str(p.get("sellEndTm") or _SELL_END_TM)
    hms = _nowHms()
    return start <= hms < end


def _intradayWindow():
    """매도·매수 창을 모두 덮는 폴링 구간 (매도 먼저, 이후 매수)."""
    p = _loadParams()
    starts = [
        str(p.get("buyStartTm") or _BUY_START_TM),
        str(p.get("sellStartTm") or _SELL_START_TM),
    ]
    ends = [
        str(p.get("buyEndTm") or _BUY_END_TM),
        str(p.get("sellEndTm") or _SELL_END_TM),
    ]
    hms = _nowHms()
    return min(starts) <= hms < max(ends)


def _loadParams():
    global _params, _paramsAt
    now = time.time()
    if _params is not None and now - _paramsAt < 2.0:
        return _params
    spec = loadStratSpec(_STRAT_ID) or {}
    p = dict(spec.get("params") or {})
    try:
        from config.settingsStore import getStratLiveAmts, loadSettings

        amts = getStratLiveAmts(_ALG_CD) or {}
        # MSE01 은 단일금액. buyAmt5 로 덮지 않음
        if amts.get("buyAmtWon"):
            p["buyAmtWon"] = float(amts["buyAmtWon"])
        live = (loadSettings() or {}).get("stratLive") or {}
        row = live.get(_ALG_CD) if isinstance(live, dict) else {}
        if isinstance(row, dict) and "enabled" in row:
            p["_enabled"] = bool(row.get("enabled"))
        else:
            p["_enabled"] = bool(spec.get("enabled", True))
        if isinstance(row, dict) and "paperOnly" in row:
            p["paperOnly"] = bool(row.get("paperOnly"))
    except Exception:
        p["_enabled"] = bool(spec.get("enabled", True))
    p.setdefault("smaLen", 5)
    p.setdefault("weakBearMaxPct", 2.5)
    p.setdefault("dojiMaxPct", 0.5)
    p.setdefault("buyAmtWon", 2_000_000)
    p.setdefault("banSameDayBreakoutBuy", True)
    p.setdefault("buyStartTm", _BUY_START_TM)
    p.setdefault("buyEndTm", _BUY_END_TM)
    p.setdefault("sellStartTm", _SELL_START_TM)
    p.setdefault("sellEndTm", _SELL_END_TM)
    p.setdefault("paperOnly", True)
    p["_paperOnly"] = bool(p.get("paperOnly", True))
    _params = p
    _paramsAt = now
    return p


def _isEnabled():
    return bool(_loadParams().get("_enabled", True))


def _ordSession():
    """deskTradeMode + paperOnly 주문 세션."""
    return getOrdSession(paperOnly=bool(_loadParams().get("_paperOnly", True)))


def _ordExch(stkCd):
    try:
        from config.settingsStore import getDeskTradeExch
        from utils.stkInfoStore import isNxtEnabled

        if getDeskTradeExch() == "NXT" and isNxtEnabled(stkCd):
            return "NXT"
    except Exception:
        pass
    return "KRX"


def _waitOrdGap():
    global _lastOrdAt
    gap = _PAPER_WAIT_SEC - (time.time() - _lastOrdAt)
    if gap > 0:
        time.sleep(gap)
    _lastOrdAt = time.time()


def _saveState():
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tradeDt": _todayYmd(),
            "brokeOut": _brokeOut,
            "lastSellYmd": _lastSellYmd,
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as err:
        termLog(f"[maSupportEod] 상태 저장 실패: {err}")


def pruneMse01BrokeOut(keepCds):
    """관심 전량 교체 후, 빠진 종목의 돌파 플래그를 메모리·파일에서 제거한다."""
    global _brokeOut
    keep = {normStkCd(c) for c in (keepCds or []) if normStkCd(c)}
    with _lock:
        if not _brokeOut:
            _loadState()
        _brokeOut = {k: v for k, v in (_brokeOut or {}).items() if k in keep}
        _saveState()
    termLog(f"[maSupportEod] brokeOut 유지 {len(_brokeOut)}종 (관심 교체)")


def _loadState():
    global _brokeOut, _lastSellYmd
    _brokeOut = {}
    try:
        if not _STATE_PATH.is_file():
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        raw = data.get("brokeOut") or {}
        if isinstance(raw, dict):
            _brokeOut = {normStkCd(k): bool(v) for k, v in raw.items() if normStkCd(k)}
        _lastSellYmd = str(data.get("lastSellYmd") or data.get("lastRunYmd") or "")
    except Exception as err:
        termLog(f"[maSupportEod] 상태 로드 실패: {err}")


def _targetCodes():
    """MSE01 관리종목 + 원장 미청산. 이평터치 USE_TRADE 전체는 넣지 않는다."""
    cds = set()
    try:
        from utils.algStkStore import listAlgStk

        for r in listAlgStk(_ALG_CD, limit=2000) or []:
            cd = normStkCd(r.get("stkCd") or r.get("STK_CD"))
            if cd:
                cds.add(cd)
    except Exception:
        pass
    try:
        from utils.stratOrdLedgerStore import listOpenLedgerStkCds

        for cd in listOpenLedgerStkCds(_STRAT_ID) or []:
            n = normStkCd(cd)
            if n:
                cds.add(n)
    except Exception:
        pass
    return sorted(cds)


def _rotateBuyCodes(codes):
    """직전 폴링이 멈춘 지점부터 시작하도록 회전."""
    if not codes:
        return []
    start = _buyCursor % len(codes)
    return list(codes[start:]) + list(codes[:start])


def _advanceBuyCursor(seen, total):
    """이번 폴링에서 본 종목 수만큼 시작 위치를 밀어둔다."""
    global _buyCursor
    if total <= 0:
        _buyCursor = 0
        return
    _buyCursor = (_buyCursor + max(0, int(seen))) % total


def _lookupNm(stkCd):
    try:
        from utils.watchStkStore import listActiveWatch

        for w in listActiveWatch(mktTp="KR") or []:
            if normStkCd(w.get("STK_CD")) == stkCd:
                return w.get("STK_NM") or stkCd
    except Exception:
        pass
    return stkCd


def _loadDayBarsWarm(stkCd, endDt):
    from backtest.engine import loadDayBars

    start = (
        datetime.strptime(endDt, "%Y%m%d") - timedelta(days=_WARM_DAYS)
    ).strftime("%Y%m%d")
    return loadDayBars(stkCd, start, endDt)


def _liveOhlc(stkCd, lastPx=None):
    """
    장중 당일 봉 (ka10001). 당일 1분봉이 없는 매수·매도 창에서 쓴다.
    라이브 봉의 종가는 곧 현재가라, 0B 틱이 있으면 더 신선한 값으로 맞춘다.
    """
    from utils.liveOhlcSnap import fetchLiveOhlc

    snap = fetchLiveOhlc(stkCd)
    if not snap:
        return None
    ohlc = dict(snap)
    last = float(lastPx) if lastPx and lastPx > 0 else None
    if last is not None:
        ohlc["high"] = max(ohlc["high"], last)
        ohlc["low"] = min(ohlc["low"], last)
        ohlc["close"] = last
    return ohlc


def _todayOhlc(stkCd, ymd, lastPx=None, useLastPxAsClose=True, allowLive=False):
    """
    당일 OHLC. 1분봉 → (장중이면 ka10001 라이브) → 일봉 순.
    매수는 분봉 종가를 유지(현재가 덮어쓰기로 약음봉이 사라지지 않게).
    매도는 현재가로 종가를 맞춰 SMA5 이탈을 바로 본다.
    """
    from backtest.engine import loadDayBars, loadMinBars

    last = float(lastPx) if lastPx and lastPx > 0 else None
    mins = loadMinBars(stkCd, "1", ymd, ymd) or []
    if mins:
        o = float(mins[0]["open"])
        h = max(float(b["high"]) for b in mins)
        l = min(float(b["low"]) for b in mins)
        c = float(mins[-1]["close"])
        if last is not None:
            h = max(h, last)
            l = min(l, last)
            if useLastPxAsClose:
                c = last
        qty = 0
        for b in mins:
            try:
                qty += int(b.get("qty") or 0)
            except (TypeError, ValueError):
                pass
        return {"open": o, "high": h, "low": l, "close": c, "qty": qty}
    # 장중: 당일 1분봉은 장마감 배치에서 들어오므로 매수 창에는 없다 → ka10001
    if allowLive:
        live = _liveOhlc(stkCd, lastPx=last)
        if live:
            return live
    days = loadDayBars(stkCd, ymd, ymd) or []
    if days:
        b = days[-1]
        o = float(b["open"])
        h = float(b["high"])
        l = float(b["low"])
        c = float(b["close"])
        if last is not None:
            h = max(h, last)
            l = min(l, last)
            if useLastPxAsClose:
                c = last
        try:
            qty = int(b.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        return {"open": o, "high": h, "low": l, "close": c, "qty": qty}
    if last is not None:
        return {"open": last, "high": last, "low": last, "close": last, "qty": 0}
    return None


def _lastWatchPx(stkCd):
    try:
        from trading.watchRtMonitor import getWatchLastPxMap
        from api.getAcntEvlt import parsePadNum

        tick = (getWatchLastPxMap() or {}).get(stkCd) or {}
        raw = tick.get("curPrc") or tick.get("krxPrc") or tick.get("nxtPrc")
        n = parsePadNum(raw)
        return abs(int(n)) if n else None
    except Exception:
        return None


def _openLedgerRec(stkCd):
    try:
        from utils.stratOrdLedgerStore import getOpenLedgerRow, rowToLivePos

        row = getOpenLedgerRow(_STRAT_ID, stkCd)
        if not row:
            return None
        return rowToLivePos(row, todayYmd=_todayYmd())
    except Exception:
        return None


def _placeBuy(stkCd, qty, px):
    try:
        from trading.realAcntMgr import acntOrderBlockReason

        if acntOrderBlockReason(stkCd):
            termLog(f"[이평지지] 실계좌 미등록 — {stkCd} 매수 생략")
            return False
    except Exception:
        pass
    try:
        from trading.crossAlgBuyGuard import isNewBuyBlocked, logBuySkip, skipReasonLabel

        blocked, why = isNewBuyBlocked(_ALG_CD, stkCd)
        if blocked:
            logBuySkip("이평지지", stkCd, why)
            try:
                from utils.algEvtStore import insertAlgEvt

                insertAlgEvt(
                    {
                        "algCd": _ALG_CD,
                        "evtTp": "BUY_SKIP",
                        "reason": "CROSS_ALG_HOLD",
                        "stkCd": stkCd,
                        "stkNm": _lookupNm(stkCd),
                        "mode": _tradeMode(),
                        "tradeDt": _todayYmd(),
                        "evtTm": _nowHms(),
                        "px": int(px or 0),
                        "qty": 0,
                        "note": f"{why}|{skipReasonLabel(why)}"[:200],
                    }
                )
            except Exception:
                pass
            return False
    except Exception as err:
        termLog(f"[이평지지] 복수전략가드 오류: {err}")
    sess = _ordSession()
    if sess is None:
        termLog(f"[이평지지] {modeLabel()} 미로그인/불가 — {stkCd} 매수 생략")
        return False
    hostUrl, token, tokenType = sess
    _waitOrdGap()
    result = placeBuyOrd(
        hostUrl, token, _ordExch(stkCd), stkCd, str(qty), _TRDE_TP_MARKET, tokenType=tokenType
    )
    if not result.get("ok"):
        from utils.tokenAuth import (
            isTokenAuthFailText,
            setTokenAlive,
            tryRenewOnAuthFail,
        )
        from utils.tradeSession import getDeskTradeMode

        errMsg = str(result.get("errMsg") or "")
        if isTokenAuthFailText(errMsg):
            setTokenAlive(getDeskTradeMode(), False)
            if tryRenewOnAuthFail(getDeskTradeMode(), reportTelegram=True):
                sess2 = _ordSession()
                if sess2 is not None:
                    hostUrl, token, tokenType = sess2
                    _waitOrdGap()
                    result = placeBuyOrd(
                        hostUrl,
                        token,
                        _ordExch(stkCd),
                        stkCd,
                        str(qty),
                        _TRDE_TP_MARKET,
                        tokenType=tokenType,
                    )
                    if result.get("ok"):
                        setTokenAlive(getDeskTradeMode(), True)
                        termLog(
                            f"[이평지지] 토큰재발급 후 매수 재시도 성공 {stkCd}"
                        )
    if not result.get("ok"):
        errMsg = str(result.get("errMsg") or "")
        try:
            from utils.tokenAuth import isTokenAuthFailText, setTokenAlive
            from utils.tradeSession import getDeskTradeMode

            if isTokenAuthFailText(errMsg):
                setTokenAlive(getDeskTradeMode(), False)
        except Exception:
            pass
        termLog(f"[이평지지매수실패] {stkCd}: {errMsg}")
        return False
    nm = _lookupNm(stkCd)
    try:
        from utils.stratOrdLedgerStore import upsertLedgerBuy

        upsertLedgerBuy(
            _STRAT_ID,
            stkCd,
            nm,
            px,
            qty,
            5,
            {"qty": qty, "avgPx": px, "filled": [5], "halfTaken": False},
            mode=_tradeMode(),
        )
    except Exception as err:
        termLog(f"[이평지지] 원장 매수 실패: {err}")
    termLog(f"[이평지지매수] {nm}({stkCd}) {qty}주 ≈{px:,.0f}")
    try:
        from utils.algEvtStore import insertAlgEvt

        insertAlgEvt(
            {
                "algCd": _ALG_CD,
                "evtTp": "BUY",
                "reason": "sma5_support_weak_bear",
                "stkCd": stkCd,
                "stkNm": nm,
                "mode": _tradeMode(),
                "tradeDt": _todayYmd(),
                "evtTm": _nowHms(),
                "px": int(px),
                "qty": qty,
            }
        )
    except Exception:
        pass
    return True


def _writeSellEvt(stkCd, nm, px, qty, reason):
    """매도 체결 로그. 라운드 손익 집계의 기준이라 이력 저장보다 먼저 남긴다."""
    try:
        from utils.algEvtStore import insertAlgEvt

        insertAlgEvt(
            {
                "algCd": _ALG_CD,
                "evtTp": "SELL",
                "reason": reason,
                "stkCd": stkCd,
                "stkNm": nm,
                "mode": _tradeMode(),
                "tradeDt": _todayYmd(),
                "evtTm": _nowHms(),
                "px": int(px),
                "qty": qty,
            }
        )
    except Exception as err:
        termLog(f"[이평지지] 매도 이벤트 기록 실패 {stkCd}: {err}")


def _placeSell(stkCd, qty, px, reason):
    try:
        from trading.realAcntMgr import acntOrderBlockReason

        if acntOrderBlockReason(stkCd):
            termLog(f"[이평지지] 실계좌 미등록 — {stkCd} 매도 생략")
            return False
    except Exception:
        pass
    sess = _ordSession()
    if sess is None:
        termLog(f"[이평지지] {modeLabel()} 미로그인/불가 — {stkCd} 매도 생략")
        return False
    hostUrl, token, tokenType = sess
    _waitOrdGap()
    result = placeSellOrd(
        hostUrl, token, _ordExch(stkCd), stkCd, str(qty), _TRDE_TP_MARKET, tokenType=tokenType
    )
    if not result.get("ok"):
        from utils.tokenAuth import (
            isTokenAuthFailText,
            setTokenAlive,
            tryRenewOnAuthFail,
        )
        from utils.tradeSession import getDeskTradeMode

        errMsg = str(result.get("errMsg") or "")
        if isTokenAuthFailText(errMsg):
            setTokenAlive(getDeskTradeMode(), False)
            if tryRenewOnAuthFail(getDeskTradeMode(), reportTelegram=True):
                sess2 = _ordSession()
                if sess2 is not None:
                    hostUrl, token, tokenType = sess2
                    _waitOrdGap()
                    result = placeSellOrd(
                        hostUrl,
                        token,
                        _ordExch(stkCd),
                        stkCd,
                        str(qty),
                        _TRDE_TP_MARKET,
                        tokenType=tokenType,
                    )
                    if result.get("ok"):
                        setTokenAlive(getDeskTradeMode(), True)
                        termLog(
                            f"[이평지지] 토큰재발급 후 매도 재시도 성공 {stkCd}"
                        )
    if not result.get("ok"):
        errMsg = str(result.get("errMsg") or "")
        try:
            from utils.tokenAuth import isTokenAuthFailText, setTokenAlive
            from utils.tradeSession import getDeskTradeMode

            if isTokenAuthFailText(errMsg):
                setTokenAlive(getDeskTradeMode(), False)
        except Exception:
            pass
        termLog(f"[이평지지매도실패] {stkCd}: {errMsg}")
        return False
    nm = _lookupNm(stkCd)
    # 라운드 손익은 체결 로그로 세므로 이 매도를 먼저 남긴 뒤 집계한다
    _writeSellEvt(stkCd, nm, px, qty, reason)
    rec = _openLedgerRec(stkCd) or {
        "qty": qty,
        "avgPx": px,
        "buyAmt": px * qty,
        "sellAmt": 0,
        "buyQty": qty,
        "sellQty": 0,
        "halfTaken": False,
        "filled": [5],
    }
    try:
        from utils.stratOrdLedgerStore import upsertLedgerSell
        from utils.stratLiveTradeStore import insertLiveTrade
        from utils.stratRoundFill import calcRoundFill

        avg = float(rec.get("avgPx") or px)
        buyAmt = float(rec.get("buyAmt") or avg * qty)
        sellAmt = float(rec.get("sellAmt") or 0) + px * qty
        buyQty = int(rec.get("buyQty") or qty)
        sellQty = int(rec.get("sellQty") or 0) + qty
        # 원장이 없으면 위 fallback 이 buyAmt=매도가×수량 이라 손익이 0원으로 찍힌다.
        # 체결 로그(BUY − BUY_CNCL vs SELL)가 진실이므로 그쪽을 우선한다.
        entryDt = str(rec.get("entryDt") or "")[:8] or _todayYmd()
        fill = calcRoundFill(_ALG_CD, stkCd, entryDt, _todayYmd(), mode=_tradeMode())
        if fill.get("ok") and fill.get("closed"):
            buyQty = int(fill.get("buyQty") or 0)
            sellQty = int(fill.get("sellQty") or 0)
            buyAmt = float(fill.get("buyAmt") or 0)
            sellAmt = float(fill.get("sellAmt") or 0)
            avg = float(fill.get("avgBuyPx") or avg)
        pnl = sellAmt - buyAmt
        pnlPct = (pnl / buyAmt * 100.0) if buyAmt > 0 else 0.0
        upsertLedgerSell(
            _STRAT_ID, stkCd, px, qty, reason, rec, True, mode=_tradeMode()
        )
        insertLiveTrade(
            {
                "algCd": _ALG_CD,
                "stratId": _STRAT_ID,
                "mode": _tradeMode(),
                "stkCd": stkCd,
                "stkNm": nm,
                "entryDt": rec.get("entryDt") or _todayYmd(),
                "exitDt": _todayYmd(),
                "exitTm": _nowHms(),
                "exitReason": "STOP" if "break" in reason else "TAKE",
                "buyQty": buyQty,
                "sellQty": sellQty,
                "buyAmt": buyAmt,
                "sellAmt": sellAmt,
                "avgBuyPx": avg,
                "exitPx": px,
                "pnlWon": pnl,
                "pnlPct": pnlPct,
                "halfYn": "N",
                "steps": "5",
            }
        )
        try:
            sendTelegramMsg(
                f"{nm} {'승' if pnl > 0 else ('패' if pnl < 0 else '본전')}\n"
                f"{pnlPct:+.2f}% · {pnl:+,.0f}원",
                pushKind="closePnl",
            )
        except Exception:
            pass
    except Exception as err:
        termLog(f"[이평지지] 원장/이력 실패: {err}")
    termLog(f"[이평지지매도] {nm}({stkCd}) {qty}주 ≈{px:,.0f} · {reason}")
    return True


def _buildStratContext(stkCd, ymd, barTm, forSell=False, allowLive=False, liveBudget=None):
    """
    hist + 당일 봉 준비.
    allowLive: 당일 1분봉이 없으면 ka10001 로 장중 봉을 만든다.
    liveBudget: {"left": n} 한 폴링에서 허용할 ka10001 호출 수 (모의 1초 1회라 제한).
    반환: (strat, events, skip) skip=(reason, note, px)|None
    """
    p = _loadParams()
    strat = MaSupportEodStrategy(p)
    if stkCd in _brokeOut:
        strat.brokeOut = bool(_brokeOut[stkCd])

    dayBars = _loadDayBarsWarm(stkCd, ymd)
    if not dayBars:
        return None, None, ("NO_OHLC", "일봉 없음", None)

    hist = [b for b in dayBars if str(b.get("tradeDt")) < ymd]
    hist = strat.prepare(list(hist))
    for i in range(len(hist)):
        strat.onBar(hist, i)
    histBroke = bool(strat.brokeOut)

    rec = _openLedgerRec(stkCd)
    hasPos = bool(rec and int(rec.get("qty") or 0) > 0)
    # 매수 스캔은 TR 을 쓰기 전에 걸러낸다: 보유 중이거나 돌파 이력이 없으면 당일 봉이 필요 없다
    if not forSell:
        if hasPos:
            _brokeOut[stkCd] = histBroke
            return strat, None, None
        if not histBroke:
            _brokeOut[stkCd] = histBroke
            return strat, None, ("NO_BREAKOUT", "SMA5 돌파 이력 없음", None)

    lastPx = _lastWatchPx(stkCd)
    useLive = bool(allowLive)
    if useLive and liveBudget is not None:
        # 예산이 없으면 이번 폴링은 건너뛴다 (다음 폴링에서 재확인)
        if int(liveBudget.get("left") or 0) <= 0:
            _brokeOut[stkCd] = histBroke
            return strat, None, None
    ohlc = _todayOhlc(
        stkCd, ymd, lastPx, useLastPxAsClose=forSell, allowLive=useLive
    )
    trUsed = str((ohlc or {}).get("src") or "") == "ka10001" and not (ohlc or {}).get("cached")
    if useLive and liveBudget is not None and trUsed:
        liveBudget["left"] = int(liveBudget.get("left") or 0) - 1
    if not ohlc:
        _brokeOut[stkCd] = histBroke
        return strat, None, ("NO_OHLC", "당일 봉 없음", None)

    todayBar = {
        "tradeDt": ymd,
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "close": ohlc["close"],
        "qty": int(ohlc.get("qty") or 0),
        "cntrTm": ymd + str(barTm),
        "barTm": str(barTm),
        "tf": "1d",
    }
    bars = strat.prepare(hist + [todayBar])

    if forSell:
        if hasPos:
            strat.pos = {
                "qty": int(rec["qty"]),
                "avgPx": float(rec.get("avgPx") or 0),
                "entryDt": rec.get("entryDt") or ymd,
            }
            strat.state = "IN_POSITION"
        else:
            strat.pos = None
        ev = strat.onBar(bars, len(bars) - 1)
        _brokeOut[stkCd] = bool(strat.brokeOut)
        return strat, ev, None

    strat.pos = None
    ev = strat.onBar(bars, len(bars) - 1)
    _brokeOut[stkCd] = bool(strat.brokeOut)
    bought = False
    for e in ev or []:
        if e.get("type") == "BUY":
            bought = True
            break
    skip = None
    if not bought:
        code, note = strat.explainNoBuy(bars, len(bars) - 1, brokeOut=histBroke)
        skip = (code, note, ohlc.get("close"))
    return strat, ev, skip


def _notePendingSkip(stkCd, skip):
    """매수 창 동안 사유를 모아 창이 끝나면 1종 1건 적재."""
    if not skip:
        return
    code, note, px = skip
    if not code or code == "IN_POS":
        return
    _pendingSkip[stkCd] = {
        "reason": str(code)[:40],
        "note": str(note or "")[:200],
        "px": px,
    }


def _flushBuySkips(ymd):
    """매수 창 종료 후 BUY_SKIP 을 종목당 1건. 산 종목은 제외."""
    global _skipFlushYmd, _pendingSkip, _boughtToday
    if _skipFlushYmd == ymd:
        return 0
    n = 0
    try:
        from utils.algEvtStore import insertAlgEvt

        for cd, info in list(_pendingSkip.items()):
            if cd in _boughtToday:
                continue
            insertAlgEvt(
                {
                    "algCd": _ALG_CD,
                    "evtTp": "BUY_SKIP",
                    "reason": info.get("reason") or "NO_SIGNAL",
                    "stkCd": cd,
                    "stkNm": _lookupNm(cd),
                    "mode": _tradeMode(),
                    "tradeDt": ymd,
                    "evtTm": _nowHms(),
                    "px": int(info["px"]) if info.get("px") else None,
                    "qty": 0,
                    "note": info.get("note") or "",
                }
            )
            n += 1
    except Exception as err:
        termLog(f"[이평지지] 스킵복기 실패: {err}")
    _skipFlushYmd = ymd
    _pendingSkip = {}
    _boughtToday = set()
    if n:
        termLog(f"[이평지지] 매수스킵 복기 {n}건")
    return n


def runMaSupportBuyScan(force=False):
    """15:18~15:20(미만) 장중 매수 스캔. 구간 내 반복 호출 가능."""
    global _boughtToday, _buyCursor, _buyCursorYmd
    if not _isEnabled():
        return {"ok": True, "skipped": "disabled"}
    ymd = _todayYmd()
    if not isTradingDay(ymd):
        return {"ok": True, "skipped": "holiday"}
    if (not force) and not _buyWindow():
        return {"ok": True, "skipped": "outside_buy_window"}
    p = _loadParams()
    if getOrdSession(paperOnly=bool(p.get("_paperOnly", True))) is None:
        return {"ok": False, "errMsg": f"{modeLabel()} 미로그인/불가"}

    codes = _targetCodes()
    buys = 0
    barTm = _nowHms()[:6]
    if _skipFlushYmd and _skipFlushYmd != ymd:
        _pendingSkip.clear()
        _boughtToday.clear()
    if _buyCursorYmd != ymd:
        _buyCursor = 0
        _buyCursorYmd = ymd
    # 다음 폴링은 이어진 위치부터 본다 (모의 1초 1회라 한 폴링이 전 종목을 못 볼 수 있다)
    scanCodes = _rotateBuyCodes(codes)
    liveBudget = {"left": _LIVE_TR_CHUNK}
    passAt = time.time()
    seen = 0
    with _lock:
        for cd in scanCodes:
            if int(liveBudget.get("left") or 0) <= 0:
                # 이번 묶음 소진 — 남은 종목은 다음 폴링에서 본다
                break
            # 창이 닫히면 즉시 중단 — 동시호가(15:20) 이후로 주문이 넘어가지 않게 한다
            if (not force) and not _buyWindow():
                break
            if time.time() - passAt >= _BUY_PASS_MAX_SEC:
                # 한 폴링을 오래 붙잡지 않는다 (같은 창에서 매도 스캔도 다시 돌아야 한다)
                break
            seen += 1
            try:
                _, ev, skip = _buildStratContext(
                    cd, ymd, barTm, forSell=False, allowLive=True, liveBudget=liveBudget
                )
                bought = False
                for e in ev or []:
                    if e.get("type") != "BUY":
                        continue
                    px = float(e.get("price") or 0)
                    qty = int(e.get("qty") or 0)
                    if qty > 0 and _placeBuy(cd, qty, px):
                        buys += 1
                        bought = True
                if bought:
                    _boughtToday.add(cd)
                    _pendingSkip.pop(cd, None)
                else:
                    _notePendingSkip(cd, skip)
            except Exception as err:
                termLog(f"[이평지지] 매수 {cd} 오류: {err}")
        _saveState()

    used = _LIVE_TR_CHUNK - int(liveBudget.get("left") or 0)
    _advanceBuyCursor(seen, len(codes))
    if buys:
        termLog(f"[이평지지] 장중매수 {buys}건 ({barTm})")
    return {
        "ok": True,
        "buyN": buys,
        "codes": len(codes),
        "scanN": seen,
        "liveTrN": used,
    }


def runMaSupportSellScan(force=False):
    """
    15:18~15:20 장중 SMA5 이탈 매도. 구간 내 반복 호출 가능.
    사용중지(enabled=false)여도 원장 미청산 매도는 계속한다 (추가매수만 중단).
    """
    global _lastSellYmd
    ymd = _todayYmd()
    if not isTradingDay(ymd):
        return {"ok": True, "skipped": "holiday"}
    if (not force) and not _sellWindow():
        return {"ok": True, "skipped": "outside_sell_window"}
    p = _loadParams()
    if getOrdSession(paperOnly=bool(p.get("_paperOnly", True))) is None:
        return {"ok": False, "errMsg": f"{modeLabel()} 미로그인/불가"}

    codes = _targetCodes()
    sells = 0
    barTm = _nowHms()[:6]

    with _lock:
        for cd in codes:
            try:
                if not _openLedgerRec(cd):
                    continue
                # 보유 종목만이라 수가 적다 — 조회 예산 없이 매번 현재가 봉을 만든다
                _, ev, _skip = _buildStratContext(
                    cd, ymd, barTm, forSell=True, allowLive=True
                )
                if not ev:
                    continue
                for e in ev if isinstance(ev, list) else [ev]:
                    if e.get("type") != "SELL":
                        continue
                    px = float(e.get("price") or 0)
                    qty = int(e.get("qty") or 0)
                    if qty > 0 and _placeSell(cd, qty, px, e.get("reason") or "sma5_break_eod"):
                        sells += 1
            except Exception as err:
                termLog(f"[이평지지] 매도 {cd} 오류: {err}")

        if sells:
            _lastSellYmd = ymd
        _saveState()

    if sells:
        termLog(f"[이평지지] 장중매도 {sells}건 ({barTm})")
    return {"ok": True, "sellN": sells, "codes": len(codes)}


def runMaSupportEodScan(force=False):
    """수동: 매수·매도 모두 (force 시 시간창 무시)."""
    buyRes = runMaSupportBuyScan(force=force)
    sellRes = runMaSupportSellScan(force=force)
    return {
        "ok": True,
        "buyN": buyRes.get("buyN", 0),
        "sellN": sellRes.get("sellN", 0),
        "buy": buyRes,
        "sell": sellRes,
    }


def _schedLoop():
    global _wasBuyWin
    while True:
        try:
            # 사용중지여도 보유(원장) 매도 감시는 유지. 매수만 enabled 일 때.
            if isTradingDay(_todayYmd()):
                enabled = _isEnabled()
                inBuy = _buyWindow() if enabled else False
                # 창이 끝나면 당일 미매수 사유를 복기에 남긴다
                if enabled and _wasBuyWin and not inBuy:
                    _flushBuySkips(_todayYmd())
                _wasBuyWin = inBuy if enabled else False
                if _intradayWindow():
                    if _sellWindow():
                        runMaSupportSellScan(force=False)
                    if enabled and inBuy:
                        runMaSupportBuyScan(force=False)
                    time.sleep(_INTRADAY_POLL_SEC)
                    continue
        except Exception as err:
            termLog(f"[이평지지] 스케줄 오류: {err}")
        time.sleep(30)


def _startMmg01():
    try:
        from trading.manualMgrLive import startManualMgrLive

        startManualMgrLive()
    except Exception as err:
        termLog(f"[MMG01] 기동 실패: {err}")


def startMaSupportEodLive():
    _startMmg01()
    """프로세스 기동 시 상태 로드 + 스케줄 스레드."""
    global _workerStarted
    if not _isEnabled():
        termLog("[이평지지] 비활성 — 기동 생략")
        return
    _loadParams()
    _loadState()
    if _workerStarted:
        return
    _workerStarted = True
    th = threading.Thread(target=_schedLoop, name="maSupportEodEod", daemon=True)
    th.start()
    p = _loadParams()
    enTxt = "ON" if p.get("_enabled", True) else "OFF(매수만중단·보유매도유지)"
    termLog(
        f"[이평지지] 기동 {enTxt} — 매수 {p.get('buyStartTm')}~{p.get('buyEndTm')} "
        f"· 매도 {p.get('sellStartTm')}~{p.get('sellEndTm')}"
    )
