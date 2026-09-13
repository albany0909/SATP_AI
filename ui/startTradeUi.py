"""
PySide6 매매 화면 기동.
Qt 이벤트 루프는 메인 스레드에서만 돌리고, 텔레그램 폴링은 호출측에서 백그라운드로 둔다.
로그인보다 창을 먼저 띄워, 기동이 느려도 화면이 보이게 한다.
main.py(프로세스)가 끝나면 창도 같이 닫는다.
"""

import atexit
import signal
import sys
import traceback

from utils.termLog import termLog

_app = None
_win = None
# Windows SetConsoleCtrlHandler 콜백은 GC되면 안 되므로 모듈에 붙잡아 둔다.
_winCtrlHandler = None
_stopHandlersInstalled = False
# qInstallMessageHandler 콜백은 GC되면 안 된다
_qtErrHandler = None


def closeTradeUi():
    """
    매매 창을 닫고 Qt 루프를 끝낸다.
    QApplication.quit()은 스레드 안전 — 콘솔 Ctrl+C/종료 핸들러에서 호출해도 된다.
    """
    global _app, _win
    try:
        from telegram.commands.offCommand import requestShutdown

        requestShutdown()
    except Exception:
        pass
    app = _app
    if app is not None:
        try:
            app.quit()
        except Exception:
            pass


def _onAboutToQuit():
    """quit() 이후 GUI 스레드에서 창을 닫아 좀비 윈도우를 남기지 않는다."""
    global _win
    try:
        from utils.tradeLogHook import clearTradeLogHooks
        from ui.tradeLogBus import clearTradeLogBus
        from ui.deskErrBus import clearDeskErrBus
        from utils.termLog import clearDeskErrHook

        clearTradeLogHooks()
        clearTradeLogBus()
        clearDeskErrHook()
        clearDeskErrBus()
    except Exception:
        pass
    win = _win
    _win = None
    if win is None:
        return
    try:
        win.close()
    except Exception:
        pass


def _onProcessStop(signum=None, frame=None):
    closeTradeUi()


def _installStopHandlers():
    """
    Windows에서 Qt exec() 중에는 Python SIGINT가 거의 안 들어온다.
    콘솔 Ctrl+C·터미널 종료(CTRL_CLOSE)를 받아 quit하면 main.py 중지 시 창이 따라 닫힌다.
    QApplication 생성 후에 설치해야 Qt가 콘솔 핸들러를 덮어쓰지 않는다.
    """
    global _winCtrlHandler, _stopHandlersInstalled
    if _stopHandlersInstalled:
        return
    _stopHandlersInstalled = True

    for sigName in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sigName, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _onProcessStop)
        except Exception:
            pass
    atexit.register(closeTradeUi)

    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        Handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _consoleCtrl(_ctrlType):
            closeTradeUi()
            return True

        _winCtrlHandler = Handler(_consoleCtrl)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_winCtrlHandler, True)
    except Exception:
        pass


def _dropStayOnTop(win):
    """
    최상위만 해제한다.
    setWindowFlag()는 이미 보이는 창을 숨겼다가 show 해야 해서 한 번 깜빡인다.
    """
    if win is None:
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(win.winId())
        if not hwnd:
            return
        hwndNotopmost = -2
        swpNosize = 0x0001
        swpNomove = 0x0002
        swpNoactivate = 0x0010
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            hwndNotopmost,
            0,
            0,
            0,
            0,
            swpNomove | swpNosize | swpNoactivate,
        )
    except Exception:
        pass


def _installUiExcepthook():
    """슬롯 예외가 콘솔에만 사라지고 원인을 못 남기지 않게 한다."""
    prev = sys.excepthook

    def _hook(excType, exc, tb):
        try:
            termLog("[UI] 처리되지 않은 예외")
            termLog("".join(traceback.format_exception(excType, exc, tb)))
        except Exception:
            pass
        try:
            prev(excType, exc, tb)
        except Exception:
            pass

    sys.excepthook = _hook


def _installQtErrHandler():
    """
    Qt 치명/스레드 경고를 콘솔+오류 탭으로 보낸다.
    일반 스타일 경고는 넣지 않는다.
    """
    global _qtErrHandler
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        from utils.termLog import looksLikeErr, termLog
    except Exception:
        return

    def _qtMsg(mode, _ctx, message):
        msg = str(message or "")
        if not msg:
            return
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            termLog(f"[Qt오류] {msg}")
            return
        if mode == QtMsgType.QtWarningMsg and looksLikeErr(msg):
            termLog(f"[Qt경고] {msg}")

    _qtErrHandler = _qtMsg
    try:
        qInstallMessageHandler(_qtErrHandler)
    except Exception:
        pass


def createTradeUi():
    """
    QApplication + 매매 창을 만들고 즉시 show 한다. exec는 하지 않는다.
    실패 시 (None, None).
    """
    global _app, _win
    _installUiExcepthook()
    try:
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication
    except ImportError as err:
        termLog(f"[UI] PySide6 import 실패: {err}")
        return None, None

    try:
        from telegram.commands.offCommand import isShutdownRequested
        from ui.tradeDeskWin import TradeDeskWin

        _app = QApplication.instance() or QApplication(sys.argv)
        _app.setApplicationName("SATP_AI")
        _app.setQuitOnLastWindowClosed(True)
        _installQtErrHandler()
        _app.aboutToQuit.connect(_onAboutToQuit)
        _installStopHandlers()
        _win = TradeDeskWin()
        # 다른 창 뒤에 숨지 않게
        _win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        _win.show()
        _win.raise_()
        _win.activateWindow()
        _app.processEvents()

        # off 텔레그램·Ctrl+C 플래그를 Qt 루프에서 폴링 (시그널이 막혀도 창 종료)
        offWatch = QTimer(_win)
        offWatch.timeout.connect(
            lambda: closeTradeUi() if isShutdownRequested() else None
        )
        offWatch.start(500)

        # 3초 후 always-on-top 해제. setWindowFlag+show 는 창이 깜빡이므로 Win32만 쓴다.
        topOff = QTimer(_win)
        topOff.setSingleShot(True)
        topOff.timeout.connect(lambda: _dropStayOnTop(_win))
        topOff.start(3000)

        termLog("[UI] 매매 화면 표시")
        return _app, _win
    except Exception as err:
        termLog(f"[UI] 창 생성 실패: {err}")
        termLog(traceback.format_exc())
        return None, None


def runTradeUiLoop(app):
    """Qt 이벤트 루프. 창을 닫으면 반환한다."""
    if app is None:
        return False
    termLog("[UI] 매매 화면 기동 (PySide6)")
    app.exec()
    return True


def startTradeUi():
    """창 생성 후 닫힐 때까지 대기. 실패 시 False."""
    app, _win = createTradeUi()
    if app is None:
        return False
    return runTradeUiLoop(app)


if __name__ == "__main__":
    # 레이아웃만 확인할 때: python -m ui.startTradeUi
    startTradeUi()
