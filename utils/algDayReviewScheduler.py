"""
거래일 16:05 알고리즘 당일 복기 스케줄.

16:00 유니버스·순위 적재가 시작된 뒤, 당일 이벤트·청산을 묶어 리포트한다.
"""

import threading
import time
from datetime import datetime

from config.krHoliday import isTradingDay
from utils.termLog import termLog

_RUN_START_HM = "16:05"
_RUN_END_HM = "16:45"
_POLL_SEC = 45
_lastRunYmd = ""
_workerStarted = False


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _nowHm():
    return datetime.now().strftime("%H:%M")


def _inWindow():
    hm = _nowHm()
    return _RUN_START_HM <= hm <= _RUN_END_HM


def _isEnabled():
    try:
        from config.opsSettings import getBatchSchedMap

        return bool(getBatchSchedMap().get("algDayReview", True))
    except Exception:
        return True


def runAlgDayReviewJob(force=False):
    """1일 1회 복기 실행."""
    global _lastRunYmd
    ymd = _todayYmd()
    if not force:
        if not isTradingDay(ymd):
            return {"ok": True, "skipped": "holiday"}
        if not _inWindow():
            return {"ok": True, "skipped": "outside_window"}
        if _lastRunYmd == ymd:
            return {"ok": True, "skipped": "already"}
    if not _isEnabled():
        return {"ok": True, "skipped": "disabled"}

    from utils.algDayReview import runAllAlgDayReviews, sendAlgDayReviewTelegram

    # 16:00 순위·관심 반영 후 당일 관심종목 스냅샷
    try:
        from utils.watchStkDayStore import snapshotWatchStkDay

        snapshotWatchStkDay(tradeDt=ymd)
    except Exception as err:
        termLog(f"[watchStkDay] 스냅샷 실패: {err}")

    result = runAllAlgDayReviews(tradeDt=ymd, logDetail=False)
    _lastRunYmd = ymd

    # 당일 복기 리포트 — 텔레그램만 (알고리즘별 1통)
    try:
        sendAlgDayReviewTelegram(result.get("reviews"), tradeDt=ymd)
    except Exception as err:
        termLog(f"[algDayReview] 텔레그램 실패: {err}")

    termLog(f"[algDayReview] 완료 {ymd} · {len(result.get('reviews') or [])} alg · 텔레그램 전송")
    return result


def _loop():
    while True:
        try:
            if _isEnabled() and isTradingDay(_todayYmd()) and _inWindow():
                runAlgDayReviewJob(force=False)
        except Exception as err:
            termLog(f"[algDayReview] 스케줄 오류: {err}")
        time.sleep(_POLL_SEC)


def startAlgDayReviewScheduler():
    """백그라운드 기동."""
    global _workerStarted
    if _workerStarted:
        return
    _workerStarted = True
    th = threading.Thread(target=_loop, name="algDayReviewSched", daemon=True)
    th.start()
    termLog(
        f"[algDayReview] 기동 — 거래일 {_RUN_START_HM}~{_RUN_END_HM} "
        "복기 JSON 저장 + 텔레그램(dayReview)만 알림"
    )
