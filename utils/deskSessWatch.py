"""
계정 세션 생존 감시 — 다른 곳 로그인 시 매매/로그인 창 종료.
"""

from __future__ import annotations

from utils.termLog import termLog

_TIMER = None
_ON_KICK = None


def startDeskSessWatch(intervalMs=45000, onKick=None):
    """
    QTimer 로 주기적으로 서버 세션 확인.
    onKick(msg) 호출 후 기본은 앱 종료 유도.
    """
    global _TIMER, _ON_KICK
    stopDeskSessWatch()
    _ON_KICK = onKick
    try:
        from PySide6.QtCore import QTimer
    except Exception:
        return False

    def _tick():
        try:
            from utils.deskLoginSession import readLoginSession
            from utils.deskLoginSess import checkDeskLogin

            data = readLoginSession() or {}
            logId = str(data.get("logId") or "").strip()
            sessTok = str(data.get("sessToken") or "").strip()
            if not logId or not sessTok:
                return
            got = checkDeskLogin(logId, sessTok)
            if got.get("valid"):
                return
            if got.get("softFail"):
                return
            host = str(got.get("hostNm") or "").strip()
            msg = str(got.get("errMsg") or "다른 곳에서 로그인되어 종료합니다")
            if host:
                msg = f"{msg}\n(위치: {host})"
            termLog(f"[deskSess] kick — {msg.replace(chr(10), ' ')}")
            cb = _ON_KICK
            stopDeskSessWatch()
            if callable(cb):
                try:
                    cb(msg)
                    return
                except Exception as err:
                    termLog(f"[deskSess] onKick 실패: {err}")
            _defaultKick(msg)
        except Exception as err:
            termLog(f"[deskSess] watch 오류: {err}")

    t = QTimer()
    t.setInterval(max(15000, int(intervalMs or 45000)))
    t.timeout.connect(_tick)
    t.start()
    _TIMER = t
    # 즉시 1회
    try:
        _tick()
    except Exception:
        pass
    return True


def stopDeskSessWatch():
    global _TIMER, _ON_KICK
    t = _TIMER
    _TIMER = None
    _ON_KICK = None
    if t is None:
        return
    try:
        t.stop()
        t.deleteLater()
    except Exception:
        pass


def releaseCurrentDeskSess():
    """로컬 세션 파일의 서버 토큰 해제."""
    try:
        from utils.deskLoginSession import clearLoginSession, readLoginSession
        from utils.deskLoginSess import releaseDeskLogin

        data = readLoginSession() or {}
        logId = str(data.get("logId") or "").strip()
        sessTok = str(data.get("sessToken") or "").strip()
        if logId and sessTok:
            releaseDeskLogin(logId, sessTok)
        clearLoginSession()
    except Exception as err:
        termLog(f"[deskSess] release 실패: {err}")


def _defaultKick(msg):
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        QMessageBox.warning(None, "중복 로그인", msg)
        if app is not None:
            app.quit()
    except Exception:
        pass
