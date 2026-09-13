"""tpr / 익절 {기준} — 익절 비율(%) 설정 명령"""

from config.settingsStore import getTprPct, setTprPct
from utils.termLog import termLog


def handleTpr(pctRaw=""):
    """
    익절 기준(%)을 설정하거나, 인자 없으면 현재 값을 안내한다.
    양수/음수 입력 모두 양수로 저장한다. 예) tpr 5 → 5%
    """
    usage = (
        "사용법: tpr {익절 기준} 또는 익절 {익절 기준}\n"
        "예) tpr 5 — 수익률 5% 도달 시 익절\n"
        "인자 없이 입력하면 현재 값을 표시합니다."
    )
    raw = (pctRaw or "").strip()
    if not raw:
        cur = getTprPct()
        msg = f"[익절] 현재 기준: {cur}%"
        termLog(msg)
        return msg

    ok, stored = setTprPct(raw)
    if not ok:
        failMsg = f"익절 기준은 0이 아닌 숫자여야 합니다. 예) tpr 5\n\n{usage}"
        termLog(failMsg)
        return failMsg

    okMsg = f"[익절] 설정 완료: {stored}% 도달 시 익절"
    termLog(okMsg)
    return okMsg
