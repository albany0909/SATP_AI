"""
cnsr / 조건식 — 목록조회·실시간 시작/중지
  cnsr                     — 조건식 목록 (CNSRLST)
  cnsr start {seq}         — 실시간 종목조회 시작 (CNSRREQ)
  cnsr stop [seq|all]      — 실시간 해제 (CNSRCLR)
  cnsr status              — 감시 중 조건식 상태
"""

from config import runtimeState
from realtime.cnsrList import fetchCnsrList
from realtime.cnsrRealtime import (
    getCnsrRealtimeStatus,
    startCnsrRealtime,
    stopCnsrRealtime,
)
from utils.termLog import termLog


def formatCnsrListMsg(items, modeLabel=""):
    """조건식 목록을 텔레그램용 텍스트로."""
    title = "[조건식 목록]"
    if modeLabel:
        title = f"{title} ({modeLabel})"
    if not items:
        return (
            f"{title}\n등록된 조건식이 없습니다.\n"
            f"(영웅문4에서 조건식을 먼저 만드세요)"
        )

    lines = [
        title,
        f"총 {len(items)}건",
        "실시간: cnsr start {일련번호}",
    ]
    for item in items:
        seq = item.get("seq", "-")
        name = item.get("name", "-")
        lines.append(f"  {seq}. {name}")
    return "\n".join(lines)


def _usage():
    return (
        "사용법:\n"
        "cnsr — 조건식 목록\n"
        "cnsr start {일련번호} — 실시간 종목조회 시작\n"
        "  예) cnsr start 114\n"
        "cnsr stop {일련번호} — 해당 조건 실시간 해제\n"
        "cnsr stop all — 전부 해제\n"
        "cnsr status — 감시 상태"
    )


def handleCnsr(rawParts=None):
    """
    cnsr / 조건식 명령 분기.
    rawParts: ['cnsr'] 또는 ['cnsr','start','114'] …
    """
    parts = list(rawParts or [])
    # 첫 토큰은 명령어
    args = [p for p in parts[1:]] if parts else []

    if not args:
        return _handleList()

    sub = str(args[0]).strip().lower()

    if sub in ("status", "상태"):
        msg = getCnsrRealtimeStatus()
        termLog(msg)
        return msg

    if sub in ("start", "시작", "run"):
        seq = args[1] if len(args) >= 2 else ""
        ok, msg = startCnsrRealtime(seq)
        termLog(msg)
        return msg

    if sub in ("stop", "중지", "해제"):
        seq = args[1] if len(args) >= 2 else "all"
        ok, msg = stopCnsrRealtime(seq)
        termLog(msg)
        return msg

    # cnsr 114 처럼 seq만 넣은 경우 → start
    if sub.isdigit():
        ok, msg = startCnsrRealtime(sub)
        termLog(msg)
        return msg

    return _usage()


def _handleList():
    result = fetchCnsrList(timeoutSec=15)
    if not result.get("ok"):
        failMsg = f"[조건식실패] {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    modeLabel = "모의투자" if runtimeState.isPaperLoggedIn() else "실투자"
    msg = formatCnsrListMsg(result.get("items") or [], modeLabel=modeLabel)
    termLog(msg)
    return msg
