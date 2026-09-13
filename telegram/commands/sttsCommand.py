"""stts / stts default — settings.json 조회·디폴트 복원 명령"""

from config.settingsStore import loadSettings, resetSettingsToDefault
from utils.termLog import termLog

# 알려진 설정 키 → 한글 라벨 (신규 키는 키 이름 그대로 표시)
_KEY_LABELS = {
    "mktOpenHm": "장 시작(매매)",
    "mktCloseHm": "장 종료(매매)",
    "notifyOpenHm": "알림 시작",
    "notifyCloseHm": "알림 종료",
    "tprPct": "익절",
    "slrPct": "손절",
}


def formatSettingsMsg(settings, title="[설정]"):
    """
    settings dict를 텔레그램/터미널용 텍스트로 만든다.
    알려진 키는 한글 라벨+키명, 그 외는 키명만 표시한다.
    """
    lines = [title]
    if not settings:
        lines.append("(저장된 설정 없음)")
        return "\n".join(lines)

    for key, val in settings.items():
        label = _KEY_LABELS.get(key)
        # 퍼센트 설정은 % 단위로 표시
        if key in ("tprPct", "slrPct"):
            disp = f"{val}%"
        else:
            disp = val
        if label:
            lines.append(f"{label}({key}): {disp}")
        else:
            # 이후 추가되는 키도 누락 없이 출력
            lines.append(f"{key}: {disp}")
    return "\n".join(lines)


def handleStts():
    """settings.json 현재 값을 모두 조회해 결과 메시지를 반환한다."""
    settings = loadSettings()
    msg = formatSettingsMsg(settings)
    termLog(msg)
    return msg


def handleSttsDefault():
    """
    모든 세팅을 settings-default.json 값으로 되돌린다.
    복원된 설정 목록을 함께 안내한다.
    """
    settings = resetSettingsToDefault()
    msg = formatSettingsMsg(settings, title="[설정] 디폴트로 복원했습니다.")
    termLog(msg)
    return msg


def handleSttsCmd(subRaw=""):
    """
    stts 하위 분기.
    인자 없음: 조회 / default|def: 디폴트 복원
    """
    sub = (subRaw or "").strip().lower()
    if not sub:
        return handleStts()
    if sub in ("default", "def"):
        return handleSttsDefault()
    return (
        "사용법:\n"
        "stts — 현재 설정 조회\n"
        "stts default (또는 stts def) — 모든 설정을 디폴트로 복원"
    )
