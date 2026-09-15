"""
이평선터치 매매 모의 실시간 실행기.

- 관심종목(USE_TRADE=Y) 0B 현재가 + 전일 확정 일봉 SMA
- 모의 세션만 주문 (paperOnly)
- SMA20 터치 후 **당일** 3분봉이 SMA20을 종가 돌파하고 **그 봉이 양봉**이면
  SMA20 **인근 호가 지정가** 매수. **10분** 미체결이면 잔량 취소만 (시장가 전환 없음)
  (터치 즉시·틱 회복 매수 없음. confirmMode=reclaim 이면 예전 회복 매수)
- 1차: 평단 **+1.5%** 보유 **30%** / 2차: **+2%** 보유 **30%** / 3차: **+4%** 보유 **50%** / 4차: **+7%** 잔량 전량
- 1·2차 전 하드 **-2%** / **1차 또는 2차 후** 잔량 **0%(본전)** 이하면 전량 / 수익 중 **RSI14≥70** 전량
- 당일 매수: 15:18 미수익(현재가≤평단)이면 전량. 익일 보유는 15:18 SMA20 손절
- 매일 15:15부터 평단 0% 이상 3% 이하면 전량 (손실 제외, 3% 초과는 보유)
- 전량 청산 후 당일 재매수 금지
- 평단·잔량은 **00 실체결** 및 **kt00004 계좌평가**로만 잡는다 (주문 접수만으로 보유/원장/BUY·SELL 이벤트를 만들지 않는다)
- 매수·매도 원장·익절 플래그는 체결 성공 기준. 계좌평가는 주기 동기화
- 매수·매도는 AI_STRAT_ORD_LEDGER 원장에 적재. 장 시작 전 미청산을 로드
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from api.buyStk import placeBuyOrd
from api.cnclStk import placeCnclOrd
from api.getAcntEvlt import parsePadNum
from api.sellStk import placeSellOrd
from config import runtimeState
from strategies.impl.maTouchScale import (
    applyBar3Confirm,
    bar3BucketTm,
    bar3ClosePx,
    buyAmtForStep,
    buyCutoffApplies,
    confirmModeIsBar3,
    confirmModeIsBar3Limit,
    inNextClosedHalfBand,
    normalizeFilledSteps,
    parseBuyAmtByStep,
    scaleOutQty,
)
from strategies.specLoader import loadStratSpec
from telegram.telSend import sendTelegramMsg
from utils.dbConn import openDbConn
from utils.krxTick import roundToKrxTick
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.tradeSession import getOrdSession, isModeLoggedIn, modeLabel

# 계좌평가(kt00004) 주기 동기화 간격(초). 모의 TR 한도 고려.
_ACNT_SYNC_SEC = 30.0
# 매도실패 후 동일 종목 재시도 쿨다운(초) — SELL_FAIL 폭주 방지
_SELL_FAIL_COOL_SEC = 60.0


def _tradeMode():
    """원장·복기·손익 적재용 deskTradeMode."""
    try:
        from utils.tradeSession import getDeskTradeMode

        return getDeskTradeMode()
    except Exception:
        return "paper"

_STATE_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "maTouchState.json"
_STRAT_ID = "maTouchScale_v1"
# 복기·원장은 상용 알고리즘코드로 연결 (파일명은 내부 로드용)
_ALG_CD = "MTS01"
_TRDE_TP_LIMIT = "0"
_TRDE_TP_MARKET = "3"
_PAPER_WAIT_SEC = 1.05
_LIMIT_WAIT_SEC = 600.0

_lock = threading.Lock()
# stkCd → {qty, avgPx, filled, halfTaken, lastPx, tradeDt}
_pos = {}
# stkCd → {5: ma, 10: ma, ...}
_smaMap = {}
_smaYmd = ""
# 당일 시가 · 전일 종가 (양봉 판정)
_openMap = {}
_prevCloseMap = {}
_lastPx = {}
# 일봉 RSI 히스토리. {stkCd: {closes, lastDt}} — 당일 1회
_rsiHist = {}
_rsiHistYmd = ""
_lastOrdAt = 0.0
_params = None
_paramsAt = 0.0
_ordQ = []
_pending = set()
_workerStarted = False
# 주문번호 → 미체결 대기. 평단은 00 체결·kt00004 까지 시세가로 올리지 않는다
_ordWait = {}
# 만료된 매도 대기. 손절 봉인을 풀어도 늦은 00 체결은 여기서 찾는다
_ordWaitGone = {}
# 같은 날·종목·매차·사유 스킵은 1회만 복기에 남긴다
_skipOnce = set()
# 당일: 이평 터치는 됐지만 3분봉 확인(또는 회복)을 기다리는 매차
# stkCd → {step: {maPx, touchTm, phase, brokeBucket}}
_waitConfirm = {}
_waitYmd = ""
# 0B 틱을 3분봉으로 합침. stkCd → {bucket, ymd, o, h, l, c}
_bar3Map = {}
# 전량 청산 당일 재매수 금지. stkCd → ymd
_buyBanYmd = {}
# 계좌평가 마지막 동기 시각 · 백그라운드 폴링
_acntSyncAt = 0.0
_acntSyncStarted = False
# 매도실패 쿨다운. stkCd → epoch until
_sellFailUntil = {}


def _notify(msg):
    """매수·분할매도는 콘솔만. 텔레그램은 전량 청산 요약만."""
    termLog(msg)


def _pushClosePnl(stkNm, stkCd, pnlPct, pnlWon, evalPct=None):
    """전량 청산 때만 승패·평가%/실현%·손익 금액 푸시."""
    name = stkNm or stkCd
    if pnlWon > 0:
        result = "승"
    elif pnlWon < 0:
        result = "패"
    else:
        result = "본전"
    try:
        realRt = float(pnlPct)
    except (TypeError, ValueError):
        realRt = 0.0
    try:
        evRt = float(evalPct) if evalPct is not None else None
    except (TypeError, ValueError):
        evRt = None
    if evRt is not None:
        pctLine = f"평가 {evRt:+.2f}% · 실현 {realRt:+.2f}%"
    else:
        pctLine = f"실현 {realRt:+.2f}%"
    msg = f"{name} {result}\n{pctLine} · {pnlWon:+,.0f}원"
    termLog(f"[이평터치청산] {name} {result} {pctLine} {pnlWon:+,.0f}원")
    try:
        sendTelegramMsg(msg, pushKind="closePnl")
    except Exception as err:
        termLog(f"[maTouch] 청산 텔레그램 실패: {err}")


def _loadParams():
    """스펙 + 설정 금액 오버라이드."""
    spec = loadStratSpec(_STRAT_ID) or {}
    p = dict(spec.get("params") or {})
    try:
        from config.settingsStore import getMaTouchLive

        live = getMaTouchLive()
        # 설정 금액이 있으면 매차별로 덮어쓴다
        liveBy = live.get("buyAmtByStep") or {}
        specBy = dict(p.get("buyAmtByStep") or {})
        for k, v in liveBy.items():
            specBy[str(int(k))] = v
        p["buyAmtByStep"] = specBy
        p["_enabled"] = bool(live.get("enabled", True))
        p["_paperOnly"] = bool(live.get("paperOnly", True))
        # v41: 1차 +1.5%. 예전 1차 절반 MFE 통계는 덮어쓰지 않는다.
        p["halfTpFromStat"] = False
        # v46: 3분봉 양봉 돌파 → SMA20 인근 지정가, 10분 미체결 취소만 (시장가 없음)
        p["confirmMode"] = "bar3BreakLimit"
        p["limitWaitSec"] = 600
        p.setdefault("tp1Pct", 1.5)
        p.setdefault("tp1QtyRatio", 0.3)
        p.setdefault("tp2Pct", 2)
        p.setdefault("tp2QtyRatio", 0.3)
        p.setdefault("tp3Pct", 4)
    except Exception:
        p["_enabled"] = True
        p["_paperOnly"] = True
    p.setdefault("maSteps", [20])
    p.setdefault("buyAmtByStep", {"20": 1_000_000})
    p.setdefault("exitAfterTm", "151800")
    p.setdefault("buyCutoffTm", "140000")
    p.setdefault("buyCutoffIfNextClosed", True)
    p.setdefault("stopMa", 20)
    p.setdefault("takeMa", 10)
    p.setdefault("confirmMode", "bar3BreakLimit")
    p.setdefault("confirmBarMin", 3)
    p.setdefault("limitWaitSec", 600)
    p.setdefault("halfTpPct", 3)
    p.setdefault("halfTpFromStat", False)
    p.setdefault("tp1Pct", 1.5)
    p.setdefault("tp1QtyRatio", 0.3)
    p.setdefault("tp2Pct", 2)
    p.setdefault("tp2QtyRatio", 0.3)
    p.setdefault("hardStopPct", 2)
    p.setdefault("afterHalfStopPct", 0)
    p.setdefault("breakevenAfterTp1", True)
    p.setdefault("buyBanUntilTm", "000000")
    p.setdefault("reclaimMinPct", 0)
    p.setdefault("banBuyIfOpenBelowSma", False)
    p.setdefault("halfAddNextDay", False)
    p.setdefault("halfAddMinPnlPct", 5)
    p.setdefault("halfAddQtyRatio", 0.5)
    p.setdefault("banRebuySameDay", True)
    p.setdefault("nextClosedHalfTm", "151500")
    p.setdefault("nextClosedHalfMinPct", 0)
    p.setdefault("nextClosedHalfMaxPct", 3)
    p.setdefault("eodFlatIfNoProfit", True)
    p.setdefault("rsiTpPeriod", 14)
    p.setdefault("rsiTpMin", 70)
    p.setdefault("tp3Pct", 4)
    p.setdefault("tp3QtyRatio", 0.5)
    p.setdefault("tp4Pct", 7)
    # 구 3차(+15%/RSI65) 키는 끄고 둔다. 스펙에 0 이 없어도 다시 켜지지 않게.
    p.setdefault("afterHalfTrailMinPct", 0)
    p.setdefault("afterHalfTrailTpPct", 0)
    p.setdefault("afterHalfRsiMin", 0)
    return p


def _refreshParams():
    """웹에서 금액을 바꿔도 수 초 안에 다음 매수에 반영."""
    global _params, _paramsAt
    now = time.time()
    if _params is None or now - _paramsAt > 2.0:
        _params = _loadParams()
        _paramsAt = now
    return _params


def invalidateLiveParams():
    """장마감 1차 통계 반영 직후 캐시를 버려 다음 틱에 새 % 를 쓴다."""
    global _paramsAt
    _paramsAt = 0.0


def _isEnabled():
    p = _refreshParams()
    return bool(p.get("_enabled", True))


def _ordExch(stkCd):
    """주문 거래소. NXT 매매 + NXT가능 종목만 NXT, 나머지는 KRX."""
    try:
        from config.settingsStore import getDeskTradeExch
        from utils.stkInfoStore import isNxtEnabled

        if getDeskTradeExch() == "NXT" and isNxtEnabled(stkCd):
            return "NXT"
    except Exception:
        pass
    return "KRX"


def _ordSession():
    """deskTradeMode + paperOnly 에 맞는 주문 세션 (모의/실전)."""
    return getOrdSession(paperOnly=bool((_params or {}).get("_paperOnly", True)))


def _absPrice(raw):
    num = parsePadNum(raw)
    if num is None:
        return None
    return abs(int(num))


def _nowHms():
    return datetime.now().strftime("%H%M%S")


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _liveRsi(stkCd, curPx, period=14):
    """
    관심종목과 같은 일봉 Wilder RSI.
    DB 히스토리는 종목·당일 1회만 읽고, 현재가로 마지막 봉만 맞춘다.
    """
    global _rsiHist, _rsiHistYmd
    cd = normStkCd(stkCd)
    if not cd:
        return None
    ymd = _todayYmd()
    if str(_rsiHistYmd or "") != ymd:
        _rsiHist = {}
        _rsiHistYmd = ymd
    rec = _rsiHist.get(cd)
    if rec is None:
        try:
            from utils.daySmaSnap import loadRsiCloseHist

            got = loadRsiCloseHist([cd]) or {}
            rec = got.get(cd) or {"closes": [], "lastDt": ""}
        except Exception:
            rec = {"closes": [], "lastDt": ""}
        _rsiHist[cd] = rec
    try:
        from utils.daySmaSnap import liveRsiFromPriorCloses

        return liveRsiFromPriorCloses(rec, curPx=curPx, period=period)
    except Exception:
        return None


def _smaSnap(sma):
    """복기용 이평 스냅샷. 틱마다 전부 저장하지 않고 이벤트에만 붙인다."""
    m = sma or {}
    return {
        "sma5": m.get(5),
        "sma10": m.get(10),
        "sma15": m.get(15),
        "sma20": m.get(20),
    }


def _writeAlgEvt(evtTp, stkCd, reason="", px=None, sma=None, qty=None, note=""):
    """
    상용 ALG_CD 에 복기 행을 남긴다.
    주문 실패를 막기 위해 예외는 삼킨다.
    """
    try:
        from utils.algEvtStore import insertAlgEvt

        rec = _pos.get(stkCd) or {}
        snap = _smaSnap(sma if sma is not None else (_smaMap.get(stkCd) or {}))
        insertAlgEvt(
            {
                "algCd": _ALG_CD,
                "evtTp": evtTp,
                "reason": reason,
                "stkCd": stkCd,
                "stkNm": rec.get("stkNm") or _lookupStkNm(stkCd),
                "tradeCd": rec.get("tradeCd"),
                "mode": _tradeMode(),
                "tradeDt": _todayYmd(),
                "evtTm": _nowHms(),
                "px": px,
                "qty": qty,
                "note": note,
                **snap,
            }
        )
    except Exception as err:
        termLog(f"[maTouch] 복기이벤트 실패: {err}")


def _lookupStkNm(stkCd):
    """관심종목 이름. 없으면 코드만."""
    try:
        from utils.watchStkStore import listActiveWatch

        for w in listActiveWatch(mktTp="KR") or []:
            if normStkCd(w.get("STK_CD")) == stkCd:
                return w.get("STK_NM") or ""
    except Exception:
        pass
    return ""


def _normOrdNo(raw):
    """키움 주문번호 앞자리 0 제거. 빈 값은 빈 문자열."""
    text = str(raw or "").strip()
    if not text:
        return ""
    return text.lstrip("0") or "0"


def _blankPos(entryDt=""):
    return {
        "qty": 0,
        "avgPx": 0.0,
        "filled": [],
        "halfTaken": False,
        "tp1Taken": False,
        "tp2Taken": False,
        "tp3Taken": False,
        "halfDt": "",
        "halfAddDone": False,
        # 매수 후 장중 고가/현재가가 SMA(takeMa) 이상이면 True → SMA10 익절
        "brokeAboveTakeMa": False,
        "buyAmt": 0.0,
        "sellAmt": 0.0,
        "buyQty": 0,
        "sellQty": 0,
        "entryDt": entryDt or _todayYmd(),
        "maxPnlPct": 0.0,
        "maxPx": 0.0,
    }


def _normTpFlags(rec):
    """구 절반익절 플래그를 1·2차로 맞춘다. 절반을 친 포지션은 2차까지 끝난 것으로 본다."""
    if not rec:
        return rec
    if rec.get("halfTaken") and (not rec.get("tp2Taken")):
        rec["tp1Taken"] = True
        rec["tp2Taken"] = True
    rec.setdefault("tp1Taken", False)
    rec.setdefault("tp2Taken", False)
    rec.setdefault("tp3Taken", False)
    return rec


def _hasOrdWait(cd, side=None):
    """해당 종목 미체결 주문이 있으면 True. 중복 손절·재매수 방지."""
    for wait in _ordWait.values():
        if wait.get("cd") != cd:
            continue
        if side and wait.get("side") != side:
            continue
        return True
    return False



def _buyFillPending(cd, side="BUY"):
    """
    00 체결 전 매수 대기면 True.
    계좌동기(kt00004)가 지정가 체결을 먼저 보여줘도, gotQty==0 이면
    아직 공식 매수 반영 전으로 본다 (아난티 레이스 방지).
    """
    cd = normStkCd(cd)
    if not cd:
        return False
    for wait in _ordWait.values():
        if wait.get("cd") != cd:
            continue
        if side and wait.get("side") != side:
            continue
        if int(wait.get("gotQty") or 0) <= 0:
            return True
    return False


def _registerOrdWait(ordNo, wait):
    key = _normOrdNo(ordNo) or (
        f"{wait.get('side')}:{wait.get('cd')}:{int(time.time() * 1000)}"
    )
    wait["at"] = time.time()
    _ordWait[key] = wait
    return key


def _pruneGoneWaits():
    """만료 매도 대기는 당일·4시간만 유지. 다음날 오매칭 방지."""
    now = time.time()
    today = _todayYmd()
    for key, wait in list(_ordWaitGone.items()):
        oldDay = str(wait.get("tradeDt") or "")[:8]
        age = now - float(wait.get("at") or 0)
        if (oldDay and oldDay != today) or age > 4 * 3600:
            _ordWaitGone.pop(key, None)


def _stashGoneSellWait(key, wait):
    """손절이 막히지 않게 산 대기는 지우고, 늦은 00 매칭용으로만 남긴다."""
    if not wait or str(wait.get("side") or "") != "SELL":
        return
    snap = wait
    snap["tradeDt"] = str(wait.get("tradeDt") or _todayYmd())
    _ordWaitGone[key] = snap
    cd = wait.get("cd")
    if cd:
        _ordWaitGone[f"SELL:{cd}"] = snap


def _dropExpiredWaits():
    """
    접수만 되고 체결이 안 온 대기. 시장가는 60초면 풀어 손절이 영구 봉인되지 않게 한다.
    지정가(3분 대기)는 타임아웃 처리기가 맡기므로 여기서 지우지 않는다.
    일부 체결(gotQty>0)은 00/계좌가 마칠 때까지 유지.
    매도는 만료해도 _ordWaitGone 에 남겨 늦은 체결을 버리지 않는다.
    """
    _pruneGoneWaits()
    now = time.time()
    for key, wait in list(_ordWait.items()):
        if int(wait.get("gotQty") or 0) > 0:
            continue
        # 지정가 매수는 10분 체결 대기가 있다. 60초 만료로 지우면 안 된다
        if str(wait.get("trdeTp") or "") == _TRDE_TP_LIMIT:
            continue
        if now - float(wait.get("at") or 0) < 60.0:
            continue
        _ordWait.pop(key, None)
        _stashGoneSellWait(key, wait)
        termLog(
            f"[maTouch] 주문대기 만료 {wait.get('cd')} {wait.get('side')} "
            f"{wait.get('expectQty')}주"
        )


def _findStoreWait(store, ordNo, cd, side):
    """한 대기 맵에서 주문번호·종목·방향 순으로 찾는다."""
    key = _normOrdNo(ordNo)
    if key and key in store:
        wait = store[key]
        if wait.get("cd") == cd and (not side or wait.get("side") == side):
            return key, wait
    for wKey, wait in store.items():
        if wait.get("cd") != cd or wait.get("side") != side:
            continue
        if int(wait.get("gotQty") or 0) < int(wait.get("expectQty") or 0):
            return wKey, wait
    return "", None


def _findOrdWait(ordNo, cd, side):
    """
    00 체결을 대기 주문에 매칭.
    산 대기 → 만료 매도 대기. 주문번호가 있으면 새 매도 대기보다 옛 만료를 우선한다.
    """
    key = _normOrdNo(ordNo)
    if key and key in _ordWait:
        wait = _ordWait[key]
        if wait.get("cd") == cd and (not side or wait.get("side") == side):
            return key, wait
    # 늦은 00 는 주문번호로 만료 매도를 먼저 본다 (이후 손절 대기와 섞이지 않게)
    if key and key in _ordWaitGone:
        wait = _ordWaitGone[key]
        if wait.get("cd") == cd and (not side or wait.get("side") == side):
            return key, wait
    wKey, wait = _findStoreWait(_ordWait, "", cd, side)
    if wait:
        return wKey, wait
    if side == "SELL":
        return _findStoreWait(_ordWaitGone, "", cd, side)
    return "", None


def _forgetWait(wait):
    """같은 대기 객체를 산/만료 맵에서 모두 뺀다."""
    if not wait:
        return
    for store in (_ordWait, _ordWaitGone):
        for k, w in list(store.items()):
            if w is wait:
                store.pop(k, None)


def _dropOrdWait(key):
    wait = _ordWait.get(key) or _ordWaitGone.get(key)
    if wait:
        _forgetWait(wait)
        return
    _ordWait.pop(key, None)
    _ordWaitGone.pop(key, None)


def _patchLedgerPos(stkCd, rec):
    """평단·잔량·매차만 원장에 반영. 매수 슬롯은 추가하지 않는다."""
    try:
        from utils.stratOrdLedgerStore import patchOpenLedger

        patchOpenLedger(_STRAT_ID, stkCd, rec)
    except Exception as err:
        termLog(f"[maTouch] 원장 평단동기 실패: {err}")


def listOpenPosAlgMap():
    """메모리 미청산 {stkCd: algCd}. 원장 적재 전 행번호 색용."""
    out = {}
    with _lock:
        for cd, rec in _pos.items():
            ncd = normStkCd(cd)
            if not ncd or int((rec or {}).get("qty") or 0) <= 0:
                continue
            out[ncd] = str((rec or {}).get("algCd") or _ALG_CD).strip() or _ALG_CD
    return out


def _ensureOpenLedger(stkCd, rec, px, qty, step):
    """
    미청산 원장이 없으면 INSERT, 있으면 평단만 패치.
    00 체결이 주문대기 등록보다 먼저 오면 upsert 가 빠져
    당일 신규가 원장·행번호 색에서 빠진다.
    """
    cd = normStkCd(stkCd)
    rec = rec or {}
    try:
        from utils.stratOrdLedgerStore import getOpenLedgerRow

        if getOpenLedgerRow(_STRAT_ID, cd):
            _patchLedgerPos(cd, rec)
            return
    except Exception as err:
        termLog(f"[maTouch] 원장 존재확인 실패: {err}")
    seed = dict(rec)
    useQty = int(seed.get("qty") or qty or 0)
    usePx = float(seed.get("avgPx") or px or 0)
    if useQty <= 0 or usePx <= 0:
        return
    seed["qty"] = useQty
    if int(seed.get("buyQty") or 0) <= 0:
        seed["buyQty"] = useQty
    if float(seed.get("buyAmt") or 0) <= 0:
        seed["buyAmt"] = usePx * useQty
    seed["avgPx"] = usePx
    seed.setdefault("algCd", _ALG_CD)
    _syncLedgerBuy(cd, usePx, useQty, step, seed)


def _holdMapFromAcnt(body):
    """kt00004 종목별 잔량·평단."""
    out = {}
    rawList = (body or {}).get("stk_acnt_evlt_prst") or []
    if not isinstance(rawList, list):
        return out
    for item in rawList:
        if not isinstance(item, dict):
            continue
        cd = normStkCd(item.get("stk_cd"))
        qty = parsePadNum(item.get("rmnd_qty")) or 0
        avgPx = _absPrice(item.get("avg_prc")) or 0
        if not cd or qty <= 0 or avgPx <= 0:
            continue
        out[cd] = {"qty": int(qty), "avgPx": float(avgPx)}
    return out


def _syncLedgerBuy(stkCd, px, qty, step, rec):
    """매수 후 원장 INSERT/UPDATE. 당일 청산 재매수는 새 거래코드."""
    try:
        from utils.stratOrdLedgerStore import upsertLedgerBuy

        tradeCd = upsertLedgerBuy(
            _STRAT_ID,
            stkCd,
            rec.get("stkNm") or _lookupStkNm(stkCd),
            px,
            qty,
            step,
            rec,
            mode=_tradeMode(),
        )
        if tradeCd:
            rec["tradeCd"] = tradeCd
    except Exception as err:
        termLog(f"[maTouch] 원장 매수 적재 실패: {err}")


def _syncLedgerSell(stkCd, px, qty, reason, rec, closed):
    """매도 후 원장 갱신. 익절/손절 슬롯을 나눈다."""
    sellKind = "TP"
    if reason.startswith("손절") or "본전" in reason:
        sellKind = "SL"
    try:
        from utils.stratOrdLedgerStore import upsertLedgerSell

        upsertLedgerSell(
            _STRAT_ID,
            stkCd,
            px,
            qty,
            sellKind,
            rec,
            closed,
            mode=_tradeMode(),
        )
    except Exception as err:
        termLog(f"[maTouch] 원장 매도 갱신 실패: {err}")


def _saveState():
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tradeDt": _todayYmd(),
            "smaYmd": _smaYmd,
            "pos": _pos,
            "waitConfirm": _waitConfirm,
            "buyBanYmd": _buyBanYmd,
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as err:
        termLog(f"[maTouch] 상태 저장 실패: {err}")


def _loadBuyBanFromFile(today):
    """당일 재매수 금지 목록 복구. 날짜가 지난 항목은 버린다."""
    global _buyBanYmd
    _buyBanYmd = {}
    try:
        if not _STATE_PATH.is_file():
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        raw = data.get("buyBanYmd") or {}
        if not isinstance(raw, dict):
            return
        out = {}
        for cd, ymd in raw.items():
            ncd = normStkCd(cd)
            day = str(ymd or "")
            if ncd and day == today:
                out[ncd] = day
        _buyBanYmd = out
        if out:
            termLog(f"[maTouch] 당일재매수금지 {len(out)}종 복구")
    except Exception as err:
        termLog(f"[maTouch] 재매수금지 로드 실패: {err}")


def _banBuyToday(stkCd):
    """전량 청산 후 당일 재매수 금지."""
    global _buyBanYmd
    p = _refreshParams()
    if not bool(p.get("banRebuySameDay", True)):
        return
    cd = normStkCd(stkCd)
    if not cd:
        return
    today = _todayYmd()
    with _lock:
        _buyBanYmd[cd] = today
        _disarmConfirm(cd)
        _saveState()
    termLog(f"[maTouch] 당일재매수금지 {cd}")


def _isBuyBanned(stkCd):
    """당일 전량 청산으로 재매수가 막혔는지."""
    p = _refreshParams()
    if not bool(p.get("banRebuySameDay", True)):
        return False
    cd = normStkCd(stkCd)
    return bool(cd) and _buyBanYmd.get(cd) == _todayYmd()


def _loadWaitConfirmFromFile(today):
    """당일 20선 회복 대기만 상태파일에서 복구. 날짜가 바뀌면 버린다."""
    global _waitConfirm, _waitYmd
    _waitConfirm = {}
    _waitYmd = today
    try:
        if not _STATE_PATH.is_file():
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        if str(data.get("tradeDt") or "") != today:
            return
        raw = data.get("waitConfirm") or {}
        if not isinstance(raw, dict):
            return
        out = {}
        for cd, steps in raw.items():
            ncd = normStkCd(cd)
            if not ncd:
                continue
            got = {}
            if not isinstance(steps, dict):
                continue
            for k, v in steps.items():
                try:
                    step = int(k)
                except (TypeError, ValueError):
                    continue
                if isinstance(v, dict):
                    rec = {
                        "maPx": float(v.get("maPx") or 0),
                        "touchTm": str(v.get("touchTm") or ""),
                        "phase": str(v.get("phase") or "touch"),
                    }
                    if v.get("brokeBucket"):
                        rec["brokeBucket"] = str(v.get("brokeBucket"))
                    got[step] = rec
                elif v:
                    got[step] = {"maPx": 0.0, "touchTm": "", "phase": "touch"}
            if got:
                out[ncd] = got
        _waitConfirm = out
        if out:
            termLog(f"[maTouch] 20선회복 대기 {len(out)}종 복구")
    except Exception as err:
        termLog(f"[maTouch] 20선회복 대기 로드 실패: {err}")


def _buyCutoffOn(ymd=None):
    """다음날 휴장(주말·공휴일)일 때만 14:00 매수금지를 켠다."""
    return buyCutoffApplies(ymd or _todayYmd(), _params or {})


def _armConfirm(cd, step, maPx, touchTm):
    """터치됨 — 3분봉 양봉 돌파(또는 회복)를 기다린다. 재터치면 대기 리셋."""
    global _waitConfirm, _waitYmd
    today = _todayYmd()
    if _waitYmd != today:
        _waitConfirm = {}
        _waitYmd = today
    _waitConfirm.setdefault(cd, {})[int(step)] = {
        "maPx": float(maPx or 0),
        "touchTm": str(touchTm or ""),
        "phase": "touch",
    }
    _saveState()


def _feedBar3(cd, px, nowTm):
    """틱을 3분봉에 넣고, 버킷이 바뀌면 직전 완성 봉을 반환."""
    global _bar3Map
    today = _todayYmd()
    bkt = bar3BucketTm(nowTm)
    rec = _bar3Map.get(cd)
    if rec is None or rec.get("ymd") != today:
        _bar3Map[cd] = {
            "bucket": bkt,
            "ymd": today,
            "o": px,
            "h": px,
            "l": px,
            "c": px,
        }
        return None
    if rec["bucket"] != bkt:
        closed = {
            "bucket": rec["bucket"],
            "open": rec["o"],
            "high": rec["h"],
            "low": rec["l"],
            "close": rec["c"],
        }
        _bar3Map[cd] = {
            "bucket": bkt,
            "ymd": today,
            "o": px,
            "h": px,
            "l": px,
            "c": px,
        }
        return closed
    rec["h"] = max(rec["h"], px)
    rec["l"] = min(rec["l"], px)
    rec["c"] = px
    return None


def _tryLiveBar3Buy(cd, closed3, filled, queuedBuy, sma, openBelowSma, actions, skips):
    """완성된 3분봉이 양봉 돌파면 SMA20 인근 호가 지정가."""
    if not closed3:
        return
    useLimit = confirmModeIsBar3Limit(_params or {})
    closePx = bar3ClosePx(closed3)
    for step in list((_waitConfirm.get(cd) or {}).keys()):
        step = int(step)
        if step in filled or step in queuedBuy:
            continue
        if _hasOrdWait(cd, "BUY"):
            continue
        if openBelowSma:
            skips.append((step, "OPEN_BELOW_SMA20"))
            _disarmConfirm(cd, step)
            continue
        pend = (_waitConfirm.get(cd) or {}).get(step)
        if not isinstance(pend, dict):
            continue
        maPx = float(pend.get("maPx") or 0)
        if maPx <= 0 and sma.get(step) is not None:
            maPx = float(sma.get(step))
            pend["maPx"] = maPx
        if maPx <= 0:
            continue
        act = applyBar3Confirm(pend, closed3, maPx)
        _saveState()
        if act != "buy":
            continue
        if useLimit:
            limitPx = float(roundToKrxTick(maPx))
            if limitPx <= 0:
                continue
            qty = int(buyAmtForStep(_params, step) // limitPx)
            if qty < 1:
                skips.append((step, "QTY0"))
                _disarmConfirm(cd, step)
                continue
            # fillPx=지정가. 워커가 보통(지정가) 주문으로 낸다
            actions.append(("BUY", step, qty, limitPx, maPx))
            queuedBuy.add(step)
            _disarmConfirm(cd, step)
            continue
        if not closePx:
            continue
        qty = int(buyAmtForStep(_params, step) // closePx)
        if qty < 1:
            skips.append((step, "QTY0"))
            _disarmConfirm(cd, step)
            continue
        # 5번째 인자 없음 → 워커가 시장가로 낸다
        actions.append(("BUY", step, qty, closePx))
        queuedBuy.add(step)
        _disarmConfirm(cd, step)


def _disarmConfirm(cd, step=None):
    """확인 대기 해제. step 없으면 그 종목 전부."""
    global _waitConfirm
    wa = _waitConfirm.get(cd)
    if not wa:
        return
    if step is None:
        _waitConfirm.pop(cd, None)
        _saveState()
        return
    wa.pop(int(step), None)
    if not wa:
        _waitConfirm.pop(cd, None)
    _saveState()


def _loadBrokeAboveTakeMaFromFile():
    """상태파일 pos 에서 10이평 돌파·익절단계 플래그를 복구 (원장 로드 후 병합)."""
    try:
        if not _STATE_PATH.is_file():
            return {}
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        raw = (data or {}).get("pos") or {}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for cd, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            ncd = normStkCd(cd)
            if not ncd:
                continue
            flags = {}
            if bool(rec.get("brokeAboveTakeMa")):
                flags["brokeAboveTakeMa"] = True
            for key in ("tp1Taken", "tp2Taken", "tp3Taken", "halfTaken"):
                if rec.get(key):
                    flags[key] = True
            if flags:
                out[ncd] = flags
        return out
    except Exception:
        return {}


def _loadState():
    """미청산 원장을 우선 로드. 파일은 백업용."""
    global _pos
    today = _todayYmd()
    loaded = {}
    try:
        from utils.stratOrdLedgerStore import listOpenLedgerRows, rowToLivePos

        for row in listOpenLedgerRows(stratId=_STRAT_ID):
            rec = rowToLivePos(row, todayYmd=today)
            if not rec:
                continue
            cd = normStkCd(row.get("STK_CD"))
            if not cd or int(rec.get("qty") or 0) <= 0:
                continue
            rec["filled"] = normalizeFilledSteps(
                rec.get("filled") or [], (_params or {}).get("maSteps")
            )
            rec.setdefault("brokeAboveTakeMa", False)
            loaded[cd] = rec
    except Exception as err:
        termLog(f"[maTouch] 원장 로드 실패: {err}")
    if loaded:
        # 원장에 없는 당일 신규는 상태파일에서 보탠다.
        # (원장이 1종이라도 있으면 예전에는 상태파일을 버려 당일 매수가 사라짐)
        loaded = _mergeTodayStatePos(loaded, today)
        # 원장에는 없는 10이평 돌파·익절단계 플래그를 상태파일에서 보강
        flags = _loadBrokeAboveTakeMaFromFile()
        for cd, on in flags.items():
            if cd not in loaded:
                continue
            if on is True:
                loaded[cd]["brokeAboveTakeMa"] = True
                continue
            if not isinstance(on, dict):
                continue
            for key, val in on.items():
                if val:
                    loaded[cd][key] = True
        _mergePendSellFromFile(loaded)
        _pos = loaded
        termLog(f"[maTouch] 원장 미청산 {len(_pos)}종 로드")
        _loadWaitConfirmFromFile(today)
        _loadBuyBanFromFile(today)
        return
    # 원장이 없을 때만 예전 상태파일로 복구
    try:
        if not _STATE_PATH.is_file():
            _loadBuyBanFromFile(today)
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        savedDt = str(data.get("tradeDt") or "")
        raw = data.get("pos") or {}
        if not isinstance(raw, dict):
            return
        _pos = {}
        for cd, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            item = dict(rec)
            item["filled"] = normalizeFilledSteps(
                item.get("filled") or [], (_params or {}).get("maSteps")
            )
            item.setdefault("brokeAboveTakeMa", False)
            _pos[normStkCd(cd)] = item
        _loadWaitConfirmFromFile(today)
        _loadBuyBanFromFile(today)
    except Exception as err:
        termLog(f"[maTouch] 상태 로드 실패: {err}")


def _mergeTodayStatePos(loaded, today):
    """
    원장에 없는 당일 신규매수를 상태파일에서 합친다.
    전일 청산분이 상태파일에 남아도 entryDt!=오늘이면 넣지 않는다.
    """
    loaded = dict(loaded or {})
    extra = 0
    try:
        if not _STATE_PATH.is_file():
            return loaded
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        raw = (data or {}).get("pos") or {}
        if not isinstance(raw, dict):
            return loaded
        for cd, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            ncd = normStkCd(cd)
            if not ncd or ncd in loaded:
                continue
            if int(rec.get("qty") or 0) <= 0:
                continue
            ent = str(rec.get("entryDt") or rec.get("tradeDt") or "")[:8]
            if ent != today:
                continue
            item = dict(rec)
            item["filled"] = normalizeFilledSteps(
                item.get("filled") or [], (_params or {}).get("maSteps")
            )
            item.setdefault("brokeAboveTakeMa", False)
            item.setdefault("algCd", _ALG_CD)
            loaded[ncd] = item
            extra += 1
        if extra:
            termLog(f"[maTouch] 상태파일 당일신규 {extra}종 원장과 병합")
    except Exception as err:
        termLog(f"[maTouch] 당일신규 병합 실패: {err}")
    return loaded


def _restoreWaitFromToday():
    """
    오늘 이미 터치했는데 3분봉 확인(또는 회복)이 안 된 매차를 대기로 복구.
    WAIT_SMA20 / WAIT_BAR3 / WAIT_BULL 모두 대기로 본다.
    """
    global _waitConfirm, _waitYmd
    today = _todayYmd()
    if _waitYmd != today:
        _waitConfirm = {}
        _waitYmd = today
    filled = set()
    added = 0
    waitRsns = ("WAIT_SMA20", "WAIT_BAR3", "WAIT_BULL")
    try:
        from utils.algEvtStore import listAlgEvt

        for row in listAlgEvt(_ALG_CD, today, limit=500) or []:
            cd = normStkCd(row.get("STK_CD") or row.get("stkCd"))
            if not cd:
                continue
            tp = str(row.get("EVT_TP") or row.get("evtTp") or "").upper()
            reason = str(row.get("REASON") or row.get("reason") or "")
            note = str(row.get("NOTE") or row.get("note") or "")
            step = None
            for text in (reason, note):
                t = str(text).upper()
                if t.startswith("SMA") and t[3:].isdigit():
                    step = int(t[3:])
                    mapped = normalizeFilledSteps([step])
                    if mapped:
                        step = mapped[0]
                    break
            if tp == "BUY" and step:
                filled.add((cd, step))
                wa = _waitConfirm.get(cd)
                if wa:
                    wa.pop(step, None)
                    if not wa:
                        _waitConfirm.pop(cd, None)
            elif tp == "BUY_SKIP" and reason in waitRsns and step:
                if (cd, step) in filled:
                    continue
                if step not in (_waitConfirm.get(cd) or {}):
                    _waitConfirm.setdefault(cd, {})[step] = {
                        "maPx": 0.0,
                        "touchTm": str(row.get("EVT_TM") or row.get("evtTm") or ""),
                        "phase": "broke" if reason == "WAIT_BULL" else "touch",
                    }
                    added += 1
    except Exception as err:
        termLog(f"[maTouch] 당일 20선회복 대기 복구 실패: {err}")
        return
    try:
        from utils.algTouchSumStore import listTouchSum

        for row in listTouchSum(today, algCd=_ALG_CD) or []:
            if str(row.get("BUY_YN") or "N") == "Y":
                continue
            rsn = str(row.get("LAST_SKIP_RSN") or "")
            if rsn not in waitRsns:
                continue
            cd = normStkCd(row.get("STK_CD"))
            step = int(row.get("MA_STEP") or 0)
            mapped = normalizeFilledSteps([step]) if step else []
            if mapped:
                step = mapped[0]
            if not cd or not step or (cd, step) in filled:
                continue
            if step not in (_waitConfirm.get(cd) or {}):
                maPx = 0.0
                try:
                    maPx = float(row.get("MA_PX") or 0)
                except (TypeError, ValueError):
                    maPx = 0.0
                _waitConfirm.setdefault(cd, {})[step] = {
                    "maPx": maPx,
                    "touchTm": "",
                    "phase": "broke" if rsn == "WAIT_BULL" else "touch",
                }
                added += 1
    except Exception:
        pass
    if added:
        _saveState()
        termLog(f"[maTouch] 당일 20선회복 대기 복구 {added}매차")


def _mergePendSellFromFile(loaded):
    """원장 로드 후 상태파일의 미체결 매도 힌트를 보탠다."""
    try:
        if not _STATE_PATH.is_file():
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        raw = (data or {}).get("pos") or {}
        if not isinstance(raw, dict):
            return
        keys = (
            "pendSellReason",
            "pendSellQty",
            "pendSellQtyBefore",
            "pendSellOrdNo",
        )
        for cd, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            ncd = normStkCd(cd)
            if ncd not in loaded:
                continue
            for key in keys:
                val = rec.get(key)
                if val not in (None, ""):
                    loaded[ncd][key] = val
    except Exception as err:
        termLog(f"[maTouch] 매도대기힌트 로드 실패: {err}")


def _restoreGoneSellFromToday():
    """
    오늘 매도주문은 있는데 매도(체결)가 부족한 종목을 만료대기처럼 복원.
    재기동 후에도 늦은 00 가 거래탭·원장에 붙게 한다.
    """
    today = _todayYmd()
    try:
        from utils.algEvtStore import listAlgEvt

        rows = listAlgEvt(_ALG_CD, today, limit=2000) or []
    except Exception as err:
        termLog(f"[maTouch] 매도체결 대기복원 실패: {err}")
        return
    ords = {}
    sold = {}
    for row in rows:
        cd = normStkCd(row.get("STK_CD") or row.get("stkCd"))
        if not cd:
            continue
        tp = str(row.get("EVT_TP") or row.get("evtTp") or "").upper()
        try:
            qty = int(float(row.get("QTY") or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        if tp == "SELL_ORD":
            ords.setdefault(cd, []).append(
                {
                    "qty": qty,
                    "reason": str(row.get("REASON") or row.get("reason") or "매도체결"),
                }
            )
        elif tp == "SELL":
            sold[cd] = sold.get(cd, 0) + qty
    added = 0
    with _lock:
        for cd, items in ords.items():
            rec = _pos.get(cd)
            if not rec:
                continue
            leftSold = int(sold.get(cd) or 0)
            for item in items:
                ordQty = int(item.get("qty") or 0)
                if leftSold >= ordQty:
                    leftSold -= ordQty
                    continue
                unfilled = ordQty - leftSold
                leftSold = 0
                if unfilled <= 0:
                    continue
                goneKey = f"SELL:{cd}"
                if goneKey in _ordWaitGone or _hasOrdWait(cd, "SELL"):
                    continue
                posQty = int(rec.get("qty") or 0)
                buyQty = int(rec.get("buyQty") or 0)
                sellQty = int(rec.get("sellQty") or 0)
                qtyBefore = int(rec.get("pendSellQtyBefore") or 0)
                # 매도 이벤트 없이 잔량만 줄었으면 주문 전 수량은 매수수량
                if qtyBefore <= 0:
                    if sellQty <= 0 and buyQty > posQty > 0:
                        qtyBefore = buyQty
                    else:
                        qtyBefore = posQty
                wait = {
                    "cd": cd,
                    "side": "SELL",
                    "kind": "SELL",
                    "step": 0,
                    "expectQty": unfilled,
                    "gotQty": 0,
                    "qtyBefore": qtyBefore,
                    "reason": item.get("reason") or "매도체결",
                    "ledgerBuy": False,
                    "tradeDt": today,
                    "at": time.time(),
                    "restored": True,
                }
                _ordWaitGone[goneKey] = wait
                added += 1
                termLog(
                    f"[maTouch] 매도체결 대기복원 {cd} {unfilled}주 · "
                    f"{wait.get('reason')}"
                )
    if added:
        termLog(f"[maTouch] 미체결 매도주문 {added}건 대기복원")


def _smaReady(cd):
    """매차·익절·손절 이평이 모두 있으면 재조회하지 않는다."""
    smap = _smaMap.get(cd) or {}
    p = _params or {}
    needPs = list(p.get("maSteps") or [20])
    needPs.append(int(p.get("takeMa", 10)))
    needPs.append(int(p.get("stopMa", 20)))
    # 매매창 관심종목 그리드(5·10·20일) 표시용
    needPs.extend([5, 10])
    for per in needPs:
        if smap.get(int(per)) is None:
            return False
    return True


def invalidateSmaCache(reason=""):
    """
    일봉이 정정되면 캐시된 SMA 를 버린다.
    SMA 는 하루 한 번만 계산하고 재사용하므로, 장중값으로 굳은 일봉을
    나중에 실제 종가로 고쳐도 캐시가 남아 있으면 그대로 틀린 값으로 매매한다.
    """
    global _smaMap, _smaYmd, _openMap, _prevCloseMap
    hadCnt = len(_smaMap or {})
    _smaMap = {}
    _openMap = {}
    _prevCloseMap = {}
    _smaYmd = None
    termLog(
        f"[maTouch] SMA 캐시 무효화 {hadCnt}종 — 다음 준비 시 재계산"
        + (f" ({reason})" if reason else "")
    )
    return hadCnt


def _loadSmaForCodes(codes):
    """전일까지 확정 일봉으로 SMA 계산. 당일 시가·전일 종가도 챙긴다."""
    global _smaMap, _smaYmd, _openMap, _prevCloseMap
    today = _todayYmd()
    # 장중 15분 편입·이평 미완성 종목만 보강
    if _smaYmd == today and _smaMap:
        miss = [
            normStkCd(c)
            for c in (codes or [])
            if normStkCd(c) and not _smaReady(normStkCd(c))
        ]
        if not miss:
            return
        codes = miss
    p = _params or _loadParams()
    periods = sorted(
        set(
            list(p.get("maSteps") or [])
            + [
                p.get("stopMa"),
                p.get("takeMa"),
                5,
                10,
            ]
        )
    )
    need = max(int(x) for x in periods if x)
    # 신규 편입은 DB에 일봉이 없을 수 있어 SMA 전에 한 종목씩 조회
    try:
        from utils.dayPrcEnsureSma import ensureDayPrcForSma

        ensureDayPrcForSma(codes, minBars=max(need, 20), endDt=today)
    except Exception as err:
        termLog(f"[maTouch] 일봉 보강 실패: {err}")
    out = {}
    openMap = {}
    prevCloseMap = {}
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            for cd in codes:
                cur.execute(
                    """
                    SELECT CLOSE_PRC
                    FROM AI_DAY_STK_PRC
                    WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT < %s
                    ORDER BY TRADE_DT DESC
                    LIMIT %s
                    """,
                    (cd, today, need),
                )
                rows = cur.fetchall() or []
                closes = [
                    float(r["CLOSE_PRC"])
                    for r in reversed(rows)
                    if r.get("CLOSE_PRC") is not None
                ]
                smap = {}
                for per in periods:
                    per = int(per)
                    if len(closes) < per:
                        smap[per] = None
                    else:
                        smap[per] = sum(closes[-per:]) / float(per)
                out[cd] = smap
                if closes:
                    prevCloseMap[cd] = closes[-1]
                cur.execute(
                    """
                    SELECT OPEN_PRC
                    FROM AI_DAY_STK_PRC
                    WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT=%s
                    LIMIT 1
                    """,
                    (cd, today),
                )
                todayRow = cur.fetchone()
                if todayRow and todayRow.get("OPEN_PRC") is not None:
                    openMap[cd] = abs(float(todayRow["OPEN_PRC"]))
    finally:
        conn.close()
    if _smaYmd == today and _smaMap:
        _smaMap.update(out)
        _openMap.update(openMap)
        _prevCloseMap.update(prevCloseMap)
    else:
        _smaMap = out
        _openMap = openMap
        _prevCloseMap = prevCloseMap
        _smaYmd = today
    termLog(f"[maTouch] 일봉 SMA 준비 {len(_smaMap)}종 · 기준 전일까지")


def _waitOrdGap():
    """모의 주문 간격."""
    global _lastOrdAt
    now = time.time()
    gap = _PAPER_WAIT_SEC - (now - _lastOrdAt)
    if gap > 0:
        time.sleep(gap)
    _lastOrdAt = time.time()


def holdsOrPendingBuy(stkCd):
    """다른 전략 가드용 — 이평터치 잔량·매차·매수주문대기 여부."""
    cd = normStkCd(stkCd)
    if not cd:
        return False
    with _lock:
        _dropExpiredWaits()
        rec = _pos.get(cd) or {}
        if int(rec.get("qty") or 0) > 0:
            return True
        if list(rec.get("filled") or []):
            return True
        for wait in _ordWait.values():
            if wait.get("cd") == cd and wait.get("side") == "BUY":
                return True
    return False


def _placeBuy(stkCd, qty, px, step, isAdd=False, isRemain=False, maPx=None):
    """
    매수 주문. 신규 3분확인은 SMA20 인근 호가 지정가.
    isRemain 이면 예전 잔량 시장가 경로(v46은 타임아웃에서 쓰지 않음).
    """
    if (not isAdd) and (not isRemain) and _isBuyBanned(stkCd):
        _notify(f"[이평터치] 당일재매수금지 — {stkCd} SMA{step} 매수 생략")
        _writeAlgEvt("BUY_SKIP", stkCd, reason="REBUY_BANNED", px=px, note=f"SMA{step}")
        return False
    try:
        from trading.crossAlgBuyGuard import isNewBuyBlocked, logBuySkip, skipReasonLabel

        blocked, why = (False, "")
        if (not isAdd) and (not isRemain):
            blocked, why = isNewBuyBlocked(_ALG_CD, stkCd)
        if blocked:
            logBuySkip("이평터치", stkCd, why)
            _notify(
                f"[이평터치] 복수전략스킵 — {stkCd} SMA{step} "
                f"{skipReasonLabel(why)}"
            )
            _writeAlgEvt(
                "BUY_SKIP",
                stkCd,
                reason="CROSS_ALG_HOLD",
                px=px,
                note=f"SMA{step}|{why}",
            )
            return False
    except Exception as err:
        termLog(f"[maTouch] 복수전략가드 오류: {err}")
    sess = _ordSession()
    if sess is None:
        _notify(f"[이평터치] {modeLabel()} 미로그인/불가 — {stkCd} {step}일선 매수 생략")
        return False
    useLimit = (
        (not isAdd)
        and (not isRemain)
        and confirmModeIsBar3Limit(_params or {})
        and maPx is not None
    )
    trdeTp = _TRDE_TP_LIMIT if useLimit else _TRDE_TP_MARKET
    ordUv = str(int(px)) if useLimit else ""
    waitSec = float((_params or {}).get("limitWaitSec") or _LIMIT_WAIT_SEC)
    smaRef = float(maPx) if maPx not in (None, "") else float(px or 0)
    hostUrl, token, tokenType = sess
    _waitOrdGap()
    result = placeBuyOrd(
        hostUrl,
        token,
        _ordExch(stkCd),
        stkCd,
        str(qty),
        trdeTp,
        ordUv=ordUv,
        tokenType=tokenType,
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
                        trdeTp,
                        ordUv=ordUv,
                        tokenType=tokenType,
                    )
                    if result.get("ok"):
                        setTokenAlive(getDeskTradeMode(), True)
                        termLog(
                            f"[이평터치] 토큰재발급 후 매수 재시도 성공 {stkCd}"
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
        _notify(f"[이평터치매수실패] {stkCd} SMA{step} {qty}주: {errMsg}")
        _writeAlgEvt(
            "BUY_FAIL",
            stkCd,
            reason=f"SMA{step}",
            px=px,
            qty=qty,
            note=errMsg[:200],
        )
        return False
    # 평단·수량은 00 체결·kt00004 에서만 올린다.
    # 주문 접수만으로 원장/BUY 이벤트/보유수량을 만들지 않는다 (미체결 허수 포지션 방지).
    with _lock:
        rec = _pos.get(stkCd) or _blankPos()
        # 재주문 방지용 매차 표시만. qty 는 체결 전까지 0 유지
        if int(rec.get("qty") or 0) <= 0:
            rec["qty"] = 0
        filled = list(rec.get("filled") or [])
        if step not in filled:
            filled.append(step)
        rec["filled"] = filled
        rec["lastPx"] = px
        rec["tradeDt"] = _todayYmd()
        if isAdd:
            rec["halfAddDone"] = True
        if not rec.get("stkNm"):
            rec["stkNm"] = _lookupStkNm(stkCd)
        _pos[stkCd] = rec
        wait = {
            "cd": stkCd,
            "side": "BUY",
            "kind": "BUY",
            "step": int(step),
            "expectQty": int(qty),
            "gotQty": 0,
            "qtyBefore": int(rec.get("qty") or 0),
            "ledgerBuy": bool(isRemain or rec.get("tradeCd")),
            "trdeTp": trdeTp,
            "origOrdNo": str(result.get("ordNo") or ""),
            "maPx": smaRef,
            "reason": f"SMA{step}",
            "ordNote": (
                "지정가"
                if useLimit
                else ("시장가잔량" if isRemain else "")
            ),
        }
        if useLimit:
            wait["limitUntil"] = time.time() + max(1.0, waitSec)
        _registerOrdWait(result.get("ordNo"), wait)
        _saveState()
    wasWait = bool((_waitConfirm.get(stkCd) or {}).get(int(step)))
    _disarmConfirm(stkCd, step)
    how = "지정가" if useLimit else "시장가"
    extra = f" · SMA20 {smaRef:,.0f} 호가" if useLimit else ""
    if isRemain:
        extra = " · 지정가 3분미체결 잔량"
    _notify(
        f"[이평터치매수주문] ({modeLabel()}) {stkCd} SMA{step} {qty}주 {how} ≈{px:,.0f}원{extra}\n"
        f"체결 후에만 보유·원장 반영 · 주문번호 {result.get('ordNo') or '-'}"
    )
    # 복기용 주문접수(거래탭 BUY 는 체결 시만)
    note = wait.get("ordNote") or ""
    if wasWait and not note:
        note = "3분확인"
    _writeAlgEvt(
        "BUY_ORD",
        stkCd,
        reason=f"SMA{step}",
        px=px,
        qty=qty,
        note=(note or "주문접수")[:80],
    )
    return True


def _cnclOpenBuyLimits(stkCd):
    """매도 전에 미체결 지정가 매수 잔량을 취소한다. 전량 청산 후 지정가 체결을 막기 위함."""
    jobs = []
    with _lock:
        for wait in list(_ordWait.values()):
            if wait.get("cd") != stkCd or wait.get("side") != "BUY":
                continue
            leftover = int(wait.get("expectQty") or 0) - int(wait.get("gotQty") or 0)
            gotQty = int(wait.get("gotQty") or 0)
            orig = str(wait.get("origOrdNo") or "")
            if leftover > 0 and orig:
                jobs.append((orig, leftover, gotQty))
    if not jobs:
        return
    sess = _ordSession()
    if sess is None:
        return
    hostUrl, token, tokenType = sess
    for orig, leftover, gotQty in jobs:
        _waitOrdGap()
        result = placeCnclOrd(
            hostUrl,
            token,
            _ordExch(stkCd),
            orig,
            stkCd,
            cnclQty="0",
            tokenType=tokenType,
        )
        if result.get("ok"):
            termLog(f"[maTouch] 매수잔량취소 {stkCd} orig={orig} 잔량≈{leftover}")
            # 부분체결 후 잔량 취소만 BUY_CNCL (체결분 차감용).
            # 전량 미체결 취소는 BUY 이벤트가 없으므로 CNCL 을 남기지 않는다.
            if gotQty > 0:
                _writeAlgEvt(
                    "BUY_CNCL",
                    stkCd,
                    reason="SELL_BEFORE_CNCL",
                    px=float(_lastPx.get(stkCd) or 0),
                    qty=leftover,
                    note="매도 전 매수잔량 취소",
                )
        else:
            termLog(
                f"[maTouch] 매수잔량취소실패 {stkCd} orig={orig}: {result.get('errMsg')}"
            )


def _voidUnfilledLedger(stkCd, reason="LIMIT_UNFILL"):
    """
    전량 미체결 취소면 주문 시점에 만들어진 원장 행도 함께 정리한다.
    메모리 포지션만 지우면 원장이 미청산으로 남고, 나중에 계좌잔량0 청산으로
    닫히면서 매수가=매도가인 0원 유령 라운드가 된다.
    """
    try:
        from utils.stratOrdLedgerStore import voidOpenLedger

        voidOpenLedger(_STRAT_ID, stkCd, reason=reason)
    except Exception as err:
        termLog(f"[maTouch] 미체결 원장 정리 실패 {stkCd}: {err}")


def _unfillStepIfEmpty(stkCd, step):
    """지정가 전량 미체결이면 매차를 풀어 이후 재터치가 가능하게 한다."""
    with _lock:
        rec = _pos.get(stkCd)
        if not rec:
            return
        if int(rec.get("qty") or 0) > 0:
            return
        filled = [s for s in (rec.get("filled") or []) if int(s) != int(step)]
        rec["filled"] = filled
        if filled:
            _pos[stkCd] = rec
        else:
            # 체결 없는 빈 슬롯은 포지션으로 남기지 않는다
            _pos.pop(stkCd, None)
        _saveState()


def _enqueueLimitTimeouts():
    """지정가 10분이 지났으면 워커에 취소 작업을 넣는다."""
    now = time.time()
    jobs = []
    with _lock:
        for key, wait in list(_ordWait.items()):
            if wait.get("side") != "BUY":
                continue
            if str(wait.get("trdeTp") or "") != _TRDE_TP_LIMIT:
                continue
            if wait.get("limitToQueued"):
                continue
            until = float(wait.get("limitUntil") or 0)
            if until <= 0 or now < until:
                continue
            leftover = int(wait.get("expectQty") or 0) - int(wait.get("gotQty") or 0)
            if leftover <= 0:
                continue
            pendKey = (wait.get("cd"), "LIMIT_TO", int(wait.get("step") or 20))
            if pendKey in _pending:
                continue
            wait["limitToQueued"] = True
            _pending.add(pendKey)
            jobs.append(
                (
                    wait.get("cd"),
                    (
                        "LIMIT_TO",
                        int(wait.get("step") or 20),
                        leftover,
                        0.0,
                        key,
                    ),
                )
            )
        for item in jobs:
            _ordQ.append(item)



def _cnclErrNoCancelableQty(errMsg):
    """이미 체결·취소되어 취소할 잔량이 없는 증권사 응답."""
    msg = str(errMsg or "")
    if "취소가능수량이 없습니다" in msg:
        return True
    if "506550" in msg:
        return True
    return False


def _fetchAcntHoldMap():
    """kt00004 한 번 조회 → {stkCd: {qty, avgPx}}. 실패 시 {}."""
    try:
        from utils.tradeSession import getOrdSession, isModeLoggedIn

        if not isModeLoggedIn():
            return {}
        sess = getOrdSession(paperOnly=False)
        if sess is None:
            return {}
        hostUrl, token, tokenType = sess
        from api.getAcntEvlt import fetchAcntEvlt

        got = fetchAcntEvlt(
            hostUrl, token, qryTp="1", dmstStexTp="KRX", tokenType=tokenType
        )
        if not got.get("ok"):
            termLog(f"[maTouch] 체결재확인 계좌조회 실패: {got.get('errMsg')}")
            return {}
        return _holdMapFromAcnt(got.get("body") or got)
    except Exception as err:
        termLog(f"[maTouch] 체결재확인 계좌조회 예외: {err}")
        return {}


def _clearWaitConfirmCd(stkCd, step=None):
    """터치 대기 슬롯 제거 (체결 복구·미체결 정리)."""
    cd = normStkCd(stkCd)
    if not cd:
        return
    with _lock:
        wc = _waitConfirm.get(cd)
        if not wc:
            return
        if step is None:
            _waitConfirm.pop(cd, None)
        else:
            wc.pop(str(int(step)), None)
            wc.pop(int(step), None)
            if not wc:
                _waitConfirm.pop(cd, None)
            else:
                _waitConfirm[cd] = wc
        _saveState()


def _promoteBuyFromAcntHold(stkCd, step, wait, hold, note):
    """
    00 체결 누락분 — 계좌 잔량·평단으로 공식 매수 반영.
    BUY 이벤트·원장·포지션을 남기고 주문대기를 제거한다.
    """
    cd = normStkCd(stkCd)
    qty = int((hold or {}).get("qty") or 0)
    avgPx = float((hold or {}).get("avgPx") or 0)
    if not cd or qty <= 0 or avgPx <= 0:
        return False
    # 이미 보유 반영됐으면 BUY 중복 이벤트 방지 (재기동 동기 두 번 등)
    with _lock:
        cur = _pos.get(cd) or {}
        if int(cur.get("qty") or 0) > 0 and float(cur.get("avgPx") or 0) > 0:
            for k, w in list(_ordWait.items()):
                if w.get("cd") == cd and w.get("side") == "BUY":
                    _dropOrdWait(k)
            termLog(f"[maTouch] 매수복구 스킵(이미보유) {cd} {cur.get('qty')}주")
            return False
    step = int(step or (wait or {}).get("step") or 20)
    wait = dict(wait or {})
    qtyBefore = int(wait.get("qtyBefore") or 0)
    fillQty = max(0, qty - qtyBefore)
    if fillQty <= 0:
        fillQty = qty
    fillPx = avgPx
    closeJob = None
    buyJob = None
    with _lock:
        # 잔여 BUY 대기 전부 제거 (gotQty=0 고착 해제)
        for k, w in list(_ordWait.items()):
            if w.get("cd") == cd and w.get("side") == "BUY":
                _dropOrdWait(k)
        synWait = {
            "cd": cd,
            "side": "BUY",
            "kind": "BUY",
            "step": step,
            "expectQty": fillQty,
            "gotQty": 0,
            "qtyBefore": qtyBefore,
            "ledgerBuy": False,
            "reason": str(wait.get("reason") or f"SMA{step}"),
            "ordNote": str(note or "계좌재확인체결")[:80],
        }
        rec, firstSlot, step2 = _applyBuyFillLocked(cd, fillPx, fillQty, synWait)
        # 계좌 잔량이 곧 진실 — 평단·수량 덮어쓰기
        rec["avgPx"] = avgPx
        rec["qty"] = qty
        if int(rec.get("buyQty") or 0) < qty:
            rec["buyQty"] = qty
        filled = list(rec.get("filled") or [])
        if step not in filled and str(step) not in [str(x) for x in filled]:
            filled.append(step)
            rec["filled"] = filled
        if not rec.get("stkNm"):
            rec["stkNm"] = _lookupStkNm(cd)
        _pos[cd] = rec
        _saveState()
        buyJob = (dict(rec), firstSlot, step, fillPx, fillQty, synWait)
    if buyJob:
        rec, firstSlot, step, fillPx, fillQty, waitSnap = buyJob
        _ensureOpenLedger(cd, rec, avgPx, qty, step)
        reason = str((waitSnap or {}).get("reason") or f"SMA{step}")
        _writeAlgEvt(
            "BUY",
            cd,
            reason=reason,
            px=fillPx,
            qty=fillQty,
            note=str((waitSnap or {}).get("ordNote") or "계좌재확인체결")[:200],
        )
        try:
            from utils.algTouchSumStore import markTouchBuy

            markTouchBuy(
                _ALG_CD,
                _todayYmd(),
                cd,
                step,
                _nowHms(),
                fillPx,
                fillQty,
                stepSeq=len(list(rec.get("filled") or [])),
                stkNm=rec.get("stkNm") or "",
            )
        except Exception as err:
            termLog(f"[maTouch] 터치요약 매수표시 실패: {err}")
        _clearWaitConfirmCd(cd, step)
        _notify(
            f"[이평터치체결] {cd} 매수복구 {fillQty}주 {fillPx:,.0f}원\n"
            f"평단 {rec.get('avgPx', 0):,.0f} · 보유 {rec.get('qty', 0)}주 "
            f"(00누락·계좌재확인)"
        )
        termLog(
            f"[maTouch] 00누락 계좌복구 {cd} {fillQty}주 @{fillPx:,.0f} "
            f"계좌잔량 {qty}"
        )
        return True
    return False


def _reconcileLimitBuyAfterCnclFail(stkCd, step, waitKey, wait, maPx, leftover, errMsg):
    """
    지정가 10분 취소 실패 시 체결 여부 재확인.
    취소가능수량 없음 = 이미 체결·소멸 가능 → 계좌로 판정.
    """
    cd = normStkCd(stkCd)
    wait = dict(wait or {})
    step = int(step or wait.get("step") or 20)
    holds = _fetchAcntHoldMap()
    hold = holds.get(cd)
    qtyBefore = int(wait.get("qtyBefore") or 0)
    acntQty = int((hold or {}).get("qty") or 0)
    filledLikely = acntQty > qtyBefore

    if filledLikely and hold:
        ok = _promoteBuyFromAcntHold(
            cd,
            step,
            wait,
            hold,
            note=f"LIMIT_CNCL_FAIL 계좌복구:{str(errMsg or '')[:80]}",
        )
        if ok:
            return
        # 복구 실패해도 대기는 제거해 buyFillPending 고착을 푼다

    # 미체결로 보이거나 복구 실패 — 대기 제거 + 정리
    with _lock:
        if waitKey in _ordWait:
            _dropOrdWait(waitKey)
        else:
            for k, w in list(_ordWait.items()):
                if w.get("cd") == cd and w.get("side") == "BUY":
                    _dropOrdWait(k)
    _writeAlgEvt(
        "BUY_FAIL",
        cd,
        reason="LIMIT_CNCL_FAIL",
        px=maPx,
        qty=leftover,
        note=str(errMsg or "")[:200],
    )
    if not filledLikely:
        _writeAlgEvt(
            "BUY_ORD_CNCL",
            cd,
            reason="LIMIT_UNFILL_10M",
            px=float(_lastPx.get(cd) or maPx or 0) or maPx,
            qty=leftover,
            note=f"SMA{step} 취소실패후 재확인·미체결",
        )
        _unfillStepIfEmpty(cd, step)
        _voidUnfilledLedger(cd, reason="LIMIT_UNFILL_10M")
        _clearWaitConfirmCd(cd, step)
    _notify(f"[이평터치] 지정가취소실패 재확인 {cd}: {errMsg}")



def _limitTimeoutThenMarket(stkCd, step, waitKey):
    """지정가 10분 미체결 → 잔량 취소만. 시장가로 바꾸지 않는다."""
    with _lock:
        wait = _ordWait.get(waitKey)
        if wait is None:
            for k, w in _ordWait.items():
                if w.get("cd") == stkCd and w.get("side") == "BUY":
                    wait = w
                    waitKey = k
                    break
        if not wait:
            return
        leftover = int(wait.get("expectQty") or 0) - int(wait.get("gotQty") or 0)
        orig = str(wait.get("origOrdNo") or "")
        maPx = float(wait.get("maPx") or 0)
        gotQty = int(wait.get("gotQty") or 0)
    if leftover <= 0:
        return
    sess = _ordSession()
    if sess is None:
        _notify(f"[이평터치] {modeLabel()} 미로그인/불가 — {stkCd} 지정가 취소 생략")
        return
    hostUrl, token, tokenType = sess
    pxNow = float(_lastPx.get(stkCd) or maPx or 0)
    if orig:
        _waitOrdGap()
        cncl = placeCnclOrd(
            hostUrl,
            token,
            _ordExch(stkCd),
            orig,
            stkCd,
            cnclQty="0",
            tokenType=tokenType,
        )
        if not cncl.get("ok"):
            errMsg = str(cncl.get("errMsg") or "")
            # 10분 만료 후 취소 실패: 이미 체결·소멸 가능 → 계좌로 재확인.
            # 단순 BUY_FAIL return 은 _ordWait(gotQty=0) 고착 → 계좌동기 스킵 →
            # 실보유인데 익절 불가 (금호전기 2026-09-15).
            _reconcileLimitBuyAfterCnclFail(
                stkCd,
                step,
                waitKey,
                wait,
                maPx,
                leftover,
                errMsg,
            )
            return
    with _lock:
        wait = _ordWait.get(waitKey) or wait
        leftover = int(wait.get("expectQty") or 0) - int(wait.get("gotQty") or 0)
        gotQty = int(wait.get("gotQty") or 0)
        _dropOrdWait(waitKey)
    if leftover <= 0:
        return
    _notify(f"[이평터치] 지정가 10분 미체결 취소 {stkCd} {leftover}주")
    # 부분체결 잔량 취소만 BUY_CNCL. 전량 미체결은 BUY 가 없으므로 ORD 취소만.
    if gotQty > 0:
        _writeAlgEvt(
            "BUY_CNCL",
            stkCd,
            reason="LIMIT_UNFILL_10M",
            px=pxNow or maPx,
            qty=leftover,
            note=f"SMA{step} 지정가",
        )
    else:
        _writeAlgEvt(
            "BUY_ORD_CNCL",
            stkCd,
            reason="LIMIT_UNFILL_10M",
            px=pxNow or maPx,
            qty=leftover,
            note=f"SMA{step} 미체결취소",
        )
    if gotQty <= 0:
        _unfillStepIfEmpty(stkCd, step)
        _voidUnfilledLedger(stkCd, reason="LIMIT_UNFILL_10M")



def _isFlatExitReason(reason):
    """잔량 전량 청산 계열 (분할 익절 아님)."""
    r = str(reason or "")
    keys = (
        "4차익절",
        "잔량트레일",
        "15:15",
        "당일미수익",
        "익절 RSI",
        "손절",
        "익절후손절",
        "전량",
    )
    return any(k in r for k in keys)


def _flatSellQty(memQty, acntQty):
    """전량 매도 수량: 계좌 잔량이 있으면 계좌 기준, 없으면 메모리."""
    memQty = int(memQty or 0)
    acntQty = int(acntQty if acntQty is not None else -1)
    if acntQty > 0:
        return acntQty
    return max(0, memQty)


def _placeSell(stkCd, qty, px, reason):
    sess = _ordSession()
    if sess is None:
        _notify(f"[이평터치] {modeLabel()} 미로그인/불가 — {stkCd} 매도 생략")
        return False
    cd = normStkCd(stkCd)
    # 계좌평가 캐시 잔량 0이면 주문 자체를 넣지 않는다 (허수 포지션 SELL_FAIL 폭주 방지)
    try:
        from trading.crossAlgBuyGuard import getAcntHoldQty

        acntQty = int(getAcntHoldQty(cd) or 0)
    except Exception:
        acntQty = -1  # 캐시 조회 실패 시에는 주문 시도(기존 경로)
    if acntQty == 0:
        with _lock:
            rec = _pos.get(cd)
            # 내부만 수량이 있으면 허수로 보고 0으로 맞춤
            if rec is not None and int(rec.get("qty") or 0) > 0:
                rec["qty"] = 0
                _pos[cd] = rec
                _saveState()
            _sellFailUntil[cd] = time.time() + _SELL_FAIL_COOL_SEC
        termLog(
            f"[이평터치] 계좌잔량0 — 매도주문 생략 {cd} · {reason}"
        )
        try:
            requestAcntSyncSoon()
        except Exception:
            pass
        return False
    # 전량 청산: 주문 전에 계좌 잔량으로 맞춤 (쪼개진 4차 방지)
    if _isFlatExitReason(reason) and acntQty > 0:
        qty = int(acntQty)
    # 매도 중 지정가 잔량이 체결되면 청산 후 재진입이 되므로 먼저 취소
    _cnclOpenBuyLimits(stkCd)
    hostUrl, token, tokenType = sess
    _waitOrdGap()
    result = placeSellOrd(
        hostUrl, token, _ordExch(stkCd), stkCd, str(qty), _TRDE_TP_MARKET, tokenType=tokenType
    )
    # 8005 등 토큰 무효면 재발급 후 주문 1회만 재시도
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
                            f"[이평터치] 토큰재발급 후 매도 재시도 성공 {stkCd}"
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
        _notify(f"[이평터치매도실패] {stkCd} {qty}주 {reason}: {errMsg}")
        _writeAlgEvt(
            "SELL_FAIL",
            stkCd,
            reason=reason,
            px=px,
            qty=qty,
            note=errMsg[:200],
        )
        # 실패 시 익절 플래그를 올리지 않음. 쿨다운으로 재시도 폭주 방지.
        with _lock:
            _sellFailUntil[normStkCd(stkCd)] = time.time() + _SELL_FAIL_COOL_SEC
        # 매도가능 0주면 허수 보유일 수 있어 계좌동기 요청
        if "800033" in errMsg or "매도가능" in errMsg:
            try:
                requestAcntSyncSoon()
            except Exception:
                pass
        return False
    # 잔량·매도금액·익절플래그·SELL 이벤트는 00 체결에서만 반영.
    with _lock:
        rec = _pos.get(stkCd) or {}
        rec["lastPx"] = px
        _pos[stkCd] = rec
        qtyBefore = int(rec.get("qty") or 0)
        rec["pendSellReason"] = reason
        rec["pendSellQty"] = int(qty)
        rec["pendSellQtyBefore"] = qtyBefore
        rec["pendSellOrdNo"] = _normOrdNo(result.get("ordNo"))
        if _isFlatExitReason(reason):
            # 체결 완료(잔량0) 전까지 분할익절로 되돌아가지 않음
            rec["flatExitPending"] = True
        _pos[stkCd] = rec
        _registerOrdWait(
            result.get("ordNo"),
            {
                "cd": stkCd,
                "side": "SELL",
                "kind": "SELL",
                "step": 0,
                "expectQty": int(qty),
                "gotQty": 0,
                "qtyBefore": qtyBefore,
                "reason": reason,
                "ledgerBuy": False,
                "tradeDt": _todayYmd(),
            },
        )
        _sellFailUntil.pop(normStkCd(stkCd), None)
        _saveState()
    _notify(
        f"[이평터치매도주문] ({modeLabel()}) {stkCd} {qty}주 ≈{px:,.0f}원 · {reason}\n"
        f"체결 후 잔량·익절단계 반영 · 주문번호 {result.get('ordNo') or '-'}"
    )
    _writeAlgEvt("SELL_ORD", stkCd, reason=reason, px=px, qty=qty)
    return True


def _applyBuyFillLocked(stkCd, fillPx, fillQty, wait):
    """락 안에서 매수 체결 반영. 체결가 가중평균."""
    rec = _pos.get(stkCd) or _blankPos()
    prevQty = int(rec.get("qty") or 0)
    prevAvg = float(rec.get("avgPx") or 0)
    newQty = prevQty + int(fillQty)
    if newQty <= 0:
        return rec, False, int((wait or {}).get("step") or 0)
    rec["avgPx"] = (prevAvg * prevQty + float(fillPx) * fillQty) / float(newQty)
    rec["qty"] = newQty
    rec["buyAmt"] = float(rec.get("buyAmt") or 0) + float(fillPx) * fillQty
    rec["buyQty"] = int(rec.get("buyQty") or 0) + int(fillQty)
    rec["lastPx"] = fillPx
    rec["tradeDt"] = _todayYmd()
    if not rec.get("entryDt"):
        rec["entryDt"] = _todayYmd()
    if not rec.get("stkNm"):
        rec["stkNm"] = _lookupStkNm(stkCd)
    # 신규 포지션 시작 시 10이평 돌파 이력·고점수익률 초기화
    if prevQty <= 0:
        rec["brokeAboveTakeMa"] = False
        from utils.maTouchMfe import initMaxPnl

        initMaxPnl(rec, fillPx, _nowHms())
    else:
        from utils.maTouchMfe import noteMaxPnl

        noteMaxPnl(rec, fillPx, _nowHms())
    _pos[stkCd] = rec
    step = int((wait or {}).get("step") or 0)
    firstSlot = not bool((wait or {}).get("ledgerBuy"))
    return rec, firstSlot, step


def _clearPendSell(rec):
    """대기 매도 힌트 제거. 재기동 복구용 필드."""
    if not rec:
        return
    for key in (
        "pendSellReason",
        "pendSellQty",
        "pendSellQtyBefore",
        "pendSellOrdNo",
        "flatExitPending",
    ):
        rec.pop(key, None)


def _sellQtyAlreadyCut(rec, wait, fillQty):
    """
    계좌동기가 이미 잔량을 줄였으면 True.
    이때 또 빼면 남은 보유를 유령 청산한다.
    """
    qtyBefore = int((wait or {}).get("qtyBefore") or 0)
    expect = int((wait or {}).get("expectQty") or fillQty or 0)
    posQty = int((rec or {}).get("qty") or 0)
    if qtyBefore <= 0 or expect <= 0:
        return False
    return posQty <= max(0, qtyBefore - expect)


def _buildSellWaitRecover(cd, fillQty, ordNo):
    """
    대기 없는 매도 00. 우리 포지션일 때만 복구한다.
    주문접수가 체결보다 늦거나, 60초 만료 후 체결이 온 경우.
    """
    rec = _pos.get(cd)
    if not rec:
        return None
    reason = str(rec.get("pendSellReason") or "")
    expect = int(rec.get("pendSellQty") or 0) or int(fillQty or 0)
    qtyBefore = int(rec.get("pendSellQtyBefore") or 0)
    if qtyBefore <= 0:
        posQty = int(rec.get("qty") or 0)
        buyQty = int(rec.get("buyQty") or 0)
        sellQty = int(rec.get("sellQty") or 0)
        # 원장 매도 0인데 잔량만 줄었으면 주문 전 수량은 매수수량
        if sellQty <= 0 and buyQty > posQty > 0:
            qtyBefore = buyQty
        else:
            qtyBefore = posQty
    if not reason:
        reason = "매도체결"
    return {
        "cd": cd,
        "side": "SELL",
        "kind": "SELL",
        "step": 0,
        "expectQty": max(int(expect), int(fillQty or 0)),
        "gotQty": 0,
        "qtyBefore": qtyBefore,
        "reason": reason,
        "ledgerBuy": False,
        "tradeDt": _todayYmd(),
        "recovered": True,
        "ordNo": _normOrdNo(ordNo),
    }


def _applySellFillLocked(stkCd, fillPx, fillQty, wait):
    """락 안에서 매도 체결 반영. 평단은 유지하고 잔량만 줄인다."""
    rec = _pos.get(stkCd) or {}
    posQty = int(rec.get("qty") or 0)
    qty = int(fillQty or 0)
    alreadyCut = _sellQtyAlreadyCut(rec, wait, qty)
    # 체결금액·수량은 00 기준. 잔량만 중복 차감하지 않는다
    cutQty = 0 if alreadyCut else qty
    if posQty > 0 and cutQty > posQty:
        termLog(
            f"[maTouch] 매도체결 수량 보정 {stkCd} {cutQty}→{posQty}주 (보유초과)"
        )
        cutQty = posQty
    rec["sellAmt"] = float(rec.get("sellAmt") or 0) + float(fillPx) * qty
    rec["sellQty"] = int(rec.get("sellQty") or 0) + qty
    rec["lastPx"] = fillPx
    reason = str((wait or {}).get("reason") or "")
    if "3차익절" in reason:
        rec["tp1Taken"] = True
        rec["tp2Taken"] = True
        rec["tp3Taken"] = True
        rec["halfTaken"] = True
    elif "2차익절" in reason or "절반" in reason:
        rec["tp1Taken"] = True
        rec["tp2Taken"] = True
        rec["halfTaken"] = True
    elif "1차익절" in reason:
        rec["tp1Taken"] = True
    elif "4차익절" in reason:
        rec["tp1Taken"] = True
        rec["tp2Taken"] = True
        rec["tp3Taken"] = True
        rec["halfTaken"] = True
    left = posQty if alreadyCut else posQty - cutQty
    if alreadyCut:
        termLog(
            f"[maTouch] 매도잔량 이미반영 {stkCd} 보유 {posQty}주 "
            f"체결 {qty}주 — 수량 유지"
        )
    closed = left <= 0
    rec["qty"] = 0 if closed else left
    if closed:
        rec.pop("flatExitPending", None)
        _clearPendSell(rec)
        _pos.pop(stkCd, None)
    else:
        # 전량청산 계열 부분체결이면 잔량 재청산 유지
        if _isFlatExitReason(reason):
            rec["flatExitPending"] = True
        _pos[stkCd] = rec
    return rec, closed, reason


def onBrokerFill(sideLabel, stkCd, fillQty, fillPx, ordNo=""):
    """
    00 실체결. 이평터치 매수·매도 모두 대기 없이 와도 포지션에 반영한다.
    키움 체결가가 오므로 주문 당시 시세가와 달라도 실제 체결가로 맞춘다.
    """
    if not _isEnabled():
        return
    cd = normStkCd(stkCd)
    qty = int(fillQty or 0)
    px = abs(int(fillPx or 0))
    if not cd or qty <= 0 or px <= 0:
        return
    sideTxt = str(sideLabel or "")
    if "매수" in sideTxt:
        side = "BUY"
    elif "매도" in sideTxt:
        side = "SELL"
    else:
        return
    closeJob = None
    buyJob = None
    with _lock:
        wKey, wait = _findOrdWait(ordNo, cd, side)
        # 00 체결이 _registerOrdWait 보다 먼저 오면 대기가 없다.
        # 주문접수만으로 qty>0 포지션을 만들지 않으므로, 대기 복구는 주문번호·최근 BUY 대기로.
        if not wait and side == "BUY":
            rec0 = _pos.get(cd) or {}
            filled = list(rec0.get("filled") or [])
            wait = {
                "cd": cd,
                "side": "BUY",
                "kind": "BUY",
                "step": int(filled[-1]) if filled else 20,
                "expectQty": max(int(rec0.get("buyQty") or 0), qty),
                "gotQty": 0,
                "qtyBefore": int(rec0.get("qty") or 0),
                "ledgerBuy": bool(rec0.get("tradeCd")),
                "reason": f"SMA{filled[-1] if filled else 20}",
            }
            wKey = _registerOrdWait(ordNo, wait)
            termLog(f"[maTouch] 체결선착 대기복구 {cd} {qty}주")
        # 매도도 선착·만료 후 00 가 오면 포지션이 있을 때 복구한다
        if not wait and side == "SELL":
            wait = _buildSellWaitRecover(cd, qty, ordNo)
            if wait:
                wKey = _registerOrdWait(ordNo, wait)
                termLog(f"[maTouch] 매도체결 대기복구 {cd} {qty}주")
        if not wait:
            return
        wait["gotQty"] = int(wait.get("gotQty") or 0) + qty
        if side == "BUY":
            rec, firstSlot, step = _applyBuyFillLocked(cd, px, qty, wait)
            wait["ledgerBuy"] = True
            buyJob = (rec, firstSlot, step, px, qty, dict(wait))
        else:
            rec, closed, reason = _applySellFillLocked(cd, px, qty, wait)
            closeJob = (rec, closed, reason, px, qty)
        if int(wait.get("gotQty") or 0) >= int(wait.get("expectQty") or 0):
            _dropOrdWait(wKey)
            if rec:
                _clearPendSell(rec)
        _saveState()
    if buyJob:
        rec, firstSlot, step, px, qty, waitSnap = buyJob
        _ensureOpenLedger(cd, rec, px, qty, step)
        reason = str((waitSnap or {}).get("reason") or f"SMA{step}")
        note = str((waitSnap or {}).get("ordNote") or "체결")
        _writeAlgEvt("BUY", cd, reason=reason, px=px, qty=qty, note=note)
        try:
            from utils.algTouchSumStore import markTouchBuy

            markTouchBuy(
                _ALG_CD,
                _todayYmd(),
                cd,
                step,
                _nowHms(),
                px,
                qty,
                stepSeq=len(list(rec.get("filled") or [])),
                stkNm=rec.get("stkNm") or "",
            )
        except Exception as err:
            termLog(f"[maTouch] 터치요약 매수표시 실패: {err}")
        _notify(
            f"[이평터치체결] {cd} 매수 {qty}주 {px:,.0f}원\n"
            f"평단 {rec.get('avgPx', 0):,.0f} · 보유 {rec.get('qty', 0)}주"
        )
        try:
            requestAcntSyncSoon()
        except Exception:
            pass
        return
    if not closeJob:
        return
    rec, closed, reason, px, qty = closeJob
    _syncLedgerSell(cd, px, qty, reason, rec, closed)
    left = int(rec.get("qty") or 0)
    _writeAlgEvt("SELL", cd, reason=reason, px=px, qty=qty)
    _notify(
        f"[이평터치체결] {cd} 매도 {qty}주 {px:,.0f}원 · {reason}\n"
        f"잔량 {left}주"
    )
    try:
        requestAcntSyncSoon()
    except Exception:
        pass
    # 잔량 0 이면 전량청산 이력 필수.
    # (예전: reason에 '절반' 있으면 스킵 → 1주만 남은 절반매도 시 LIVE_TRADE 누락)
    if closed:
        if "당일미수익" in reason:
            exitKind = "FLAT"
        elif "익절" in reason or "절반" in reason:
            exitKind = "TAKE"
        else:
            exitKind = "STOP"
        _onFullClose(cd, rec, px, exitKind)


def _closeGhostToMatchAcnt(stkCd, rec, reason="계좌잔량0"):
    """
    계좌에 잔량이 없는데 미청산으로 남은 원장·메모리를 닫는다.
    관심종목은 유지한다(유니버스). 행번호 색만 보유와 맞춘다.
    """
    cd = normStkCd(stkCd)
    rec = dict(rec or {})
    left = int(rec.get("qty") or 0)
    px = float(rec.get("lastPx") or rec.get("avgPx") or 0)
    # 매도 체결이 하나도 없으면 청산이 아니라 미체결 취소다.
    # 여기서 청산으로 닫으면 매도가=평단이라 0원 유령 라운드가 만들어진다.
    try:
        from utils.stratRoundFill import hasSellFill

        entryDt = str(rec.get("entryDt") or "")[:8] or _todayYmd()
        soldAny = hasSellFill(_ALG_CD, cd, entryDt, _todayYmd(), mode=_tradeMode())
    except Exception as err:
        termLog(f"[maTouch] 매도체결 확인 실패 {cd}: {err}")
        soldAny = True
    if not soldAny:
        _voidUnfilledLedger(cd, reason=reason)
        _banBuyToday(cd)
        termLog(
            f"[maTouch] 매도체결 없음 — 원장 무효 {cd} {rec.get('stkNm') or ''} "
            f"(계좌잔량0, 청산 아님)"
        )
        return
    if left > 0 and px > 0:
        rec["sellAmt"] = float(rec.get("sellAmt") or 0) + px * left
        rec["sellQty"] = int(rec.get("sellQty") or 0) + left
    rec["qty"] = 0
    _syncLedgerSell(cd, px, left, reason, rec, True)
    _banBuyToday(cd)
    termLog(
        f"[maTouch] 계좌잔량0 원장청산 {cd} {rec.get('stkNm') or ''} "
        f"잔량 {left}주 → 보유표와 일치"
    )


def syncFromAcntEvlt(body):
    """
    kt00004 계좌평단·잔량으로 미청산 원장을 덮어쓴다.
    하드스탑·익절(평단 위) 기준을 매매창과 같게 맞춘다.
    계좌에 없는 종목은 미청산을 닫아 관심 행번호 색과 보유표를 맞춘다.
    """
    global _acntSyncAt
    try:
        from trading.crossAlgBuyGuard import noteAcntHoldings

        # 복수전략 신규매수 가드용 계좌보유 캐시 (포지션 유무와 무관)
        noteAcntHoldings(body)
    except Exception as err:
        termLog(f"[maTouch] 계좌보유캐시 실패: {err}")
    if not body:
        return
    holds = _holdMapFromAcnt(body)
    changed = []
    missing = []
    promoteBuys = []
    with _lock:
        _dropExpiredWaits()
        for cd, rec in list(_pos.items()):
            item = holds.get(cd)
            acntQty = int((item or {}).get("qty") or 0)
            # 00 매수 체결 전(지정가 대기·gotQty=0): 계좌에 잔량이 보여도
            # qty/평단/원장을 올리지 않는다. (동기 선반영 → 익절 선주문 레이스 방지)
            # 지정가 시한 경과 + 계좌수량 증가면 00 누락 승격 후보.
            if _buyFillPending(cd, "BUY"):
                buyWait = None
                for w in _ordWait.values():
                    if w.get("cd") == cd and w.get("side") == "BUY":
                        buyWait = w
                        break
                acntQty = int((item or {}).get("qty") or 0)
                qtyBefore = int((buyWait or {}).get("qtyBefore") or 0)
                until = float((buyWait or {}).get("limitUntil") or 0)
                pastLimit = until > 0 and time.time() >= until
                if buyWait and item and pastLimit and acntQty > qtyBefore:
                    promoteBuys.append(
                        (
                            cd,
                            int(buyWait.get("step") or 20),
                            dict(buyWait),
                            {
                                "qty": acntQty,
                                "avgPx": float(item.get("avgPx") or 0),
                            },
                        )
                    )
                continue
            # 계좌 잔량 0 인데 원장만 남아 있으면 관심 색이 보유와 어긋난다.
            # 매수/매도 대기 중이면 계좌 TR 지연일 수 있어 유지.
            if not item or acntQty <= 0:
                if _hasOrdWait(cd):
                    continue
                missing.append((cd, dict(rec)))
                _pos.pop(cd, None)
                continue
            avgPx = float(item.get("avgPx") or 0)
            qty = acntQty
            if avgPx <= 0 or qty <= 0:
                continue
            wait = None
            for w in _ordWait.values():
                if w.get("cd") == cd:
                    wait = w
                    break
            # 00 체결은 반영됐는데 계좌 TR 이 아직 옛 잔량이면 수량은 유지
            if (
                wait
                and int(wait.get("gotQty") or 0) > 0
                and qty == int(wait.get("qtyBefore") or 0)
            ):
                if abs(float(rec.get("avgPx") or 0) - avgPx) < 1:
                    continue
                rec["avgPx"] = avgPx
                changed.append((cd, avgPx, int(rec.get("qty") or 0), "avg"))
                _pos[cd] = rec
                continue
            oldAvg = float(rec.get("avgPx") or 0)
            oldQty = int(rec.get("qty") or 0)
            rec["avgPx"] = avgPx
            rec["qty"] = qty
            if int(rec.get("buyQty") or 0) < qty:
                rec["buyQty"] = qty
            _pos[cd] = rec
            if wait:
                expect = int(wait.get("expectQty") or 0)
                before = int(wait.get("qtyBefore") or 0)
                side = wait.get("side")
                done = (
                    (side == "BUY" and qty >= before + expect)
                    or (side == "SELL" and qty <= before - expect)
                )
                if done:
                    for k, w in list(_ordWait.items()):
                        if w.get("cd") == cd:
                            _dropOrdWait(k)
            if abs(oldAvg - avgPx) >= 1 or oldQty != qty:
                changed.append((cd, avgPx, qty, "pos"))
        if changed or missing:
            _saveState()
    for cd, step, waitSnap, hold in promoteBuys:
        try:
            _promoteBuyFromAcntHold(
                cd,
                step,
                waitSnap,
                hold,
                note="sync 시한경과 계좌승격",
            )
        except Exception as err:
            termLog(f"[maTouch] 시한경과 매수승격 실패 {cd}: {err}")

    for cd, avgPx, qty, _kind in changed:
        rec = _pos.get(cd)
        if rec:
            step = 20
            filled = list(rec.get("filled") or [])
            if filled:
                try:
                    step = int(filled[-1])
                except (TypeError, ValueError):
                    step = 20
            # 당일 신규는 원장이 비어 있을 수 있어 INSERT 까지 맡긴다
            _ensureOpenLedger(cd, rec, avgPx, qty, step)
        termLog(
            f"[maTouch] 계좌평단 동기 {cd} {avgPx:,.0f}원 · {qty}주"
        )
    # 평단이 안 바뀌어도 원장이 비어 있으면 INSERT (당일 신규 색)
    try:
        from utils.stratOrdLedgerStore import getOpenLedgerRow

        for cd, rec in list(_pos.items()):
            item = holds.get(cd)
            if not item or int(item.get("qty") or 0) <= 0:
                continue
            if _buyFillPending(cd, "BUY"):
                continue
            if getOpenLedgerRow(_STRAT_ID, cd):
                continue
            step = 20
            filled = list((rec or {}).get("filled") or [])
            if filled:
                try:
                    step = int(filled[-1])
                except (TypeError, ValueError):
                    step = 20
            _ensureOpenLedger(
                cd,
                rec,
                float(item.get("avgPx") or rec.get("avgPx") or 0),
                int(item.get("qty") or rec.get("qty") or 0),
                step,
            )
    except Exception as err:
        termLog(f"[maTouch] 원장 보강 실패: {err}")
    # 재기동 등: 주문대기 없이 터치대기만 남고 계좌에 잔량이 있으면
    # 당일 BUY_ORD 가 있고 BUY 가 없을 때만 누락체결로 복구.
    try:
        from utils.algEvtStore import listAlgEvt

        evts = listAlgEvt(_ALG_CD, _todayYmd(), limit=3000) or []
        bought = set()
        ordered = set()
        for ev in evts:
            ecd = normStkCd(ev.get("STK_CD"))
            tp = str(ev.get("EVT_TP") or "")
            if not ecd:
                continue
            if tp == "BUY":
                bought.add(ecd)
            if tp == "BUY_ORD":
                ordered.add(ecd)
        adoptCds = ordered - bought
        for cd in list(adoptCds):
            if cd in _pos:
                continue
            if _buyFillPending(cd, "BUY"):
                continue
            item = holds.get(cd)
            if not item or int(item.get("qty") or 0) <= 0:
                continue
            step = 20
            with _lock:
                wc = (_waitConfirm.get(cd) or {})
                if wc:
                    try:
                        step = int(next(iter(wc.keys())))
                    except Exception:
                        step = 20
            _promoteBuyFromAcntHold(
                cd,
                step,
                {"step": step, "qtyBefore": 0, "reason": f"SMA{step}"},
                item,
                note="재기동/이벤트 누락체결 복구",
            )
    except Exception as err:
        termLog(f"[maTouch] 누락체결 복구스캔 실패: {err}")

    for cd, rec in missing:
        _closeGhostToMatchAcnt(cd, rec)
    # 메모리에 없어도 원장만 미청산인 종목도 계좌 기준으로 닫는다
    try:
        from utils.stratOrdLedgerStore import listOpenLedgerRows, rowToLivePos

        missCds = {cd for cd, _rec in missing}
        for row in listOpenLedgerRows(stratId=_STRAT_ID) or []:
            cd = normStkCd(row.get("STK_CD"))
            if not cd or cd in missCds or cd in holds:
                continue
            if _hasOrdWait(cd):
                continue
            rec = rowToLivePos(row, todayYmd=_todayYmd()) or {}
            _closeGhostToMatchAcnt(cd, rec)
    except Exception as err:
        termLog(f"[maTouch] 원장-계좌 일치 실패: {err}")
    _acntSyncAt = time.time()


def requestAcntSyncSoon(delaySec=1.2):
    """체결 직후 등 계좌평가를 빨리 한 번 돌린다 (중복 호출은 합침)."""
    global _acntSyncAt

    def _run():
        time.sleep(max(0.2, float(delaySec or 1.2)))
        # 방금 동기했으면 스킵
        if time.time() - float(_acntSyncAt or 0) < 2.0:
            return
        _fetchAndSyncAcnt(force=True)

    threading.Thread(target=_run, name="maTouchAcntSoon", daemon=True).start()


def _fetchAndSyncAcnt(force=False):
    """kt00004 조회 → syncFromAcntEvlt. 모의는 호출 간격 유지."""
    global _acntSyncAt
    if not force and (time.time() - float(_acntSyncAt or 0)) < (_ACNT_SYNC_SEC - 2):
        return
    try:
        from utils.tradeSession import getDeskTradeMode, getOrdSession

        if not isModeLoggedIn():
            return
        sess = getOrdSession(paperOnly=False)
        if sess is None:
            return
        hostUrl, token, tokenType = sess
        mode = getDeskTradeMode()
        if mode == "paper" and (time.time() - float(_acntSyncAt or 0)) < 1.05:
            time.sleep(1.05)
        from api.getAcntEvlt import fetchAcntEvlt

        got = fetchAcntEvlt(
            hostUrl, token, qryTp="1", dmstStexTp="KRX", tokenType=tokenType
        )
        if not got.get("ok"):
            termLog(f"[maTouch] 계좌동기 실패: {got.get('errMsg')}")
            return
        syncFromAcntEvlt(got.get("body") or got)
        termLog("[maTouch] 계좌보유·수량 동기 완료")
    except Exception as err:
        termLog(f"[maTouch] 계좌동기 예외: {err}")


def _acntSyncLoop():
    """장중·상시 계좌평가 주기 동기 (UI 없어도 동작)."""
    while True:
        try:
            time.sleep(_ACNT_SYNC_SEC)
            if not _isEnabled():
                continue
            _fetchAndSyncAcnt(force=False)
        except Exception as err:
            termLog(f"[maTouch] 계좌동기루프: {err}")
            time.sleep(5.0)


def _onFullClose(stkCd, rec, exitPx, exitKind):
    """전량 청산 후 검증 이력 저장 · 관심종목 해제 · 0B 해지."""
    buyQty = int(rec.get("buyQty") or 0)
    sellQty = int(rec.get("sellQty") or 0)
    avgPx = float(rec.get("avgPx") or 0)
    buyAmt = float(rec.get("buyAmt") or 0)
    sellAmt = float(rec.get("sellAmt") or 0)
    px = float(exitPx or 0)
    # 메모리·원장 수량은 주문 시점 값이라 부분체결·미체결취소가 빠질 수 있다.
    # 라운드 손익은 체결 로그(BUY − BUY_CNCL vs SELL)를 진실로 삼는다.
    fill = None
    roundOk = True
    try:
        from utils.stratRoundFill import calcRoundFill

        entryDt = str(rec.get("entryDt") or "")[:8] or _todayYmd()
        got = calcRoundFill(_ALG_CD, stkCd, entryDt, _todayYmd(), mode=_tradeMode())
        if got.get("ok") and int(got.get("buyQty") or 0) > 0:
            fill = got
    except Exception as err:
        termLog(f"[maTouch] 청산 체결집계 실패 {stkCd}: {err}")
    if fill is not None:
        fBuy = int(fill.get("buyQty") or 0)
        fSell = int(fill.get("sellQty") or 0)
        if fill.get("closed"):
            buyQty, sellQty = fBuy, fSell
            buyAmt = float(fill.get("buyAmt") or 0)
            sellAmt = float(fill.get("sellAmt") or 0)
            avgPx = float(fill.get("avgBuyPx") or avgPx)
            px = float(fill.get("exitPx") or px)
            pnlWon = float(fill.get("pnlWon") or 0)
            pnlPct = float(fill.get("pnlPct") or 0)
        elif fSell > 0:
            # 엔진이 잔량 0으로 전량청산을 확정한 상태다.
            # BUY 이벤트는 주문수량(미체결 취소 BUY_CNCL 누락)이라 buy>sell 로
            # 보일 수 있다. 실제 왕복분은 매도체결과 같으므로 매도 기준으로 닫는다.
            sellQty = fSell
            sellAmt = float(fill.get("sellAmt") or 0)
            avgPx = float(fill.get("avgBuyPx") or avgPx)
            px = float(fill.get("exitPx") or px)
            buyQty = sellQty
            buyAmt = avgPx * buyQty if avgPx > 0 else float(fill.get("buyAmt") or 0)
            # 매도분만의 원가 — 미체결 주문수량은 손익에 넣지 않는다
            pnlWon = sellAmt - buyAmt
            pnlPct = (pnlWon / buyAmt * 100.0) if buyAmt > 0 else 0.0
            termLog(
                f"[maTouch] 청산집계 보정 {stkCd} 이벤트 매수 {fBuy}주 / 매도 {fSell}주 "
                f"→ 전량청산 확정이라 매도 {fSell}주 기준으로 라운드 기록 "
                f"(pnl={pnlWon:+,.0f})"
            )
        else:
            # 매도 체결이 하나도 없으면 유령 — 승패에 넣지 않는다
            roundOk = False
            memBuy, memSell = buyQty, sellQty
            buyQty, sellQty = fBuy, fSell
            buyAmt = float(fill.get("buyAmt") or 0)
            sellAmt = float(fill.get("sellAmt") or 0)
            pnlWon = 0.0
            pnlPct = 0.0
            termLog(
                f"[maTouch] 라운드 이력 생략 {stkCd} — 매도체결 0 "
                f"(이벤트 매수 {fBuy}주, 메모리 {memBuy}/{memSell})"
            )
    else:
        # 체결 로그를 못 읽었을 때만 예전 보정 (금액이 비면 평단·청산가로 채움)
        if buyAmt <= 0 and avgPx > 0 and buyQty > 0:
            buyAmt = avgPx * buyQty
        if sellAmt <= 0 and px > 0 and sellQty > 0:
            sellAmt = px * sellQty
        pnlWon = sellAmt - buyAmt
        pnlPct = (pnlWon / buyAmt * 100.0) if buyAmt > 0 else 0.0
    # 평가%(비용 전) — 실현 반영 전에 확정
    evalPct = float(pnlPct) if roundOk else 0.0
    # 영웅문식: 수수료·세금 차감 (00 FID 또는 추정)
    if roundOk and avgPx > 0 and sellQty > 0 and px > 0:
        try:
            from utils.heroPnl import heroRealizedPnl, loadDeskFeeBuckets, takeSellFees

            mode = _tradeMode()
            buckets = loadDeskFeeBuckets(_todayYmd(), mode=mode)
            buyCmsn, sellCmsn, sellTax = takeSellFees(
                buckets.get(normStkCd(stkCd)),
                sellQty,
                sellPx=px,
                buyPx=avgPx,
                mode=mode,
            )
            got = heroRealizedPnl(
                avgPx,
                px,
                sellQty,
                buyCmsn=buyCmsn,
                sellCmsn=sellCmsn,
                sellTax=sellTax,
            )
            evalPct = float(got.get("evalRt") if got.get("evalRt") is not None else evalPct)
            pnlWon = float(got.get("netPnl") or pnlWon)
            pnlPct = float(got.get("pnlRt") or pnlPct)
            if buyCmsn or sellCmsn or sellTax:
                termLog(
                    f"[maTouch] 영웅문손익 {stkCd} 수수료 "
                    f"매수{buyCmsn}+매도{sellCmsn} 세금{sellTax} "
                    f"→ 평가{evalPct:+.2f}% 실현{pnlPct:+.2f}% {pnlWon:+,.0f}원"
                )
        except Exception as err:
            termLog(f"[maTouch] 수수료반영 실패 {stkCd}: {err}")
    steps = rec.get("filled") or []
    halfYn = "Y" if rec.get("halfTaken") else "N"
    stkNm = ""
    try:
        from utils.watchStkStore import listActiveWatch

        for w in listActiveWatch(mktTp="KR") or []:
            if normStkCd(w.get("STK_CD")) == stkCd:
                stkNm = w.get("STK_NM") or ""
                break
    except Exception:
        pass
    if not stkNm:
        stkNm = str(rec.get("stkNm") or "")

    if not roundOk:
        termLog(
            f"[maTouch] 라운드 이력 생략 {stkCd} {stkNm} — 체결 매수 {buyQty}주 / "
            f"매도 {sellQty}주 (전량청산 아님). 승패·손익에 넣지 않는다"
        )
    closeNote = (
        f"eval={evalPct:+.2f} real={pnlPct:+.2f}" if roundOk else None
    )
    try:
        from utils.stratLiveTradeStore import insertLiveTrade

        got = {"ok": True} if not roundOk else insertLiveTrade(
            {
                "algCd": "MTS01",
                "stratId": _STRAT_ID,
                "mode": _tradeMode(),
                "stkCd": stkCd,
                "stkNm": stkNm,
                "entryDt": rec.get("entryDt") or rec.get("tradeDt"),
                "exitDt": _todayYmd(),
                "exitTm": _nowHms(),
                "exitReason": exitKind,
                "buyQty": buyQty,
                "sellQty": sellQty,
                "buyAmt": buyAmt,
                "sellAmt": sellAmt,
                "avgBuyPx": avgPx if avgPx > 0 else None,
                "exitPx": px if px > 0 else exitPx,
                "pnlWon": pnlWon,
                "pnlPct": pnlPct,
                "halfYn": halfYn,
                "steps": ",".join(str(s) for s in steps),
                "maxPnlPct": rec.get("maxPnlPct"),
                "maxPx": rec.get("maxPx"),
                "note": closeNote,
            }
        )
        if not (got or {}).get("ok"):
            termLog(
                f"[maTouch] 청산이력 저장 실패 {stkCd}: "
                f"{(got or {}).get('errMsg') or 'unknown'}"
            )
    except Exception as err:
        termLog(f"[maTouch] 청산이력 저장 실패: {err}")

    try:
        from utils.watchStkStore import deactivateWatchStk

        # 전량 청산 종목은 유니버스에서 빼 재진입을 막고, 검증 이력만 남긴다
        deactivateWatchStk(
            stkCd, note=f"exitClosed:{exitKind}:{_todayYmd()}"
        )
    except Exception as err:
        termLog(f"[maTouch] 관심종목 해제 실패: {err}")

    try:
        from trading.watchRtMonitor import unregWatchStk

        unregWatchStk(stkCd, f"전량청산 {exitKind}")
    except Exception as err:
        termLog(f"[maTouch] 0B 해지 실패: {err}")

    # 승패 푸시도 라운드가 실제로 닫혔을 때만 (잔량 보유 중이면 승패 미확정)
    if roundOk:
        _pushClosePnl(stkNm, stkCd, pnlPct, pnlWon, evalPct=evalPct)
    # 전량 청산 당일 재매수 금지 (관심 해제와 별도로 당일 재터치 차단)
    _banBuyToday(stkCd)


def onExternalFullClose(stkCd, exitPx, reasonLabel, qty=0, avgPx=0):
    """
    STLS 등 외부 전량 청산.
    이평터치 원장이 있으면 그걸 쓰고, 없으면 평단·수량으로 수익률을 계산한다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return
    # 이미 오늘 전량청산 이력이 있으면 이중 적재하지 않는다
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TRADE_ID FROM AI_STRAT_LIVE_TRADE
                     WHERE MODE=%s AND ALG_CD=%s AND STK_CD=%s AND EXIT_DT=%s
                     LIMIT 1
                    """,
                    (_tradeMode(), "MTS01", cd, _todayYmd()),
                )
                if cur.fetchone():
                    termLog(f"[maTouch] 외부청산 스킵 {cd} — 당일 이력 있음")
                    return
        finally:
            conn.close()
    except Exception:
        pass
    exitKind = "STLS_TAKE" if "익절" in str(reasonLabel or "") else "STLS_STOP"
    rec = None
    with _lock:
        rec = _pos.pop(cd, None)
        if rec is not None:
            left = int(rec.get("qty") or 0)
            if left > 0:
                rec["sellAmt"] = float(rec.get("sellAmt") or 0) + float(exitPx) * left
                rec["sellQty"] = int(rec.get("sellQty") or 0) + left
            rec["qty"] = 0
            _syncLedgerSell(
                cd,
                exitPx,
                left,
                reasonLabel or "",
                rec,
                True,
            )
            _saveState()
    if rec is None:
        onWatch = False
        openRow = None
        try:
            from utils.watchStkStore import listActiveWatch

            onWatch = any(
                normStkCd(w.get("STK_CD")) == cd
                for w in (listActiveWatch(mktTp="KR") or [])
            )
        except Exception:
            onWatch = False
        try:
            from utils.stratOrdLedgerStore import getOpenLedgerRow, rowToLivePos

            openRow = getOpenLedgerRow(_STRAT_ID, cd)
        except Exception:
            openRow = None
        # 관심·원장 둘 다 없으면 STLS 일반 보유 청산으로 보고 검증 이력은 남기지 않음
        if not onWatch and not openRow:
            return
        if openRow:
            rec = rowToLivePos(openRow, todayYmd=_todayYmd()) or {}
            left = int(rec.get("qty") or qty or 0)
            rec["sellAmt"] = float(rec.get("sellAmt") or 0) + float(exitPx) * left
            rec["sellQty"] = int(rec.get("sellQty") or 0) + left
            rec["qty"] = 0
            _syncLedgerSell(cd, exitPx, left, reasonLabel or "", rec, True)
        else:
            q = int(qty or 0)
            a = float(avgPx or 0)
            px = float(exitPx or 0)
            rec = {
                "buyAmt": a * q,
                "sellAmt": px * q,
                "buyQty": q,
                "sellQty": q,
                "avgPx": a,
                "filled": [],
                "halfTaken": False,
                "entryDt": _todayYmd(),
            }
    _onFullClose(cd, rec, exitPx, exitKind)


def onMaTouchTick(stkCd, curPrcRaw, tmRaw="", fluRtRaw=None):
    """
    0B 현재가 한 틱. watchRtMonitor 에서 호출.
    주문은 별도 락 안에서 짧게 판단하고, API 는 락 밖에서 호출한다.
    """
    if not _isEnabled():
        return
    cd = normStkCd(stkCd)
    px = _absPrice(curPrcRaw)
    if not cd or px is None or px <= 0:
        return

    global _params, _waitConfirm, _waitYmd
    _refreshParams()
    if getOrdSession(paperOnly=bool(_params.get("_paperOnly", True))) is None:
        return

    nowTm = str(tmRaw or _nowHms()).zfill(6)[-6:]
    buyCut = str(_params.get("buyCutoffTm", "140000")).zfill(6)
    exitTm = str(_params.get("exitAfterTm", "151800")).zfill(6)
    cutOn = _buyCutoffOn()

    with _lock:
        _dropExpiredWaits()
        if not _smaMap or _smaYmd != _todayYmd():
            return
        sma = _smaMap.get(cd)
        if not sma:
            return
        prevPx = _lastPx.get(cd)
        _lastPx[cd] = px
        useBar3 = confirmModeIsBar3(_params)
        closed3 = None
        if useBar3:
            closed3 = _feedBar3(cd, px, nowTm)
        if prevPx is None:
            return

        rec = _normTpFlags(
            _pos.get(cd)
            or {
                "qty": 0,
                "avgPx": 0.0,
                "filled": [],
                "halfTaken": False,
                "tp1Taken": False,
                "tp2Taken": False,
                "tp3Taken": False,
                "halfDt": "",
                "halfAddDone": False,
                "entryDt": "",
            }
        )
        filled = list(rec.get("filled") or [])
        actions = []
        skips = []
        touches = []
        qtyNow = int(rec.get("qty") or 0)
        avgPx = float(rec.get("avgPx") or 0)
        if qtyNow > 0 and avgPx > 0:
            from utils.maTouchMfe import noteMaxPnl

            if noteMaxPnl(rec, px, nowTm):
                _pos[cd] = rec
                # 고점만 올랐을 때는 상태파일만. 원장은 0.1%p 이상일 때
                prevLed = rec.get("_ledMaxPnl")
                try:
                    prevLedN = float(prevLed) if prevLed is not None else None
                except (TypeError, ValueError):
                    prevLedN = None
                curMfe = float(rec.get("maxPnlPct") or 0)
                if prevLedN is None or curMfe - prevLedN >= 0.1:
                    rec["_ledMaxPnl"] = curMfe
                    _pos[cd] = rec
                    _saveState()
                    try:
                        _patchLedgerPos(cd, rec)
                    except Exception:
                        pass
                else:
                    _pos[cd] = rec

        halfTaken = bool(rec.get("tp2Taken") or rec.get("halfTaken"))
        tp1Taken = bool(rec.get("tp1Taken") or halfTaken)
        tp2Taken = halfTaken
        brokeAboveTakeMa = bool(rec.get("brokeAboveTakeMa"))
        entryDt = rec.get("entryDt") or rec.get("tradeDt") or ""
        enteredToday = bool(qtyNow > 0 and entryDt == _todayYmd())
        takeMa = sma.get(int(_params.get("takeMa", 10)))
        stopMa = sma.get(int(_params.get("stopMa", 20)))
        hardPct = float(_params.get("hardStopPct", 2))
        tp1Pct = float(_params.get("tp1Pct", 1.5) or 0)
        tp1Ratio = float(_params.get("tp1QtyRatio", 0.3) or 0)
        tp2Pct = float(_params.get("tp2Pct", 2) or 0)
        tp2Ratio = float(_params.get("tp2QtyRatio", 0.3) or 0)
        tp3Pct = float(_params.get("tp3Pct", 4) or 0)
        tp3Ratio = float(_params.get("tp3QtyRatio", 0.5) or 0)
        tp4Pct = float(_params.get("tp4Pct", 7) or 0)
        tp3Taken = bool(rec.get("tp3Taken"))
        buyBanned = _isBuyBanned(cd)
        afterHalfStopPct = float(_params.get("afterHalfStopPct") or 0)
        breakevenAfterTp1 = bool(_params.get("breakevenAfterTp1", True))
        reclaimMinPct = float(_params.get("reclaimMinPct", 0))
        banUntilTm = str(_params.get("buyBanUntilTm", "000000")).zfill(6)[-6:]
        openTmBan = nowTm < banUntilTm
        sma20 = sma.get(20)
        if sma20 is None:
            sma20 = stopMa
        openPx = _openMap.get(cd)
        openBelowSma = bool(
            _params.get("banBuyIfOpenBelowSma", False)
            and openPx is not None
            and sma20 is not None
            and float(openPx) <= float(sma20)
        )

        # 매수 후 장중 현재가가 takeMa(SMA10) 이상이면 돌파 이력 확정
        if qtyNow > 0 and takeMa is not None and px >= float(takeMa):
            if not brokeAboveTakeMa:
                brokeAboveTakeMa = True
                rec["brokeAboveTakeMa"] = True
                _pos[cd] = rec
                _saveState()

        # 위험 청산 우선. 15:18 이평 이탈 익절(SMA10/SMA20)은 쓰지 않는다.
        # 매도 미체결이면 잔량이 안 줄어 같은 손절이 두 번 나가지 않게 막는다.
        rsiMin = float(_params.get("rsiTpMin") or 0)
        needRsi = rsiMin > 0
        rsiNow = None
        if qtyNow >= 1 and avgPx > 0 and px > avgPx and needRsi:
            rsiPer = max(2, int(_params.get("rsiTpPeriod") or 14))
            rsiNow = _liveRsi(cd, px, period=rsiPer)
        pnlPctNow = ((px - avgPx) / avgPx * 100.0) if avgPx > 0 else 0.0
        sellCool = float(_sellFailUntil.get(cd) or 0) > time.time()
        try:
            from trading.crossAlgBuyGuard import getAcntHoldQty

            acntQtyNow = int(getAcntHoldQty(cd) or 0)
        except Exception:
            acntQtyNow = -1
        # 체결 보유(qty>0)만 매도. 계좌 0주·쿨다운이면 주문 자체를 큐에 넣지 않음.
        # 매수 00 체결 전엔 익절·손절·15:15 등 매도 금지 (계좌동기 허수 포지션 차단)
        if (
            qtyNow > 0
            and avgPx > 0
            and (not _hasOrdWait(cd, "SELL"))
            and (not _buyFillPending(cd, "BUY"))
            and (not sellCool)
            and acntQtyNow != 0
        ):
            flatOnly = bool(rec.get("flatExitPending"))
            if flatOnly:
                flatQty = _flatSellQty(qtyNow, acntQtyNow)
                if flatQty >= 1:
                    why = str(rec.get("pendSellReason") or "잔량 전량청산")
                    kind = "FLAT"
                    if "4차" in why:
                        kind = "TP4"
                    elif "트레일" in why:
                        kind = "TRAIL"
                    elif "손절" in why:
                        kind = "HARD"
                    elif "RSI" in why:
                        kind = "TAKE"
                    actions.append((kind, 0, flatQty, px, why))
            scaleStop = tp2Taken or (breakevenAfterTp1 and tp1Taken)
            stopPct = afterHalfStopPct if scaleStop else hardPct
            if (not flatOnly) and px <= avgPx * (1.0 - stopPct / 100.0):
                why = (
                    (
                        f"익절후손절 -{stopPct:g}%"
                        if stopPct
                        else "익절후손절 0%"
                    )
                    if scaleStop
                    else f"손절 -{stopPct:g}% 하드"
                )
                flatQty = _flatSellQty(qtyNow, acntQtyNow)
                if flatQty >= 1:
                    actions.append(("HARD", 0, flatQty, px, why))
            elif (
                (not flatOnly)
                and nowTm >= str(_params.get("nextClosedHalfTm", "151500")).zfill(6)
                and qtyNow >= 1
                and inNextClosedHalfBand(
                    px,
                    avgPx,
                    float(_params.get("nextClosedHalfMinPct", 0)),
                    float(_params.get("nextClosedHalfMaxPct", 3)),
                )
            ):
                # 매일 15:15부터 0~3% 전량. 손실·3% 초과는 들고 간다.
                flatQty = _flatSellQty(qtyNow, acntQtyNow)
                if flatQty >= 1:
                    actions.append(("FLAT", 0, flatQty, px, "15:15 0~3% 전량"))
            elif (not flatOnly) and rsiNow is not None and rsiMin > 0 and rsiNow >= rsiMin:
                # 수익 중 일봉 RSI 과매수 → 부분익절보다 먼저 잔량 전량
                flatQty = _flatSellQty(qtyNow, acntQtyNow)
                if flatQty >= 1:
                    actions.append(("TAKE", 0, flatQty, px, f"익절 RSI{rsiMin:g}"))
            elif (not flatOnly) and qtyNow == 1:
                # 1주는 비율 분할 불가 → 해당 익절% 도달 시 전량
                flatQty = _flatSellQty(qtyNow, acntQtyNow)
                if flatQty >= 1:
                    if (not tp1Taken) and tp1Pct > 0 and pnlPctNow >= tp1Pct:
                        actions.append(
                            ("TP1", 0, flatQty, px, f"1차익절 +{tp1Pct:g}%")
                        )
                    elif (
                        tp1Taken
                        and (not tp2Taken)
                        and tp2Pct > 0
                        and pnlPctNow >= tp2Pct
                    ):
                        actions.append(
                            ("TP2", 0, flatQty, px, f"2차익절 +{tp2Pct:g}%")
                        )
                    elif (
                        tp2Taken
                        and (not tp3Taken)
                        and tp3Pct > 0
                        and pnlPctNow >= tp3Pct
                    ):
                        actions.append(
                            ("TP3", 0, flatQty, px, f"3차익절 +{tp3Pct:g}%")
                        )
                    elif tp1Taken:
                        trailMin = float(_params.get("afterHalfTrailMinPct") or 0)
                        trailDd = float(_params.get("afterHalfTrailTpPct") or 0)
                        maxPnl = float(rec.get("maxPnlPct") or 0)
                        if (
                            trailMin > 0
                            and trailDd > 0
                            and maxPnl + 1e-9 >= trailMin
                            and pnlPctNow <= maxPnl - trailDd + 1e-9
                        ):
                            actions.append(
                                (
                                    "TRAIL",
                                    0,
                                    flatQty,
                                    px,
                                    f"잔량트레일 고점+{maxPnl:g}% -{trailDd:g}%p",
                                )
                            )
                        elif tp4Pct > 0 and pnlPctNow >= tp4Pct:
                            actions.append(
                                ("TP4", 0, flatQty, px, f"4차익절 +{tp4Pct:g}%")
                            )
            elif (not flatOnly) and (not tp1Taken) and tp1Pct > 0 and qtyNow >= 2 and pnlPctNow >= tp1Pct:
                sellQty = scaleOutQty(qtyNow, tp1Ratio)
                if sellQty >= 1:
                    actions.append(("TP1", 0, sellQty, px, f"1차익절 +{tp1Pct:g}%"))
            elif (
                (not flatOnly)
                and tp1Taken
                and (not tp2Taken)
                and tp2Pct > 0
                and qtyNow >= 2
                and pnlPctNow >= tp2Pct
            ):
                sellQty = scaleOutQty(qtyNow, tp2Ratio)
                if sellQty >= 1:
                    actions.append(("TP2", 0, sellQty, px, f"2차익절 +{tp2Pct:g}%"))
            elif (
                (not flatOnly)
                and tp2Taken
                and (not tp3Taken)
                and tp3Pct > 0
                and qtyNow >= 2
                and pnlPctNow >= tp3Pct
            ):
                # 3차: 2차 후 잔량 +tp3Pct 이상이면 보유 50%
                sellQty = scaleOutQty(qtyNow, tp3Ratio)
                if sellQty >= 1:
                    actions.append(("TP3", 0, sellQty, px, f"3차익절 +{tp3Pct:g}%"))
            elif (not flatOnly) and qtyNow >= 1 and (tp3Taken or qtyNow < 2) and (tp2Taken or tp1Taken):
                # 잔량(3차 후 또는 1주라 3차 불가): 트레일 vs 4차 중 먼저
                trailMin = float(_params.get("afterHalfTrailMinPct") or 0)
                trailDd = float(_params.get("afterHalfTrailTpPct") or 0)
                maxPnl = float(rec.get("maxPnlPct") or 0)
                if (
                    trailMin > 0
                    and trailDd > 0
                    and maxPnl + 1e-9 >= trailMin
                    and pnlPctNow <= maxPnl - trailDd + 1e-9
                ):
                    flatQty = _flatSellQty(qtyNow, acntQtyNow)
                    if flatQty >= 1:
                        actions.append(
                            (
                                "TRAIL",
                                0,
                                flatQty,
                                px,
                                f"잔량트레일 고점+{maxPnl:g}% -{trailDd:g}%p",
                            )
                        )
                elif tp4Pct > 0 and pnlPctNow >= tp4Pct:
                    # 4차: 3차 후 잔량 +tp4Pct 이상 전량. 계좌잔량 우선.
                    flatQty = _flatSellQty(qtyNow, acntQtyNow)
                    if flatQty >= 1:
                        actions.append(
                            ("TP4", 0, flatQty, px, f"4차익절 +{tp4Pct:g}%")
                        )
            elif (not flatOnly) and nowTm >= exitTm:
                # 당일 매수가 미수익이면 들고 가지 않는다 (시초 갭 방지)
                eodFlat = bool(_params.get("eodFlatIfNoProfit", True))
                if eodFlat and enteredToday and px <= avgPx:
                    actions.append(("FLAT", 0, qtyNow, px, "당일미수익 15:18"))
                elif stopMa is not None and px < float(stopMa) and (not enteredToday):
                    actions.append(("STOP", 0, qtyNow, px))

        # 절반 익절 종목만 다음날, +N% 이상이면 잔량 절반 추가 (시초 금지시간과 겹치면 안 함)
        if (
            bool(_params.get("halfAddNextDay", False))
            and halfTaken
            and (not rec.get("halfAddDone"))
            and qtyNow >= 2
            and avgPx > 0
            and (not _hasOrdWait(cd, "BUY"))
            and (not _hasOrdWait(cd, "SELL"))
            and (not openTmBan)
            and str(rec.get("halfDt") or "")
            and _todayYmd() > str(rec.get("halfDt"))
            and px >= avgPx * (1.0 + float(_params.get("halfAddMinPnlPct", 5)) / 100.0)
        ):
            addQty = int(qtyNow * float(_params.get("halfAddQtyRatio", 0.5)))
            if addQty < 1:
                addQty = 1
            actions.append(("ADD", 20, addQty, px, "절반다음날재진입"))

        # 당일 대기 매차는 날짜가 바뀌면 무효 (익일 재터치부터 다시)
        if _waitYmd != _todayYmd():
            _waitConfirm = {}
            _waitYmd = _todayYmd()

        queuedBuy = set()
        # 전량 청산 당일이면 신규 매수·확인 대기 금지
        if buyBanned:
            if _waitConfirm.get(cd):
                _disarmConfirm(cd)
                skips.append((0, "REBUY_BANNED"))

        # 터치는 대기만. 즉시 매수하지 않는다
        for step in _params.get("maSteps") or []:
            step = int(step)
            ma = sma.get(step)
            if ma is None:
                continue
            ma = float(ma)
            if not (prevPx > ma and px <= ma):
                continue
            if buyBanned:
                skips.append((step, "REBUY_BANNED"))
                touches.append((step, ma, "REBUY_BANNED"))
                continue
            if openBelowSma:
                skips.append((step, "OPEN_BELOW_SMA20"))
                touches.append((step, ma, "OPEN_BELOW_SMA20"))
                continue
            if step in filled or step in queuedBuy:
                touches.append((step, ma, "FILLED"))
                continue
            if cutOn and nowTm >= buyCut:
                skips.append((step, "AFTER_1400"))
                touches.append((step, ma, "AFTER_1400"))
                continue
            qty = int(buyAmtForStep(_params, step) // px)
            if qty < 1:
                skips.append((step, "QTY0"))
                touches.append((step, ma, "QTY0"))
                continue
            waitRsn = "WAIT_BAR3" if useBar3 else "WAIT_SMA20"
            _armConfirm(cd, step, ma, nowTm)
            skips.append((step, waitRsn))
            touches.append((step, ma, waitRsn))

        # 당일 터치 대기: 3분봉 돌파+다음 양봉, 또는 reclaim 틱 회복
        if (not buyBanned) and (not (cutOn and nowTm >= buyCut)):
            if useBar3:
                if closed3 and (not openTmBan):
                    _tryLiveBar3Buy(
                        cd,
                        closed3,
                        filled,
                        queuedBuy,
                        sma,
                        openBelowSma,
                        actions,
                        skips,
                    )
            else:
                for step in list((_waitConfirm.get(cd) or {}).keys()):
                    step = int(step)
                    if step in filled or step in queuedBuy:
                        continue
                    if openBelowSma:
                        skips.append((step, "OPEN_BELOW_SMA20"))
                        _disarmConfirm(cd, step)
                        continue
                    if openTmBan:
                        skips.append((step, "OPEN_TM_BAN"))
                        continue
                    pend = dict((_waitConfirm.get(cd) or {}).get(step) or {})
                    maPx = float(pend.get("maPx") or 0)
                    if maPx <= 0 and sma.get(step) is not None:
                        maPx = float(sma.get(step))
                    needPx = maPx * (1.0 + reclaimMinPct / 100.0)
                    if maPx <= 0 or px <= needPx:
                        continue
                    qty = int(buyAmtForStep(_params, step) // px)
                    if qty < 1:
                        skips.append((step, "QTY0"))
                        _disarmConfirm(cd, step)
                        continue
                    actions.append(("BUY", step, qty, px))
                    queuedBuy.add(step)
                    _disarmConfirm(cd, step)

        if cutOn and nowTm >= buyCut:
            for step in list((_waitConfirm.get(cd) or {}).keys()):
                skips.append((int(step), "AFTER_1400"))
                _disarmConfirm(cd, int(step))

    for step, reason in skips:
        key = (_todayYmd(), cd, int(step), reason)
        if key in _skipOnce:
            continue
        _skipOnce.add(key)
        _writeAlgEvt(
            "BUY_SKIP",
            cd,
            reason=reason,
            px=px,
            sma=sma,
            note=f"SMA{step}",
        )

    # 터치 요약 — 락 밖에서 DB 반영 (틱 처리 지연 방지)
    if touches:
        try:
            from utils.algTouchSumStore import upsertTouch

            gateMa = sma.get(int((_params or {}).get("stopMa", 20)))
            for step, maPx, skipRsn in touches:
                upsertTouch(
                    _ALG_CD,
                    _todayYmd(),
                    cd,
                    step,
                    px,
                    _nowHms(),
                    maPx=maPx,
                    sma20=gateMa,
                    stkNm=(_pos.get(cd) or {}).get("stkNm") or _lookupStkNm(cd),
                    skipRsn=skipRsn,
                )
        except Exception as err:
            termLog(f"[maTouch] 터치요약 적재 실패 {cd}: {err}")

    if not actions:
        return
    kind, step, qty, fillPx = actions[0][:4]
    key = (cd, kind, int(step))
    with _lock:
        if key in _pending:
            return
        _pending.add(key)
        _ordQ.append((cd, actions[0]))


def prepareMaTouchSession(codes):
    """장 시작·구독 시 SMA·상태 준비."""
    global _params, _paramsAt
    _params = _loadParams()
    _paramsAt = time.time()
    if not _params.get("_enabled", True):
        termLog("[maTouch] 비활성 (settings.maTouchLive.enabled=false)")
        return
    _loadState()
    _restoreWaitFromToday()
    _restoreGoneSellFromToday()
    cds = [normStkCd(c) for c in (codes or []) if normStkCd(c)]
    if not cds:
        return
    _loadSmaForCodes(cds)
    if not isModeLoggedIn() or (
        _params.get("_paperOnly", True) and not isModeLoggedIn("paper")
    ):
        termLog(f"[maTouch] {modeLabel()} 미로그인/불가 — 신호만 대기, 주문 안 함")
    else:
        amts = parseBuyAmtByStep(_params)
        amtTxt = "/".join(
            f"{int(s)}일 {int(amts.get(int(s), 0)):,}"
            for s in (_params.get("maSteps") or [])
        )
        termLog(f"[maTouch] {modeLabel()} 실행 준비 {len(cds)}종 · 매차 {amtTxt}원")


def _ordWorker():
    """주문 큐 처리 — 모의 1초 1회 한도를 여기서 지킨다."""
    while True:
        _enqueueLimitTimeouts()
        item = None
        with _lock:
            if _ordQ:
                item = _ordQ.pop(0)
        if item is None:
            time.sleep(0.05)
            continue
        cd, action = item
        kind, step, qty, fillPx = action[:4]
        extra = action[4] if len(action) > 4 else ""
        key = (cd, kind, int(step))
        try:
            if kind == "BUY":
                maPx = None
                if extra not in (None, ""):
                    try:
                        maPx = float(extra)
                    except (TypeError, ValueError):
                        maPx = None
                _placeBuy(cd, qty, fillPx, step, maPx=maPx)
            elif kind == "ADD":
                _placeBuy(cd, qty, fillPx, step or 20, isAdd=True)
            elif kind == "LIMIT_TO":
                _limitTimeoutThenMarket(cd, step, extra)
            elif kind == "TP1":
                # 익절 플래그는 00 매도체결에서만 (주문접수 성공과 분리)
                _placeSell(cd, qty, fillPx, extra or "1차익절")
            elif kind == "TP2":
                _placeSell(cd, qty, fillPx, extra or "2차익절")
            elif kind == "TP3":
                _placeSell(cd, qty, fillPx, extra or "3차익절")
            elif kind == "TP4":
                _placeSell(cd, qty, fillPx, extra or "4차익절")
            elif kind == "HALF":
                _placeSell(cd, qty, fillPx, extra or "2차익절")
            elif kind == "HARD":
                _placeSell(cd, qty, fillPx, extra or "손절 하드")
            elif kind == "STOP":
                _placeSell(cd, qty, fillPx, "손절 SMA20 이탈")
            elif kind == "FLAT":
                _placeSell(cd, qty, fillPx, extra or "당일미수익 15:18")
            elif kind == "TAKE":
                # RSI 등 잔량 전량 익절. 15:18 SMA10/SMA20 이탈 익절은 사용하지 않음
                _placeSell(cd, qty, fillPx, extra or "익절")
        except Exception as err:
            termLog(f"[maTouch] 주문워커 오류 {cd} {kind}: {err}")
        finally:
            with _lock:
                _pending.discard(key)


def _smaInt(raw):
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _stepLabel(step):
    """관심종목 상태열용. 5 → 5이평."""
    return f"{int(step)}이평"


def _priceOrderStatus(px, sma5, sma10, sma20):
    """
    현재가·5/10/20이평을 큰 순서로 부등호 나열.
    예: 20 > 10 > 현재 > 5
    """
    items = []
    try:
        if px is not None and float(px) > 0:
            items.append(("현재", float(px)))
    except (TypeError, ValueError):
        pass
    for name, raw in (("5", sma5), ("10", sma10), ("20", sma20)):
        try:
            if raw is None:
                continue
            items.append((name, float(raw)))
        except (TypeError, ValueError):
            continue
    if not items:
        return "이평없음"
    # 큰 값 우선. 동률이면 이름 순(표시 안정)
    # 이름: 현재 / 5 / 10 / 20 (이평 글자 생략)
    items.sort(key=lambda x: (-x[1], x[0]))
    parts = [items[0][0]]
    for i in range(1, len(items)):
        op = "=" if items[i][1] == items[i - 1][1] else ">"
        parts.append(op)
        parts.append(items[i][0])
    return " ".join(parts)


def _nearestStep(px, sma, steps):
    """현재가와 이평 거리가 가장 짧은 매차. 동률이면 짧은 이평 우선."""
    best = None
    bestDist = None
    for step in steps:
        ma = sma.get(int(step))
        if ma is None:
            continue
        dist = abs(float(px) - float(ma))
        if best is None or dist < bestDist:
            best = int(step)
            bestDist = dist
    return best


def _buyWaitReason(px, sma, filled, nowTm, waitMap=None):
    """
    관심종목 상태열.
    매수한 매차: '20이평 매수'
    터치 후 당일 3분 돌파·양봉 대기: '20이평 3분돌파대기' / '20이평 3분양봉대기'
    대기: 현재가에 가장 가까운 미매수 이평 '20이평 매수대기'
    """
    p = _params or {}
    steps = [int(s) for s in (p.get("maSteps") or [20])]
    takeMa = int(p.get("takeMa", 10))
    stopMa = int(p.get("stopMa", 20))
    buyCut = str(p.get("buyCutoffTm", "140000")).zfill(6)
    cutOn = _buyCutoffOn()
    if not sma:
        return "이평없음"
    # 매수 이평 + 익절(take)·손절(stop) 이평이 있어야 상태 판정 가능
    need = list(steps) + [takeMa, stopMa]
    if any(sma.get(s) is None for s in need):
        return "이평미완"
    filledSet = {int(s) for s in (filled or [])}
    unfilled = [s for s in steps if s not in filledSet]
    buyTxt = ", ".join(f"{_stepLabel(s)} 매수" for s in steps if s in filledSet)
    if not unfilled:
        return buyTxt or "매수완료"
    if px is None or px <= 0:
        waitTxt = "시세대기"
    elif cutOn and nowTm >= buyCut:
        waitTxt = "14시이후"
    else:
        rawWait = waitMap or {}
        waitSet = []
        for k in rawWait.keys():
            try:
                n = int(k)
            except (TypeError, ValueError):
                continue
            if n not in filledSet:
                waitSet.append(n)
        if waitSet:
            near = _nearestStep(px, sma, waitSet) or waitSet[0]
            if confirmModeIsBar3(p):
                waitTxt = f"{_stepLabel(near)} 3분양봉돌파대기"
            else:
                waitTxt = f"{_stepLabel(near)} 회복대기"
        else:
            near = _nearestStep(px, sma, unfilled)
            if near is None:
                near = unfilled[0]
            waitTxt = f"{_stepLabel(near)} 매수대기"
    if buyTxt:
        return f"{buyTxt} · {waitTxt}"
    return waitTxt


def getMaTouchEvalMap():
    """매매창 관심종목 그리드용. {stkCd: sma5/10/20, reason}."""
    _refreshParams()
    nowTm = _nowHms()
    pxMap = {}
    try:
        from trading.watchRtMonitor import getWatchLastPxMap

        pxMap = getWatchLastPxMap() or {}
    except Exception:
        pxMap = {}
    with _lock:
        out = {}
        for cd, sma in (_smaMap or {}).items():
            rec = _pos.get(cd) or {}
            px = _lastPx.get(cd)
            if px is None or px <= 0:
                tick = pxMap.get(cd) or {}
                px = _absPrice(
                    tick.get("curPrc") or tick.get("krxPrc") or tick.get("nxtPrc")
                )
            sma5 = _smaInt((sma or {}).get(5))
            sma10 = _smaInt((sma or {}).get(10))
            sma20 = _smaInt((sma or {}).get(20))
            out[cd] = {
                "sma5": sma5,
                "sma10": sma10,
                "sma20": sma20,
                # 상태열: 가격 크기 순서 (매수대기 문구는 buyWait 로 별도)
                "reason": _priceOrderStatus(px, sma5, sma10, sma20),
                "buyWait": _buyWaitReason(
                    px,
                    sma or {},
                    rec.get("filled") or [],
                    nowTm,
                    _waitConfirm.get(cd) or {},
                ),
            }
        return out


def startMaTouchLive():
    """프로세스 기동 시 원장 미청산을 읽어둠. SMA는 장 시작 전/구독 시 채운다."""
    global _params, _paramsAt, _workerStarted, _acntSyncStarted
    _params = _loadParams()
    _paramsAt = time.time()
    # v41: 1차 +1.5%. MFE 통계로 tp1 을 덮지 않는다.
    if bool(_params.get("halfTpFromStat", False)):
        try:
            from utils.maTouchMfeStats import refreshHalfTpFromLiveTrades

            got = refreshHalfTpFromLiveTrades(force=False)
            if got is not None:
                _params = _loadParams()
                _paramsAt = time.time()
        except Exception as err:
            termLog(f"[maTouch] 1차익절 통계 시드 실패: {err}")
    _loadState()
    _restoreWaitFromToday()
    _restoreGoneSellFromToday()
    if not _workerStarted:
        _workerStarted = True
        threading.Thread(target=_ordWorker, name="maTouchOrd", daemon=True).start()
    # UI 유무와 무관하게 계좌 보유·수량을 주기 동기 (체결 기준 포지션 보정)
    if not _acntSyncStarted:
        _acntSyncStarted = True
        threading.Thread(target=_acntSyncLoop, name="maTouchAcnt", daemon=True).start()
        # 기동 직후 1회
        threading.Thread(
            target=lambda: (time.sleep(2.0), _fetchAndSyncAcnt(force=True)),
            name="maTouchAcntBoot",
            daemon=True,
        ).start()
    termLog(
        f"[maTouch] 기동 — "
        f"{'ON' if _params.get('_enabled') else 'OFF'} · {modeLabel()}"
        f"{'(paperOnly)' if _params.get('_paperOnly') else ''}"
        f" · 체결기준 원장 · 계좌동기 {_ACNT_SYNC_SEC:g}초"
        f" · 1차 +{float(_params.get('tp1Pct') or 1.5):g}% 30%"
        f" · 2차 +{float(_params.get('tp2Pct') or 2):g}% 30%"
        f" · RSI≥{float(_params.get('rsiTpMin') or 70):g} 전량"
        f" · 3차 +{float(_params.get('tp3Pct') or 4):g}% 50%"
        f" · 4차 +{float(_params.get('tp4Pct') or 7):g}% 전량"
        f" · 1·2차후 본전전량"
    )
