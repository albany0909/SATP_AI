"""
SATP_AI 진입점.
기동 시 deskTradeMode 로 로그인 후,
PySide6 매매 화면(메인 스레드)과 텔레그램 폴링(백그라운드)을 함께 유지한다.
"""

import os
import threading

from telegram.commands.loginCommand import handleLogin
from telegram.telRecv import startPolling
from telegram.telSend import sendTelegramMsg
from realtime.indsRtMonitor import startIndsRtMonitor
from realtime.wsHub import startWsHub
from trading.ordExecNotifier import startOrdExecNotifier
from trading.watchRtMonitor import startWatchRtMonitor
from utils.algDayReviewScheduler import startAlgDayReviewScheduler
from utils.dayInqRankScheduler import startDayInqRankScheduler
from utils.dayInvSrScheduler import startDayInvSrScheduler
from utils.ntlFirstScheduler import startNtlFirstScheduler
from utils.usDayInqRankScheduler import startUsDayInqRankScheduler
from utils.rsvScheduler import startRsvScheduler
from utils.termLog import termLog
from utils.tokenRenewal import startRenewalScheduler
from utils.watchNewsDiscScheduler import startWatchNewsDiscScheduler


def _wantTradeUi():
    """SATP_HEADLESS=1 이면 창 없이 텔레그램만 기동."""
    raw = str(os.environ.get("SATP_HEADLESS") or "").strip().lower()
    return raw not in ("1", "y", "yes", "true", "on")


def _startTelegramBg():
    """Qt 메인 루프를 막지 않도록 폴링은 별도 스레드."""
    th = threading.Thread(target=startPolling, name="telegramPoll", daemon=True)
    th.start()
    return th


def _bootLogin():
    """
    설정 deskTradeMode 로 모의/실전 로그인.
    UI·일봉스냅보다 먼저 호출해 모의 토큰이 먼저 박히지 않게 한다.
    """
    try:
        from config.settingsStore import getDeskTradeMode

        bootMode = getDeskTradeMode()
    except Exception:
        bootMode = "paper"
    # 저장된 모드와 paperOnly 를 기동 시 일치 (부분 저장/재시작 후 모의 고정 방지)
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
    try:
        sendTelegramMsg(loginMsg, pushKind="boot")
    except Exception as err:
        termLog(f"로그인 결과 텔레그램 전송 실패: {err}")
    return bootMode


def main():
    # 로그인 후 매일 지정 시각에 토큰을 갱신하는 백그라운드 스케줄러
    startRenewalScheduler()
    # 예약 명령(월~금) 실행 스케줄러
    startRsvScheduler()
    # 거래일 장마감 후 실시간조회순위(당일누적) → AI_DAY_INQ_RANK
    startDayInqRankScheduler()
    # 거래일 장마감 후 최초 52주 신고 진입 → AI_DAY_NTL_FIRST
    startNtlFirstScheduler()
    # 거래일 16:00 관심∪순위 종목별투자자(ka10059) → AI_DAY_INV_SR
    # 적재 완료 시 관심종목 순위 점수(AI_RANK_SCORE)까지 이어서 계산
    startDayInvSrScheduler()
    # 미국장 마감 후(KST 오전) 미국순위 당일누적 → AI_US_DAY_INQ_RANK
    startUsDayInqRankScheduler()
    # 관심종목 공시(DART)·뉴스 주기 수집 (Finance Agent)
    startWatchNewsDiscScheduler()

    # 기동 직후 운영자에게 시작을 알림 — 이후 폴링 전에 먼저 통지
    try:
        sendTelegramMsg("SATP_AI 프로그램이 시작되었습니다.", pushKind="boot")
        termLog("시작 알림 텔레그램 전송 완료")
    except Exception as err:
        termLog(f"시작 알림 텔레그램 전송 실패: {err}")

    # 체결알림 핸들러를 먼저 등록 (LOGIN→00 REG 순서 — WS는 로그인 후)
    startOrdExecNotifier()

    # UI·스냅보다 먼저 로그인 — deskTradeMode(실전이면 실전) 토큰을 먼저 둔다
    _bootLogin()

    # 로그인 완료 후 매매 창 — 관심 스냅/일봉보강이 모의로 새 토큰을 발급하지 않음
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

    # 관심종목 USE_TRADE=Y → 다음 장 시작 시 0B 구독 (추가 즉시 아님)
    startWatchRtMonitor()
    # 매매창 상단 코스피/코스닥/코스피200 — 0J (LOGIN 후 구독)
    startIndsRtMonitor()
    # 이평터치 대상: 거래일 16:00 ka00198 상위20 ∪ ka10032 대금상위30 → 다음 거래일 관리종목
    from trading.maTouchUnivLoad import startMaTouchUnivSched

    startMaTouchUnivSched()

    # 거래일 16:05 알고리즘 당일 복기 (승률·손익·개선힌트 → JSON·텔레그램)
    startAlgDayReviewScheduler()

    # 주문체결(00) 실시간 — 토큰당 WS 세션 1개
    if startWsHub():
        termLog("주문체결 실시간 수신(WS) 기동 완료")
    else:
        termLog("주문체결 WS 기동 실패 — 로그인 상태를 확인하세요")

    if tradeApp is not None and runLoop is not None:
        _startTelegramBg()
        try:
            runLoop(tradeApp)
        except KeyboardInterrupt:
            termLog("Ctrl+C — 매매 화면 종료")
        finally:
            from ui.startTradeUi import closeTradeUi

            closeTradeUi()
            termLog("매매 화면 종료")
        return

    # 헤드리스 또는 UI 실패: 프로그램 생명주기 = 텔레그램 롱폴링 루프
    startPolling()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        termLog("Ctrl+C — 프로그램 종료")
        if _wantTradeUi():
            from ui.startTradeUi import closeTradeUi

            closeTradeUi()
