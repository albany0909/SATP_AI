"""
장마감 실시간조회순위(당일누적) → 관심종목 반영.

조건:
- AI_DAY_INQ_RANK (qry_tp=4) 기준
- 코스피·코스닥만 (ETF/코넥스 등 제외)
- 기준일 일봉 양봉(종가 > 시가)
- EMA 정배열은 쓰지 않음 (이평터치 모의 15분 편입과 동일)
"""

import time

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from api.getInqRank import QRY_TP_DAY
from utils.dayInqRankLoad import ensureInqRankReady
from strategies.indicators import calcEmaSeries
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.watchStkStore import (
    deactivateWatchByGrp,
    listExitClosedStkCds,
    listTradeWatch,
    upsertWatchStkRows,
)
from utils.watchStkTables import ensureWatchStkTable

# 관심 그룹 — 순위 장마감 연동
WATCH_GRP_RANK = "RANK"
WATCH_SRC = "rank_close"
MKT_KR = "KR"

# 정배열 EMA 기간
_EMA_FAST = 20
_EMA_MID = 50
_EMA_SLOW = 100
_EMA_LOOKBACK = 160

# 코스피/코스닥만 — MRKT_TP 또는 MARKET_NAME
_OK_MRKT_TP = {"0", "10"}
_OK_MKT_NAME_KEYS = ("코스피", "kospi", "코스닥", "kosdaq")
_EXCLUDE_NAME_KEYS = ("etf", "etn", "etp", "코넥스", "konex")


def _pickSession():
    # 웹 프로세스(수동 배치)에서도 쓰이도록 .env 앱키 발급까지 위임
    return acquireKiwoomSession()


def _isKospiKosdaq(mrktTp, marketName):
    """코스피·코스닥 여부. ETF/코넥스 제외."""
    mktNm = str(marketName or "").strip().lower()
    for kw in _EXCLUDE_NAME_KEYS:
        if kw in mktNm:
            return False

    tp = str(mrktTp or "").strip()
    if tp in _OK_MRKT_TP:
        return True

    for kw in _OK_MKT_NAME_KEYS:
        if kw in mktNm:
            return True
    return False


def _loadRankRows(tradeDt, qryTp=QRY_TP_DAY):
    """순위 스냅샷 + 종목마스터 조인 정보."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.RANK_NO, r.STK_CD, r.STK_NM, r.IND_GRP,
                       i.MRKT_TP, i.MARKET_NAME, i.STK_NM AS INFO_NM
                FROM AI_DAY_INQ_RANK r
                LEFT JOIN AI_STK_INFO i ON i.STK_CD = r.STK_CD
                WHERE r.TRADE_DT=%s AND r.QRY_TP=%s
                ORDER BY r.RANK_NO ASC
                """,
                (str(tradeDt), str(qryTp)),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def _getDayOhlc(stkCd, tradeDt):
    """AI_DAY_STK_PRC 기준일 시가·종가. 없으면 None."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT OPEN_PRC, CLOSE_PRC
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND TRADE_DT=%s AND INDC_TP=%s
                LIMIT 1
                """,
                (str(stkCd), str(tradeDt), INDC_TP_QTY),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "openPrc": row.get("OPEN_PRC"),
                "closePrc": row.get("CLOSE_PRC"),
            }
    finally:
        conn.close()


def _ensureDayOhlc(stkCd, tradeDt, hostUrl, token, tokenType, isPaper):
    """
    기준일 OHLC 확보. DB 없으면 일봉 조회 후 저장.
    반환: {openPrc, closePrc} 또는 None
    """
    got = _getDayOhlc(stkCd, tradeDt)
    if got and got.get("openPrc") is not None and got.get("closePrc") is not None:
        return got

    fetched = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCd,
        tradeDt,
        tradeDt,
        indcTp=INDC_TP_QTY,
        tokenType=tokenType,
        isPaper=isPaper,
    )
    if not fetched.get("ok"):
        termLog(f"[watchFromRank] 일봉실패 {stkCd}: {fetched.get('errMsg')}")
        return None

    rows = fetched.get("rows") or []
    if rows:
        upsertDayStkPrcRows(rows)

    return _getDayOhlc(stkCd, tradeDt)


def _isBullCandle(openPrc, closePrc):
    """양봉: 종가 > 시가."""
    try:
        o = int(openPrc)
        c = int(closePrc)
    except (TypeError, ValueError):
        return False
    return c > o


def _loadClosesAsOf(stkCd, tradeDt, limit=_EMA_LOOKBACK):
    """
    기준일 포함 이전 종가 (오래된→최신).
    EMA 정배열은 기준일 시점으로만 판정.
    """
    code = normStkCd(stkCd)
    if not code:
        return []
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CLOSE_PRC
                FROM (
                  SELECT TRADE_DT, CLOSE_PRC
                  FROM AI_DAY_STK_PRC
                  WHERE STK_CD=%s AND TRADE_DT<=%s
                    AND INDC_TP=%s AND CLOSE_PRC IS NOT NULL
                  ORDER BY TRADE_DT DESC
                  LIMIT %s
                ) t
                ORDER BY TRADE_DT ASC
                """,
                (code, str(tradeDt), INDC_TP_QTY, int(limit)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    closes = []
    for r in rows:
        try:
            closes.append(float(r["CLOSE_PRC"]))
        except (TypeError, ValueError):
            continue
    return closes


def _emaLast(closes, period):
    """종가(오름차순) → 최신 EMA. 부족 시 None."""
    if not closes or period <= 0 or len(closes) < period:
        return None
    series = calcEmaSeries(closes, period)
    if not series:
        return None
    return series[-1]


def _isEmaAlign20gt50gt100(stkCd, tradeDt):
    """
    기준일 지수이동평균 정배열: EMA20 > EMA50 > EMA100.
    반환: (ok, ema20, ema50, ema100)
    """
    closes = _loadClosesAsOf(stkCd, tradeDt)
    ema20 = _emaLast(closes, _EMA_FAST)
    ema50 = _emaLast(closes, _EMA_MID)
    ema100 = _emaLast(closes, _EMA_SLOW)
    if ema20 is None or ema50 is None or ema100 is None:
        return False, ema20, ema50, ema100
    return (ema20 > ema50 > ema100), ema20, ema50, ema100


def syncWatchFromRankClose(tradeDt, qryTp=QRY_TP_DAY):
    """
    장마감 순위 → 관심종목 동기화.
    조건: 코스피/코스닥 + 양봉 + EMA20 > EMA50 > EMA100
    """
    reqYmd = str(tradeDt or "").strip()
    resolved = ensureInqRankReady(reqYmd or None, qryTp=qryTp)
    ymd = str(resolved.get("tradeDt") or "")
    fallback = bool(resolved.get("fallback"))
    src = resolved.get("source") or ""
    if not ymd:
        return {
            "ok": False,
            "errMsg": (
                resolved.get("errMsg")
                or (
                    f"순위 데이터 없음: {resolved.get('reqDt') or tradeDt} "
                    f"qry_tp={qryTp} (휴일·직전영업일 없음, 실시간 조회 실패)"
                )
            ),
            "upsertCnt": 0,
            "rankCnt": 0,
        }

    ensureWatchStkTable()
    rankRows = _loadRankRows(ymd, qryTp=qryTp)
    if not rankRows:
        return {
            "ok": False,
            "errMsg": f"순위 데이터 없음: {ymd} qry_tp={qryTp}",
            "upsertCnt": 0,
            "rankCnt": 0,
        }

    session = _pickSession()
    hostUrl = token = tokenType = None
    isPaper = True
    if session:
        mode, hostUrl, token, tokenType = session
        isPaper = mode == "paper"

    waitSec = 1.05 if isPaper else 0.3
    watchRows = []
    skipNotMkt = 0
    skipNoBar = 0
    skipNotBull = 0
    kospiKosdaqCnt = 0
    bullCnt = 0
    fetchedExtra = 0
    reopenCnt = 0
    # 전량청산 종목도 순위에 다시 뜨면 선정 조건 통과 시 재진입(매매 ON)
    closedCds = set(listExitClosedStkCds(mktTp=MKT_KR) or [])

    for r in rankRows:
        stkCd = normStkCd(r.get("STK_CD") or "")
        if not stkCd:
            continue

        mrktTp = r.get("MRKT_TP")
        marketName = r.get("MARKET_NAME") or ""
        if not _isKospiKosdaq(mrktTp, marketName):
            skipNotMkt += 1
            continue

        kospiKosdaqCnt += 1
        ohlc = _getDayOhlc(stkCd, ymd)
        if (
            not ohlc
            or ohlc.get("openPrc") is None
            or ohlc.get("closePrc") is None
        ) and hostUrl and token:
            if fetchedExtra > 0:
                time.sleep(waitSec)
            ohlc = _ensureDayOhlc(
                stkCd, ymd, hostUrl, token, tokenType, isPaper
            )
            fetchedExtra += 1

        if not ohlc or ohlc.get("openPrc") is None or ohlc.get("closePrc") is None:
            skipNoBar += 1
            continue

        if not _isBullCandle(ohlc["openPrc"], ohlc["closePrc"]):
            skipNotBull += 1
            continue

        bullCnt += 1

        rankNo = r.get("RANK_NO")
        try:
            rankNo = int(rankNo) if rankNo is not None else None
        except (TypeError, ValueError):
            rankNo = None

        priority = rankNo if rankNo is not None else 100
        stkNm = (r.get("STK_NM") or r.get("INFO_NM") or stkCd).strip()
        wasClosed = stkCd in closedCds
        if wasClosed:
            reopenCnt += 1
        useTrade = "Y" if wasClosed else "N"
        note = (
            f"rank재진입+양봉 {ymd}"
            if wasClosed
            else f"rank양봉 {ymd}"
        )
        if fallback:
            note += f" 대체{src or 'prev'}from{resolved.get('reqDt')}"

        watchRows.append(
            {
                "STK_CD": stkCd,
                "MKT_TP": MKT_KR,
                "STK_NM": stkNm,
                "MARKET_NAME": str(marketName).strip() or None,
                "IND_GRP": (str(r.get("IND_GRP") or "").strip() or None),
                "GRP_CD": WATCH_GRP_RANK,
                "STATUS": "Y",
                "USE_BT": "Y",
                "USE_TRADE": useTrade,
                "PRIORITY": priority,
                "RANK_NO": rankNo,
                "REF_TRADE_DT": ymd,
                "LOAD_DT": ymd,
                "OPEN_PRC": int(ohlc["openPrc"]),
                "CLOSE_PRC": int(ohlc["closePrc"]),
                "SRC": WATCH_SRC,
                "NOTE": note,
            }
        )

    activeCds = [w["STK_CD"] for w in watchRows]
    # 장중 15분 편입(매매 ON)은 장마감 교체로 끄지 않는다
    for w in listTradeWatch(mktTp=MKT_KR, limit=180) or []:
        cd = normStkCd(w.get("STK_CD"))
        if cd and cd not in activeCds:
            activeCds.append(cd)
    deact = deactivateWatchByGrp(
        WATCH_GRP_RANK, exceptStkCds=activeCds, mktTp=MKT_KR
    )
    saved = upsertWatchStkRows(watchRows)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg"),
            "upsertCnt": 0,
            "rankCnt": len(rankRows),
            "kospiKosdaqCnt": kospiKosdaqCnt,
            "bullCnt": bullCnt,
        }

    srcLabel = {
        "holiday": "휴일순위",
        "prevBiz": "직전영업일",
        "live": "실시간조회",
    }.get(src, "")
    fbTxt = (
        f" (요청 {resolved.get('reqDt')} → {srcLabel or '대체'} {ymd})"
        if fallback
        else ""
    )
    summary = (
        f"[관심종목] 순위장마감 반영 완료\n"
        f"기준일: {ymd}{fbTxt}\n"
        f"순위 {len(rankRows)} → 코스피/닥 {kospiKosdaqCnt} "
        f"→ 양봉 {len(watchRows)}건 활성"
        f"(청산재진입 {reopenCnt})\n"
        f"조건: 코스피/닥 + 양봉\n"
        f"비활성(교체): {deact.get('deactCnt') or 0} · "
        f"제외: 시장{skipNotMkt}/일봉없음{skipNoBar}/음·보합{skipNotBull}"
    )
    termLog(summary)

    return {
        "ok": True,
        "tradeDt": ymd,
        "reqDt": resolved.get("reqDt") or ymd,
        "fallback": fallback,
        "source": src,
        "rankCnt": len(rankRows),
        "kospiKosdaqCnt": kospiKosdaqCnt,
        "bullCnt": bullCnt,
        "emaAlignCnt": len(watchRows),
        "upsertCnt": saved.get("upsertCnt") or 0,
        "deactCnt": deact.get("deactCnt") or 0,
        "skipNoBar": skipNoBar,
        "skipNotBull": skipNotBull,
        "skipNotMkt": skipNotMkt,
        "reopenCnt": reopenCnt,
        "summary": summary,
    }
