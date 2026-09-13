"""
rsv {시간} {명령어} / rsv once {시간} {명령어}
rsv list / rsv remove {일련번호} / rsv remove all
예약 명령 등록·조회·삭제
"""

from config.rsvStore import addSchedule, clearSchedule, loadSchedule, removeSchedule
from config.settingsStore import parseHm
from utils.termLog import termLog

# 예약으로 넣으면 위험한/무의미한 명령 — 등록 자체를 막음
_BLOCKED_CMDS = {"rsv", "off"}


def _usage():
    return (
        "사용법:\n"
        "rsv {시간} {명령어} — 예약 추가 (월~금 매일)\n"
        "  예) rsv 10:00 buy 005930 5\n"
        "rsv once {시간} {명령어} — 1회만 실행\n"
        "  예) rsv once 10:00 buy 005930 5\n"
        "rsv list — 예약 목록\n"
        "rsv remove {일련번호} — 예약 삭제\n"
        "rsv remove all — 전체 삭제"
    )


def _onceLabel(once):
    """목록/메시지용 반복 구분 라벨."""
    return "1회" if once else "매일"


def handleRsvList():
    """예약 목록을 일련번호와 함께 반환한다."""
    items = loadSchedule()
    if not items:
        msg = "[예약] 등록된 예약이 없습니다."
        termLog(msg)
        return msg

    lines = ["[예약 목록] (월~금 실행)"]
    for idx, item in enumerate(items, start=1):
        kind = _onceLabel(item.get("once", False))
        lines.append(f"{idx}. [{kind}] {item['runHm']} — {item['cmdText']}")
    msg = "\n".join(lines)
    termLog(msg)
    return msg


def handleRsvRemoveAll():
    """모든 예약을 삭제한다."""
    count = clearSchedule()
    if count == 0:
        msg = "[예약] 삭제할 예약이 없습니다."
    else:
        msg = f"[예약] 전체 삭제 완료 — {count}건"
    termLog(msg)
    return msg


def handleRsvRemove(serialRaw):
    """1-based 일련번호로 예약을 삭제한다. 'all'이면 전체 삭제."""
    raw = (serialRaw or "").strip()
    if raw.lower() == "all":
        return handleRsvRemoveAll()

    if not raw.isdigit():
        return (
            "일련번호는 숫자이거나 all 이어야 합니다. "
            f"예) rsv remove 1 / rsv remove all\n\n{_usage()}"
        )

    serialNo = int(raw)
    ok, removed = removeSchedule(serialNo)
    if not ok or removed is None:
        failMsg = f"[예약] 일련번호 {serialNo} 항목이 없습니다. rsv list 로 확인하세요."
        termLog(failMsg)
        return failMsg

    kind = _onceLabel(removed.get("once", False))
    okMsg = (
        f"[예약] 삭제 완료 — #{serialNo} [{kind}] "
        f"{removed['runHm']} — {removed['cmdText']}"
    )
    termLog(okMsg)
    return okMsg


def handleRsvAdd(timeRaw, cmdParts, once=False):
    """
    시각 + 명령어 조각으로 예약을 추가한다.
    once=True면 1회만 실행.
    cmdParts: ['buy', '005930', '5'] 처럼 공백으로 나뉜 토큰
    """
    runHm = parseHm(timeRaw)
    if runHm is None:
        example = "rsv once 10:00 buy 005930 5" if once else "rsv 10:00 buy 005930 5"
        return (
            "시각 형식이 올바르지 않습니다. 24시간제 HH:MM을 사용하세요.\n"
            f"예) {example}\n\n{_usage()}"
        )

    if not cmdParts:
        return f"예약할 명령어를 입력하세요.\n\n{_usage()}"

    cmdText = " ".join(str(p) for p in cmdParts).strip()
    if not cmdText:
        return f"예약할 명령어를 입력하세요.\n\n{_usage()}"

    firstCmd = cmdText.split()[0].lower()
    if firstCmd in _BLOCKED_CMDS:
        failMsg = f"[예약] '{firstCmd}' 명령은 예약할 수 없습니다."
        termLog(failMsg)
        return failMsg

    serialNo = addSchedule(runHm, cmdText, once=once)
    kind = _onceLabel(once)
    if once:
        hint = f"(월~금 중 다음 {runHm}에 1회 실행 후 삭제)"
    else:
        hint = f"(월~금 매일 {runHm}에 실행)"
    okMsg = f"[예약] 등록 완료 — #{serialNo} [{kind}] {runHm} — {cmdText}\n{hint}"
    termLog(okMsg)
    return okMsg


def handleRsv(rawParts):
    """
    rsv 하위 명령 분기.
    rawParts: 전체 토큰 ['rsv', ...] (원문 split 결과)
    """
    if len(rawParts) < 2:
        return _usage()

    sub = rawParts[1].lower()

    if sub == "list":
        return handleRsvList()

    if sub == "remove":
        serialRaw = rawParts[2] if len(rawParts) >= 3 else ""
        return handleRsvRemove(serialRaw)

    if sub == "once":
        # rsv once {시간} {명령어...}
        timeRaw = rawParts[2] if len(rawParts) >= 3 else ""
        cmdParts = rawParts[3:] if len(rawParts) >= 4 else []
        return handleRsvAdd(timeRaw, cmdParts, once=True)

    # rsv {시간} {명령어...} — 매일
    timeRaw = rawParts[1]
    cmdParts = rawParts[2:]
    return handleRsvAdd(timeRaw, cmdParts, once=False)
