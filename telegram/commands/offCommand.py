"""off 명령 — 프로그램 정상 종료 요청"""

from utils.termLog import termLog

# off 수신 시 True. 폴링 루프가 응답 전송 후 이 플래그를 보고 종료한다.
_shutdownRequested = False


def requestShutdown():
    """종료 플래그만 켠다. 텔레그램 문구 없이 시그널/콘솔 종료에서 쓴다."""
    global _shutdownRequested
    _shutdownRequested = True


def handleOff():
    """
    종료를 요청하고 안내 메시지를 반환한다.
    실제 프로세스 종료는 텔레그램 응답 전송 후 telRecv에서 수행한다.
    """
    requestShutdown()
    msg = "프로그램을 종료합니다."
    termLog(msg)
    return msg


def isShutdownRequested():
    """off 명령으로 종료가 요청되었는지 여부."""
    return _shutdownRequested
