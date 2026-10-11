"""
MMG01 수동정찰 종가 피라미딩 (manualMgr_v1) — 라이브(모의 우선).

흐름
- 풀: AI_ALG_STK(ALG_CD=MMG01) + 관심종목(AI_WATCH_STK GRP_CD=MMG01) 표시.
  사용자 등록(src=USER, 만료 없음) + 스크리너(src=SCR, 10일 만료, 최대 10).
- 편입: 풀 종목의 수동(HTS/MTS, 봇 주문번호 아님) 매수 체결 → MMG01 포지션.
- 자동정찰: 15:15 판단 시 E1~E4 신호 → 1주 (≤300,000원/주, 하루 ≤2종목).
- 추가: Stage2 + 평단 +5% 이상 + SMA50 상승 + 돌파/눌림 셋업, 40/35/25% × 종목당 최대금액, 최대 3회.
- 손절: 장중 재앙 −8%(추가 후 포지션) / 추가 후 평단−2ATR / +10% 본전 / +50% 래칫 max(평단, 고점×0.75).
- EOD 전량: 종가<SMA50 또는 SMA150, 종가<고점−3ATR.
- 판단 15:15 → 주문 15:21~15:27 지정가(호가 v63)로 종가 동시호가 참여.
- 리스크: 노출 60%, 6종목, 추가 1회 위험 0.5%, 당일 −2% 이면 추가 중지, 청산 후 60일 재진입 금지.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_ROOT = Path(__file__).resolve().parents[1]
_STATE_PATH = _ROOT / "config" / "data" / "manualMgrState.json"
_DECISION_DIR = _ROOT / "config" / "data" / "mmgDecision"
_BAR_DIR = _ROOT / "config" / "data" / "mmgBars"
_STRAT_ID = "manualMgr_v1"
_ALG_CD = "MMG01"
_TRDE_TP_LIMIT = "0"
_TRDE_TP_MARKET = "3"
_PAPER_WAIT_SEC = 1.05

_DECIDE_TM = "151500"
_RESCAN_TM = "151000"
_EXEC_START_TM = "152100"
_EXEC_END_TM = "152700"
_INTRADAY_START = "090100"
_INTRADAY_END = "151900"
_SCREEN_TM = "161000"
_POOL_CACHE_SEC = 30

_lock = threading.RLock()
_state = None
_params = None
_paramsAt = 0.0
_poolCache = (0.0, {})
_barMem = {}
_workerStarted = False
_lastOrdAt = 0.0
_injected = {}  # 테스트용 주입 (session/order/bars/live/equity/regime/now)


# ── 공용 ──────────────────────────────────────────────

def _now():
    fn = _injected.get("now")
    return fn() if fn else datetime.now()


def _todayYmd():
    return _now().strftime("%Y%m%d")


def _nowHms():
    return _now().strftime("%H%M%S")


def _tradeMode():
    try:
        from utils.tradeSession import getDeskTradeMode

        return getDeskTradeMode()
    except Exception:
        return "paper"


def _notify(msg):
    termLog(msg)
    if _injected.get("noTelegram"):
        return
    try:
        from telegram.telSend import sendTelegramMsg

        sendTelegramMsg(msg)
    except Exception:
        pass


def _loadParams():
    global _params, _paramsAt
    now = time.time()
    if _params is not None and now - _paramsAt < 2.0:
        return _params
    from utils.mmgSignals import DEFAULTS

    p = dict(DEFAULTS)
    spec = {}
    try:
        from strategies.specLoader import loadStratSpec

        spec = loadStratSpec(_STRAT_ID) or {}
    except Exception:
        spec = {}
    live = dict(spec.get("live") or {})
    p.update({k: v for k, v in live.items() if k in DEFAULTS or k in (
        "maxPositions", "maxExposurePct", "dailyLossHaltPct", "autoScoutMaxPerDay",
        "screenerMaxNames", "screenerExpireDays", "equityFallbackWon", "autoScout",
        "intradayPollSec", "paperOnly")})
    p.setdefault("maxPositions", 6)
    p.setdefault("maxExposurePct", 60.0)
    p.setdefault("dailyLossHaltPct", 2.0)
    p.setdefault("autoScoutMaxPerDay", 2)
    p.setdefault("screenerMaxNames", 10)
    p.setdefault("screenerExpireDays", 10)
    p.setdefault("equityFallbackWon", 10_000_000)
    p.setdefault("autoScout", True)
    p.setdefault("intradayPollSec", 30)
    p.setdefault("paperOnly", True)
    p["_enabled"] = bool(spec.get("enabled", False))
    try:
        from config.settingsStore import loadSettings

        row = ((loadSettings() or {}).get("stratLive") or {}).get(_ALG_CD)
        if isinstance(row, dict):
            if "enabled" in row:
                p["_enabled"] = bool(row.get("enabled"))
            if "paperOnly" in row:
                p["paperOnly"] = bool(row.get("paperOnly"))
    except Exception:
        pass
    try:
        from config.mmgSettings import getMmgMaxBuyAmt

        p["maxBuyAmt"] = float(getMmgMaxBuyAmt())
    except Exception:
        p["maxBuyAmt"] = 3_000_000.0
    if "params" in _injected:
        p.update(_injected["params"])
    _params = p
    _paramsAt = now
    return p


def isMmgEnabled():
    return bool(_loadParams().get("_enabled"))


def _session():
    if "session" in _injected:
        return _injected["session"]
    from utils.tradeSession import getOrdSession

    return getOrdSession(paperOnly=bool(_loadParams().get("paperOnly", True)))


def _ordExch(stkCd):
    try:
        from config.settingsStore import getDeskTradeExch
        from utils.stkInfoStore import isNxtEnabled

        if getDeskTradeExch() == "NXT" and isNxtEnabled(stkCd):
            return "NXT"
    except Exception:
        pass
    return "KRX"


# ── 상태 ──────────────────────────────────────────────

def _emptyState():
    return {"pos": {}, "lastExit": {}, "ordNos": {}, "days": {}}


def _st():
    global _state
    if _state is None:
        try:
            _state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _state = _emptyState()
        for k, v in _emptyState().items():
            _state.setdefault(k, v)
    return _state


def _saveState():
    if _injected.get("noDisk"):
        return
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_st(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as err:
        termLog(f"[MMG01] 상태 저장 실패: {err}")


def _day(ymd=None):
    ymd = ymd or _todayYmd()
    days = _st()["days"]
    d = days.setdefault(ymd, {"realized": 0.0, "autoScouts": [], "decided": False,
                              "executed": False, "rescanned": False, "screened": False,
                              "plan": [], "log": []})
    for k in list(days.keys()):
        if k < (_now() - timedelta(days=20)).strftime("%Y%m%d"):
            days.pop(k, None)
    return d


def listMmgPositions():
    with _lock:
        return {cd: dict(v) for cd, v in _st()["pos"].items()}


def holdsMmg(stkCd):
    cd = normStkCd(stkCd)
    with _lock:
        return int((_st()["pos"].get(cd) or {}).get("qty") or 0) > 0


# ── 풀 ────────────────────────────────────────────────

def _lookupNm(stkCd):
    cd = normStkCd(stkCd)
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT STK_NM FROM AI_STK_INFO WHERE STK_CD=%s", (cd,))
                row = cur.fetchone()
                if row:
                    return (row.get("STK_NM") if isinstance(row, dict) else row[0]) or ""
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def listPool(force=False):
    """{cd: {nm, src, note, expYmd}}"""
    global _poolCache
    if "pool" in _injected:
        return dict(_injected["pool"])
    at, cache = _poolCache
    if not force and time.time() - at < _POOL_CACHE_SEC:
        return dict(cache)
    out = {}
    try:
        from utils.algStkStore import listAlgStk

        for r in listAlgStk(_ALG_CD, limit=500) or []:
            cd = normStkCd(r.get("stkCd") or r.get("STK_CD"))
            if not cd:
                continue
            note = str(r.get("note") or r.get("NOTE") or "")
            exp = ""
            for part in note.split("|"):
                if part.startswith("exp="):
                    exp = part[4:12]
            out[cd] = {"nm": r.get("stkNm") or r.get("STK_NM") or "",
                       "src": str(r.get("src") or r.get("SRC") or ""), "note": note, "expYmd": exp}
    except Exception as err:
        termLog(f"[MMG01] 풀 조회 실패: {err}")
        return dict(cache)
    _poolCache = (time.time(), out)
    return dict(out)


def isMmgPoolStk(stkCd):
    """다른 전략(MTS01/MSE01) 신규매수 차단용: 풀 종목 또는 MMG01 보유."""
    cd = normStkCd(stkCd)
    if not cd:
        return False
    if holdsMmg(cd):
        return True
    if not isMmgEnabled():
        return False
    return cd in listPool()


def _watchRow(cd, nm, note):
    return {
        "STK_CD": cd, "MKT_TP": "KR", "STK_NM": nm or cd, "MARKET_NAME": "", "IND_GRP": "",
        "GRP_CD": "MMG01", "STATUS": "Y", "USE_BT": "N", "USE_TRADE": "N", "PRIORITY": 50,
        "RANK_NO": None, "REF_TRADE_DT": _todayYmd(), "LOAD_DT": _todayYmd(),
        "OPEN_PRC": None, "CLOSE_PRC": None, "SRC": "MMG01", "NOTE": note[:200],
    }


def addPool(stkCd, src="USER", expYmd="", why=""):
    global _poolCache
    cd = normStkCd(stkCd)
    if not cd:
        return False, "종목코드 오류"
    nm = _lookupNm(cd)
    note = f"exp={expYmd}|{why}" if expYmd else (why or "user")
    from utils.algStkStore import upsertAlgStk

    got = upsertAlgStk(_ALG_CD, cd, nm, src=src, note=note)
    if not got.get("ok"):
        return False, str(got.get("errMsg") or "등록 실패")
    try:
        from utils.watchStkStore import upsertWatchStkRows

        upsertWatchStkRows([_watchRow(cd, nm, f"MMG01 {src} {why}".strip())])
    except Exception as err:
        termLog(f"[MMG01] 관심종목 태그 실패 {cd}: {err}")
    _poolCache = (0.0, {})
    return True, f"{nm}({cd})"


def delPool(stkCd):
    global _poolCache
    cd = normStkCd(stkCd)
    from utils.algStkStore import deactivateAlgStk

    deactivateAlgStk(_ALG_CD, cd)
    try:
        from utils.watchStkStore import deactivateWatchStk, listActiveWatch

        if any(normStkCd(w.get("STK_CD")) == cd for w in listActiveWatch(grpCd="MMG01") or []):
            deactivateWatchStk(cd, note="MMG01 해제")
    except Exception:
        pass
    _poolCache = (0.0, {})
    return True


# ── 시세 ──────────────────────────────────────────────

def _fetchDayBarsRaw(cd):
    if "bars" in _injected:
        return _injected["bars"](cd)
    sess = _session()
    if sess is None:
        return None
    from api.getDayChart import fetchDayBars

    hostUrl, token, tokenType = sess
    got = fetchDayBars(hostUrl, token, cd, _todayYmd(), minBars=260, tokenType=tokenType,
                       isPaper=_tradeMode() != "real")
    if not got.get("ok"):
        return None
    rows = []
    for b in reversed(got.get("bars") or []):
        rows.append({"dt": str(b.get("dt")), "open": float(b.get("openPric") or b.get("curPrc")),
                     "high": float(b.get("highPric") or b.get("curPrc")),
                     "low": float(b.get("lowPric") or b.get("curPrc")),
                     "close": float(b.get("curPrc")), "vol": float(b.get("trdeQty") or 0)})
    return rows


def _closedBars(cd):
    """전일까지 확정 일봉 (당일 행 제외). 하루 1회 조회 후 캐시."""
    ymd = _todayYmd()
    key = (cd, ymd)
    if key in _barMem:
        return _barMem[key]
    rows = _fetchDayBarsRaw(cd)
    if rows is None:
        return None
    rows = [r for r in rows if r["dt"] < ymd]
    _barMem.clear() if len(_barMem) > 400 else None
    _barMem[key] = rows
    return rows


def _liveOhlc(cd):
    if "live" in _injected:
        return _injected["live"](cd)
    from utils.liveOhlcSnap import fetchLiveOhlc

    return fetchLiveOhlc(cd, cacheSec=10, preferMode=_tradeMode())


def _barsWithToday(cd, final=False):
    """판단용 봉: 확정봉 + 당일(장중=현재가 봉, 장후 final=일봉 당일행)."""
    if final:
        rows = _fetchDayBarsRaw(cd)
        return rows
    base = _closedBars(cd)
    if not base:
        return None
    live = _liveOhlc(cd)
    if not live or not live.get("close"):
        return None
    return base + [{"dt": _todayYmd(), "open": float(live["open"]), "high": float(live["high"]),
                    "low": float(live["low"]), "close": float(live["close"]),
                    "vol": float(live.get("qty") or 0)}]


def _equity():
    if "equity" in _injected:
        return float(_injected["equity"])
    sess = _session()
    if sess is not None:
        try:
            from api.getAcntEvlt import fetchAcntEvlt, parsePadNum

            hostUrl, token, tokenType = sess
            got = fetchAcntEvlt(hostUrl, token, tokenType=tokenType)
            val = parsePadNum(((got or {}).get("body") or {}).get("prsm_dpst_aset_amt"))
            if val and val > 0:
                with _lock:
                    _st()["equity"] = float(val)
                return float(val)
        except Exception as err:
            termLog(f"[MMG01] 추정자산 조회 실패: {err}")
    return float(_st().get("equity") or _loadParams()["equityFallbackWon"])


def _regimeOk():
    """KOSPI 또는 KOSDAQ 종가 > SMA50."""
    if "regime" in _injected:
        return bool(_injected["regime"])
    d = _day()
    if "regime" in d:
        return bool(d["regime"])
    ok = False
    sess = _session()
    if sess is not None:
        try:
            from api.getDayIndsPrc import fetchDayIndsPrcRange, rowToRecord

            hostUrl, token, tokenType = sess
            start = (_now() - timedelta(days=130)).strftime("%Y%m%d")
            for code in ("001", "101"):
                got = fetchDayIndsPrcRange(hostUrl, token, code, start, _todayYmd(),
                                           tokenType=tokenType, isPaper=_tradeMode() != "real")
                recs = [rowToRecord(r, code) for r in (got.get("rows") or [])]
                cl = [r["CLOSE_PRC"] for r in sorted([r for r in recs if r], key=lambda r: r["TRADE_DT"])
                      if r.get("CLOSE_PRC")]
                if len(cl) >= 50 and cl[-1] > sum(cl[-50:]) / 50.0:
                    ok = True
                time.sleep(_PAPER_WAIT_SEC)
        except Exception as err:
            termLog(f"[MMG01] 지수 필터 조회 실패: {err}")
    d["regime"] = ok
    return ok


def _earnOk(cd):
    if "earn" in _injected:
        return bool(_injected["earn"](cd))
    try:
        from utils.mmgEarnings import isEarnOk

        return isEarnOk(cd, _todayYmd())
    except Exception:
        return False


# ── 이벤트·원장 ───────────────────────────────────────

def _evt(evtTp, cd, reason="", px=0, qty=0, note=""):
    if _injected.get("noDb"):
        _injected.setdefault("evts", []).append((evtTp, cd, reason, px, qty, note))
        return
    try:
        from utils.algEvtStore import insertAlgEvt

        insertAlgEvt({"algCd": _ALG_CD, "evtTp": evtTp, "reason": reason, "stkCd": cd,
                      "stkNm": (_st()["pos"].get(cd) or {}).get("nm") or "", "mode": _tradeMode(),
                      "tradeDt": _todayYmd(), "evtTm": _nowHms(), "px": int(px or 0),
                      "qty": int(qty or 0), "note": str(note or "")[:200]})
    except Exception as err:
        termLog(f"[MMG01] 이벤트 기록 실패 {cd}: {err}")


def _ledgerBuy(cd, pos, px, qty):
    if _injected.get("noDb"):
        return
    try:
        from utils.stratOrdLedgerStore import upsertLedgerBuy

        upsertLedgerBuy(_STRAT_ID, cd, pos.get("nm") or "", px, qty, int(pos.get("nAdds") or 0),
                        {"qty": pos["qty"], "avgPx": pos["avg"], "filled": list(range(int(pos.get("nAdds") or 0) + 1)),
                         "halfTaken": False}, mode=_tradeMode())
    except Exception as err:
        termLog(f"[MMG01] 원장 매수 실패 {cd}: {err}")


def _closeRound(cd, pos, px, reason):
    """전량청산: 원장 마감 + LIVE_TRADE (체결로그 기준 집계)."""
    if _injected.get("noDb"):
        _injected.setdefault("closed", []).append((cd, reason, dict(pos), px))
        return
    try:
        from utils.stratLiveTradeStore import insertLiveTrade
        from utils.stratOrdLedgerStore import getOpenLedgerRow, rowToLivePos, upsertLedgerSell
        from utils.stratRoundFill import calcRoundFill

        row = getOpenLedgerRow(_STRAT_ID, cd)
        rec = rowToLivePos(row, todayYmd=_todayYmd()) if row else {
            "qty": 0, "avgPx": pos["avg"], "buyAmt": pos["cost"], "buyQty": pos.get("buyQty") or 0}
        upsertLedgerSell(_STRAT_ID, cd, px, int(pos.get("lastSellQty") or 0), reason, rec, True, mode=_tradeMode())
        entryDt = str(pos.get("entryDt") or _todayYmd())[:8]
        fill = calcRoundFill(_ALG_CD, cd, entryDt, _todayYmd(), mode=_tradeMode())
        buyQty = int(pos.get("buyQty") or 0)
        buyAmt = float(pos.get("buyAmt") or 0)
        sellQty = int(pos.get("sellQty") or 0)
        sellAmt = float(pos.get("sellAmt") or 0)
        if fill.get("ok") and fill.get("closed"):
            buyQty, sellQty = int(fill.get("buyQty") or 0), int(fill.get("sellQty") or 0)
            buyAmt, sellAmt = float(fill.get("buyAmt") or 0), float(fill.get("sellAmt") or 0)
        pnl = sellAmt - buyAmt
        insertLiveTrade({
            "algCd": _ALG_CD, "stratId": _STRAT_ID, "mode": _tradeMode(), "stkCd": cd,
            "stkNm": pos.get("nm") or "", "entryDt": entryDt, "exitDt": _todayYmd(), "exitTm": _nowHms(),
            "exitReason": "TAKE" if pnl > 0 else "STOP", "buyQty": buyQty, "sellQty": sellQty,
            "buyAmt": buyAmt, "sellAmt": sellAmt, "avgBuyPx": (buyAmt / buyQty) if buyQty else pos["avg"],
            "exitPx": px, "pnlWon": pnl, "pnlPct": (pnl / buyAmt * 100.0) if buyAmt > 0 else 0.0,
            "halfYn": "N", "steps": str(int(pos.get("nAdds") or 0)),
        })
        _notify(f"[MMG01 청산] {pos.get('nm')}({cd}) {reason} {pnl:+,.0f}원 "
                f"({(pnl / buyAmt * 100.0) if buyAmt else 0:+.2f}%)")
    except Exception as err:
        termLog(f"[MMG01] 청산 기록 실패 {cd}: {err}")


# ── 체결 반영 ─────────────────────────────────────────

def _newPos(cd, px, src):
    from utils.mmgSignals import baseStop

    p = _loadParams()
    return {"cd": cd, "nm": _lookupNm(cd) if not _injected.get("noDb") else cd, "qty": 0, "avg": 0.0,
            "cost": 0.0, "buyQty": 0, "buyAmt": 0.0, "sellQty": 0, "sellAmt": 0.0,
            "entryDt": _todayYmd(), "entryPx": float(px), "nAdds": 0, "lastAddDt": _todayYmd(),
            "lastAddPx": float(px), "stop": baseStop(px, p), "hi": float(px), "s2": False, "src": src}


def _applyBuy(cd, qty, px, kind, src="MANUAL"):
    with _lock:
        st = _st()
        pos = st["pos"].get(cd)
        if not pos:
            pos = _newPos(cd, px, src)
            st["pos"][cd] = pos
        pos["qty"] = int(pos["qty"]) + qty
        pos["cost"] = float(pos["cost"]) + px * qty
        pos["avg"] = pos["cost"] / pos["qty"]
        pos["buyQty"] = int(pos.get("buyQty") or 0) + qty
        pos["buyAmt"] = float(pos.get("buyAmt") or 0) + px * qty
        pos["hi"] = max(float(pos.get("hi") or 0), float(px))
        if kind == "ADD":
            pass  # nAdds·손절은 주문 시점 계획으로 이미 반영
        _saveState()
    _evt("BUY", cd, reason=kind, px=px, qty=qty, note=src)
    _ledgerBuy(cd, pos, px, qty)
    return pos


def _applySell(cd, qty, px, reason):
    closed = None
    with _lock:
        st = _st()
        pos = st["pos"].get(cd)
        if not pos:
            return None
        qty = min(int(qty), int(pos["qty"]))
        if qty <= 0:
            return None
        avg = float(pos["avg"])
        pos["qty"] = int(pos["qty"]) - qty
        pos["cost"] = avg * pos["qty"]
        pos["sellQty"] = int(pos.get("sellQty") or 0) + qty
        pos["sellAmt"] = float(pos.get("sellAmt") or 0) + px * qty
        pos["lastSellQty"] = qty
        _day()["realized"] = float(_day()["realized"]) + (px - avg) * qty
        if pos["qty"] <= 0:
            closed = st["pos"].pop(cd)
            st["lastExit"][cd] = _todayYmd()
        _saveState()
    _evt("SELL", cd, reason=reason, px=px, qty=qty)
    if closed:
        _closeRound(cd, closed, px, reason)
    return closed


def _isMyOrd(ordNo):
    from utils.botOrdRegistry import normOrdNo

    no = normOrdNo(ordNo)
    with _lock:
        return _st()["ordNos"].get(no) if no else None


def onBrokerFill(sideLabel, stkCd, fillQty, fillPx, ordNo=""):
    """00 체결 → MMG01 주문분 반영 / 풀 종목 수동 매수 편입 / 수동 매도 반영."""
    cd = normStkCd(stkCd)
    qty = int(fillQty or 0)
    px = abs(float(fillPx or 0))
    if not cd or qty <= 0 or px <= 0:
        return
    side = "BUY" if "매수" in str(sideLabel) else ("SELL" if "매도" in str(sideLabel) else "")
    if not side:
        return
    mine = _isMyOrd(ordNo)
    if mine:
        if side == "BUY":
            _applyBuy(cd, qty, px, mine.get("kind") or "BUY", src=mine.get("src") or "AUTO")
            termLog(f"[MMG01체결] {cd} 매수 {qty}주 {px:,.0f} ({mine.get('kind')})")
        else:
            _applySell(cd, qty, px, mine.get("reason") or "SELL")
            termLog(f"[MMG01체결] {cd} 매도 {qty}주 {px:,.0f} ({mine.get('reason')})")
        return
    from utils.botOrdRegistry import isBotOrd

    if isBotOrd(ordNo, cd, side):
        return  # 다른 전략·수동명령(텔레그램) 주문
    if not isMmgEnabled():
        return
    if side == "BUY":
        if cd not in listPool() and not holdsMmg(cd):
            return
        with _lock:
            had = holdsMmg(cd)
            last = _st()["lastExit"].get(cd)
        pos = _applyBuy(cd, qty, px, "MANUAL_ADD" if had else "ADOPT", src="MANUAL")
        _notify(f"[MMG01 편입] {pos.get('nm')}({cd}) 수동매수 {qty}주 {px:,.0f}원 → 관리 시작"
                + (f" (최근 청산 {last})" if last else ""))
    else:
        if holdsMmg(cd):
            _applySell(cd, qty, px, "MANUAL_SELL")
            _notify(f"[MMG01] {cd} 수동매도 {qty}주 반영")


def adoptFromAccount(stkCd):
    """mmg adopt — 계좌 보유분을 평단 그대로 MMG01 로 편입 (풀에도 등록)."""
    cd = normStkCd(stkCd)
    if holdsMmg(cd):
        return False, "이미 MMG01 관리 중"
    try:
        from trading.crossAlgBuyGuard import _openAlgCdsForStk

        others = [a for a in _openAlgCdsForStk(cd) if a != _ALG_CD]
        if others:
            return False, f"다른 전략 보유 중({others[0]}) — 한 종목 한 전략"
    except Exception:
        pass
    sess = _session()
    if sess is None:
        return False, "주문 세션 없음(미로그인/모드)"
    from api.getAcntEvlt import fetchAcntEvlt
    from trading.maTouchLive import _holdMapFromAcnt

    hostUrl, token, tokenType = sess
    got = fetchAcntEvlt(hostUrl, token, tokenType=tokenType)
    hold = _holdMapFromAcnt((got or {}).get("body") or {}).get(cd)
    if not hold:
        return False, "계좌에 보유 없음"
    addPool(cd, src="USER", why="adopt")
    pos = _applyBuy(cd, int(hold["qty"]), float(hold["avgPx"]), "ADOPT", src="MANUAL")
    return True, f"{pos.get('nm')}({cd}) {pos['qty']}주 평단 {pos['avg']:,.0f} 편입"


# ── 주문 ──────────────────────────────────────────────

def _waitOrdGap():
    global _lastOrdAt
    gap = _PAPER_WAIT_SEC - (time.time() - _lastOrdAt)
    if gap > 0 and not _injected.get("noSleep"):
        time.sleep(gap)
    _lastOrdAt = time.time()


def _placeOrd(cd, side, qty, refPx, meta, auction=True):
    """
    지정가(동시호가 참여용 여유 2%) 또는 장중 시장가. 단가는 api 계층에서 v63 호가 보정.
    meta: {kind, reason, src}
    """
    from utils.krxTick import ceilToKrxTick, floorToKrxTick

    sess = _session()
    if sess is None:
        termLog(f"[MMG01] 주문 세션 없음 — {cd} {side} 생략")
        return None
    hostUrl, token, tokenType = sess
    if auction:
        trdeTp = _TRDE_TP_LIMIT
        px = floorToKrxTick(refPx * 1.02) if side == "BUY" else ceilToKrxTick(refPx * 0.98)
        ordUv = str(int(px))
    else:
        trdeTp, ordUv, px = _TRDE_TP_MARKET, "", refPx
    _waitOrdGap()
    if "order" in _injected:
        result = _injected["order"](cd, side, qty, trdeTp, ordUv)
    elif side == "BUY":
        from api.buyStk import placeBuyOrd

        result = placeBuyOrd(hostUrl, token, _ordExch(cd), cd, str(qty), trdeTp, ordUv=ordUv, tokenType=tokenType)
    else:
        from api.sellStk import placeSellOrd

        result = placeSellOrd(hostUrl, token, _ordExch(cd), cd, str(qty), trdeTp, ordUv=ordUv, tokenType=tokenType)
    if not result.get("ok"):
        _evt(f"{side}_FAIL", cd, reason=meta.get("kind") or meta.get("reason"), px=px, qty=qty,
             note=str(result.get("errMsg") or ""))
        _notify(f"[MMG01 주문실패] {cd} {side} {qty}주: {result.get('errMsg')}")
        return None
    from utils.botOrdRegistry import normOrdNo

    no = normOrdNo(result.get("ordNo"))
    with _lock:
        _st()["ordNos"][no] = dict(meta, cd=cd, side=side, qty=qty, at=time.time(), ymd=_todayYmd())
        for k in [k for k, v in _st()["ordNos"].items() if time.time() - float(v.get("at") or 0) > 5 * 86400]:
            _st()["ordNos"].pop(k, None)
        _saveState()
    _evt(f"{side}_ORD", cd, reason=meta.get("kind") or meta.get("reason"), px=px, qty=qty, note=f"ord={no}")
    return no


# ── 판단 ──────────────────────────────────────────────

def _barsSince(bars, ymd):
    return sum(1 for b in bars if b["dt"] > str(ymd or ""))


def _daysSince(ymd):
    if not ymd:
        return None
    try:
        return (_now() - datetime.strptime(ymd, "%Y%m%d")).days
    except Exception:
        return None


def _exposure(lastPx):
    return sum(int(v["qty"]) * float(lastPx.get(cd) or v["avg"]) for cd, v in _st()["pos"].items())


def decideEod(final=False):
    """
    15:15 판단. 보유: EXIT/ADD/HOLD, 풀 미보유: SCOUT 후보.
    반환 plan 목록 (실행은 executePlan).
    """
    from utils.mmgSignals import addPlan, calcInd, entryAllowed, eodExitReason, updateStops

    p = _loadParams()
    ymd = _todayYmd()
    plan, log = [], []
    equity = _equity()
    pos = listMmgPositions()
    inds, lastPx = {}, {}
    for cd in pos:
        bars = _barsWithToday(cd, final=final)
        ind = calcInd(bars) if bars else None
        inds[cd] = (ind, bars)
        if ind:
            lastPx[cd] = ind["close"]
    with _lock:
        dayPnl = float(_day()["realized"])
        for cd, v in pos.items():
            ind = inds[cd][0]
            if ind:
                dayPnl += int(v["qty"]) * (ind["close"] - ind["prevClose"])
        halt = dayPnl < -float(p["dailyLossHaltPct"]) / 100.0 * equity
        expo = _exposure(lastPx)
    if halt:
        log.append(f"당일손익 {dayPnl:+,.0f} < −{p['dailyLossHaltPct']}% → 추가 중지")
    for cd, v in pos.items():
        ind, bars = inds[cd]
        if not ind:
            log.append(f"{cd} 지표 부족/시세 없음 → HOLD")
            plan.append({"cd": cd, "act": "HOLD", "why": "NO_DATA"})
            continue
        with _lock:
            live = _st()["pos"].get(cd)
            if not live:
                continue
            updateStops(live, ind, p)
            v = dict(live)
            _saveState()
        why = eodExitReason(v, ind, p)
        if not why and ind["close"] <= float(v.get("stop") or 0):
            why = "EXIT_STOP"
        if why:
            plan.append({"cd": cd, "act": "EXIT", "qty": int(v["qty"]), "px": ind["close"], "why": why})
            continue
        ctx = {"maxBuyAmt": p["maxBuyAmt"], "equity": equity, "exposure": expo,
               "maxExposurePct": p["maxExposurePct"], "haltAdds": halt,
               "barsSinceAdd": _barsSince(bars, v.get("lastAddDt"))}
        ap, whyNot = addPlan(v, ind, ctx, p)
        if ap:
            plan.append({"cd": cd, "act": "ADD", "qty": ap["qty"], "px": ap["px"], "why": ap["setup"],
                         "stopAfter": ap["stopAfter"], "n": int(v.get("nAdds") or 0) + 1})
            expo += ap["qty"] * ap["px"]
        else:
            plan.append({"cd": cd, "act": "HOLD", "why": whyNot, "stop": round(float(v["stop"]))})
    # 자동정찰
    if p.get("autoScout"):
        d = _day()
        slots = int(p["autoScoutMaxPerDay"]) - len(d["autoScouts"])
        nPos = len(pos)
        regime = _regimeOk()
        for cd, meta in listPool().items():
            if slots <= 0 or nPos >= int(p["maxPositions"]):
                break
            if cd in pos or cd in d["autoScouts"]:
                continue
            try:
                from trading.crossAlgBuyGuard import isNewBuyBlocked

                blocked, bwhy = isNewBuyBlocked(_ALG_CD, cd) if not _injected.get("noDb") else (False, "")
            except Exception:
                blocked, bwhy = False, ""
            if blocked:
                log.append(f"{cd} 정찰 스킵 {bwhy}")
                continue
            bars = _barsWithToday(cd, final=final)
            ind = calcInd(bars) if bars else None
            ok, whyNot = entryAllowed(cd, ind, {"regimeOk": regime, "earnOk": _earnOk(cd),
                                                "daysSinceExit": _daysSince(_st()["lastExit"].get(cd))}, p)
            if not ok:
                log.append(f"{cd} 정찰X {whyNot}")
                continue
            if expo + ind["close"] > equity * float(p["maxExposurePct"]) / 100.0:
                log.append(f"{cd} 정찰X EXPOSURE")
                continue
            plan.append({"cd": cd, "act": "SCOUT", "qty": 1, "px": ind["close"], "why": "+".join(ind["sigTags"])})
            slots -= 1
            nPos += 1
            expo += ind["close"]
    with _lock:
        d = _day(ymd)
        d["plan"], d["log"], d["decided"] = plan, log, True
        d["decidedAt"] = _nowHms()
        _saveState()
    for it in plan:
        if it["act"] != "HOLD" or not _injected.get("quietHold"):
            _evt("DECIDE", it["cd"], reason=f"{it['act']}:{it.get('why')}", px=it.get("px") or 0,
                 qty=it.get("qty") or 0)
    _writeDecisionLog(ymd)
    acts = [f"{it['act']} {it['cd']} {it.get('qty', '')} {it.get('why', '')}".strip()
            for it in plan if it["act"] != "HOLD"]
    _notify(f"[MMG01 15:15 판단] 보유 {len(pos)} · 실행 {len(acts)}\n" + ("\n".join(acts) or "변경 없음"))
    return plan


def executePlan(auction=True):
    """판단 결과 주문 (15:21~15:27 동시호가 지정가)."""
    with _lock:
        d = _day()
        plan = list(d.get("plan") or [])
        if d.get("executed"):
            return []
        d["executed"] = True
        _saveState()
    done = []
    for it in plan:
        cd, act = it["cd"], it["act"]
        if act == "EXIT":
            no = _placeOrd(cd, "SELL", it["qty"], it["px"], {"reason": it["why"]}, auction=auction)
        elif act == "ADD":
            no = _placeOrd(cd, "BUY", it["qty"], it["px"], {"kind": "ADD", "src": "AUTO"}, auction=auction)
            if no:
                with _lock:
                    v = _st()["pos"].get(cd)
                    if v:
                        v["nAdds"] = int(v.get("nAdds") or 0) + 1
                        v["lastAddDt"], v["lastAddPx"] = _todayYmd(), float(it["px"])
                        v["stop"] = max(float(v.get("stop") or 0), float(it["stopAfter"]))
                        _saveState()
        elif act == "SCOUT":
            no = _placeOrd(cd, "BUY", 1, it["px"], {"kind": "SCOUT", "src": "AUTO"}, auction=auction)
            if no:
                with _lock:
                    _day()["autoScouts"].append(cd)
                    _saveState()
        else:
            continue
        it["ordNo"] = no or ""
        done.append(it)
    _writeDecisionLog(_todayYmd())
    return done


def checkIntradayStops():
    """장중 보호 손절 (추가 1회 이상 포지션). 시장가."""
    from utils.mmgSignals import intradayStopHit

    hits = []
    for cd, v in listMmgPositions().items():
        live = _liveOhlc(cd)
        if not live or not live.get("close"):
            continue
        if intradayStopHit(v, live["close"]):
            with _lock:
                if _st()["pos"].get(cd, {}).get("stopOrdYmd") == _todayYmd():
                    continue
                _st()["pos"][cd]["stopOrdYmd"] = _todayYmd()
                _saveState()
            no = _placeOrd(cd, "SELL", int(v["qty"]), live["close"], {"reason": "INTRADAY_STOP"}, auction=False)
            _notify(f"[MMG01 장중손절] {cd} {v['qty']}주 현재가 {live['close']:,.0f} ≤ 손절 {v['stop']:,.0f}")
            hits.append((cd, no))
    return hits


def reconcileAcnt():
    """계좌 잔량 0 인데 MMG01 보유면 외부청산으로 닫는다 (2회 연속 확인)."""
    try:
        from trading.crossAlgBuyGuard import getAcntHoldCds, getAcntHoldQty
    except Exception:
        return
    cds = getAcntHoldCds()
    if not cds:
        return
    for cd, v in listMmgPositions().items():
        if getAcntHoldQty(cd) > 0:
            with _lock:
                if cd in _st()["pos"]:
                    _st()["pos"][cd].pop("acntMiss", None)
            continue
        recent = any(o.get("cd") == cd and time.time() - float(o.get("at") or 0) < 180
                     for o in _st()["ordNos"].values())
        if recent:
            continue
        with _lock:
            cur = _st()["pos"].get(cd)
            if not cur:
                continue
            cur["acntMiss"] = int(cur.get("acntMiss") or 0) + 1
            miss = cur["acntMiss"]
        if miss >= 2:
            live = _liveOhlc(cd) or {}
            _applySell(cd, int(v["qty"]), float(live.get("close") or v["avg"]), "EXT_CLOSE")
            _notify(f"[MMG01] {cd} 계좌잔량 0 → 외부청산 처리")


def _writeDecisionLog(ymd):
    if _injected.get("noDisk"):
        return
    try:
        _DECISION_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            d = dict(_day(ymd))
            d["positions"] = listMmgPositions()
        (_DECISION_DIR / f"{ymd}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as err:
        termLog(f"[MMG01] 판단로그 저장 실패: {err}")


# ── 스크리너 ──────────────────────────────────────────

def _universe():
    from utils.dbConn import openDbConn

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT STK_CD, STK_NM FROM AI_STK_INFO WHERE MARKET_NAME IN ('거래소','코스닥') "
                        "AND (LAST_PRICE IS NULL OR LAST_PRICE >= 1000)")
            rows = cur.fetchall() or []
    finally:
        conn.close()
    out = []
    for r in rows:
        cd = normStkCd(r.get("STK_CD") if isinstance(r, dict) else r[0])
        nm = str((r.get("STK_NM") if isinstance(r, dict) else r[1]) or "")
        # 보통주만 (우선주·스팩 제외)
        if not cd or not cd.endswith("0") or "스팩" in nm or nm.endswith("우") or "우B" in nm:
            continue
        out.append(cd)
    return out


def runScreener(cds=None, final=True, write=True, maxScan=None):
    """
    장마감 후(16:10) 조기상승 후보 스캔 → 풀(src=SCR, 10일 만료) 갱신.
    반환 [{cd, tags, close, val20, vr, earn}] 점수순.
    """
    from utils.mmgSignals import calcInd

    p = _loadParams()
    cds = list(cds or _universe())[: maxScan or None]
    try:
        from utils.mmgEarnings import refreshEarnings

        if not _injected.get("noDb"):
            refreshEarnings(cds)
    except Exception as err:
        termLog(f"[MMG01] 실적 갱신 실패: {err}")
    regime = _regimeOk()
    hits = []
    t0 = time.time()
    for i, cd in enumerate(cds):
        rows = _fetchDayBarsRaw(cd)
        if not _injected.get("noSleep"):
            time.sleep(_PAPER_WAIT_SEC if _tradeMode() != "real" else 0.3)
        if not rows:
            continue
        if not final:
            rows = [r for r in rows if r["dt"] < _todayYmd()]
        ind = calcInd(rows)
        if not ind or not ind["entrySig"] or ind["close"] > float(p["scoutMaxPx"]):
            continue
        earn = _earnOk(cd)
        if not earn:
            continue
        score = len(ind["sigTags"]) * 10 + min(ind["vr"], 5) + (2 if ind["stage2"] else 0)
        hits.append({"cd": cd, "tags": ind["sigTags"], "close": ind["close"], "val20": ind["val20"],
                     "vr": round(ind["vr"], 2), "stage2": ind["stage2"], "score": score})
        if i % 200 == 0:
            termLog(f"[MMG01 스크리너] {i}/{len(cds)} · 후보 {len(hits)} · {time.time() - t0:.0f}s")
    hits.sort(key=lambda h: -h["score"])
    top = hits[: int(p["screenerMaxNames"])]
    if write and regime:
        exp = (_now() + timedelta(days=int(p["screenerExpireDays"]) * 7 // 5)).strftime("%Y%m%d")
        _expireScreener()
        pool = listPool(force=True)
        nScr = sum(1 for v in pool.values() if v["src"] == "SCR")
        for h in top:
            if h["cd"] in pool:
                continue
            if nScr >= int(p["screenerMaxNames"]):
                break
            addPool(h["cd"], src="SCR", expYmd=exp, why="+".join(h["tags"]))
            nScr += 1
    elif write:
        termLog("[MMG01 스크리너] 지수 필터(KOSPI/KOSDAQ>SMA50) 불충족 — 풀 추가 생략")
    with _lock:
        _day()["screen"] = top
        _day()["screened"] = True
        _saveState()
    _notify("[MMG01 스크리너] " + (", ".join(f"{h['cd']}({'+'.join(h['tags'])})" for h in top) or "후보 없음"))
    return top


def _expireScreener():
    ymd = _todayYmd()
    for cd, v in listPool(force=True).items():
        if v["src"] == "SCR" and v["expYmd"] and v["expYmd"] < ymd and not holdsMmg(cd):
            delPool(cd)
            termLog(f"[MMG01] 스크리너 만료 해제 {cd}")


# ── 스케줄 ────────────────────────────────────────────

def _isTradingDay():
    try:
        from config.krHoliday import isTradingDay

        return isTradingDay(_now().date())
    except Exception:
        return _now().weekday() < 5


def _tick():
    if not isMmgEnabled() or not _isTradingDay():
        return
    hms = _nowHms()
    d = _day()
    if _INTRADAY_START <= hms <= _INTRADAY_END:
        last = float(_st().get("_lastIntraday") or 0)
        if time.time() - last >= float(_loadParams()["intradayPollSec"]):
            _st()["_lastIntraday"] = time.time()
            checkIntradayStops()
            reconcileAcnt()
    if hms >= _RESCAN_TM and not d.get("rescanned") and hms < _DECIDE_TM:
        d["rescanned"] = True
        for cd in list(listPool().keys()) + list(listMmgPositions().keys()):
            _closedBars(cd)
    if _DECIDE_TM <= hms < _EXEC_START_TM and not d.get("decided"):
        decideEod()
    if _EXEC_START_TM <= hms <= _EXEC_END_TM and d.get("decided") and not d.get("executed"):
        executePlan(auction=True)
    if hms >= _SCREEN_TM and not d.get("screened"):
        d["screened"] = True
        _saveState()
        threading.Thread(target=_safeScreen, name="mmgScreener", daemon=True).start()


def _safeScreen():
    try:
        runScreener()
    except Exception as err:
        termLog(f"[MMG01 스크리너] 오류: {err}")


def _loop():
    while True:
        try:
            _tick()
        except Exception as err:
            termLog(f"[MMG01] 루프 오류: {err}")
        time.sleep(10)


def startManualMgrLive():
    global _workerStarted
    if _workerStarted:
        return
    if not isMmgEnabled():
        termLog("[MMG01] 비활성 — 기동 생략")
        return
    _workerStarted = True
    threading.Thread(target=_loop, name="mmg01Live", daemon=True).start()
    termLog(f"[MMG01] 수동정찰 피라미딩 기동 · {_tradeMode()} · paperOnly={_loadParams().get('paperOnly')} "
            f"· 종목당 최대 {_loadParams()['maxBuyAmt']:,.0f}원 · 보유 {len(listMmgPositions())}")


def statusText():
    p = _loadParams()
    pos = listMmgPositions()
    lines = [f"MMG01 {'ON' if p['_enabled'] else 'OFF'} · {_tradeMode()} · paperOnly={p.get('paperOnly')} "
             f"· 종목당 최대 {p['maxBuyAmt']:,.0f}원"]
    for cd, v in pos.items():
        lines.append(f"- {v.get('nm')}({cd}) {v['qty']}주 평단 {v['avg']:,.0f} 추가 {v.get('nAdds', 0)}/3 "
                     f"손절 {float(v.get('stop') or 0):,.0f}{' S2' if v.get('s2') else ''}")
    return "\n".join(lines)
