"""
SATP_AI 매매창 진입점.

역할 규칙:
  - Cursor/개발: 로그인 생략 + deskRole=main (배치·텔레그램·전략)
  - 로그인 런처로 기동된 모든 PC: deskRole=client 만 (DESK_GRADE 무관)

환경변수:
  SATP_SKIP_LOGIN=1     — 로그인 세션 생략 (개발)
  SATP_DESK_ROLE=main|client
  SATP_FROM_LOGIN_LAUNCHER=1 — 로그인 설치파일에서 기동
"""

import os
import sys
import threading
from pathlib import Path

# embed 런타임(python*._pth)은 runtime 폴더만 path 에 넣고
# PYTHONPATH 도 무시하므로, 앱 루트(telegram/api/…)를 직접 넣는다.
_appRoot = Path(__file__).resolve().parent
_appRootStr = str(_appRoot)
if _appRootStr not in sys.path:
    sys.path.insert(0, _appRootStr)

from realtime.indsRtMonitor import startIndsRtMonitor
from realtime.wsHub import startWsHub
from trading.ordExecNotifier import startOrdExecNotifier
from trading.watchRtMonitor import startWatchRtMonitor
from utils.termLog import termLog
from utils.tokenRenewal import startRenewalScheduler


def _wantTradeUi():
    """SATP_HEADLESS=1 이면 창 없이 텔레그램만 기동."""
    raw = str(os.environ.get("SATP_HEADLESS") or "").strip().lower()
    return raw not in ("1", "y", "yes", "true", "on")


def _skipLoginUi():
    raw = str(os.environ.get("SATP_SKIP_LOGIN") or "").strip().lower()
    return raw in ("1", "y", "yes", "true", "on")


def _fromLoginLauncher():
    raw = str(os.environ.get("SATP_FROM_LOGIN_LAUNCHER") or "").strip().lower()
    return raw in ("1", "y", "yes", "true", "on")


def _isDevWorkspace():
    """소스 트리(.cursor/.git)에서 직접 실행 중인지 — 설치 tradeApp 과 구분."""
    try:
        if (_appRoot / ".cursor").is_dir() or (_appRoot / ".git").is_dir():
            return True
    except Exception:
        pass
    return False


def _isCursorDevRun():
    """
    Cursor/VS Code 에서 개발 실행인지.
    로그인 런처 기동은 제외. 워크스페이스 소스 + (SKIP_LOGIN 또는 Cursor/VSCode env).
    """
    if _fromLoginLauncher():
        return False
    if not _isDevWorkspace():
        return False
    if _skipLoginUi():
        return True
    # Cursor / VS Code 통합 터미널·디버그
    markers = (
        "CURSOR_TRACE_ID",
        "CURSOR_AGENT",
        "VSCODE_PID",
        "VSCODE_INJECTION",
        "TERM_PROGRAM",
    )
    for key in markers:
        val = str(os.environ.get(key) or "").strip()
        if not val:
            continue
        if key == "TERM_PROGRAM" and val.lower() not in ("vscode", "cursor"):
            continue
        return True
    return False


def _applyDevMainMode():
    """Cursor 개발 기동: 로그인 생략 + main 역할."""
    os.environ["SATP_SKIP_LOGIN"] = "1"
    os.environ["SATP_DESK_ROLE"] = "main"
    try:
        from config.settingsStore import setDeskRole, patchSettings

        setDeskRole("main")
        # 개발 main 은 로컬 DB 사용 (서버 전용 강제 해제)
        patchSettings({"deskDataViaServer": False})
    except Exception as err:
        termLog(f"[기동] 개발 main 설정 실패: {err}")
    termLog("[기동] Cursor/개발 — 로그인 생략 · deskRole=main")


def _deskRole():
    try:
        from config.settingsStore import getDeskRole

        return getDeskRole()
    except Exception:
        return "main"


def _isDeskMain():
    return _deskRole() == "main"


def _requireLoginLauncherSession():
    """
    매매창은 로그인 런처 세션 필수.
    Cursor/개발(SKIP) 제외.
    """
    if _skipLoginUi():
        termLog("[기동] SATP_SKIP_LOGIN — 로그인 세션 검사 생략")
        return True

    from utils.deskLoginSession import hasValidSession, readLoginSession, sessionEnvName

    if hasValidSession(requireEnvMatch=True):
        termLog("[기동] 로그인 런처 세션 OK")
        return True

    envTok = str(os.environ.get(sessionEnvName()) or "").strip()
    data = readLoginSession()
    fileTok = str((data or {}).get("token") or "").strip()
    termLog(
        f"[기동] 세션 실패 env={bool(envTok)} file={bool(fileTok)} "
        f"match={bool(envTok and fileTok and envTok == fileTok)} "
        f"tradeAppDir={os.environ.get('SATP_TRADE_APP_DIR') or ''}"
    )
    try:
        from utils.appPaths import configDataDir

        logPath = configDataDir() / "tradeLaunch.log"
        logPath.parent.mkdir(parents=True, exist_ok=True)
        with logPath.open("a", encoding="utf-8") as fp:
            fp.write(
                f"session_fail env={bool(envTok)} file={bool(fileTok)} "
                f"cwd={os.getcwd()} argv={sys.argv}\n"
            )
    except Exception:
        pass

    msg = (
        "매매창은 로그인 설치파일로만 실행할 수 있습니다.\n"
        "SATP_AI 로그인(loginMain)을 실행한 뒤 로그인하세요.\n"
        f"(세션 env={'Y' if envTok else 'N'} file={'Y' if fileTok else 'N'})"
    )
    termLog(f"[기동] 차단 — {msg.replace(chr(10), ' ')}")
    if _wantTradeUi():
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            QMessageBox.warning(None, "로그인 필요", msg)
        except Exception:
            pass
    return False


def _startTelegramBg():
    """Qt 메인 루프를 막지 않도록 폴링은 별도 스레드. (main 전용 · 지연 import)"""
    from telegram.telRecv import startPolling

    th = threading.Thread(target=startPolling, name="telegramPoll", daemon=True)
    th.start()
    return th


def _bootLogin():
    """
    설정 deskTradeMode 로 모의/실전 로그인.
    UI·일봉스냅보다 먼저 호출해 모의 토큰이 먼저 박히지 않게 한다.
    """
    from telegram.commands.loginCommand import handleLogin

    # 런처가 넘긴 모드 우선
    bootMode = str(os.environ.get("SATP_DESK_TRADE_MODE") or "").strip().lower()
    if bootMode not in ("paper", "real"):
        try:
            from config.settingsStore import getDeskTradeMode

            bootMode = getDeskTradeMode()
        except Exception:
            bootMode = "paper"
    try:
        from config.settingsStore import setDeskTradeMode

        setDeskTradeMode(bootMode)
    except Exception:
        pass
    try:
        from utils.tradeSession import applyPaperOnlyOn, applyRealPaperOnlyOff

        if bootMode == "real":
            applyRealPaperOnlyOff()
        else:
            applyPaperOnlyOn()
    except Exception as err:
        termLog(f"[기동] paperOnly 동기 실패: {err}")
    termLog(f"[기동] deskTradeMode={bootMode} 로그인")
    loginMsg = handleLogin(bootMode)
    termLog(loginMsg)
    # UI·로그 파일에 실패 사유를 남김 (시크릿 없음)
    try:
        from utils.deskFileLog import appendDeskFileLog
        from utils.tradeSession import isModeLoggedIn

        ok = isModeLoggedIn(bootMode)
        appendDeskFileLog(
            f"bootLogin mode={bootMode} ok={ok} msg={(loginMsg or '')[:200]}",
            tag="LOGIN",
        )
        # 매매창이 읽을 짧은 상태 파일
        from utils.appPaths import configDataDir

        statusPath = configDataDir() / "lastBootLogin.txt"
        statusPath.write_text(
            f"ok={'Y' if ok else 'N'}\nmode={bootMode}\n{(loginMsg or '').strip()}\n",
            encoding="utf-8",
        )
        if not ok:
            os.environ["SATP_BOOT_LOGIN_ERR"] = (loginMsg or "키움 로그인 실패")[:300]
    except Exception as err:
        termLog(f"[기동] bootLogin 상태기록 실패: {err}")
    if _isDeskMain():
        try:
            from telegram.telSend import sendTelegramMsg

            sendTelegramMsg(loginMsg, pushKind="boot")
        except Exception as err:
            termLog(f"로그인 결과 텔레그램 전송 실패: {err}")
    return bootMode


def _startMainBatchSchedulers():
    """관심선정·DB 적재·복기 등 — main 역할만."""
    from utils.algDayReviewScheduler import startAlgDayReviewScheduler
    from utils.dayInqRankScheduler import startDayInqRankScheduler
    from utils.watchDayPrcScheduler import startWatchDayPrcScheduler
    from utils.dayInvSrScheduler import startDayInvSrScheduler
    from utils.ntlFirstScheduler import startNtlFirstScheduler
    from utils.rsvScheduler import startRsvScheduler
    from utils.usDayInqRankScheduler import startUsDayInqRankScheduler
    from utils.watchNewsDiscScheduler import startWatchNewsDiscScheduler

    startRsvScheduler()
    startDayInqRankScheduler()
    startWatchDayPrcScheduler()
    startNtlFirstScheduler()
    startDayInvSrScheduler()
    startUsDayInqRankScheduler()
    startWatchNewsDiscScheduler()
    from trading.maTouchUnivLoad import startMaTouchUnivSched

    startMaTouchUnivSched()
    startAlgDayReviewScheduler()
    termLog("[기동] main 배치·스케줄 기동 완료")


def main():
    # Cursor/개발: 로그인 없이 main. 로그인 런처 기동은 client.
    if _fromLoginLauncher():
        os.environ["SATP_DESK_ROLE"] = "client"
        try:
            from config.settingsStore import setDeskRole

            setDeskRole("client")
        except Exception:
            pass
        termLog("[기동] 로그인 런처 — deskRole=client 고정")
    elif _isCursorDevRun():
        _applyDevMainMode()

    # 로그인 런처 세션 없으면 매매창 기동 불가 (개발 SKIP 제외)
    if not _requireLoginLauncherSession():
        return False

    role = _deskRole()
    termLog(f"[기동] deskRole={role}")

    startRenewalScheduler()

    if _isDeskMain():
        _startMainBatchSchedulers()
    else:
        termLog("[기동] client — 배치·관심선정·복기 스케줄 생략")

    if _isDeskMain():
        try:
            from telegram.telSend import sendTelegramMsg

            sendTelegramMsg("SATP_AI 프로그램이 시작되었습니다.", pushKind="boot")
            termLog("시작 알림 텔레그램 전송 완료")
        except Exception as err:
            termLog(f"시작 알림 텔레그램 전송 실패: {err}")

    startOrdExecNotifier()
    _bootLogin()

    tradeApp = None
    tradeWin = None
    runLoop = None
    if _wantTradeUi():
        from ui.startTradeUi import createTradeUi, runTradeUiLoop

        runLoop = runTradeUiLoop
        tradeApp, tradeWin = createTradeUi()
        if tradeWin is not None:
            try:
                tradeWin.refreshSessionLabel()
                tradeWin.refreshAcnt()
            except Exception:
                pass

    startWatchRtMonitor()
    startIndsRtMonitor()

    if startWsHub():
        termLog("주문체결 실시간 수신(WS) 기동 완료")
    else:
        termLog("주문체결 WS 기동 실패 — 로그인 상태를 확인하세요")

    if tradeApp is not None and runLoop is not None:
        if _isDeskMain():
            _startTelegramBg()
        try:
            runLoop(tradeApp)
        except KeyboardInterrupt:
            termLog("Ctrl+C — 매매 화면 종료")
        finally:
            from ui.startTradeUi import closeTradeUi

            closeTradeUi()
            termLog("매매 화면 종료")
        return True

    if _isDeskMain():
        from telegram.telRecv import startPolling

        startPolling()
        return True

    termLog("[기동] client·헤드리스 — 스케줄 없음, Ctrl+C 까지 대기")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        termLog("Ctrl+C — 프로그램 종료")
    return True

if __name__ == "__main__":
    try:
        ok = main()
        # 세션 거부 등 False 반환 시 런처가 즉시 감지하도록 1 종료
        if ok is False:
            sys.exit(1)
    except KeyboardInterrupt:
        termLog("Ctrl+C — 프로그램 종료")
        if _wantTradeUi():
            from ui.startTradeUi import closeTradeUi

            closeTradeUi()
    except Exception as err:
        # 매매창 일자 로그 + tradeLaunch 호환
        try:
            from utils.deskFileLog import appendDeskFileLog, deskLogFilePath
            import traceback

            tb = traceback.format_exc()
            appendDeskFileLog(f"치명 오류: {err!r}", tag="ERR")
            for ln in tb.splitlines():
                appendDeskFileLog(ln, tag="ERR")
            termLog(f"[기동] 치명 오류: {err} → {deskLogFilePath()}")
        except Exception:
            pass
        try:
            from utils.appPaths import configDataDir

            logPath = configDataDir() / "tradeLaunch.log"
            logPath.parent.mkdir(parents=True, exist_ok=True)
            with logPath.open("a", encoding="utf-8") as fp:
                import traceback

                fp.write(f"\ncrash={err!r}\n")
                fp.write(traceback.format_exc())
        except Exception:
            pass
        termLog(f"[기동] 치명 오류: {err}")
        raise
