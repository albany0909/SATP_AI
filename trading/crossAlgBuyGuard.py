"""
복수 전략 병행 시 동일 종목 신규매수 차단.

- 다른 알고리즘 원장 미청산이 있으면 스킵
- 계좌(kt00004)에 잔량이 있고, 요청 alg 원장에 미청산이 없으면 스킵
  (수동·기타 보유 포함 / 본인 전략 추가매수는 허용)
"""

from __future__ import annotations

import threading

from strategies.specLoader import normAlgCd
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_lock = threading.Lock()
# 계좌 잔량>0 종목 및 수량 (kt00004 캐시)
_acntHoldCds = set()
_acntHoldQty = {}


def noteAcntHoldings(body):
    """kt00004 본문으로 보유 종목·수량을 갱신한다."""
    global _acntHoldCds, _acntHoldQty
    from api.getAcntEvlt import parsePadNum

    cds = set()
    qtyMap = {}
    rawList = (body or {}).get("stk_acnt_evlt_prst") or []
    if isinstance(rawList, list):
        for item in rawList:
            if not isinstance(item, dict):
                continue
            cd = normStkCd(item.get("stk_cd"))
            qty = parsePadNum(item.get("rmnd_qty")) or 0
            if cd and int(qty) > 0:
                cds.add(cd)
                qtyMap[cd] = int(qty)
    with _lock:
        _acntHoldCds = cds
        _acntHoldQty = qtyMap


def getAcntHoldCds():
    with _lock:
        return set(_acntHoldCds)


def getAcntHoldQty(stkCd):
    """계좌평가 캐시 잔량. 없으면 0 (미동기·미보유)."""
    cd = normStkCd(stkCd)
    if not cd:
        return 0
    with _lock:
        return int(_acntHoldQty.get(cd) or 0)


def _openAlgCdsForStk(stkCd):
    """종목의 미청산 원장 algCd 목록."""
    from utils.stratOrdLedgerStore import listOpenLedgerRows

    cd = normStkCd(stkCd)
    out = []
    seen = set()
    for row in listOpenLedgerRows(None) or []:
        if normStkCd(row.get("STK_CD")) != cd:
            continue
        alg = normAlgCd(row.get("ALG_CD"))
        if not alg or alg in seen:
            continue
        seen.add(alg)
        out.append(alg)
    return out


def _otherLivePending(myAlg, stkCd):
    """
    원장 반영 전 주문대기·메모리 포지션.
    현재는 MTS01(이평터치)만 인메모리 보유/대기 검사.
    """
    if myAlg == "MTS01":
        return False, ""
    try:
        from trading.maTouchLive import holdsOrPendingBuy

        if holdsOrPendingBuy(stkCd):
            return True, "MTS01_LIVE"
    except Exception:
        pass
    return False, ""


def isNewBuyBlocked(algCd, stkCd):
    """
    신규매수 차단 여부.
    returns (blocked: bool, reason: str)
    reason 예: OTHER_ALG:MTS01 / ACNT_HOLD / MTS01_LIVE
    """
    myAlg = normAlgCd(algCd)
    cd = normStkCd(stkCd)
    if not myAlg or not cd:
        return False, ""

    openAlgs = _openAlgCdsForStk(cd)
    others = [a for a in openAlgs if a != myAlg]
    if others:
        return True, f"OTHER_ALG:{others[0]}"

    pending, pendReason = _otherLivePending(myAlg, cd)
    if pending:
        return True, pendReason

    myHas = myAlg in openAlgs
    # 본인 원장 미청산이면 추가매수(스케일) 허용 — 계좌만으로 막지 않음
    if myHas:
        return False, ""

    with _lock:
        inAcnt = cd in _acntHoldCds
    if inAcnt:
        return True, "ACNT_HOLD"

    return False, ""


def skipReasonLabel(reason):
    """로그·알림용 짧은 문구."""
    r = str(reason or "")
    if r.startswith("OTHER_ALG:"):
        return f"다른전략보유({r.split(':', 1)[1]})"
    if r == "ACNT_HOLD":
        return "계좌보유"
    if r == "MTS01_LIVE":
        return "이평터치보유/주문중"
    return r or "중복보유"


def logBuySkip(algTag, stkCd, reason):
    termLog(
        f"[{algTag}] 복수전략 신규매수스킵 {normStkCd(stkCd)} · {skipReasonLabel(reason)}"
    )
