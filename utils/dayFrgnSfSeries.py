"""외국인 현선물 20MA + 60/120MA · KOSPI 시계열."""

from datetime import datetime, timedelta

from utils.dayFrgnSfStore import listFrgnSf
from utils.dbConn import openDbConn


# 20MA 위에 120MA를 올리려면 거래일 약 140일 + 여유
_LOOKBACK_CAL_DAYS = 280
_WON_PER_EOK = 100_000_000.0


def _ymdToIso(ymd):
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _addCalDays(ymd, days):
    dt = datetime.strptime(ymd, "%Y%m%d") + timedelta(days=int(days))
    return dt.strftime("%Y%m%d")


def _sma(vals, win):
    """연속 거래일 SMA. 창이 덜 차면 None."""
    n = int(win)
    out = [None] * len(vals)
    if n <= 0:
        return out
    acc = 0.0
    q = []
    for i, raw in enumerate(vals):
        if raw is None:
            q = []
            acc = 0.0
            continue
        q.append(float(raw))
        acc += float(raw)
        if len(q) > n:
            acc -= q.pop(0)
        if len(q) == n:
            out[i] = acc / n
    return out


def _wonToEok(raw):
    if raw is None:
        return None
    try:
        return float(raw) / _WON_PER_EOK
    except (TypeError, ValueError):
        return None


def _loadKospiMap(startDt, endDt):
    """일자 → 종가."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, CLOSE_PRC
                FROM AI_DAY_INDS_PRC
                WHERE MKT_TP='KR' AND INDS_CD='001'
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (startDt, endDt),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    out = {}
    for r in rows:
        ymd = str(r.get("TRADE_DT") or "")
        try:
            px = float(r.get("CLOSE_PRC") or 0)
        except (TypeError, ValueError):
            continue
        if ymd and px > 0:
            out[ymd] = px
    return out


def _crossMarks(ma60, ma120, dates):
    """60MA가 120MA를 상향/하향 돌파한 날. 참고 차트 원 표시용."""
    marks = []
    prevUp = None
    for i in range(len(dates)):
        a = ma60[i]
        b = ma120[i]
        if a is None or b is None:
            continue
        up = a > b
        if prevUp is not None and up != prevUp:
            marks.append(
                {
                    "dt": dates[i],
                    "date": _ymdToIso(dates[i]),
                    "kind": "golden" if up else "dead",
                    "ma60": round(a, 2),
                    "ma120": round(b, 2),
                }
            )
        prevUp = up
    return marks


def buildFrgnSfSeries(startDt, endDt):
    """
    화면용 시계열.
    막대 = 현선물 합의 20MA(억원), 선 = 그 20MA의 60/120MA, 우측 = KOSPI.
    """
    start = str(startDt or "").replace("-", "")[:8]
    end = str(endDt or "").replace("-", "")[:8]
    if len(start) != 8 or len(end) != 8:
        return {"ok": False, "errMsg": "일자 형식 YYYYMMDD", "points": []}
    if start > end:
        start, end = end, start

    lookStart = _addCalDays(start, -_LOOKBACK_CAL_DAYS)
    kospi = _loadKospiMap(lookStart, end)
    invRows = listFrgnSf(lookStart, end)
    invByDt = {}
    for r in invRows:
        ymd = str(r.get("TRADE_DT") or "")
        total = r.get("SPOT_FUT_NET")
        if total is None:
            spot = int(r.get("SPOT_NET") or 0)
            fut = int(r.get("FUT_NET") or 0)
            total = spot + fut
        invByDt[ymd] = {
            "spot": _wonToEok(r.get("SPOT_NET")),
            "fut": _wonToEok(r.get("FUT_NET")),
            "total": _wonToEok(total),
        }

    # 이평은 수급이 있는 거래일만. 지수만 있는 날은 차트에 KOSPI만 그림
    invDts = sorted(invByDt.keys())
    dailyEok = [invByDt[d].get("total") for d in invDts]
    ma20 = _sma(dailyEok, 20)
    ma60 = _sma(ma20, 60)
    ma120 = _sma(ma20, 120)
    ma20By = {invDts[i]: ma20[i] for i in range(len(invDts))}
    ma60By = {invDts[i]: ma60[i] for i in range(len(invDts))}
    ma120By = {invDts[i]: ma120[i] for i in range(len(invDts))}
    crossesAll = _crossMarks(ma60, ma120, invDts)

    allDts = sorted(set(kospi.keys()) | set(invByDt.keys()))
    points = []
    for ymd in allDts:
        if ymd < start or ymd > end:
            continue
        rec = invByDt.get(ymd) or {}
        points.append(
            {
                "dt": ymd,
                "date": _ymdToIso(ymd),
                "kospi": round(kospi[ymd], 2) if ymd in kospi else None,
                "dailyEok": None
                if rec.get("total") is None
                else round(rec["total"], 2),
                "spotEok": None
                if rec.get("spot") is None
                else round(rec["spot"], 2),
                "futEok": None if rec.get("fut") is None else round(rec["fut"], 2),
                "ma20": None if ma20By.get(ymd) is None else round(ma20By[ymd], 2),
                "ma60": None if ma60By.get(ymd) is None else round(ma60By[ymd], 2),
                "ma120": None if ma120By.get(ymd) is None else round(ma120By[ymd], 2),
            }
        )

    crosses = [c for c in crossesAll if start <= c["dt"] <= end]
    invCnt = sum(1 for p in points if p.get("ma20") is not None)
    kospiCnt = sum(1 for p in points if p.get("kospi") is not None)
    futCnt = sum(1 for p in points if p.get("futEok") is not None)
    note = ""
    if kospiCnt == 0:
        note = "KOSPI 일봉 없음 (ka20006 적재 필요)"
    elif invCnt == 0:
        note = "외국인 수급 없음. 화면에서 수급 적재를 누르세요."
    elif futCnt == 0:
        note = "현물(코스피 외국인). 선물 일별은 미적재."

    return {
        "ok": True,
        "startDt": start,
        "endDt": end,
        "startDate": _ymdToIso(start),
        "endDate": _ymdToIso(end),
        "points": points,
        "crosses": crosses,
        "rowCnt": len(points),
        "invCnt": invCnt,
        "kospiCnt": kospiCnt,
        "futCnt": futCnt,
        "note": note,
        "unit": "억원",
    }
