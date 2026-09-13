"""
관심종목 공시·뉴스 주기 수집 스케줄러 (Finance Agent 도메인).

- 국내 거래일 08:00~22:00 (KST 로컬) — 주말·공휴일은 자동 수집 안 함
- 기본 30분 간격
- 신규 건만 텔레그램 푸시 (사이클당 최대 12건)
- 기동 후 약 90초 뒤 1회 선행 수집 (윈도우 안일 때)
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from telegram.telSend import sendTelegramMsg
from utils.termLog import termLog

# ── 주기 설정 ──
_INTERVAL_SEC = 30 * 60  # 30분
_WINDOW_START = "08:00"
_WINDOW_END = "22:00"
_START_DELAY_SEC = 90
_DAYS_BACK = 3
_NEWS_PAGES = 1
_MAX_STKS = 80
_SLEEP_BETWEEN = 0.9
_MAX_PUSH = 12

_schedulerStarted = False
_lastRunKey = ""
_lastResult = {}
_running = False
_lock = threading.Lock()


def getWatchNewsDiscSchedStatus():
    return {
        "started": _schedulerStarted,
        "intervalMin": _INTERVAL_SEC // 60,
        "window": f"{_WINDOW_START}~{_WINDOW_END}",
        "tradingDayOnly": True,
        "isSchedDay": _isSchedDay(),
        "lastRunKey": _lastRunKey,
        "running": _running,
        "lastSummary": (_lastResult or {}).get("summary") or "",
        "lastOk": (_lastResult or {}).get("ok"),
    }


def _inWindow(now=None):
    dt = now or datetime.now()
    hm = dt.strftime("%H:%M")
    return _WINDOW_START <= hm <= _WINDOW_END


def _isSchedDay(now=None):
    """
    자동 수집은 국내 거래일만. 휴장일엔 새 공시·뉴스가 사실상 없어 헛조회다.
    수동 실행(텔레그램 wnews run·배치 화면 실행)은 이 제한을 받지 않는다.
    """
    return isTradingDay(now or datetime.now())


def _runCycle(reason="sched"):
    """한 사이클 수집 (동시 1회)."""
    global _lastRunKey, _lastResult, _running

    with _lock:
        if _running:
            return {"ok": False, "errMsg": "이미 수집 중", "skipped": True}
        _running = True

    try:
        from research.watchNewsDiscLoad import (
            formatNewPushMsgs,
            loadWatchNewsDisc,
        )
        from utils.watchNewsDiscStore import markDiscPushed, markNewsPushed

        termLog(f"[watchNewsSched] 수집 시작 ({reason})")
        # 스케줄만 on/off — 텔레그램 wnews run 수동은 항상 실행
        if reason != "manual" and not isBatchSchedEnabled("watchNewsDisc"):
            termLog("[watchNewsSched] 배치 OFF — 건너뜀")
            return {"ok": True, "skipped": True, "summary": "배치 스케줄 OFF"}
        result = loadWatchNewsDisc(
            daysBack=_DAYS_BACK,
            newsPages=_NEWS_PAGES,
            maxStks=_MAX_STKS,
            sleepSec=_SLEEP_BETWEEN,
        )
        _lastResult = result
        _lastRunKey = datetime.now().strftime("%Y%m%d%H%M")

        newD = result.get("newDisc") or []
        newN = result.get("newNews") or []
        # 첫 기동 대량 푸시 방지: 신규 너무 많으면 요약만
        totalNew = len(newD) + len(newN)
        if totalNew <= 0:
            termLog(f"[watchNewsSched] 신규 없음 — {result.get('summary')}")
            return result

        if totalNew > 40:
            # 백로그 — 푸시 생략, 이미 DB 적재됨, PUSHED 는 N 유지해도 무방
            # 대량 시 알림은 요약 1통
            msg = (
                f"{result.get('summary')}\n"
                f"(신규 다수 {totalNew}건 — 개별 푸시 생략, wnews last 로 확인)"
            )
            try:
                sendTelegramMsg(msg, pushKind="wnews")
            except Exception as err:
                termLog(f"[watchNewsSched] 텔레그램 실패: {err}")
            return result

        msgs = formatNewPushMsgs(newD, newN, maxMsgs=_MAX_PUSH)
        for m in msgs:
            try:
                sendTelegramMsg(m, pushKind="wnews")
                time.sleep(0.4)
            except Exception as err:
                termLog(f"[watchNewsSched] 푸시 실패: {err}")

        markDiscPushed([r.get("RCEPT_NO") for r in newD if r.get("RCEPT_NO")])
        markNewsPushed(
            [
                (r.get("STK_CD"), r.get("NEWS_ID"))
                for r in newN
                if r.get("STK_CD") and r.get("NEWS_ID")
            ]
        )
        if totalNew > len(msgs):
            try:
                sendTelegramMsg(
                    f"[관심 공시·뉴스] 추가 신규 {totalNew - len(msgs)}건 더 있음 "
                    f"(wnews last)",
                    pushKind="wnews",
                )
            except Exception:
                pass
        return result
    except Exception as err:
        termLog(f"[watchNewsSched] 사이클 오류: {err}")
        _lastResult = {"ok": False, "errMsg": str(err), "summary": str(err)}
        return _lastResult
    finally:
        with _lock:
            _running = False


def runWatchNewsDiscNow(force=False):
    """수동 즉시 실행 (윈도우 무시 가능)."""
    if not force and not _inWindow():
        return {
            "ok": False,
            "errMsg": f"수집 시간대 밖 ({_WINDOW_START}~{_WINDOW_END}). force=1 로 강제.",
        }
    return _runCycle(reason="manual")


def _schedulerLoop():
    # 기동 지연 후 1회
    time.sleep(_START_DELAY_SEC)
    try:
        if _inWindow() and _isSchedDay():
            _runCycle(reason="boot")
    except Exception as err:
        termLog(f"[watchNewsSched] boot 사이클 오류: {err}")

    while True:
        try:
            time.sleep(30)  # 분 정합보다 30초 폴링
            if not _inWindow() or not _isSchedDay():
                continue
            # 마지막 실행 후 INTERVAL 경과했는지
            if _lastRunKey:
                try:
                    last = datetime.strptime(_lastRunKey, "%Y%m%d%H%M")
                    if (datetime.now() - last).total_seconds() < _INTERVAL_SEC:
                        continue
                except ValueError:
                    pass
            _runCycle(reason="interval")
        except Exception as err:
            termLog(f"[watchNewsSched] 루프 오류: {err}")
            time.sleep(60)


def startWatchNewsDiscScheduler():
    """백그라운드 관심 공시·뉴스 스케줄러."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    th = threading.Thread(
        target=_schedulerLoop, name="watchNewsDiscSched", daemon=True
    )
    th.start()
    termLog(
        f"[watchNewsSched] 기동 — 거래일 {_WINDOW_START}~{_WINDOW_END} "
        f"매 {_INTERVAL_SEC // 60}분 · 관심종목 공시(DART)+뉴스(네이버)"
    )
