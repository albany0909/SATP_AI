"""실계좌·관심종목 일봉 증분 적재.

DB에 이미 있는 마지막 거래일 이후(당일 포함 정정)만 키움에서 받아 AI_DAY_STK_PRC 에 붙인다.
차트(loadDeskDayBars)가 비지 않도록 장마감 후 매일 돌린다.
"""

from __future__ import annotations

from datetime import datetime

from utils.rankDayPrcLoad import DEFAULT_MONTHS, loadDayPrcForRankStks
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog


def collectWatchHoldCds():
    """실계좌 관심(WATCH/TRADE) ∪ 활성 관심종목 코드."""
    cds = []
    seen = set()

    def _add(raw):
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            cds.append(cd)

    try:
        from trading.realAcntMgr import listWatchCds

        for cd in listWatchCds() or []:
            _add(cd)
    except Exception as err:
        termLog(f"[watchDayPrc] 실계좌 목록 실패: {err}")

    try:
        from utils.watchStkStore import listActiveWatch

        for row in listActiveWatch() or []:
            _add(row.get("STK_CD") or row.get("stkCd"))
    except Exception as err:
        termLog(f"[watchDayPrc] 관심종목 목록 실패: {err}")

    # 원장·실보유에만 있는 종목도 차트용 일봉 대상에 포함
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                for sql in (
                    "SELECT DISTINCT STK_CD FROM AI_LIVE_TRADE WHERE IFNULL(QTY,0) > 0",
                    "SELECT DISTINCT STK_CD FROM AI_STRAT_ORD_LEDGER WHERE IFNULL(OPEN_QTY,0) > 0",
                ):
                    try:
                        cur.execute(sql)
                        for row in cur.fetchall() or []:
                            _add(row.get("STK_CD"))
                    except Exception:
                        pass
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[watchDayPrc] 보유원장 목록 실패: {err}")

    return cds


def loadDayPrcForWatchHolds(
    endDt=None,
    months=DEFAULT_MONTHS,
    refetchLastDay=True,
    onProgress=None,
):
    """
    관심·실계좌 종목 일봉 증분 적재.
    skipIfEnough=True → maxDt 이후분만 조회. refetchLastDay로 당일봉 정정.
    """
    from utils.chartBarPlan import collectSurgeChartCds, mergeLoadCodes

    cds = mergeLoadCodes(collectWatchHoldCds(), collectSurgeChartCds())
    ymd = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    if not cds:
        return {
            "ok": True,
            "stkCnt": 0,
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
            "summary": "[관심일봉] 대상 없음",
            "tradeDt": ymd,
        }
    termLog(f"[watchDayPrc] 관심·급등 {len(cds)}종 · 증분 적재 시작 ({ymd})")
    result = loadDayPrcForRankStks(
        cds,
        months=months,
        endDt=ymd,
        skipIfEnough=True,
        refetchLastDay=bool(refetchLastDay),
        onProgress=onProgress,
    )
    result = dict(result or {})
    result["tradeDt"] = ymd
    result["source"] = "watchHold"
    if result.get("ok"):
        result["summary"] = (
            f"[관심일봉] 대상 {len(cds)}종 · "
            f"적재 {result.get('loadedCnt') or 0} · "
            f"스킵 {result.get('skipCnt') or 0} · "
            f"실패 {result.get('failCnt') or 0}"
        )
        termLog(result["summary"])
    try:
        from utils.chartBarLoad import loadWeekMonthForCodes

        pole = loadWeekMonthForCodes(cds, refetch=False, endDt=ymd)
        if pole.get("summary"):
            termLog(pole.get("summary"))
        result["pole"] = pole
    except Exception as err:
        termLog(f"[watchDayPrc] 주월봉 실패: {err}")
    return result
