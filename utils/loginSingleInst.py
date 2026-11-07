"""
로그인 창 단일 실행.

이미 떠 있으면 기존 창을 앞으로 가져오고, 새 프로세스는 종료한다.
"""

from __future__ import annotations

_SERVER_NAME = "SATP_AI.Login.Singleton"
_server = None


def tryClaimLoginInstance(onRaise=None):
    """
    이번 프로세스가 로그인 창 주인이면 True.
    이미 실행 중이면 기존 인스턴스에 RAISE 신호를 보내고 False.
    """
    global _server
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if sock.waitForConnected(400):
        try:
            sock.write(b"RAISE\n")
            sock.flush()
            sock.waitForBytesWritten(400)
        except Exception:
            pass
        try:
            sock.disconnectFromServer()
        except Exception:
            pass
        return False

    # 비정상 종료로 남은 소켓 이름 정리
    try:
        QLocalServer.removeServer(_SERVER_NAME)
    except Exception:
        pass

    server = QLocalServer()
    if not server.listen(_SERVER_NAME):
        return False

    def _onNewConn():
        client = server.nextPendingConnection()
        if client is None:
            return

        def _onReady():
            try:
                _ = bytes(client.readAll())
            except Exception:
                pass
            if callable(onRaise):
                try:
                    onRaise()
                except Exception:
                    pass
            try:
                client.disconnectFromServer()
            except Exception:
                pass

        client.readyRead.connect(_onReady)
        # 바로 온 데이터도 처리
        if client.bytesAvailable():
            _onReady()

    server.newConnection.connect(_onNewConn)
    _server = server
    return True
