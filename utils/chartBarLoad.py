"""차트용 일봉·주봉·월봉을 함께 받는다.

일봉은 기존 순위 일봉 적재(ka10086 → AI_DAY_STK_PRC).
주봉은 ka10082 → AI_WK_STK_PRC, 월봉은 ka10083 → AI_MON_STK_PRC.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from utils.chartBarPlan import CHART_PERIODS, poleFetchDecision
from utils.termLog import termLog

_MIN_BARS = {"week": 60, "month": 36}


def loadStoredDayBars(stkCd, endYmd=None):
    """AI_DAY_STK_PRC 일봉. UI·백테스트 엔진을 불러오지 않는다."""
    from datetime import timedelta

    from utils.dbConn import openDbConn
    from utils.stkCdUtil import normStkCd

    cd = normStkCd(stkCd)
    if not cd:
        return []
    end = str(endYmd or datetime.now().strftime("%Y%m%d"))[:8]
    try:
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=365 * 20)).strftime("%Y%m%d")
    except ValueError:
        start = end
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0'
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (cd, start, end),
            )
            rows = cur.fetchall() or []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    bars = []
    for row in rows:
        if row.get("CLOSE_PRC") is None:
            continue
        dt = str(row.get("TRADE_DT") or "")[:8]
        bars.append(
            {
                "tradeDt": dt,
                "open": float(row.get("OPEN_PRC") or 0),
                "high": float(row.get("HIGH_PRC") or 0),
                "low": float(row.get("LOW_PRC") or 0),
                "close": float(row.get("CLOSE_PRC") or 0),
                "qty": int(row.get("TRDE_QTY") or 0),
            }
        )
    return bars


def loadDeskChartBundle(stkCd, endYmd=None):
    """차트 통합봉만 읽는다. 매매용 AI_DAY_STK_PRC 는 보지 않는다."""
    from datetime import timedelta

    from utils.chartBarPlan import CHART_LOOKBACK_DAYS
    from utils.chartUnifStore import loadUnifBars

    bundle = {"day": [], "week": [], "month": [], "errors": []}
    end = str(endYmd or datetime.now().strftime("%Y%m%d"))[:8]
    try:
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=CHART_LOOKBACK_DAYS)).strftime("%Y%m%d")
    except ValueError:
        start = "19900101"
    for key in ("day", "week", "month"):
        try:
            bundle[key] = list(loadUnifBars(stkCd, key, startDt=start, endDt=end) or [])
        except Exception as err:
            termLog(f"[deskChart] 통합 {key} 실패 {stkCd}: {err}")
            bundle["errors"].append(f"{key}:{err}")
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
    """한 종목의 통합 일·주·월봉. 매매 일봉 테이블은 건드리지 않는다."""
    from utils.chartUnifFetch import refreshUnifiedCharts

    got = refreshUnifiedCharts([stkCd], backfill=True) or {}
    return {
        "ok": bool(got.get("ok")),
        "failCnt": int(got.get("failCnt") or 0),
        "upsertCnt": int(got.get("upsertCnt") or 0),
        "startDt": got.get("startDt") or "",
        "endDt": got.get("endDt") or "",
        "errMsg": got.get("errMsg") or "",
        "periods": list(CHART_PERIODS),
        "unified": got,
    }


_surgeLoadLock = threading.Lock()
_surgeLoadBusy = False
_surgeLoadPending = None


def runSurgeChartLoad(codes, reason="surge"):
    """표시 종목의 통합 일·주·월봉. 오늘 봉은 다시 덮어쓴다. 매매 일봉은 쓰지 않는다."""
    from utils.chartBarPlan import mergeLoadCodes
    from utils.chartUnifFetch import refreshUnifiedCharts

    wanted = mergeLoadCodes(codes)
    termLog(f"[deskChart] 급등주 장중 적재 시작 {len(wanted)}종 ({reason})")
    if not wanted:
        return {"ok": True, "stkCnt": 0, "reason": reason, "summary": "대상 없음"}
    got = refreshUnifiedCharts(wanted, backfill=True) or {}
    if not got.get("ok"):
        termLog(f"[deskChart] 통합봉 적재 실패 ({reason}): {got.get('errMsg')}")
    return {"ok": bool(got.get("ok")), "unified": got, "reason": reason}


def requestSurgeChartBars(codes, reason="startup"):
    """장중 백그라운드 1건. 이미 돌면 끝난 뒤 한 번 더 받는다."""
    global _surgeLoadBusy, _surgeLoadPending
    from utils.chartBarPlan import intradayChartWanted, mergeLoadCodes

    if not intradayChartWanted(reason):
        return {"started": False, "reason": "SKIP"}
    wanted = mergeLoadCodes(codes)
    with _surgeLoadLock:
        if _surgeLoadBusy:
            _surgeLoadPending = (wanted, reason)
            termLog(f"[deskChart] 급등주 장중 적재 진행 중 — {reason} {len(wanted)}종은 이어서")
            return {"started": False, "reason": "BUSY", "codes": wanted}
        _surgeLoadBusy = True
    termLog(f"[deskChart] 급등주 장중 적재 예약 {len(wanted)}종 ({reason})")

    def _run(batch, why):
        global _surgeLoadBusy, _surgeLoadPending
        try:
            runSurgeChartLoad(batch, why)
        except Exception as err:
            termLog(f"[deskChart] 급등주 장중 적재 실패 ({why}): {err}")
        finally:
            with _surgeLoadLock:
                _surgeLoadBusy = False
                pending = _surgeLoadPending
                _surgeLoadPending = None
        if pending:
            requestSurgeChartBars(pending[0], pending[1])

    threading.Thread(target=_run, args=(wanted, reason), name="surgeChartBars", daemon=True).start()
    return {"started": True, "reason": reason, "codes": wanted}
