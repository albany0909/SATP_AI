"""관심·실계좌 일봉 증분 스케줄러 (장마감~18:00, 거래일 1회)."""

import threading
import time
from datetime import datetime

from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from config.settingsStore import getMktHours
from utils.termLog import termLog

_DEADLINE_HM = "18:00"
_started = False
_loadedYmd = ""


def _hmNow():
    return datetime.now().strftime("%H:%M")


def _shouldRun(now=None):
    now = now or datetime.now()
    if not isTradingDay(now):
        return False
    try:
        _, closeHm = getMktHours()
    except Exception:
        closeHm = "15:30"
    closeHm = (closeHm or "15:30")[:5]
    hm = now.strftime("%H:%M")
    return closeHm <= hm <= _DEADLINE_HM


def runWatchDayPrcNow(force=False):
    if not force and not isBatchSchedEnabled("watchDayPrc"):
        return {"ok": False, "errMsg": "watchDayPrc 배치 비활성"}
    from utils.watchDayPrcLoad import loadDayPrcForWatchHolds

    return loadDayPrcForWatchHolds(refetchLastDay=True)


def _loop():
    global _loadedYmd
    while True:
        try:
            now = datetime.now()
            ymd = now.strftime("%Y%m%d")
            if (
                isBatchSchedEnabled("watchDayPrc")
                and _shouldRun(now)
                and _loadedYmd != ymd
            ):
                got = runWatchDayPrcNow()
                if got.get("ok"):
                    _loadedYmd = ymd
                    termLog(got.get("summary") or "[watchDayPrc] ok")
                else:
                    termLog(f"[watchDayPrc] {got.get('errMsg')}")
        except Exception as err:
            termLog(f"[watchDayPrc] 스케줄 오류: {err}")
        time.sleep(30)


def startWatchDayPrcScheduler():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="watchDayPrcSched", daemon=True).start()
    termLog("[watchDayPrcSched] 기동 — 거래일 장마감~18:00 관심·실계좌 일봉 증분")
