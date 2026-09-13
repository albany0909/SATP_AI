"""
이평터치 1차 익절(halfTpPct) 후보 통계.

최근 10거래일 **매수** 라운드 중, 1차 절반 전에 -2% 하드손절 난 건의
손절 전 최대수익률(MFE) 평균을 장마감 복기에서 실매매 1차에 넣는다.
"""

from utils.maTouchMfe import pnlPctFromAvg
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 1차 절반 익절 후보 % (도달률 표시용)
HALF_TP_CAND = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
# 매수일 기준 롤링 거래일
HALF_TP_BUY_LOOKBACK = 10
# 1차 전 하드손절 % (통계 가정)
HALF_TP_HARD_STOP_PCT = 2.0
# 예전 누적 시작일 (조회 실패 폴백)
HALF_TP_PCT_FROM_YMD = "20260824"


def _flt(val):
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def resolveHalfTpBuyFromYmd(endDt=None):
    """최근 HALF_TP_BUY_LOOKBACK 거래일의 첫날 (매수 ENTRY_DT 하한)."""
    from config.krHoliday import tradingDaysBack

    start, _days = tradingDaysBack(HALF_TP_BUY_LOOKBACK, endDt)
    return start or HALF_TP_PCT_FROM_YMD


def resolveHalfTpStatFromYmd():
    """
    1차 익절 통계 시작일.
    최근 10거래일 매수 구간. 예전 호출부와 호환.
    """
    return resolveHalfTpBuyFromYmd()


def _median(nums):
    arr = sorted(nums)
    n = len(arr)
    if n <= 0:
        return None
    mid = n // 2
    if n % 2:
        return arr[mid]
    return (arr[mid - 1] + arr[mid]) / 2.0


def _mfeOfRow(row, useMinBars=True):
    """저장된 MAX_PNL_PCT. 없으면 고가, 그다음 분봉 고가."""
    stored = _flt(row.get("MAX_PNL_PCT"))
    if stored is not None:
        return stored
    avgPx = _flt(row.get("AVG_BUY_PX"))
    maxPx = _flt(row.get("MAX_PX"))
    if avgPx and maxPx:
        got = pnlPctFromAvg(maxPx, avgPx)
        if got is not None:
            return got
    if not useMinBars:
        return None
    return _mfeFromMinBars(row)


def _mfeFromMinBars(row):
    """AI_MIN_STK_PRC 1분 고가 / 평단. 분봉 없으면 None."""
    cd = normStkCd(row.get("STK_CD"))
    avgPx = _flt(row.get("AVG_BUY_PX"))
    entryDt = str(row.get("ENTRY_DT") or "")[:8]
    exitDt = str(row.get("EXIT_DT") or "")[:8]
    if not cd or not avgPx or not entryDt or not exitDt:
        return None
    buyTm = str(row.get("ENTRY_TM") or "").zfill(6)[-6:] if row.get("ENTRY_TM") else ""
    exitTm = str(row.get("EXIT_TM") or "235959").zfill(6)[-6:]
    conn = None
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        with conn.cursor() as cur:
            if not buyTm or buyTm == "000000":
                cur.execute(
                    """
                    SELECT MIN(EVT_TM) AS TM
                    FROM AI_ALG_EVT
                    WHERE ALG_CD='MTS01' AND STK_CD=%s AND TRADE_DT=%s
                      AND EVT_TP='BUY'
                    """,
                    (cd, entryDt),
                )
                buyTm = str((cur.fetchone() or {}).get("TM") or "090000").zfill(6)[-6:]
            cur.execute(
                """
                SELECT MAX(HIGH_PRC) AS HI
                FROM AI_MIN_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE='1'
                  AND HIGH_PRC IS NOT NULL
                  AND TRADE_DT BETWEEN %s AND %s
                  AND NOT (TRADE_DT=%s AND BAR_TM < %s)
                  AND NOT (TRADE_DT=%s AND BAR_TM > %s)
                """,
                (cd, entryDt, exitDt, entryDt, buyTm, exitDt, exitTm),
            )
            hit = cur.fetchone() or {}
        hi = _flt(hit.get("HI"))
        if not hi:
            return None
        return pnlPctFromAvg(hi, avgPx)
    except Exception as err:
        termLog(f"[maTouchMfe] 분봉 고점 조회 실패 {cd}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _isPreHalfHardStop(row):
    """1차 절반 전에 손절(STOP/HARD)로 전량된 라운드."""
    if str(row.get("HALF_YN") or "N").upper() == "Y":
        return False
    reason = str(row.get("EXIT_REASON") or "").upper()
    return "STOP" in reason or "HARD" in reason


def summarizeHalfTpFromRows(rows, cand=None, useMinBars=True):
    """
    청산 행 → MFE 분포·후보 도달률.
    권고는 1차 절반 전 손절 건의 MFE 평균 (손절 -2% 가정).
    """
    ths = list(cand or HALF_TP_CAND)
    mfes = []
    stopMfes = []
    for row in rows or []:
        mfe = _mfeOfRow(row, useMinBars=useMinBars)
        if mfe is None:
            continue
        mfes.append(mfe)
        if _isPreHalfHardStop(row):
            stopMfes.append(mfe)
    n = len(mfes)
    hits = []
    for t in ths:
        c = sum(1 for x in mfes if x + 1e-9 >= t)
        hits.append(
            {
                "pct": t,
                "cnt": c,
                "rate": (c / n * 100.0) if n else 0.0,
            }
        )
    # 1차 전에 -2% 손절 난 건의 고점 평균 → 1차 익절 후보
    suggest = None
    avgStop = (sum(stopMfes) / len(stopMfes)) if stopMfes else None
    medStop = _median(stopMfes) if stopMfes else None
    if avgStop is not None and avgStop > 0:
        suggest = round(float(avgStop), 1)
        if suggest < 0.5:
            suggest = 0.5
        if suggest > 5:
            suggest = 5.0
    return {
        "n": n,
        "stopN": len(stopMfes),
        "avgMfe": (sum(mfes) / n) if n else None,
        "medMfe": _median(mfes),
        "avgStopMfe": avgStop,
        "medStopMfe": medStop,
        "hits": hits,
        "suggestPct": suggest,
        "sampleOk": len(stopMfes) >= 5,
        "lookbackDays": HALF_TP_BUY_LOOKBACK,
        "hardStopPct": HALF_TP_HARD_STOP_PCT,
    }


def mfeOfRow(row):
    """청산 행의 MFE %. 저장된 MAX_PNL_PCT, 없으면 고가/분봉 보강."""
    return _mfeOfRow(row)


def formatHalfTpStatsLines(rows, halfTpNow=3.0, title=None, useMinBars=True):
    """텔레그램/터미널용 줄 목록."""
    sumr = summarizeHalfTpFromRows(rows, useMinBars=useMinBars)
    n = int(sumr.get("n") or 0)
    head = title or "[1차 익절 통계] 매수~청산 최대수익률(MFE)"
    lines = ["", head]
    if n <= 0:
        lines.append("  고점 데이터 없음 — 모의 청산이 쌓이면 산출")
        return lines
    med = sumr.get("medMfe")
    avg = sumr.get("avgMfe")
    medS = sumr.get("medStopMfe")
    avgS = sumr.get("avgStopMfe")
    lines.append(
        f"  표본 {n}건 · 중앙 {med:+.2f}% · 평균 {avg:+.2f}%"
        + (f" · 1차전손절고점평균 {avgS:+.2f}%" if avgS is not None else "")
        + (f" · 손절중앙 {medS:+.2f}%" if medS is not None else "")
    )
    now = float(halfTpNow or 3)
    for h in sumr.get("hits") or []:
        mark = " ←현재" if abs(float(h["pct"]) - now) < 1e-9 else ""
        lines.append(
            f"  +{h['pct']:g}% 도달 {h['cnt']}/{n} ({h['rate']:.0f}%){mark}"
        )
    sug = sumr.get("suggestPct")
    if sug is not None:
        note = "" if sumr.get("sampleOk") else " (표본 부족, 참고)"
        lines.append(
            f"  권고 1차 익절 +{sug:g}%{note} "
            f"(최근 {HALF_TP_BUY_LOOKBACK}거래일 매수 · "
            f"1차전 -{HALF_TP_HARD_STOP_PCT:g}%손절 고점 평균) "
            "— 장마감 복기에서 실매매 1차에 반영"
        )
    else:
        lines.append("  권고 없음 (고점이 거의 0% 이하)")
    return lines


def applySuggestHalfTp(suggestPct, tradeDt=None, sampleN=0, fromYmd=None):
    """
    MFE 권고 % 를 이평터치 실매매 1차에 저장.
    spec JSON 은 건드리지 않고 settings.maTouchLive 만 덮는다.
    """
    if suggestPct is None:
        return None
    try:
        pct = float(suggestPct)
    except (TypeError, ValueError):
        return None
    if pct < 0.5:
        pct = 0.5
    if pct > 5:
        pct = 5.0
    from config.settingsStore import setMaTouchHalfTpFromStat

    startYmd = str(fromYmd or "").replace("-", "")[:8] or resolveHalfTpBuyFromYmd(
        tradeDt
    )
    setMaTouchHalfTpFromStat(
        pct, tradeDt=tradeDt, sampleN=sampleN, fromYmd=startYmd
    )
    try:
        from trading.maTouchLive import invalidateLiveParams

        invalidateLiveParams()
    except Exception:
        pass
    termLog(
        f"[maTouchMfe] 1차익절 +{pct:g}% 반영 (통계치) "
        f"{startYmd}~ ymd={str(tradeDt or '')[:8] or '-'} n={int(sampleN or 0)}"
    )
    return pct


def refreshHalfTpFromLiveTrades(force=False, tradeDt=None):
    """
    청산 이력 MFE 로 1차를 다시 계산.
    표본은 최근 10거래일 매수 · 1차 전 손절 고점 평균.
    force=False 이면 이미 통계치가 있으면 건너뛴다 (장중 재시작용).
    force=True 는 장마감 복기.
    """
    from config.settingsStore import getMaTouchLive

    ymd = str(tradeDt or "").replace("-", "")[:8]
    fromYmd = resolveHalfTpBuyFromYmd(ymd or None)
    live = getMaTouchLive() or {}
    # 장중 재시작 때 1차를 다시 쓰지 않는다. 16:05 복기만 갱신.
    if (
        not force
        and live.get("halfTpFromStat")
        and live.get("halfTpPct") is not None
    ):
        try:
            return float(live.get("halfTpPct"))
        except (TypeError, ValueError):
            pass
    try:
        from utils.stratLiveTradeStore import listLiveTrades

        rows = listLiveTrades(algCd="MTS01", entryDtFrom=fromYmd, limit=500) or []
    except Exception as err:
        termLog(f"[maTouchMfe] 청산이력 조회 실패: {err}")
        return None
    sumr = summarizeHalfTpFromRows(rows, useMinBars=True)
    sug = sumr.get("suggestPct")
    if sug is None:
        termLog("[maTouchMfe] 권고 없음 — 기존 1차 유지")
        return None
    n = int(sumr.get("stopN") or sumr.get("n") or 0)
    if not sumr.get("sampleOk"):
        termLog(
            f"[maTouchMfe] 1차전손절 {n}건 (5건 미만) {fromYmd}~ — "
            f"권고 +{float(sug):g}% 그대로 반영"
        )
    return applySuggestHalfTp(sug, tradeDt=ymd, sampleN=n, fromYmd=fromYmd)

