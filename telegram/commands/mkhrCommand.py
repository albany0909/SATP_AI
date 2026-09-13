"""mkhr {시작시간} {종료시간} — 장 시간(정규 거래시간) 설정 명령"""

from config.settingsStore import getMktHours, parseHm, setMktHours
from utils.termLog import termLog


def handleMkhr(openRaw, closeRaw):
    """
    장 시작·종료 시각을 설정하거나, 인자 없으면 현재 값을 안내한다.
    24시간제 HH:MM. 시작 < 종료 이어야 한다.
    """
    usage = (
        "사용법: mkhr {시작시간} {종료시간}\n"
        "예) mkhr 9:30 15:00\n"
        "인자 없이 mkhr 만 입력하면 현재 설정을 표시합니다."
    )

    openText = (openRaw or "").strip()
    closeText = (closeRaw or "").strip()

    # 인자 없음 → 현재 설정 조회
    if not openText and not closeText:
        openHm, closeHm = getMktHours()
        msg = f"[장시간] 현재 설정: {openHm} ~ {closeHm}"
        termLog(msg)
        return msg

    # 한쪽만 있으면 사용법
    if not openText or not closeText:
        return usage

    openHm = parseHm(openText)
    closeHm = parseHm(closeText)
    if openHm is None or closeHm is None:
        failMsg = (
            "시각 형식이 올바르지 않습니다. 24시간제 HH:MM을 사용하세요.\n"
            f"예) mkhr 9:30 15:00\n\n{usage}"
        )
        termLog(failMsg)
        return failMsg

    # 시작이 종료보다 같거나 늦으면 의미 없는 구간
    if openHm >= closeHm:
        failMsg = (
            f"시작시간({openHm})은 종료시간({closeHm})보다 빨라야 합니다.\n\n{usage}"
        )
        termLog(failMsg)
        return failMsg

    setMktHours(openHm, closeHm)
    okMsg = f"[장시간] 설정 완료: {openHm} ~ {closeHm}"
    termLog(okMsg)
    return okMsg
