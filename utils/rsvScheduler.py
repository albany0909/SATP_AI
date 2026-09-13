"""
예약 명령 스케줄러.
월~금, schedule.json의 runHm에 맞춰 해당 명령을 1일 1회 실행한다.
once=true 항목은 실행 후 목록에서 제거한다.
"""

import threading
import time
from datetime import datetime

from config.rsvStore import loadSchedule, removeOnceItem
from telegram.telSend import sendTelegramMsg
from utils.termLog import termLog

# 스케줄러 중복 기동 방지
_schedulerStarted = False
# 당일 이미 실행한 예약 키 집합 — "YYYY-MM-DD|idx|HH:MM|cmdText|once"
_ranKeys = set()
_ranDate = ""


def _isWeekday(now=None):
    """월(0)~금(4)이면 True. 토·일 제외."""
    dt = now or datetime.now()
    return dt.weekday() < 5


def _runKey(dateStr, idx, runHm, cmdText, once):
    """하루·인덱스·시각·명령 단위 중복 실행 방지 키."""
    onceFlag = "1" if once else "0"
    return f"{dateStr}|{idx}|{runHm}|{cmdText}|{onceFlag}"


def _resetRanIfNewDay(dateStr):
    """날짜가 바뀌면 실행 이력 초기화."""
    global _ranKeys, _ranDate
    if _ranDate != dateStr:
        _ranDate = dateStr
        _ranKeys = set()


def executeRsvItem(runHm, cmdText, once=False):
    """
    단일 예약을 실행한다.
    예약 실행 알림 → routeCommand → 결과 텔레그램 전송.
    once면 실행 후 schedule에서 제거.
    """
    # 순환 import 방지 — 실행 시점에 라우터 로드
    from telegram.cmdRouter import routeCommand

    onceLabel = " (1회)" if once else ""
    notice = f"[예약실행]{onceLabel} {runHm} — {cmdText}"
    termLog(notice)
    try:
        sendTelegramMsg(notice, pushKind="rsv")
    except Exception as err:
        termLog(f"예약 알림 텔레그램 전송 실패: {err}")

    try:
        resultMsg = routeCommand(cmdText)
    except Exception as err:
        resultMsg = f"[예약실행] 명령 처리 오류: {err}"
        termLog(resultMsg)

    if resultMsg:
        termLog(resultMsg)
        try:
            sendTelegramMsg(resultMsg, pushKind="rsv")
        except Exception as err:
            termLog(f"예약 결과 텔레그램 전송 실패: {err}")

    # 1회 예약은 실행 후 목록에서 제거 — 재실행 방지
    if once:
        if removeOnceItem(runHm, cmdText):
            termLog(f"[예약] 1회 예약 자동 삭제 — {runHm} — {cmdText}")
        else:
            termLog(f"[예약] 1회 예약 삭제 대상 없음 — {runHm} — {cmdText}")


def _schedulerLoop():
    """10초마다 시각을 확인해 해당 분 예약을 실행한다."""
    while True:
        try:
            now = datetime.now()
            dateStr = now.strftime("%Y-%m-%d")
            curHm = now.strftime("%H:%M")
            _resetRanIfNewDay(dateStr)

            # 주말(토·일)은 예약 실행하지 않음
            if _isWeekday(now):
                # 인덱스 포함 — 동일 시각·동일 명령 중복 예약도 각각 1회 처리
                for idx, item in enumerate(loadSchedule()):
                    runHm = item.get("runHm", "")
                    cmdText = item.get("cmdText", "")
                    once = bool(item.get("once", False))
                    if runHm != curHm or not cmdText:
                        continue
                    key = _runKey(dateStr, idx, runHm, cmdText, once)
                    if key in _ranKeys:
                        continue
                    _ranKeys.add(key)
                    executeRsvItem(runHm, cmdText, once=once)
        except Exception as err:
            termLog(f"[예약] 스케줄러 오류: {err}")

        time.sleep(10)


def startRsvScheduler():
    """데몬 스레드로 예약 스케줄러를 한 번만 기동한다."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    thr = threading.Thread(target=_schedulerLoop, name="rsvScheduler", daemon=True)
    thr.start()
    termLog("[예약] 스케줄러 시작 — 월~금, schedule.json 시각에 명령 실행")
