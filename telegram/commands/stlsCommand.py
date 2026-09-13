"""start stls / stop stls — 스탑로스 감시 시작·중지"""

from trading.stlsMonitor import getStlsStatusText, startStlsMonitor, stopStlsMonitor
from utils.termLog import termLog


def _usage():
    return (
        "사용법:\n"
        "start stls — 스탑로스 감시 시작\n"
        "stop stls — 스탑로스 감시 중지\n"
        f"현재 상태: {getStlsStatusText()}"
    )


def handleStls(action):
    """
    action: 'start' | 'stop' | 기타(안내)
    """
    act = (action or "").strip().lower()

    if act == "start":
        ok, msg = startStlsMonitor()
        termLog(msg)
        return msg

    if act == "stop":
        ok, msg = stopStlsMonitor()
        termLog(msg)
        return msg

    return _usage()


def handleStartCmd(rawParts):
    """
    start 하위 명령. rawParts: ['start', 'stls', ...]
    """
    if len(rawParts) < 2:
        return "사용법: start stls"
    sub = rawParts[1].lower()
    if sub == "stls":
        return handleStls("start")
    return f"알 수 없는 start 대상입니다: {rawParts[1]}\n사용법: start stls"


def handleStopCmd(rawParts):
    """
    stop 하위 명령. rawParts: ['stop', 'stls', ...]
    """
    if len(rawParts) < 2:
        return "사용법: stop stls"
    sub = rawParts[1].lower()
    if sub == "stls":
        return handleStls("stop")
    return f"알 수 없는 stop 대상입니다: {rawParts[1]}\n사용법: stop stls"
