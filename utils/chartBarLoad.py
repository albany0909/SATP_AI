"""차트용 일봉·주봉·월봉을 함께 받는다.

일봉은 기존 순위 일봉 적재(ka10086 → AI_DAY_STK_PRC).
주봉은 ka10082 → AI_WK_STK_PRC, 월봉은 ka10083 → AI_MON_STK_PRC.
"""

from __future__ import annotations

import time
from datetime import datetime

from utils.chartBarPlan import CHART_PERIODS, poleFetchDecision
from utils.termLog import termLog

_MIN_BARS = {"week": 60, "month": 36}


def loadDeskChartBundle(stkCd, endYmd=None):
    """DB 에 있는 일·주·월봉. 실패는 로그하고 그 주기만 비운다."""
    bundle = {"day": [], "week": [], "month": [], "errors": []}
    try:
        from ui.deskStkChart import loadDeskDayBars

        bundle["day"] = list(loadDeskDayBars(stkCd, endYmd=endYmd) or [])
    except Exception as err:
        termLog(f"[deskChart] 일봉 DB 실패 {stkCd}: {err}")
        bundle["errors"].append(f"day:{err}")
    try:
        from utils.poleStkPrcStore import loadPoleBars

        end = str(endYmd or datetime.now().strftime("%Y%m%d"))[:8]
        bundle["week"] = list(loadPoleBars(stkCd, "week", endDt=end) or [])
        bundle["month"] = list(loadPoleBars(stkCd, "month", endDt=end) or [])
    except Exception as err:
        termLog(f"[deskChart] 주월봉 DB 실패 {stkCd}: {err}")
        bundle["errors"].append(f"pole:{err}")
    return bundle


def loadWeekMonthForCodes(stkCds, refetch=False, endDt=None, onProgress=None):
    """주봉·월봉을 키움에서 받아 저장. 최신이면 건너뛴다."""
    from api.getPoleChart import fetchPoleBars
    from utils.dayPrcLoadService import acquireKiwoomSession
    from utils.poleStkPrcStore import poleBarStat, rowsFromBars, upsertPoleRows
    from utils.stkCdUtil import normStkCd

    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    today = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    if not codes:
        return {
            "ok": True,
            "stkCnt": 0,
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
            "periods": ["week", "month"],
            "summary": "[차트주월] 대상 없음",
        }
    session = acquireKiwoomSession()
    if session is None:
        msg = "키움 토큰 없음 — 주봉·월봉을 받지 못했다"
        termLog(f"[차트주월] {msg}")
        return {
            "ok": False,
            "errMsg": msg,
            "stkCnt": len(codes),
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": len(codes),
            "upsertCnt": 0,
            "periods": ["week", "month"],
        }
    mode, hostUrl, token, tokenType = session
    isPaper = mode == "paper"
    waitSec = 1.05 if isPaper else 0.35
    loadedCnt = 0
    skipCnt = 0
    failCnt = 0
    upsertCnt = 0
    fails = []
    called = 0
    termLog(f"[차트주월] {len(codes)}종 주봉·월봉 시작 ({today})")
    for stkCd in codes:
        for barTp in ("week", "month"):
            decision = poleFetchDecision(poleBarStat(stkCd, barTp), today, barTp, refetch=refetch)
            if decision == "skip":
                skipCnt += 1
                continue
            if called > 0:
                time.sleep(waitSec)
            called += 1
            fetched = fetchPoleBars(
                hostUrl,
                token,
                stkCd,
                today,
                barTp,
                tokenType=tokenType,
                isPaper=isPaper,
                minBars=_MIN_BARS[barTp],
            )
            if not fetched.get("ok"):
                failCnt += 1
                err = fetched.get("errMsg") or "조회실패"
                fails.append(f"{stkCd}:{barTp}:{err}")
                termLog(f"[차트주월] fail {stkCd} {barTp}: {err}")
                continue
            saved = upsertPoleRows(barTp, rowsFromBars(stkCd, fetched.get("bars") or []))
            if not saved.get("ok"):
                failCnt += 1
                err = saved.get("errMsg") or "DB실패"
                fails.append(f"{stkCd}:{barTp}:{err}")
                termLog(f"[차트주월] db fail {stkCd} {barTp}: {err}")
                continue
            loadedCnt += 1
            upsertCnt += int(saved.get("upsertCnt") or 0)
    summary = (
        f"[차트주월] 대상 {len(codes)}종 · 적재 {loadedCnt} · "
        f"스킵 {skipCnt} · 실패 {failCnt} · UPSERT {upsertCnt}"
    )
    if fails:
        summary += " · " + "; ".join(fails[:3])
    termLog(summary)
    if onProgress:
        onProgress(summary)
    return {
        "ok": failCnt == 0 or loadedCnt > 0 or skipCnt > 0,
        "stkCnt": len(codes),
        "loadedCnt": loadedCnt,
        "skipCnt": skipCnt,
        "failCnt": failCnt,
        "upsertCnt": upsertCnt,
        "fails": fails,
        "errMsg": "" if not fails else "; ".join(fails[:3]),
        "periods": ["week", "month"],
        "summary": summary,
    }


def ensureChartBars(stkCd, months=None, refetch=True):
    """한 종목의 일·주·월봉을 같이 받아 저장한다."""
    from utils.rankDayPrcLoad import DEFAULT_MONTHS, loadDayPrcForRankStks

    span = DEFAULT_MONTHS if months is None else months
    day = loadDayPrcForRankStks(
        [stkCd],
        months=span,
        skipIfEnough=not refetch,
        refetchLastDay=True,
    ) or {}
    pole = loadWeekMonthForCodes([stkCd], refetch=refetch) or {}
    fails = []
    if not day.get("ok"):
        fails.append(str(day.get("errMsg") or "일봉 실패"))
    if int(day.get("failCnt") or 0) > 0:
        fails.extend(day.get("fails") or [])
    if not pole.get("ok"):
        fails.append(str(pole.get("errMsg") or "주월봉 실패"))
    if int(pole.get("failCnt") or 0) > 0:
        fails.extend(pole.get("fails") or [])
    err = "; ".join(str(item) for item in fails if item)[:180]
    return {
        "ok": not err,
        "failCnt": int(day.get("failCnt") or 0) + int(pole.get("failCnt") or 0),
        "upsertCnt": int(day.get("upsertCnt") or 0) + int(pole.get("upsertCnt") or 0),
        "startDt": day.get("startDt") or "",
        "endDt": day.get("endDt") or "",
        "errMsg": err,
        "periods": list(CHART_PERIODS),
        "day": day,
        "pole": pole,
    }
