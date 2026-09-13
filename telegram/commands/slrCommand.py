"""slr / 손절 {기준} — 손절 비율(%) 설정 명령"""

from config.settingsStore import getSlrPct, setSlrPct
from utils.termLog import termLog


def handleSlr(pctRaw=""):
    """
    손절 기준(%)을 설정하거나, 인자 없으면 현재 값을 안내한다.
    양수/음수 입력 모두 음수로 저장한다. 예) slr 10 → -10%
    """
    usage = (
        "사용법: slr {손절 기준} 또는 손절 {손절 기준}\n"
        "예) slr 10 — 수익률 -10% 도달 시 손절\n"
        "인자 없이 입력하면 현재 값을 표시합니다."
    )
    raw = (pctRaw or "").strip()
    if not raw:
        cur = getSlrPct()
        msg = f"[손절] 현재 기준: {cur}%"
        termLog(msg)
        return msg

    ok, stored = setSlrPct(raw)
    if not ok:
        failMsg = f"손절 기준은 0이 아닌 숫자여야 합니다. 예) slr 10\n\n{usage}"
        termLog(failMsg)
        return failMsg

    okMsg = f"[손절] 설정 완료: {stored}% 도달 시 손절"
    termLog(okMsg)
    return okMsg
