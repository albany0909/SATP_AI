"""전일까지 확정 일봉 SMA 스냅샷 — 관심종목 그리드(5/10/20) 보강용."""

from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd

_DEFAULT_PERIODS = (5, 10, 20)


def _smaInt(raw):
    if raw is None:
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def loadPriorSmaMap(stkCds, periods=None, endDt=None, _forceLocal=False):
    """
    TRADE_DT < endDt 종가로 SMA.
    반환: {stkCd: {sma5, sma10, sma20, lastClose, fluRt, lastDt}}
    client/서버모드: deskDataApi(STP SATPAIIQ040).
    """
    if not _forceLocal:
        try:
            from config.settingsStore import getDeskDataViaServer, isDeskClient
            from utils.dbConn import isLocalDbEnabled

            if isDeskClient() or getDeskDataViaServer() or (not isLocalDbEnabled()):
                from utils.deskDataApi import loadPriorSmaMap as _via

                return _via(stkCds, periods=periods, endDt=endDt)
        except Exception:
            try:
                from utils.dbConn import isLocalDbEnabled

                if not isLocalDbEnabled():
                    return {}
            except Exception:
                pass

    pers = tuple(int(p) for p in (periods or _DEFAULT_PERIODS) if p)
    need = max(pers) if pers else 20
    today = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {}

    out = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for cd in codes:
                cur.execute(
                    """
                    SELECT TRADE_DT, CLOSE_PRC, FLU_RT
                    FROM AI_DAY_STK_PRC
                    WHERE STK_CD=%s AND INDC_TP=%s AND TRADE_DT < %s
                      AND CLOSE_PRC IS NOT NULL
                    ORDER BY TRADE_DT DESC
                    LIMIT %s
                    """,
                    (cd, INDC_TP_QTY, today, int(need)),
                )
                rows = cur.fetchall() or []
                closes = []
                lastDt = None
                lastClose = None
                fluRt = None
                for r in reversed(rows):
                    try:
                        px = abs(float(r["CLOSE_PRC"]))
                    except (TypeError, ValueError):
                        continue
                    if px <= 0:
                        continue
                    closes.append(px)
                    lastDt = str(r.get("TRADE_DT") or "")[:8]
                    lastClose = px
                    fluRaw = r.get("FLU_RT")
                    try:
                        fluRt = float(fluRaw) if fluRaw is not None else None
                    except (TypeError, ValueError):
                        fluRt = None
                rec = {
                    "sma5": None,
                    "sma10": None,
                    "sma20": None,
                    "lastClose": _smaInt(lastClose),
                    "fluRt": fluRt,
                    "lastDt": lastDt,
                }
                for per in pers:
                    if len(closes) >= per:
                        rec[f"sma{per}"] = _smaInt(
                            sum(closes[-per:]) / float(per)
                        )
                out[cd] = rec
    except Exception:
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


# Wilder RSI14 는 초반 SMA 시드 후 RMA 평활이라 20봉만 쓰면 HTS와 크게 어긋난다.
_RSI_LOOKBACK = 400
_RSI_IN_CHUNK = 50


def loadRsiCloseHist(stkCds, need=None, endDt=None, _forceLocal=False):
    """
    일봉 종가 히스토리 (오래된→최신). 당일 봉이 있으면 포함.
    반환: {stkCd: {"closes": [float, ...], "lastDt": "YYYYMMDD"}}
    client/서버모드: deskDataApi(STP SATPAIIQ041).
    """
    if not _forceLocal:
        try:
            from config.settingsStore import getDeskDataViaServer, isDeskClient
            from utils.dbConn import isLocalDbEnabled

            if isDeskClient() or getDeskDataViaServer() or (not isLocalDbEnabled()):
                from utils.deskDataApi import loadRsiCloseHist as _via

                return _via(stkCds, need=need, endDt=endDt)
        except Exception:
            try:
                from utils.dbConn import isLocalDbEnabled

                if not isLocalDbEnabled():
                    return {}
            except Exception:
                pass

    today = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    lim = max(int(need or _RSI_LOOKBACK), 16)
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {}
    # 거래일 lim 개를 커버할 달력일 (주말·휴장 여유)
    calDays = max(int(lim * 1.7) + 40, 80)
    try:
        startDt = (
            datetime.strptime(today, "%Y%m%d") - timedelta(days=calDays)
        ).strftime("%Y%m%d")
    except ValueError:
        startDt = "19900101"
    out = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for i in range(0, len(codes), _RSI_IN_CHUNK):
                chunk = codes[i : i + _RSI_IN_CHUNK]
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"""
                    SELECT STK_CD, TRADE_DT, CLOSE_PRC
                    FROM AI_DAY_STK_PRC
                    WHERE INDC_TP=%s AND TRADE_DT <= %s AND TRADE_DT >= %s
                      AND STK_CD IN ({ph})
                      AND CLOSE_PRC IS NOT NULL
                    ORDER BY STK_CD, TRADE_DT
                    """,
                    (INDC_TP_QTY, today, startDt, *chunk),
                )
                rows = cur.fetchall() or []
                byCd = {}
                for r in rows:
                    cd = normStkCd(r.get("STK_CD"))
                    if not cd:
                        continue
                    try:
                        px = abs(float(r["CLOSE_PRC"]))
                    except (TypeError, ValueError):
                        continue
                    if px <= 0:
                        continue
                    rec = byCd.setdefault(cd, {"closes": [], "lastDt": ""})
                    rec["closes"].append(px)
                    rec["lastDt"] = str(r.get("TRADE_DT") or "")[:8]
                for cd, rec in byCd.items():
                    closes = rec.get("closes") or []
                    if len(closes) > lim:
                        rec["closes"] = closes[-lim:]
                    out[cd] = rec
                # 일봉 없는 종목도 채워 매 페인트마다 재조회하지 않는다
                for cd in chunk:
                    if cd not in out:
                        out[cd] = {"closes": [], "lastDt": ""}
    except Exception:
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def loadPriorClosesMap(stkCds, need=None, endDt=None):
    """종가 리스트만 필요할 때. RSI 히스토리에서 closes 만 꺼낸다."""
    hist = loadRsiCloseHist(stkCds, need=need, endDt=endDt)
    return {cd: (rec.get("closes") or []) for cd, rec in (hist or {}).items()}


def liveRsiFromPriorCloses(closes, curPx=None, period=14, lastDt=None):
    """
    전체 일봉 Wilder RSI14.
    당일 봉이 있으면 현재가로 마지막만 치환, 없으면 현재가를 당일 봉으로 붙인다.
    SMA 전일종가로 당일 봉을 덮지 않도록, curPx 는 실시간(0B/당일종가)만 넘긴다.
    """
    from strategies.indicators import calcRsiSeries

    histLastDt = str(lastDt or "")[:8]
    if isinstance(closes, dict):
        hist = [float(x) for x in (closes.get("closes") or []) if x]
        if not histLastDt:
            histLastDt = str(closes.get("lastDt") or "")[:8]
    else:
        hist = [float(x) for x in (closes or []) if x]
    try:
        px = float(curPx) if curPx is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    ymd = datetime.now().strftime("%Y%m%d")
    # 실시간 현재가가 있을 때만 당일 봉을 맞춘다 (전일 종가 폴백은 호출측에서 제외)
    if px > 0:
        if histLastDt == ymd and hist:
            hist[-1] = px
        elif histLastDt != ymd:
            hist.append(px)
    p = max(2, int(period or 14))
    if len(hist) < p + 1:
        return None
    series = calcRsiSeries(hist, p)
    if not series:
        return None
    return series[-1]
