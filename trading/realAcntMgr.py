"""
실계좌관리 (RAM01).

계좌 잔량>0 종목은 관심(모니터링)으로만 둔다.
매매 버튼을 눌러 NOTE=TRADE 가 된 종목만 좌측 보유·주문 대상이다.
해지는 다시 WATCH. 잔량이 0이 되면 관심에서도 뺀다.
추가 매수·신규 보유는 계좌 조회(kt00004) 동기화 때 WATCH 로 붙인다.
"""

from __future__ import annotations

import threading

from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

ALG_CD = "RAM01"
NOTE_WATCH = "WATCH"
NOTE_TRADE = "TRADE"

_lock = threading.Lock()
# stkCd → WATCH | TRADE
_notes = {}
# stkCd → 종목명 (이번 계좌 스냅샷)
_holds = {}


def _noteOf(raw):
    text = str(raw or "").strip().upper()
    if text == NOTE_TRADE:
        return NOTE_TRADE
    return NOTE_WATCH


def _loadDbNotes():
    """활성 RAM01 메모. DB 실패 시 {}."""
    try:
        from utils.algStkStore import listAlgStk

        out = {}
        for row in listAlgStk(ALG_CD, limit=2000) or []:
            cd = normStkCd(row.get("stkCd") or row.get("STK_CD"))
            if not cd:
                continue
            out[cd] = _noteOf(row.get("note"))
        return out
    except Exception as err:
        termLog(f"[실계좌] 관리종목 조회 실패: {err}")
        return {}


def _writeNote(cd, note, stkNm):
    try:
        from utils.algStkStore import setAlgStkNote

        return setAlgStkNote(ALG_CD, cd, note, stkNm=stkNm or "", src="ACNT")
    except Exception as err:
        termLog(f"[실계좌] 메모 저장 실패 {cd}: {err}")
        return {"ok": False, "errMsg": str(err)}


def syncAcntHolds(rows):
    """
    kt00004 잔량>0 목록과 관심을 맞춘다.
    - 없던 종목·다시 잔량이 생긴 종목은 WATCH (매매 자동 등록 안 함)
    - 이미 TRADE 인 보유는 TRADE 유지
    - 잔량이 사라진 종목은 관심에서 해제 (전략 매도·전량 청산)
    반환: {ok, changed, added, removed, tradeCnt, watchCnt}
    """
    global _notes, _holds
    incoming = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cd = normStkCd(row.get("stkCd"))
        try:
            qty = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not cd or qty <= 0:
            continue
        incoming[cd] = str(row.get("stkNm") or "").strip()

    dbNotes = _loadDbNotes()
    added = []
    removed = []
    with _lock:
        prev = dict(_notes)
        newNotes = {}
        for cd, nm in incoming.items():
            # 메모리 TRADE 가 우선. 재기동 직후는 DB. 신규·비활성 재입고는 WATCH.
            if prev.get(cd) == NOTE_TRADE or (
                cd not in prev and dbNotes.get(cd) == NOTE_TRADE
            ):
                note = NOTE_TRADE
            else:
                note = NOTE_WATCH
            newNotes[cd] = note
            if dbNotes.get(cd) != note:
                _writeNote(cd, note, nm)
                if cd not in dbNotes and cd not in prev:
                    added.append(cd)
        for cd in dbNotes:
            if cd not in newNotes:
                removed.append(cd)
        if removed:
            try:
                from utils.algStkStore import deactivateAlgStkExcept

                deactivateAlgStkExcept(ALG_CD, list(newNotes.keys()))
            except Exception as err:
                termLog(f"[실계좌] 청산 종목 해제 실패: {err}")
        changed = prev != newNotes or bool(added) or bool(removed)
        _notes = newNotes
        _holds = dict(incoming)
        tradeCnt = sum(1 for n in newNotes.values() if n == NOTE_TRADE)
        watchCnt = len(newNotes) - tradeCnt
    if added or removed:
        termLog(
            f"[실계좌] 동기화 추가 {len(added)} · 제외 {len(removed)} "
            f"· 모니터링 {watchCnt} · 매매 {tradeCnt}"
        )
    return {
        "ok": True,
        "changed": changed,
        "added": added,
        "removed": removed,
        "tradeCnt": tradeCnt,
        "watchCnt": watchCnt,
    }


def isTradeOn(stkCd):
    """매매 버튼으로 등록된 보유만 True."""
    cd = normStkCd(stkCd)
    if not cd:
        return False
    with _lock:
        return _notes.get(cd) == NOTE_TRADE


def isWatchHold(stkCd):
    """이번 계좌 스냅샷에 있는 보유."""
    cd = normStkCd(stkCd)
    if not cd:
        return False
    with _lock:
        return cd in _holds


def listWatchCds():
    with _lock:
        return list(_holds.keys())


def acntOrderBlockReason(stkCd):
    """
    계좌에 있는데 매매 등록 전이면 주문 사유를 반환한다.
    보유가 아닌 종목(신규 매수)은 이 관문에서 막지 않는다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return ""
    with _lock:
        if cd not in _holds:
            return ""
        if _notes.get(cd) == NOTE_TRADE:
            return ""
    return "RAM_WATCH_ONLY"


def registerTrade(stkCd):
    """관심 보유를 매매 대상으로 올린다. 계좌에 있을 때만."""
    cd = normStkCd(stkCd)
    if not cd:
        return {"ok": False, "errMsg": "종목코드 없음"}
    with _lock:
        if cd not in _holds:
            return {"ok": False, "errMsg": "계좌 보유가 아닙니다"}
        nm = _holds.get(cd) or ""
        _notes[cd] = NOTE_TRADE
    got = _writeNote(cd, NOTE_TRADE, nm)
    if not got.get("ok"):
        return got
    termLog(f"[실계좌] 매매 등록 {cd} {nm}")
    return {"ok": True, "stkCd": cd, "note": NOTE_TRADE}


def unregisterTrade(stkCd):
    """매매 해제. 관심 모니터링만 남긴다."""
    cd = normStkCd(stkCd)
    if not cd:
        return {"ok": False, "errMsg": "종목코드 없음"}
    with _lock:
        if cd not in _holds and cd not in _notes:
            return {"ok": False, "errMsg": "관리 종목이 아닙니다"}
        nm = _holds.get(cd) or ""
        held = cd in _holds
        if held:
            _notes[cd] = NOTE_WATCH
        else:
            _notes.pop(cd, None)
    if held:
        got = _writeNote(cd, NOTE_WATCH, nm)
        if not got.get("ok"):
            return got
    termLog(f"[실계좌] 매매 해지 {cd} {nm}")
    return {"ok": True, "stkCd": cd, "note": NOTE_WATCH}
