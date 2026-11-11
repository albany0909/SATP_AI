"""차트 통합봉 조회. stk_cd 는 명세의 SOR 코드(6자리_AL). AI_DAY_STK_PRC 에 쓰지 않는다."""

from datetime import datetime, timedelta

from api.getDayChart import _hdr, _parseBody, getDayChart
from api.getPoleChart import _barFromRow, getPoleChart
from utils.chartBarPlan import CHART_LOOKBACK_DAYS, chartTailNeedsRefresh, unifiedStkCd
from utils.termLog import termLog
from utils.trRateLimit import acquireTrSlot

_DAY_KEY = "stk_dt_pole_chart_qry"
_POLE = {"week": ("ka10082", "stk_stk_pole_chart_qry"), "month": ("ka10083", "stk_mth_pole_chart_qry")}
_MAX_PAGES = {"day": 40, "week": 20, "month": 8}


def _cutoff(today):
    try:
        return (datetime.strptime(str(today)[:8], "%Y%m%d") - timedelta(days=CHART_LOOKBACK_DAYS)).strftime("%Y%m%d")
    except ValueError:
        return "19900101"


def _page(hostUrl, token, stk, baseDt, barTp, contYn, nextKey, tokenType, isPaper):
    acquireTrSlot("ka10081" if barTp == "day" else _POLE[barTp][0], mode="paper" if isPaper else "real")
    if barTp == "day":
        res = getDayChart(
            hostUrl, token, stk, baseDt, updStkpcTp="0",
            contYn=contYn, nextKey=nextKey, tokenType=tokenType,
        )
        ok, parsed = _parseBody(res)
        key = _DAY_KEY
    else:
        res = getPoleChart(
            hostUrl, token, stk, baseDt, barTp, updStkpcTp="0",
            contYn=contYn, nextKey=nextKey, tokenType=tokenType,
        )
        ok, parsed = _parseBody(res)
        key = _POLE[barTp][1]
    if not ok:
        return {"ok": False, "errMsg": parsed.get("errMsg"), "bars": [], "cont": "", "next": ""}
    bars = []
    for row in parsed.get(key) or []:
        bar = _barFromRow(row)
        if bar:
            bars.append(bar)
    return {
        "ok": True,
        "bars": bars,
        "cont": _hdr(res, "cont-yn", "N").upper(),
        "next": _hdr(res, "next-key", ""),
    }


def fetchUnifiedPeriod(hostUrl, token, stkCd, barTp, today, tokenType="bearer", isPaper=True, backfill=True):
    """최신 페이지는 항상 받고, 30년이 비어 있으면 이어서 받는다."""
    stk = unifiedStkCd(stkCd)
    if not stk:
        return {"ok": False, "errMsg": "종목코드 없음", "bars": []}
    cutoff = _cutoff(today)
    got = []
    seen = set()
    contYn = "N"
    nextKey = ""
    for page in range(_MAX_PAGES.get(barTp, 8)):
        if page > 0 and not backfill:
            break
        part = _page(hostUrl, token, stk, today, barTp, contYn, nextKey, tokenType, isPaper)
        if not part.get("ok"):
            if got:
                break
            return part
        for bar in part.get("bars") or []:
            if bar["tradeDt"] not in seen:
                seen.add(bar["tradeDt"])
                got.append(bar)
        oldest = min((bar["tradeDt"] for bar in got), default="")
        if oldest and oldest <= cutoff:
            break
        if part.get("cont") != "Y" or not part.get("next"):
            break
        if page == 0 and not backfill:
            break
        contYn = "Y"
        nextKey = part.get("next") or ""
    got.sort(key=lambda bar: bar["tradeDt"])
    if not got:
        return {"ok": False, "errMsg": "통합봉 없음", "bars": [], "stk": stk}
    return {"ok": True, "bars": got, "stk": stk, "barTp": barTp}


def refreshUnifiedCharts(stkCds, today=None, backfill=True):
    """표시 종목의 통합 일·주·월봉. 오늘 봉이 있으면 꼬리만 다시 덮어쓴다."""
    from utils.chartUnifStore import unifBarStat, upsertUnifBars
    from utils.dayPrcLoadService import acquireKiwoomSession
    from utils.stkCdUtil import normStkCd

    day = str(today or datetime.now().strftime("%Y%m%d"))[:8]
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {"ok": True, "stkCnt": 0, "upsertCnt": 0, "failCnt": 0, "summary": "대상 없음"}
    session = acquireKiwoomSession()
    if session is None:
        msg = "키움 토큰 없음 — 통합봉을 받지 못했다"
        termLog(f"[deskChart] {msg}")
        return {"ok": False, "errMsg": msg, "stkCnt": len(codes), "failCnt": len(codes), "upsertCnt": 0}
    _mode, hostUrl, token, tokenType = session
    isPaper = _mode == "paper"
    upsertCnt = 0
    failCnt = 0
    fails = []
    termLog(f"[deskChart] 통합봉 조회 시작 {len(codes)}종 ({day})")
    for code in codes:
        for barTp in ("day", "week", "month"):
            stat = unifBarStat(code, barTp)
            needTail = chartTailNeedsRefresh(stat.get("maxDt"), day)
            needOld = backfill and (int(stat.get("cnt") or 0) < 60 or str(stat.get("minDt") or "99999999") > _cutoff_of(day))
            if not needTail and not needOld:
                continue
            fetched = fetchUnifiedPeriod(
                hostUrl, token, code, barTp, day,
                tokenType=tokenType, isPaper=isPaper, backfill=needOld,
            )
            if not fetched.get("ok"):
                failCnt += 1
                err = fetched.get("errMsg") or "조회실패"
                fails.append(f"{code}:{barTp}:{err}")
                termLog(f"[deskChart] 통합봉 실패 {code} {barTp}: {err}")
                continue
            saved = upsertUnifBars(code, barTp, fetched.get("bars") or [])
            if not saved.get("ok"):
                failCnt += 1
                fails.append(f"{code}:{barTp}:{saved.get('errMsg')}")
                continue
            upsertCnt += int(saved.get("upsertCnt") or 0)
    summary = f"[deskChart] 통합봉 완료 {len(codes)}종 · UPSERT {upsertCnt} · 실패 {failCnt}"
    if fails:
        summary += " · " + "; ".join(fails[:3])
    termLog(summary)
    return {
        "ok": failCnt == 0 or upsertCnt > 0,
        "stkCnt": len(codes),
        "upsertCnt": upsertCnt,
        "failCnt": failCnt,
        "fails": fails,
        "errMsg": "" if not fails else "; ".join(fails[:3]),
        "startDt": _cutoff_of(day),
        "endDt": day,
        "summary": summary,
    }


def _cutoff_of(today):
    try:
        return (datetime.strptime(str(today)[:8], "%Y%m%d") - timedelta(days=CHART_LOOKBACK_DAYS)).strftime("%Y%m%d")
    except ValueError:
        return "19900101"
