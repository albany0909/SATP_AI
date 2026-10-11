"""
프로그램(봇)이 낸 주문 기록 — 수동(HTS/MTS) 체결과 구분하기 위한 공용 레지스트리.

api.buyStk / api.sellStk 가 주문 직전 inflight, 성공 후 주문번호를 남긴다.
체결(00)이 주문 응답보다 먼저 와도 inflight(종목·방향, 최근 N초)로 봇 주문으로 본다.
"""

from __future__ import annotations

import threading
import time

from utils.stkCdUtil import normStkCd

_lock = threading.Lock()
_ordNos = {}      # 정규화 주문번호 → {cd, side, at}
_inflight = {}    # (cd, side) → 마지막 주문 시도 시각
_KEEP_SEC = 3 * 86400
_INFLIGHT_SEC = 180


def normOrdNo(raw):
    text = str(raw or "").strip()
    return text.lstrip("0") or ("0" if text else "")


def _prune(now):
    for k in [k for k, v in _ordNos.items() if now - v["at"] > _KEEP_SEC]:
        _ordNos.pop(k, None)
    for k in [k for k, t in _inflight.items() if now - t > _KEEP_SEC]:
        _inflight.pop(k, None)


def noteInflight(stkCd, side):
    cd = normStkCd(stkCd)
    if not cd:
        return
    with _lock:
        _inflight[(cd, str(side).upper())] = time.time()


def noteOrdNo(ordNo, stkCd, side):
    no = normOrdNo(ordNo)
    if not no:
        return
    now = time.time()
    with _lock:
        _ordNos[no] = {"cd": normStkCd(stkCd), "side": str(side).upper(), "at": now}
        _prune(now)


def isBotOrd(ordNo, stkCd="", side="", inflightSec=_INFLIGHT_SEC):
    """봇 주문이면 True. 주문번호 일치 또는 같은 종목·방향 최근 주문 시도."""
    no = normOrdNo(ordNo)
    now = time.time()
    with _lock:
        if no and no in _ordNos:
            return True
        cd = normStkCd(stkCd)
        if cd and side:
            t = _inflight.get((cd, str(side).upper()))
            if t and now - t <= inflightSec:
                return True
    return False


def clearForTest():
    with _lock:
        _ordNos.clear()
        _inflight.clear()
