"""
거래일 장마감 후 최초 52주 신고 진입 종목 자동 적재.
- AI_DAY_NTL_FIRST
- 마감 후 ~ 18:30 1일 1회 (순위 적재와 병행 가능, 세션 로그인 필요)
"""

import threading
import time
from datetime import datetime

from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from config.settingsStore import getMktHours
from telegram.telSend import sendTelegramMsg
from utils.ntlFirstLoad import loadNtlFirst
from utils.ntlFirstStore import countNtlFirst, hasNtlFirst
from utils.termLog import termLog

_LOAD_DEADLINE_HM = "18:30"
# 순위 직후 API 혼잡 완화 — 마감 15분 뒤부터 본 스케줄 우선 시도 여유
_START_OFFSET_MIN = 10

_schedulerStarted = False
_loadedYmd = ""
_lastAttemptKey = ""


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="batch")
    except Exception as err:
        termLog(f"[ntlFirstSched] 텔레그램 전송 실패: {err}")


def _closePlusMin(closeHm, addMin):
    """'HH:MM' + 분 → 'HH:MM'."""
    try:
        h, m = closeHm.split(":")
        total = int(h) * 60 + int(m) + int(addMin)
        return f"{total // 60:02d}:{total % 60:02d}"
    except (TypeError, ValueError):
        return closeHm


def _tryLoadNtlFirst(now=None):
    global _loadedYmd, _lastAttemptKey

    dt = now or datetime.now()
    ymd = dt.strftime("%Y%m%d")
    curHm = dt.strftime("%H:%M")

    if _loadedYmd == ymd:
        return False
    if not isBatchSchedEnabled("ntlFirst"):
        return False
    if not isTradingDay(dt):
        return False

    _, closeHm = getMktHours()
    startHm = _closePlusMin(closeHm, _START_OFFSET_MIN)
    if curHm < startHm or curHm > _LOAD_DEADLINE_HM:
        return False

    # 당일 기준 진입일 스냅샷 이미 있으면 스킵
    # (동일 TRADE_DT 적재 여부 — entryDt 가 당일과 다를 수 있어 count 기준)
    if hasNtlFirst(ymd) and countNtlFirst(ymd) > 0:
        _loadedYmd = ymd
        termLog(f"[ntlFirstSched] 이미 적재됨 — {ymd}")
        return False

    attemptKey = f"{ymd}|{curHm}"
    if attemptKey == _lastAttemptKey:
        return False
    _lastAttemptKey = attemptKey

    termLog(f"[ntlFirstSched] 최초52 적재 시도 — {startHm}~{_LOAD_DEADLINE_HM} now={curHm}")
    result = loadNtlFirst(tradeDt=ymd)
    if result.get("ok"):
        _loadedYmd = ymd
        _notify(result.get("summary") or f"[ntl load] 완료 {result.get('upsertCnt')}건")
        return True

    _notify(
        f"[ntl load 실패] {result.get('errMsg')}\n"
        f"(거래일·{_LOAD_DEADLINE_HM}까지 재시도)"
    )
    return True


def runNtlFirstNow(tradeDt=None):
    """수동 실행 — 시간창·거래일·ON/OFF 무시."""
    global _loadedYmd

    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    result = loadNtlFirst(tradeDt=ymd)
    if not result.get("ok"):
        return {"ok": False, "errMsg": result.get("errMsg") or "최초52 적재 실패"}
    _loadedYmd = ymd
    summary = result.get("summary") or f"[ntl load:수동] 완료 {result.get('upsertCnt')}건"
    _notify(summary)
    return {"ok": True, "tradeDt": ymd, "summary": summary}


def _schedulerLoop():
    while True:
        try:
            _tryLoadNtlFirst()
        except Exception as err:
            termLog(f"[ntlFirstSched] 루프 오류: {err}")
        time.sleep(30)


def startNtlFirstScheduler():
    """백그라운드 최초52 일 적재 스케줄러."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    th = threading.Thread(target=_schedulerLoop, name="ntlFirstSched", daemon=True)
    th.start()
    _, closeHm = getMktHours()
    startHm = _closePlusMin(closeHm, _START_OFFSET_MIN)
    termLog(
        f"[ntlFirstSched] 기동 — 거래일 {startHm}~{_LOAD_DEADLINE_HM} "
        f"최초52신고 → AI_DAY_NTL_FIRST"
    )
