"""
미국장 마감 후 순위 리포트 스케줄러.
- 미장 개장일이 끝난 뒤, 한국시간 07:00에 1회
  (예: 월 세션 → 화 07:00 KST, 금 세션 → 토 07:00 KST)
- 당일누적 적재 → 일봉 연계 → 텔레그램 순위 리포트
"""

import threading
import time
from datetime import datetime

from api.getUsInqRank import QRY_TP_DAY
from config.opsSettings import isBatchSchedEnabled
from config.usHoliday import isUsTradingDay, resolveUsSessionDtForKstMorning
from telegram.telSend import sendTelegramMsg
from utils.termLog import termLog
from utils.usDayInqRankLoad import loadUsDayInqRank
from utils.usDayInqRankStore import hasUsDayInqRank
from utils.usRankDayPrcLoad import DEFAULT_MONTHS, loadUsDayPrcForRankStks

# 한국시간 리포트 시각
_REPORT_HM = "07:00"

_schedulerStarted = False
# 리포트 완료한 KST 일자 YYYYMMDD
_reportedKstYmd = ""
_lastAttemptKey = ""


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="batch")
    except Exception as err:
        termLog(f"[usInqRankSched] 텔레그램 전송 실패: {err}")


def _loadDayPrcThenReport(stkItems, tradeDt):
    """일봉 적재 후 EMA·테마 포함 순위 리포트 + 급등락 이슈."""
    try:
        if stkItems:
            result = loadUsDayPrcForRankStks(
                stkItems,
                months=DEFAULT_MONTHS,
                endDt=tradeDt,
                skipIfEnough=True,
            )
            _notify(result.get("summary") or str(result))

        from utils.usRankEmaUtil import formatUsRankSummaryFromDb

        emaSum = formatUsRankSummaryFromDb(tradeDt, qryTp=QRY_TP_DAY, topN=20)
        if emaSum:
            _notify(emaSum)
        else:
            from utils.usRankEmaUtil import formatLatestUsRankFromDb

            lastMsg = formatLatestUsRankFromDb(qryTp=QRY_TP_DAY, topN=20)
            if lastMsg:
                _notify(lastMsg)
    except Exception as err:
        _notify(f"[미국순위일봉/리포트실패] {err}")

    # 간밤 급등·급락 테마 이슈 (usa20930) — 아침 07:00 요약
    try:
        from utils.usDayFluIssueLoad import loadUsDayFluIssue
        from utils.usDayFluIssueReport import formatUsFluIssueReport

        fluRes = loadUsDayFluIssue(tradeDt=tradeDt)
        if fluRes.get("ok"):
            _notify(fluRes.get("summary") or "[미국급등락] 적재완료")
            fluMsg = formatUsFluIssueReport(fluRes.get("tradeDt") or tradeDt)
            if fluMsg:
                _notify(fluMsg)
        else:
            # API 실패 시 기존 DB 요약이라도
            fluMsg = formatUsFluIssueReport(tradeDt)
            if fluMsg:
                _notify(
                    f"[미국급등락] API실패 → DB요약\n"
                    f"{fluRes.get('errMsg') or ''}\n\n{fluMsg}"
                )
            else:
                _notify(f"[미국급등락적재실패] {fluRes.get('errMsg')}")
    except Exception as err:
        _notify(f"[미국급등락이슈실패] {err}")


def _tryReportUsRankAt7(now=None):
    """
    KST 07:00 + 직전 미장 개장일 세션이 있으면 적재·리포트.
    반환: True=이번 틱에서 처리, False=스킵
    """
    global _reportedKstYmd, _lastAttemptKey

    dt = now or datetime.now()
    kstYmd = dt.strftime("%Y%m%d")
    curHm = dt.strftime("%H:%M")

    if _reportedKstYmd == kstYmd:
        return False

    if not isBatchSchedEnabled("usMorningLoad"):
        return False

    if curHm != _REPORT_HM:
        return False

    # 직전 미국 세션일 (미장 열린 날)
    usDt = resolveUsSessionDtForKstMorning(dt)
    if usDt is None or not isUsTradingDay(usDt):
        termLog(f"[usInqRankSched] {kstYmd} {_REPORT_HM} — 직전 미장 개장일 없음, 스킵")
        _reportedKstYmd = kstYmd
        return False

    usYmd = usDt.strftime("%Y%m%d")
    attemptKey = f"{kstYmd}:{_REPORT_HM}:{usYmd}"
    if attemptKey == _lastAttemptKey:
        return False
    _lastAttemptKey = attemptKey

    termLog(
        f"[usInqRankSched] KST {_REPORT_HM} 리포트 시작 "
        f"(미장세션 {usYmd})"
    )

    # 해당 세션 미적재면 조회·적재, 있으면 재조회해 최신화 시도
    result = loadUsDayInqRank(qryTp=QRY_TP_DAY)
    tradeDt = usYmd
    stkItems = []

    if result.get("ok"):
        tradeDt = result.get("tradeDt") or usYmd
        stkItems = result.get("stkItems") or []
        _notify(
            f"[미국순위자동적재] 완료\n"
            f"미장세션: {tradeDt} · {result.get('qryLabel')}\n"
            f"건수: {result.get('upsertCnt')}건 · 리포트시각 KST {_REPORT_HM}"
        )
    else:
        # 적재 실패해도 DB에 세션 데이터가 있으면 리포트만
        err = result.get("errMsg") or "적재실패"
        if hasUsDayInqRank(usYmd, QRY_TP_DAY):
            tradeDt = usYmd
            _notify(
                f"[미국순위자동적재] API실패 → 기존DB로 리포트\n"
                f"미장세션: {usYmd}\n원인: {err}"
            )
        elif hasUsDayInqRank(result.get("tradeDt") or "", QRY_TP_DAY):
            tradeDt = result.get("tradeDt")
            _notify(
                f"[미국순위자동적재] 부분실패 → DB리포트\n"
                f"일자: {tradeDt}\n원인: {err}"
            )
        else:
            _notify(f"[미국순위자동적재실패] 세션 {usYmd}\n{err}")
            _reportedKstYmd = kstYmd
            return True

    _reportedKstYmd = kstYmd

    # 일봉 + 종합 리포트는 백그라운드 (조회 한도 고려)
    threading.Thread(
        target=_loadDayPrcThenReport,
        args=(stkItems, tradeDt),
        name="usRankReport0700",
        daemon=True,
    ).start()
    return True


def runUsMorningLoadNow():
    """
    수동 실행 — KST 07:00 창·ON/OFF 무시.
    미장 세션 기준일은 자동과 동일한 규칙으로 잡고, 일봉·리포트까지 동기로 끝낸다.
    """
    global _reportedKstYmd

    dt = datetime.now()
    usDt = resolveUsSessionDtForKstMorning(dt)
    result = loadUsDayInqRank(qryTp=QRY_TP_DAY)
    usYmd = usDt.strftime("%Y%m%d") if usDt is not None else ""

    if result.get("ok"):
        tradeDt = result.get("tradeDt") or usYmd
        stkItems = result.get("stkItems") or []
    elif usYmd and hasUsDayInqRank(usYmd, QRY_TP_DAY):
        # API 실패라도 세션 데이터가 있으면 리포트만 다시 만든다
        tradeDt = usYmd
        stkItems = []
    else:
        return {
            "ok": False,
            "errMsg": result.get("errMsg") or "미국 순위 적재 실패",
        }

    _reportedKstYmd = dt.strftime("%Y%m%d")
    summary = (
        f"[미국순위:수동] 세션 {tradeDt}\n"
        f"건수: {result.get('upsertCnt') or 0}건 · 선정 {len(stkItems)}종목"
    )
    _notify(summary)
    _loadDayPrcThenReport(stkItems, tradeDt)
    return {"ok": True, "tradeDt": tradeDt, "summary": summary}


def _schedulerLoop():
    termLog(
        f"[usInqRankSched] 시작 — 미장 개장일 마감 후 KST {_REPORT_HM} 리포트"
    )
    while True:
        try:
            _tryReportUsRankAt7()
        except Exception as err:
            termLog(f"[usInqRankSched] 예외: {err}")
        time.sleep(20)


def startUsDayInqRankScheduler():
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    th = threading.Thread(target=_schedulerLoop, name="usDayInqRankSched", daemon=True)
    th.start()
    termLog("[usInqRankSched] 백그라운드 스케줄러 기동")
