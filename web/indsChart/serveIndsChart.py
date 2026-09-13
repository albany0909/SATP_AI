"""
코스피/코스닥 지수 + 종목 일봉 차트 API + 정적 웹 서버.

실행:
  python web/indsChart/serveIndsChart.py
  → 브라우저에서 http://127.0.0.1:8765

데이터:
  지수 AI_DAY_INDS_PRC / 종목 AI_DAY_STK_PRC
  종목별투자자 AI_DAY_INV_SR (ka10059)
  외국인 현선물 AI_DAY_FRGN_SF (네이버 일자별 순매수)
  신호 sigAlgs.py / 지지·저항 srAlgs.py / 패턴 patternAlgs.py
  관심 AI_WATCH_STK / 순위 AI_DAY_INQ_RANK
"""

from __future__ import annotations

import json
import re
import socket
import sys
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_plus

_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = Path(__file__).resolve().parent

import sys

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.dbConn import openDbConn

from patternAlgs import buildChartPatterns  # noqa: E402
from sigAlgs import (  # noqa: E402
    DEFAULT_SIG_ALG,
    buildSignals,
    listSigAlgs,
    resolveSigAlgId,
)
from srAlgs import (  # noqa: E402
    DEFAULT_SR_ALG,
    buildSrLevels,
    listSrAlgs,
    resolveSrAlgId,
)

HOST = "127.0.0.1"
PORT = 8765

INDS_MAP = {
    "001": {"code": "001", "name": "KOSPI", "label": "코스피"},
    "101": {"code": "101", "name": "KOSDAQ", "label": "코스닥"},
    "kospi": {"code": "001", "name": "KOSPI", "label": "코스피"},
    "kosdaq": {"code": "101", "name": "KOSDAQ", "label": "코스닥"},
}


def _parseReq(rawPath):
    """
    path + query 를 str 로 분리한다.
    Python 3.14 typeshed 의 urlparse/parse_qs 가 bytes 오버로드로
    잡혀 편집기 오류가 폭주하는 것을 피한다.
    """
    text = str(rawPath or "/")
    if "?" in text:
        pathPart, query = text.split("?", 1)
    else:
        pathPart, query = text, ""
    qs: dict[str, list[str]] = {}
    if query:
        for part in query.split("&"):
            if not part:
                continue
            if "=" in part:
                key, val = part.split("=", 1)
            else:
                key, val = part, ""
            qs.setdefault(unquote_plus(key), []).append(unquote_plus(val))
    return pathPart or "/", qs


def _ymd(raw):
    text = re.sub(r"[^0-9]", "", str(raw or ""))
    if len(text) != 8:
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _ymdToIso(ymd):
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _normStkCd(raw):
    """종목코드 정규화 (숫자 위주면 6자리 제로패딩)."""
    text = re.sub(r"\s+", "", str(raw or "")).upper()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return text.zfill(6) if len(text) <= 6 else text
    return text


def _rangePreset(preset, today=None):
    t = today or datetime.now()
    end = t.strftime("%Y%m%d")
    key = str(preset or "1y").lower()
    if key in ("1y", "1year", "y1", "최근1년"):
        start = (t - timedelta(days=365)).strftime("%Y%m%d")
    elif key in ("2y", "2year", "y2", "최근2년"):
        start = (t - timedelta(days=365 * 2)).strftime("%Y%m%d")
    elif key in ("3y", "3year", "y3", "최근3년"):
        start = (t - timedelta(days=365 * 3)).strftime("%Y%m%d")
    elif key in ("ytd", "올해"):
        start = t.strftime("%Y") + "0101"
    elif key in ("ly", "작년"):
        y = t.year - 1
        start = f"{y}0101"
        end = f"{y}1231"
    elif key in ("mtd", "이번달", "month"):
        start = t.strftime("%Y%m") + "01"
    else:
        start = (t - timedelta(days=365)).strftime("%Y%m%d")
    return start, end


def _resolveRange(qs):
    preset = (qs.get("preset") or [""])[0]
    start = _ymd((qs.get("start") or [""])[0])
    end = _ymd((qs.get("end") or [""])[0])
    if not start or not end:
        if preset:
            start, end = _rangePreset(preset)
        else:
            start, end = _rangePreset("1y")
    if start > end:
        start, end = end, start
    return start, end, preset


def fetchSeries(indsCd, startDt, endDt, sigAlgId=None):
    """일봉 close 시계열 + 선택 알고리즘 신호."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, CLOSE_PRC, OPEN_PRC, HIGH_PRC, LOW_PRC
                FROM AI_DAY_INDS_PRC
                WHERE MKT_TP='KR' AND INDS_CD=%s
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (str(indsCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    points = []
    closes = []
    for r in rows:
        dt = str(r["TRADE_DT"])
        close = float(r["CLOSE_PRC"] or 0)
        if close <= 0:
            continue
        closes.append(close)
        points.append(
            {
                "dt": dt,
                "date": _ymdToIso(dt),
                "close": round(close, 2),
                "open": round(float(r["OPEN_PRC"] or 0), 2),
                "high": round(float(r["HIGH_PRC"] or 0), 2),
                "low": round(float(r["LOW_PRC"] or 0), 2),
            }
        )

    sig = buildSignals(sigAlgId or DEFAULT_SIG_ALG, points, closes)
    return {
        "points": points,
        "signals": sig["signals"],
        "lastSignalDate": sig["lastSignalDate"],
        "sigAlgId": sig["sigAlgId"],
        "sigAlgName": sig["sigAlgName"],
        "rowCnt": len(points),
    }


def _rowStkMeta(row):
    """AI_STK_INFO 등 row → 화면용 메타."""
    if not row:
        return {
            "stkCd": "",
            "stkNm": "",
            "marketName": "",
            "upName": "",
            "indGrp": "",
            "theme": "",
            "sector": "",
        }
    up = str(row.get("UP_NAME") or "").strip()
    theme = str(row.get("IND_GRP") or "").strip()
    return {
        "stkCd": str(row.get("STK_CD") or ""),
        "stkNm": str(row.get("STK_NM") or "").strip(),
        "marketName": str(row.get("MARKET_NAME") or "").strip(),
        "upName": up,
        "indGrp": theme,
        "theme": theme,  # 테마(순위 IND_GRP)
        "sector": up,  # 업종(AI_STK_INFO.UP_NAME)
    }


def _fetchIndGrpMap(stkCds):
    """종목코드 → 최신 테마(IND_GRP). 관심·순위 우선."""
    codes = [str(c) for c in (stkCds or []) if c]
    if not codes:
        return {}
    out = {}
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            # 관심
            fmt = ",".join(["%s"] * len(codes))
            cur.execute(
                f"""
                SELECT STK_CD, IND_GRP FROM AI_WATCH_STK
                WHERE MKT_TP='KR' AND STK_CD IN ({fmt})
                  AND IND_GRP IS NOT NULL AND IND_GRP <> ''
                """,
                codes,
            )
            for r in cur.fetchall() or []:
                out[str(r["STK_CD"])] = str(r["IND_GRP"]).strip()
            # 순위(관심에 없을 때만)
            need = [c for c in codes if c not in out]
            if need:
                fmt2 = ",".join(["%s"] * len(need))
                cur.execute(
                    f"""
                    SELECT r.STK_CD, r.IND_GRP
                    FROM AI_DAY_INQ_RANK r
                    INNER JOIN (
                      SELECT STK_CD, MAX(TRADE_DT) AS MAX_DT
                      FROM AI_DAY_INQ_RANK
                      WHERE STK_CD IN ({fmt2})
                        AND IND_GRP IS NOT NULL AND IND_GRP <> ''
                      GROUP BY STK_CD
                    ) t ON r.STK_CD=t.STK_CD AND r.TRADE_DT=t.MAX_DT
                    WHERE r.IND_GRP IS NOT NULL AND r.IND_GRP <> ''
                    GROUP BY r.STK_CD, r.IND_GRP
                    """,
                    need,
                )
                for r in cur.fetchall() or []:
                    cd = str(r["STK_CD"])
                    if cd not in out:
                        out[cd] = str(r["IND_GRP"]).strip()
    except Exception:
        pass
    finally:
        conn.close()
    return out


def lookupStkMeta(stkCd):
    """코드 → 이름·시장·업종·테마."""
    from utils.stkInfoStore import findStkByCd

    cd = str(stkCd or "")
    info = findStkByCd(cd) or {}
    meta = _rowStkMeta({**info, "STK_CD": cd or info.get("STK_CD")})
    if not meta["stkNm"]:
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT STK_NM FROM AI_WATCH_STK
                    WHERE STK_CD=%s AND MKT_TP='KR' LIMIT 1
                    """,
                    (cd,),
                )
                row = cur.fetchone()
                if row and row.get("STK_NM"):
                    meta["stkNm"] = str(row["STK_NM"])
                else:
                    cur.execute(
                        """
                        SELECT STK_NM FROM AI_DAY_INQ_RANK
                        WHERE STK_CD=%s AND STK_NM IS NOT NULL AND STK_NM <> ''
                        ORDER BY TRADE_DT DESC LIMIT 1
                        """,
                        (cd,),
                    )
                    row = cur.fetchone()
                    if row and row.get("STK_NM"):
                        meta["stkNm"] = str(row["STK_NM"])
        finally:
            conn.close()
    themeMap = _fetchIndGrpMap([cd])
    if themeMap.get(cd):
        meta["indGrp"] = themeMap[cd]
        meta["theme"] = themeMap[cd]
    return meta


def searchStkApi(q, limit=25):
    """종목 코드/이름 검색 + 테마 보강."""
    from utils.stkInfoStore import searchStkInfo

    rows = searchStkInfo(q, limit=limit) or []
    codes = [str(r.get("STK_CD") or "") for r in rows]
    themeMap = _fetchIndGrpMap(codes)
    items = []
    for r in rows:
        meta = _rowStkMeta(r)
        cd = meta["stkCd"]
        if themeMap.get(cd):
            meta["indGrp"] = themeMap[cd]
            meta["theme"] = themeMap[cd]
        items.append(meta)
    return items


def fetchStkSeries(stkCd, startDt, endDt, srAlgId=None):
    """종목 일봉 OHLC + 지지/저항 레벨 (수량 표시분만)."""
    meta = lookupStkMeta(stkCd)
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0'
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    points = []
    for r in rows:
        dt = str(r["TRADE_DT"])
        close = float(r["CLOSE_PRC"] or 0)
        if close <= 0:
            continue
        o = float(r["OPEN_PRC"] or 0) or close
        h = float(r["HIGH_PRC"] or 0) or max(o, close)
        l = float(r["LOW_PRC"] or 0) or min(o, close)
        points.append(
            {
                "dt": dt,
                "date": _ymdToIso(dt),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(close, 2),
                "qty": int(r["TRDE_QTY"] or 0),
            }
        )

    sr = buildSrLevels(srAlgId or DEFAULT_SR_ALG, points)
    patterns = buildChartPatterns(points)
    return {
        "stkCd": str(stkCd),
        "stkNm": meta.get("stkNm"),
        "marketName": meta.get("marketName") or "",
        "upName": meta.get("upName") or "",
        "indGrp": meta.get("indGrp") or "",
        "theme": meta.get("theme") or "",
        "sector": meta.get("sector") or "",
        "points": points,
        "levels": sr["levels"],
        "srAlgId": sr["srAlgId"],
        "srAlgName": sr["srAlgName"],
        # patterns: { dblBottom: [], boxRange: [] } (구 클라이언트 호환용 flat 없음)
        "patterns": patterns,
        "rowCnt": len(points),
    }


def fetchWatchList(limit=200):
    """활성 관심종목."""
    lim = max(1, min(int(limit or 200), 500))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
                       RANK_NO, REF_TRADE_DT, LOAD_DT, USE_BT, USE_TRADE, SRC, NOTE
                FROM AI_WATCH_STK
                WHERE STATUS='Y' AND MKT_TP='KR'
                ORDER BY PRIORITY ASC, RANK_NO ASC, STK_CD ASC
                LIMIT %s
                """,
                (lim,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    items = []
    for r in rows:
        items.append(
            {
                "stkCd": str(r["STK_CD"]),
                "stkNm": r.get("STK_NM") or "",
                "marketName": r.get("MARKET_NAME") or "",
                "indGrp": r.get("IND_GRP") or "",
                "grpCd": r.get("GRP_CD") or "",
                "rankNo": r.get("RANK_NO"),
                "refTradeDt": r.get("REF_TRADE_DT") or "",
                "loadDt": r.get("LOAD_DT") or "",
                "useBt": r.get("USE_BT") or "",
                "useTrade": r.get("USE_TRADE") or "",
                "src": r.get("SRC") or "",
                "note": r.get("NOTE") or "",
            }
        )
    return items


def fetchRankList(qryTp="4", limit=100):
    """최근 적재 조회순위 스냅샷."""
    lim = max(1, min(int(limit or 100), 300))
    qt = str(qryTp or "4")[:1]
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(TRADE_DT) AS MAX_DT
                FROM AI_DAY_INQ_RANK
                WHERE QRY_TP=%s
                """,
                (qt,),
            )
            row = cur.fetchone() or {}
            tradeDt = row.get("MAX_DT")
            if not tradeDt:
                return {"tradeDt": None, "qryTp": qt, "items": []}
            tradeDt = str(tradeDt)
            cur.execute(
                """
                SELECT RANK_NO, STK_CD, STK_NM, IND_GRP, CUR_PRC,
                       BASE_COMP_CHGR, BASE_COMP_SIGN
                FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT=%s AND QRY_TP=%s
                ORDER BY RANK_NO ASC
                LIMIT %s
                """,
                (tradeDt, qt, lim),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    items = []
    for r in rows:
        items.append(
            {
                "rankNo": int(r["RANK_NO"] or 0),
                "stkCd": str(r["STK_CD"]),
                "stkNm": r.get("STK_NM") or "",
                "indGrp": r.get("IND_GRP") or "",
                "curPrc": int(r["CUR_PRC"] or 0),
                "chgRt": float(r["BASE_COMP_CHGR"] or 0),
            }
        )
    return {"tradeDt": tradeDt, "qryTp": qt, "items": items}


def _invSrNum(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None


def _invSrRowToJson(row):
    """AI_DAY_INV_SR row → 웹 JSON (HTS 종목별투자자 컬럼)."""
    flu = row.get("FLU_RT")
    try:
        fluRt = float(flu) if flu is not None else None
    except (TypeError, ValueError):
        fluRt = None
    return {
        "stkCd": str(row.get("STK_CD") or ""),
        "tradeDt": str(row.get("TRADE_DT") or ""),
        "amtQtyTp": str(row.get("AMT_QTY_TP") or ""),
        "trdeTp": str(row.get("TRDE_TP") or ""),
        "unitTp": str(row.get("UNIT_TP") or ""),
        "curPrc": _invSrNum(row.get("CUR_PRC")),
        "preSig": row.get("PRE_SIG") or "",
        "predPre": _invSrNum(row.get("PRED_PRE")),
        "fluRt": fluRt,
        "accTrdeQty": _invSrNum(row.get("ACC_TRDE_QTY")),
        "accTrdePrica": _invSrNum(row.get("ACC_TRDE_PRICA")),
        "indInvsr": _invSrNum(row.get("IND_INVSR")),
        "frgnrInvsr": _invSrNum(row.get("FRGNR_INVSR")),
        "orgn": _invSrNum(row.get("ORGN")),
        "fnncInvt": _invSrNum(row.get("FNNC_INVT")),
        "insrnc": _invSrNum(row.get("INSRNC")),
        "invtrt": _invSrNum(row.get("INVTRT")),
        "etcFnnc": _invSrNum(row.get("ETC_FNNC")),
        "bank": _invSrNum(row.get("BANK")),
        "penfndEtc": _invSrNum(row.get("PENFND_ETC")),
        "samoFund": _invSrNum(row.get("SAMO_FUND")),
        "natn": _invSrNum(row.get("NATN")),
        "etcCorp": _invSrNum(row.get("ETC_CORP")),
        "natfor": _invSrNum(row.get("NATFOR")),
    }


def _liveRunnerMap():
    """실시간 엔진이 붙은 알고리즘코드 — deskLiveAlgs + stratDeskSummary."""
    from trading.deskLiveAlgs import listDeskLiveAlgs, stratIdForAlg
    from utils.stratDeskSummary import buildStratRunnerNote

    out = {}
    for row in listDeskLiveAlgs() or []:
        alg = str(row.get("algCd") or "").strip()
        if not alg:
            continue
        sid = row.get("stratId") or stratIdForAlg(alg) or ""
        note = buildStratRunnerNote(alg, sid)
        out[alg] = {
            "running": bool(row.get("enabled")),
            "paperOnly": bool(row.get("paperOnly", True)),
            "note": note,
        }
    return out


def _buildStratLiveView(ref):
    """알고리즘 설정 화면용: 명세 요약 + 코드별 금액 + 최근 청산."""
    from config.settingsStore import getMaTouchLive, getStratLiveAmts
    from strategies.specLoader import getAlgCd, listStratSpecs, loadStratSpec, resolveAlgRef
    from utils.stratLiveTradeStore import listLiveTrades

    specs = listStratSpecs() or []
    got = resolveAlgRef(ref or "MTS01")
    if not got.get("ok"):
        # 첫 등록 코드로 폴백
        first = next((s for s in specs if s.get("algCd")), None)
        if first:
            got = {
                "ok": True,
                "stratId": first.get("stratId") or "",
                "algCd": first.get("algCd") or "",
                "name": first.get("name") or "",
            }
        else:
            return {"ok": False, "errMsg": got.get("errMsg") or "알고리즘 없음"}
    sid = got.get("stratId") or ""
    alg = got.get("algCd") or getAlgCd(sid)

    spec = loadStratSpec(sid) or {}
    params = spec.get("params") or {}
    from utils.stratDeskSummary import buildStratRuleRows

    ruleRows = buildStratRuleRows(alg, sid)

    amts = getStratLiveAmts(alg)
    liveMeta = getMaTouchLive() if alg == "MTS01" else {}
    trades = []
    try:
        for r in listLiveTrades(algCd=alg, limit=30) or []:
            trades.append(
                {
                    "stkCd": r.get("STK_CD") or "",
                    "stkNm": r.get("STK_NM") or "",
                    "entryDt": r.get("ENTRY_DT") or "",
                    "exitDt": r.get("EXIT_DT") or "",
                    "exitTm": r.get("EXIT_TM") or "",
                    "exitReason": r.get("EXIT_REASON") or "",
                    "steps": r.get("STEPS") or "",
                    "buyAmt": float(r.get("BUY_AMT") or 0),
                    "sellAmt": float(r.get("SELL_AMT") or 0),
                    "pnlWon": float(r.get("PNL_WON") or 0),
                    "pnlPct": float(r.get("PNL_PCT") or 0),
                }
            )
    except Exception:
        trades = []

    runners = _liveRunnerMap()
    nameByCd = {s.get("algCd"): (s.get("name") or s.get("algCd")) for s in specs}
    runningNow = []
    for rid, info in runners.items():
        if not info.get("running"):
            continue
        runningNow.append(
            {
                "algCd": rid,
                "name": nameByCd.get(rid) or rid,
                "paperOnly": bool(info.get("paperOnly")),
                "note": info.get("note") or "",
            }
        )
    stratRows = []
    for s in specs:
        cd = s.get("algCd") or ""
        info = runners.get(cd) or {}
        stratRows.append(
            {
                "algCd": cd,
                "name": s.get("name") or cd,
                "status": s.get("status") or "",
                "running": bool(info.get("running")),
                "hasLive": cd in runners,
            }
        )
    thisRun = runners.get(alg) or {}

    return {
        "ok": True,
        "strats": stratRows,
        "runningNow": runningNow,
        "running": bool(thisRun.get("running")),
        "hasLive": alg in runners,
        "algCd": alg,
        "name": spec.get("name") or got.get("name") or alg,
        "status": spec.get("status") or "",
        "enabled": bool(spec.get("enabled", True)),
        "paperOnly": bool(liveMeta.get("paperOnly", params.get("paperOnly", False))),
        "timeframe": spec.get("timeframe") or "",
        "ruleRows": ruleRows,
        "amtKind": amts.get("amtKind") or "none",
        "buyAmt5Man": amts.get("buyAmt5Man") or 0,
        "buyAmt10Man": amts.get("buyAmt10Man") or 0,
        "buyAmt15Man": amts.get("buyAmt15Man") or 0,
        "buyAmtWonMan": amts.get("buyAmtWonMan") or 0,
        "trades": trades,
    }


def _buildAlgReview(algCd, tradeDt):
    """
    장마감 복기. 상용 알고리즘코드 기준으로 당일 이벤트를 묶는다.
    규칙이 바뀌면 ALG_VER 이 갈라져, 당시 버전 행만 남는다.
    """
    from utils.algEvtStore import (
        listAlgCdsWithEvt,
        listAlgEvt,
        listAlgEvtDts,
        resolveAlgMeta,
    )
    from utils.stratLiveTradeStore import listLiveTrades

    runners = _liveRunnerMap()
    from strategies.specLoader import listStratSpecs

    specs = listStratSpecs() or []
    nameByCd = {s.get("algCd"): (s.get("name") or s.get("algCd")) for s in specs}
    liveRows = []
    for cd, info in runners.items():
        liveRows.append(
            {
                "algCd": cd,
                "name": nameByCd.get(cd) or cd,
                "running": bool(info.get("running")),
                "live": True,
            }
        )
    seen = {r["algCd"] for r in liveRows}
    for row in listAlgCdsWithEvt():
        cd = str(row.get("ALG_CD") or "")
        if not cd or cd in seen:
            continue
        liveRows.append(
            {
                "algCd": cd,
                "name": nameByCd.get(cd) or cd,
                "running": False,
                "live": False,
            }
        )
        seen.add(cd)

    meta = resolveAlgMeta(algCd or "MTS01")
    code = meta.get("algCd") if meta.get("ok") else "MTS01"
    ymd = str(tradeDt or "").replace("-", "")[:8]
    days = listAlgEvtDts(code, limit=90)
    if not ymd and days:
        ymd = str(days[0].get("TRADE_DT") or "")
    evts = listAlgEvt(code, ymd, limit=800) if ymd else []
    items = []
    for r in evts:
        items.append(
            {
                "evtId": r.get("EVT_ID"),
                "algCd": r.get("ALG_CD") or "",
                "algVer": r.get("ALG_VER") or "",
                "tradeDt": r.get("TRADE_DT") or "",
                "evtTm": r.get("EVT_TM") or "",
                "evtTp": r.get("EVT_TP") or "",
                "reason": r.get("REASON") or "",
                "stkCd": r.get("STK_CD") or "",
                "stkNm": r.get("STK_NM") or "",
                "tradeCd": r.get("TRADE_CD") or "",
                "px": float(r.get("PX") or 0) if r.get("PX") is not None else None,
                "sma5": float(r.get("SMA5") or 0) if r.get("SMA5") is not None else None,
                "sma10": float(r.get("SMA10") or 0) if r.get("SMA10") is not None else None,
                "sma15": float(r.get("SMA15") or 0) if r.get("SMA15") is not None else None,
                "sma20": float(r.get("SMA20") or 0) if r.get("SMA20") is not None else None,
                "qty": r.get("QTY"),
                "note": r.get("NOTE") or "",
            }
        )
    closes = []
    try:
        for r in listLiveTrades(algCd=code, exitDt=ymd, limit=80) or []:
            closes.append(
                {
                    "stkCd": r.get("STK_CD") or "",
                    "stkNm": r.get("STK_NM") or "",
                    "exitReason": r.get("EXIT_REASON") or "",
                    "pnlWon": float(r.get("PNL_WON") or 0),
                    "pnlPct": float(r.get("PNL_PCT") or 0),
                    "steps": r.get("STEPS") or "",
                }
            )
    except Exception:
        closes = []
    buyCnt = sum(1 for x in items if x["evtTp"] == "BUY")
    skipCnt = sum(1 for x in items if x["evtTp"] == "BUY_SKIP")
    sellCnt = sum(1 for x in items if x["evtTp"] == "SELL")
    return {
        "ok": True,
        "algCd": code,
        "algVer": meta.get("algVer") if meta.get("ok") else "",
        "name": meta.get("name") if meta.get("ok") else code,
        "tradeDt": ymd,
        "algs": liveRows,
        "days": [
            {"tradeDt": d.get("TRADE_DT") or "", "cnt": int(d.get("CNT") or 0)}
            for d in days
        ],
        "buyCnt": buyCnt,
        "skipCnt": skipCnt,
        "sellCnt": sellCnt,
        "closeCnt": len(closes),
        "closes": closes,
        "items": items,
    }


class IndsChartHandler(SimpleHTTPRequestHandler):
    """정적 파일 + /api/* JSON."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        # 부모(BaseHTTPRequestHandler)와 매개변수명을 맞춰 basedpyright 호환
        sys.stderr.write("[indsChart] " + (format % args) + "\n")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, qs = _parseReq(self.path)

        if path == "/api/health":
            self._json(200, {"ok": True})
            return

        if path == "/api/opsSched":
            try:
                from utils.batchSchedRegistry import listBatchSchedJobs

                jobs = listBatchSchedJobs()
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(jobs), "items": jobs})
            return

        if path == "/api/opsSchedSet":
            jobId = (qs.get("id") or [""])[0]
            onRaw = (qs.get("on") or qs.get("enabled") or [""])[0]
            try:
                from config.opsSettings import setBatchSchedEnabled

                enabled = str(onRaw).strip().lower() in (
                    "1",
                    "y",
                    "yes",
                    "true",
                    "on",
                )
                result = setBatchSchedEnabled(jobId, enabled)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/opsSchedRun":
            jobId = (qs.get("id") or [""])[0]
            try:
                from utils.batchRunner import runBatchJob

                result = runBatchJob(jobId)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/opsSchedRunState":
            jobId = (qs.get("id") or [""])[0]
            try:
                from utils.batchRunner import getBatchRunState

                states = getBatchRunState(jobId or None)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "states": states})
            return

        if path == "/api/opsPush":
            try:
                from config.opsSettings import listTelegramPushChannels

                items = listTelegramPushChannels()
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/opsPushSet":
            chId = (qs.get("id") or [""])[0]
            onRaw = (qs.get("on") or qs.get("enabled") or [""])[0]
            try:
                from config.opsSettings import setTelegramPushEnabled

                enabled = str(onRaw).strip().lower() in (
                    "1",
                    "y",
                    "yes",
                    "true",
                    "on",
                )
                result = setTelegramPushEnabled(chId, enabled)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/sigAlgs":
            self._json(
                200,
                {
                    "ok": True,
                    "defaultId": DEFAULT_SIG_ALG,
                    "algs": listSigAlgs(),
                },
            )
            return

        if path == "/api/srAlgs":
            self._json(
                200,
                {
                    "ok": True,
                    "defaultId": DEFAULT_SR_ALG,
                    "algs": listSrAlgs(),
                },
            )
            return

        if path == "/api/watchList":
            try:
                lim = int((qs.get("limit") or ["200"])[0])
            except ValueError:
                lim = 200
            try:
                items = fetchWatchList(limit=lim)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/watchAdd":
            rawRef = (qs.get("q") or qs.get("stkCd") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "q 또는 stkCd 필수"})
                return
            try:
                from utils.stkResolve import resolveStkRef
                from utils.watchStkStore import addWatchForMon
                from trading.watchRtMonitor import nextMktOpenLabel

                resolved = resolveStkRef(rawRef)
                if not resolved.get("ok"):
                    self._json(
                        400,
                        {
                            "ok": False,
                            "errMsg": resolved.get("errMsg"),
                            "matches": [
                                {
                                    "stkCd": m.get("STK_CD"),
                                    "stkNm": m.get("STK_NM"),
                                }
                                for m in (resolved.get("matches") or [])
                            ],
                        },
                    )
                    return
                result = addWatchForMon(
                    resolved["stkCd"],
                    stkNm=resolved.get("stkNm") or "",
                    marketName="",
                    note="web",
                )
                result["nextOpen"] = nextMktOpenLabel()
                result["stkNm"] = result.get("stkNm") or resolved.get("stkNm") or ""
                if result.get("ok"):
                    result["hint"] = (
                        f"다음 장 시작({result['nextOpen']})에 실시간(0B) 감시 시작. "
                        "main.py 가 떠 있어야 합니다."
                    )
                code = 200 if result.get("ok") else 400
                self._json(code, result)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/watchDel":
            rawCd = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawCd).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 필수"})
                return
            try:
                from utils.stkResolve import resolveStkRef
                from utils.watchStkStore import deactivateWatchStk

                resolved = resolveStkRef(rawCd)
                stkCd = resolved.get("stkCd") if resolved.get("ok") else str(rawCd).strip()
                result = deactivateWatchStk(stkCd)
                code = 200 if result.get("ok") else 400
                self._json(code, result)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/algList":
            try:
                from strategies.specLoader import listStratSpecs

                items = [
                    {
                        "algCd": s.get("algCd") or "",
                        "name": s.get("name") or "",
                        "status": s.get("status") or "",
                    }
                    for s in (listStratSpecs() or [])
                    if s.get("algCd")
                ]
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/algStkList":
            algCd = (qs.get("algCd") or ["MTS01"])[0]
            try:
                lim = int((qs.get("limit") or ["300"])[0])
            except ValueError:
                lim = 300
            try:
                from utils.algStkStore import listAlgStk

                items = listAlgStk(algCd, limit=lim)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(
                200,
                {"ok": True, "algCd": algCd, "cnt": len(items), "items": items},
            )
            return

        if path == "/api/algStkAdd":
            algCd = (qs.get("algCd") or ["MTS01"])[0]
            rawRef = (qs.get("q") or qs.get("stkCd") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "q 또는 stkCd 필수"})
                return
            try:
                from utils.algStkStore import addAlgManaged
                from utils.stkResolve import resolveStkRef

                resolved = resolveStkRef(rawRef)
                if not resolved.get("ok"):
                    self._json(
                        400,
                        {
                            "ok": False,
                            "errMsg": resolved.get("errMsg"),
                            "matches": [
                                {
                                    "stkCd": m.get("STK_CD"),
                                    "stkNm": m.get("STK_NM"),
                                }
                                for m in (resolved.get("matches") or [])
                            ],
                        },
                    )
                    return
                result = addAlgManaged(
                    algCd,
                    resolved["stkCd"],
                    stkNm=resolved.get("stkNm") or "",
                    note="web",
                )
                result["stkNm"] = result.get("stkNm") or resolved.get("stkNm") or ""
                if result.get("ok"):
                    result["hint"] = f"{result.get('algCd')} 관리종목 추가"
                code = 200 if result.get("ok") else 400
                self._json(code, result)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/algStkDel":
            algCd = (qs.get("algCd") or ["MTS01"])[0]
            rawCd = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawCd).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 필수"})
                return
            try:
                from utils.algStkStore import delAlgManaged
                from utils.stkResolve import resolveStkRef

                resolved = resolveStkRef(rawCd)
                stkCd = (
                    resolved.get("stkCd") if resolved.get("ok") else str(rawCd).strip()
                )
                result = delAlgManaged(algCd, stkCd)
                code = 200 if result.get("ok") else 400
                self._json(code, result)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/tradeJournal":
            mode = (qs.get("mode") or ["paper"])[0]
            exitDt = _ymd((qs.get("exitDt") or qs.get("dt") or [""])[0])
            try:
                from utils.stratLiveTradeStore import buildTradeJournal

                data = buildTradeJournal(mode=mode, exitDt=exitDt)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/tradeCal":
            # 투자달력 — 월별 일자 요약 (계좌 당일손익=당일 매도실현)
            mode = (qs.get("mode") or ["paper"])[0]
            ymRaw = str((qs.get("ym") or [""])[0]).strip()
            year = (qs.get("year") or [""])[0]
            month = (qs.get("month") or [""])[0]
            if len(ymRaw) == 6 and ymRaw.isdigit():
                year, month = ymRaw[:4], ymRaw[4:6]
            try:
                from utils.stratLiveTradeStore import buildTradeCalMonth

                data = buildTradeCalMonth(
                    mode=mode, year=year or None, month=month or None
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/algPnl":
            # 알고리즘별 당일·월간·누적 손익 (AI_ALG_PNL_DAY)
            mode = (qs.get("mode") or ["paper"])[0]
            tradeDt = _ymd((qs.get("tradeDt") or qs.get("dt") or [""])[0])
            refresh = str((qs.get("refresh") or ["1"])[0]).strip() not in (
                "0",
                "N",
                "n",
                "false",
            )
            hist = str((qs.get("hist") or ["0"])[0]).strip() in ("1", "Y", "y", "true")
            algCd = (qs.get("algCd") or [""])[0]
            try:
                from utils.algPnlDayStore import (
                    buildAlgPnlView,
                    listAlgPnlHistory,
                    rowToJson,
                )

                if hist:
                    rows = [
                        rowToJson(r)
                        for r in listAlgPnlHistory(
                            mode=mode, algCd=algCd or None, limit=int((qs.get("limit") or ["90"])[0])
                        )
                    ]
                    data = {"ok": True, "mode": mode, "rows": rows}
                else:
                    data = buildAlgPnlView(
                        mode=mode, tradeDt=tradeDt or None, refresh=refresh
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/algReview":
            algRef = (qs.get("algCd") or ["MTS01"])[0]
            tradeDt = _ymd((qs.get("tradeDt") or qs.get("dt") or [""])[0])
            try:
                data = _buildAlgReview(algRef, tradeDt)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/algDayReview":
            algRef = (qs.get("algCd") or ["MTS01"])[0]
            tradeDt = _ymd((qs.get("tradeDt") or qs.get("dt") or [""])[0])
            mode = (qs.get("mode") or ["paper"])[0]
            refresh = str((qs.get("refresh") or ["0"])[0]).lower() in ("1", "y", "true")
            try:
                from utils.algDayReview import (
                    buildAlgDayReview,
                    loadAlgDayReview,
                    reviewNeedsRebuild,
                    saveAlgDayReview,
                )
                from utils.algDayReviewStore import listAlgDayReviewSummaries

                code = str(algRef or "MTS01").strip().upper()[:5]
                ymd = tradeDt or datetime.now().strftime("%Y%m%d")
                data = None
                if not refresh:
                    data = loadAlgDayReview(ymd, code, mode=mode)
                if refresh or reviewNeedsRebuild(data):
                    rep = buildAlgDayReview(code, ymd)
                    saveAlgDayReview(rep, mode=mode)
                    data = rep
                summaries = listAlgDayReviewSummaries(tradeDt=ymd, mode=mode)
                algs = [
                    {
                        "algCd": s.get("algCd"),
                        "name": s.get("name") or s.get("algCd"),
                        "hasReview": True,
                    }
                    for s in summaries
                ]
                if data:
                    payload: dict = dict(data)
                    payload["algs"] = algs
                    data = payload
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data or {"ok": False, "errMsg": "복기 없음"})
            return

        if path == "/api/watchStkDay":
            tradeDt = _ymd((qs.get("tradeDt") or qs.get("dt") or [""])[0])
            useTradeOnly = str((qs.get("useTradeOnly") or ["0"])[0]).lower() in (
                "1",
                "y",
                "true",
            )
            refresh = str((qs.get("refresh") or ["0"])[0]).lower() in ("1", "y", "true")
            try:
                from utils.watchStkDayStore import (
                    buildWatchStkDayView,
                    listWatchStkDayDates,
                    snapshotWatchStkDay,
                )

                if str((qs.get("listDays") or ["0"])[0]).lower() in ("1", "y", "true"):
                    data = {"ok": True, "days": listWatchStkDayDates()}
                elif refresh and tradeDt:
                    snapshotWatchStkDay(tradeDt=tradeDt)
                    data = buildWatchStkDayView(
                        tradeDt=tradeDt, useTradeOnly=useTradeOnly
                    )
                else:
                    data = buildWatchStkDayView(
                        tradeDt=tradeDt or datetime.now().strftime("%Y%m%d"),
                        useTradeOnly=useTradeOnly,
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/stratLive":
            algRef = (qs.get("algCd") or qs.get("stratId") or ["MTS01"])[0]
            try:
                data = _buildStratLiveView(algRef)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/algVerHist":
            algRef = (qs.get("algCd") or qs.get("stratId") or [""])[0]
            try:
                from utils.stratVerHist import listAlgVerHist

                data = listAlgVerHist(algRef or None)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, data)
            return

        if path == "/api/stratLiveSet":
            algRef = (qs.get("algCd") or qs.get("stratId") or ["MTS01"])[0]
            try:
                from config.settingsStore import setStratLiveAmts

                result = setStratLiveAmts(
                    algRef,
                    amt5Man=(qs.get("buyAmt5Man") or [None])[0],
                    amt10Man=(qs.get("buyAmt10Man") or [None])[0],
                    amt15Man=(qs.get("buyAmt15Man") or [None])[0],
                    amtWonMan=(qs.get("buyAmtWonMan") or [None])[0],
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/watchRtStatus":
            try:
                from trading.watchRtMonitor import readWatchRtState

                st = readWatchRtState()
                st["ok"] = True
                self._json(200, st)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/rankList":
            qryTp = (qs.get("qryTp") or ["4"])[0]
            try:
                lim = int((qs.get("limit") or ["100"])[0])
            except ValueError:
                lim = 100
            try:
                data = fetchRankList(qryTp=qryTp, limit=lim)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "tradeDt": data["tradeDt"],
                    "qryTp": data["qryTp"],
                    "cnt": len(data["items"]),
                    "items": data["items"],
                },
            )
            return

        if path == "/api/rankScore":
            condCd = (qs.get("condCd") or ["001"])[0]
            tradeDt = (qs.get("tradeDt") or [""])[0]
            rawLim = (qs.get("limit") or [""])[0]
            try:
                from utils.rankCondStore import getRankCond
                from utils.rankScoreStore import listRankScore

                condRes = getRankCond(condCd)
                # limit 미지정이면 조건셋의 표시 순위 수(TOP_N)를 따른다
                try:
                    lim = int(rawLim)
                except ValueError:
                    lim = (
                        int(condRes["cond"].get("topN") or 10)
                        if condRes.get("ok")
                        else 10
                    )
                data = listRankScore(condCd=condCd, tradeDt=tradeDt, limit=lim)
                if condRes.get("ok"):
                    data["condNm"] = condRes["cond"].get("condNm") or ""
                    data["totScore"] = condRes["cond"].get("totScore")
                    data["univDays"] = condRes["cond"].get("univDays")
                    data["itemNames"] = {
                        it["itemCd"]: it["itemNm"] for it in condRes["items"]
                    }
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if data.get("ok") else 500
            self._json(code, data)
            return

        if path == "/api/rankCond":
            condCd = (qs.get("condCd") or ["001"])[0]
            try:
                from utils.rankCondStore import getRankCond, listRankConds
                from utils.rankScoreStore import listRankScore

                data = getRankCond(condCd)
                if data.get("ok"):
                    data["conds"] = listRankConds()
                    last = listRankScore(condCd=condCd, limit=1)
                    data["lastTradeDt"] = last.get("tradeDt") or ""
                    data["lastUpdatedAt"] = last.get("updatedAt") or ""
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if data.get("ok") else 400
            self._json(code, data)
            return

        if path == "/api/rankCondSet":
            condCd = (qs.get("condCd") or ["001"])[0]
            itemCd = (qs.get("itemCd") or [""])[0]
            try:
                from utils.rankCondStore import updateRankCond, updateRankCondItem

                if str(itemCd).strip():
                    result = updateRankCondItem(
                        condCd,
                        itemCd,
                        useYn=(qs.get("useYn") or [None])[0],
                        weight=(qs.get("weight") or [None])[0],
                        paramJson=(qs.get("paramJson") or [None])[0],
                    )
                else:
                    result = updateRankCond(
                        condCd,
                        condNm=(qs.get("condNm") or [None])[0],
                        totScore=(qs.get("totScore") or [None])[0],
                        univDays=(qs.get("univDays") or [None])[0],
                        topN=(qs.get("topN") or [None])[0],
                        status=(qs.get("status") or [None])[0],
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/rankScoreCalc":
            condCd = (qs.get("condCd") or ["001"])[0]
            tradeDt = (qs.get("tradeDt") or [""])[0]
            try:
                from utils.rankScoreCalc import calcRankScore

                result = calcRankScore(tradeDt=tradeDt or None, condCd=condCd)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path == "/api/usFluDates":
            try:
                lim = int((qs.get("limit") or ["30"])[0])
            except ValueError:
                lim = 30
            try:
                from utils.usDayFluIssueStore import listUsFluTradeDts

                rows = listUsFluTradeDts(lim)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            items = []
            for r in rows:
                items.append(
                    {
                        "tradeDt": r.get("TRADE_DT"),
                        "cnt": int(r.get("CNT") or 0),
                        "upCnt": int(r.get("UP_CNT") or 0),
                        "dnCnt": int(r.get("DN_CNT") or 0),
                    }
                )
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/usFluIssue":
            rawDt = (qs.get("date") or qs.get("tradeDt") or [""])[0]
            fluTp = (qs.get("fluTp") or ["all"])[0]
            try:
                lim = int((qs.get("limit") or ["80"])[0])
            except ValueError:
                lim = 80
            try:
                from utils.usDayFluIssueReport import slimFluRows, slimThemeRows
                from utils.usDayFluIssueStore import (
                    listUsFluIssue,
                    listUsFluThemeAgg,
                    listUsFluTradeDts,
                )

                tradeDt = str(rawDt or "").replace("-", "")[:8]
                if not (tradeDt.isdigit() and len(tradeDt) == 8):
                    dts = listUsFluTradeDts(1)
                    tradeDt = (dts[0].get("TRADE_DT") if dts else "") or ""
                if not tradeDt:
                    self._json(
                        200,
                        {
                            "ok": True,
                            "tradeDt": "",
                            "items": [],
                            "themes": [],
                            "errMsg": "적재분 없음 — 웹에서 적재 또는 usflu load",
                        },
                    )
                    return
                ftp = None if fluTp in ("", "all", "0") else str(fluTp)
                items = slimFluRows(listUsFluIssue(tradeDt, fluTp=ftp, limit=lim))
                themes = slimThemeRows(
                    listUsFluThemeAgg(tradeDt, fluTp=ftp, topN=20)
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "tradeDt": tradeDt,
                    "fluTp": fluTp,
                    "cnt": len(items),
                    "items": items,
                    "themes": themes,
                },
            )
            return

        if path == "/api/usFluLoad":
            # GET으로도 허용 (로컬 도구)
            rawDt = (qs.get("date") or qs.get("tradeDt") or [""])[0]
            tradeDt = str(rawDt or "").replace("-", "")[:8]
            if tradeDt and not (tradeDt.isdigit() and len(tradeDt) == 8):
                self._json(400, {"ok": False, "errMsg": "date=YYYYMMDD"})
                return
            prefer = (qs.get("mode") or ["paper"])[0]
            if prefer not in ("paper", "real"):
                prefer = "paper"
            try:
                from utils.usDayFluIssueLoad import loadUsDayFluIssue
                from utils.usDayFluIssueReport import (
                    formatUsFluIssueReport,
                    slimFluRows,
                    slimThemeRows,
                )
                from utils.usDayFluIssueStore import (
                    listUsFluIssue,
                    listUsFluThemeAgg,
                )

                res = loadUsDayFluIssue(
                    tradeDt=tradeDt or None, preferMode=prefer
                )
                if not res.get("ok"):
                    self._json(500, {"ok": False, "errMsg": res.get("errMsg")})
                    return
                td = res.get("tradeDt")
                items = slimFluRows(listUsFluIssue(td, limit=120))
                themes = slimThemeRows(listUsFluThemeAgg(td, topN=20))
                self._json(
                    200,
                    {
                        "ok": True,
                        "tradeDt": td,
                        "upCnt": res.get("upCnt"),
                        "dnCnt": res.get("dnCnt"),
                        "summary": res.get("summary"),
                        "report": formatUsFluIssueReport(td),
                        "items": items,
                        "themes": themes,
                        "warnings": res.get("warnings") or [],
                    },
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/watchDisc":
            rawCd = (qs.get("stkCd") or qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["30"])[0])
            except ValueError:
                limit = 30
            try:
                from financeAgent.serviceApi import listWatchRecentDisc
                from utils.stkCdUtil import normStkCd

                cd = normStkCd(rawCd) if str(rawCd).strip() else None
                rows = listWatchRecentDisc(cd, limit=limit)
                items = []
                for r in rows:
                    items.append(
                        {
                            "stkCd": r.get("STK_CD"),
                            "stkNm": r.get("STK_NM"),
                            "rceptNo": r.get("RCEPT_NO"),
                            "reportNm": r.get("REPORT_NM"),
                            "rceptDt": r.get("RCEPT_DT"),
                            "flrNm": r.get("FLR_NM"),
                            "viewUrl": r.get("VIEW_URL"),
                            "corpName": r.get("CORP_NAME"),
                        }
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/watchNews":
            rawCd = (qs.get("stkCd") or qs.get("q") or [""])[0]
            issueOnly = str((qs.get("issueOnly") or ["0"])[0]).lower() in (
                "1",
                "y",
                "true",
                "yes",
            )
            try:
                limit = int((qs.get("limit") or ["30"])[0])
            except ValueError:
                limit = 30
            try:
                from financeAgent.serviceApi import listWatchRecentNews
                from utils.stkCdUtil import normStkCd

                cd = normStkCd(rawCd) if str(rawCd).strip() else None
                rows = listWatchRecentNews(
                    cd, limit=limit, issueOnly=issueOnly
                )
                items = []
                for r in rows:
                    items.append(
                        {
                            "stkCd": r.get("STK_CD"),
                            "stkNm": r.get("STK_NM"),
                            "newsId": r.get("NEWS_ID"),
                            "title": r.get("TITLE"),
                            "officeNm": r.get("OFFICE_NM"),
                            "pubDt": r.get("PUB_DT"),
                            "pubTm": r.get("PUB_TM"),
                            "newsUrl": r.get("NEWS_URL"),
                            "issueYn": r.get("ISSUE_YN"),
                        }
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "cnt": len(items), "items": items})
            return

        if path == "/api/wnewsStatus":
            try:
                from utils.watchNewsDiscScheduler import (
                    getWatchNewsDiscSchedStatus,
                )

                st = getWatchNewsDiscSchedStatus()
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(200, {"ok": True, "status": st})
            return

        if path == "/api/stkFinance":
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 또는 q 필수"})
                return
            loadRpt = str((qs.get("loadRpt") or ["0"])[0]).lower() in (
                "1",
                "y",
                "true",
                "yes",
            )
            finYear = (qs.get("year") or [""])[0]
            finYear = finYear if str(finYear).isdigit() else None
            try:
                limit = int((qs.get("limit") or ["15"])[0])
            except ValueError:
                limit = 15
            try:
                # 재무 도메인은 Finance Agent 경계 경유
                from financeAgent.serviceApi import getStkFinanceBundle

                data = getStkFinanceBundle(
                    rawRef,
                    loadRpt=loadRpt,
                    finYear=finYear,
                    rptLimit=limit,
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if data.get("ok") else 400
            self._json(code, data)
            return

        if path == "/api/corpReport":
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 또는 q 필수"})
                return
            try:
                pages = int((qs.get("pages") or ["2"])[0])
            except ValueError:
                pages = 2
            try:
                limit = int((qs.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            try:
                detailN = int((qs.get("detail") or ["4"])[0])
            except ValueError:
                detailN = 4
            try:
                from financeAgent.serviceApi import analyzeCorpReports

                result = analyzeCorpReports(
                    rawRef,
                    maxPages=pages,
                    maxItems=limit,
                    detailTopN=detailN,
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            # items 전체는 클 수 있어 summary 중심 — items는 상위만
            if result.get("ok") and result.get("analysis"):
                an = result["analysis"]
                items = an.get("items") or []
                slimItems = []
                for it in items[:15]:
                    slimItems.append(
                        {
                            "nid": it.get("nid"),
                            "title": it.get("title"),
                            "broker": it.get("broker"),
                            "pubDt": it.get("pubDt"),
                            "pubDtRaw": it.get("pubDtRaw"),
                            "opinion": it.get("opinion"),
                            "targetPrc": it.get("targetPrc"),
                            "detailUrl": it.get("detailUrl"),
                            "pdfUrl": it.get("pdfUrl"),
                            "views": it.get("views"),
                            "summary": (it.get("summary") or "")[:400],
                        }
                    )
                result = {
                    "ok": True,
                    "stkCd": result.get("stkCd"),
                    "stkNm": result.get("stkNm"),
                    "listUrl": result.get("listUrl"),
                    "cnt": result.get("cnt"),
                    "asOf": result.get("asOf"),
                    "disclaimer": result.get("disclaimer"),
                    "msgText": result.get("msgText"),
                    "summary": an.get("summary"),
                    "highlights": an.get("highlights"),
                    "risks": an.get("risks"),
                    "themes": an.get("themes"),
                    "items": slimItems,
                }
            self._json(code, result)
            return

        if path == "/api/invSr":
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 또는 q 필수"})
                return
            amtQtyTp = (qs.get("amtQtyTp") or ["2"])[0] or "2"
            trdeTp = (qs.get("trdeTp") or ["0"])[0] or "0"
            unitTp = (qs.get("unitTp") or ["1"])[0] or "1"
            start = _ymd((qs.get("start") or [""])[0])
            end = _ymd((qs.get("end") or [""])[0])
            try:
                lim = int((qs.get("limit") or ["400"])[0])
            except ValueError:
                lim = 400
            try:
                from utils.dayInvSrStore import listInvSrByStk
                from utils.stkResolve import resolveStkRef

                resolved = resolveStkRef(rawRef)
                if not resolved.get("ok"):
                    matches = []
                    for m in resolved.get("matches") or []:
                        matches.append(_rowStkMeta(m))
                    self._json(
                        400,
                        {
                            "ok": False,
                            "errMsg": resolved.get("errMsg"),
                            "matches": matches,
                        },
                    )
                    return
                stkCd = resolved["stkCd"]
                rows = listInvSrByStk(
                    stkCd,
                    amtQtyTp=amtQtyTp,
                    trdeTp=trdeTp,
                    unitTp=unitTp,
                    startDt=start,
                    endDt=end,
                    limit=lim,
                )
                meta = lookupStkMeta(stkCd)
                items = [_invSrRowToJson(r) for r in rows]
                self._json(
                    200,
                    {
                        "ok": True,
                        "stkCd": stkCd,
                        "stkNm": resolved.get("stkNm") or meta.get("stkNm") or "",
                        "marketName": meta.get("marketName") or "",
                        "amtQtyTp": str(amtQtyTp),
                        "trdeTp": str(trdeTp),
                        "unitTp": str(unitTp),
                        "startDt": start or "",
                        "endDt": end or "",
                        "cnt": len(items),
                        "items": items,
                    },
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
            return

        if path == "/api/invSrLoad":
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            amtQtyTp = (qs.get("amtQtyTp") or ["2"])[0] or "2"
            trdeTp = (qs.get("trdeTp") or ["0"])[0] or "0"
            unitTp = (qs.get("unitTp") or ["1"])[0] or "1"
            tradeDt = _ymd((qs.get("date") or qs.get("tradeDt") or [""])[0])
            prefer = (qs.get("mode") or [""])[0].strip().lower() or None
            if prefer not in ("paper", "real"):
                prefer = None
            try:
                from utils.dayInvSrLoad import loadInvSrForStk, loadInvSrUniverse

                if str(rawRef).strip():
                    from utils.stkResolve import resolveStkRef

                    resolved = resolveStkRef(rawRef)
                    if not resolved.get("ok"):
                        matches = []
                        for m in resolved.get("matches") or []:
                            matches.append(_rowStkMeta(m))
                        self._json(
                            400,
                            {
                                "ok": False,
                                "errMsg": resolved.get("errMsg"),
                                "matches": matches,
                            },
                        )
                        return
                    result = loadInvSrForStk(
                        resolved["stkCd"],
                        tradeDt=tradeDt,
                        amtQtyTp=amtQtyTp,
                        trdeTp=trdeTp,
                        unitTp=unitTp,
                        preferMode=prefer,
                    )
                else:
                    result = loadInvSrUniverse(
                        tradeDt=tradeDt,
                        amtQtyTp=amtQtyTp,
                        trdeTp=trdeTp,
                        unitTp=unitTp,
                        preferMode=prefer,
                    )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") or result.get("partial") else 400
            self._json(code, result)
            return

        if path == "/api/stkSearch":
            q = (qs.get("q") or qs.get("query") or [""])[0]
            if not str(q).strip():
                self._json(400, {"ok": False, "errMsg": "q=종목코드|종목명"})
                return
            try:
                lim = int((qs.get("limit") or ["25"])[0])
            except ValueError:
                lim = 25
            try:
                items = searchStkApi(q, limit=lim)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(
                200,
                {"ok": True, "q": str(q).strip(), "cnt": len(items), "items": items},
            )
            return

        if path == "/api/stkLoadDay":
            # 일봉 API 적재 (모의 1년은 수분 소요 가능)
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 또는 q 필수"})
                return
            start, end, _preset = _resolveRange(qs)
            # months 지정 시 preset 무시
            rawMo = (qs.get("months") or [""])[0]
            if rawMo:
                try:
                    from utils.dayPrcLoadService import defaultLoadRange

                    start, end = defaultLoadRange(int(rawMo))
                except Exception:
                    pass
            prefer = (qs.get("mode") or [""])[0].strip().lower() or None
            if prefer not in ("paper", "real"):
                prefer = None
            try:
                from utils.dayPrcLoadService import loadDayStkPrc
                from utils.stkResolve import resolveStkRef

                resolved = resolveStkRef(rawRef)
                if not resolved.get("ok"):
                    matches = []
                    for m in resolved.get("matches") or []:
                        matches.append(_rowStkMeta(m))
                    if matches:
                        # 테마 보강
                        tmap = _fetchIndGrpMap([x["stkCd"] for x in matches])
                        for m in matches:
                            if tmap.get(m["stkCd"]):
                                m["theme"] = tmap[m["stkCd"]]
                                m["indGrp"] = tmap[m["stkCd"]]
                    self._json(
                        400,
                        {
                            "ok": False,
                            "errMsg": resolved.get("errMsg"),
                            "matches": matches,
                        },
                    )
                    return
                result = loadDayStkPrc(
                    resolved["stkCd"],
                    start,
                    end,
                    preferMode=prefer,
                )
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            code = 200 if result.get("ok") else 400
            meta = lookupStkMeta(result.get("stkCd") or "")
            result = {
                **result,
                "marketName": meta.get("marketName"),
                "upName": meta.get("upName"),
                "indGrp": meta.get("indGrp"),
                "theme": meta.get("theme"),
                "sector": meta.get("sector"),
                "startDt": result.get("startDt") or start,
                "endDt": result.get("endDt") or end,
            }
            self._json(code, result)
            return

        if path == "/api/stkSeries":
            rawRef = (qs.get("stkCd") or qs.get("q") or [""])[0]
            if not str(rawRef).strip():
                self._json(400, {"ok": False, "errMsg": "stkCd 또는 q 필수"})
                return
            # 이름·코드 해석
            try:
                from utils.stkResolve import resolveStkRef

                resolved = resolveStkRef(rawRef)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            if not resolved.get("ok"):
                matches = []
                for m in resolved.get("matches") or []:
                    matches.append(_rowStkMeta(m))
                tmap = _fetchIndGrpMap([x["stkCd"] for x in matches])
                for m in matches:
                    if tmap.get(m["stkCd"]):
                        m["theme"] = tmap[m["stkCd"]]
                        m["indGrp"] = tmap[m["stkCd"]]
                self._json(
                    400,
                    {
                        "ok": False,
                        "errMsg": resolved.get("errMsg"),
                        "matches": matches,
                    },
                )
                return
            stkCd = resolved["stkCd"]
            start, end, _preset = _resolveRange(qs)
            srAlg = resolveSrAlgId((qs.get("srAlg") or [DEFAULT_SR_ALG])[0])
            try:
                data = fetchStkSeries(stkCd, start, end, srAlgId=srAlg)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "stkCd": data["stkCd"],
                    "stkNm": data["stkNm"] or resolved.get("stkNm"),
                    "marketName": data.get("marketName") or "",
                    "upName": data.get("upName") or "",
                    "indGrp": data.get("indGrp") or "",
                    "theme": data.get("theme") or "",
                    "sector": data.get("sector") or "",
                    "startDt": start,
                    "endDt": end,
                    "startDate": _ymdToIso(start),
                    "endDate": _ymdToIso(end),
                    "points": data["points"],
                    "levels": data["levels"],
                    "srAlgId": data["srAlgId"],
                    "srAlgName": data["srAlgName"],
                    "patterns": data.get("patterns") or [],
                    "rowCnt": data["rowCnt"],
                },
            )
            return

        if path == "/api/frgnSpotFut":
            start, end, _preset = _resolveRange(qs)
            try:
                from utils.dayFrgnSfSeries import buildFrgnSfSeries

                data = buildFrgnSfSeries(start, end)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            if not data.get("ok"):
                self._json(400, data)
                return
            self._json(200, data)
            return

        if path == "/api/frgnSpotFutLoad":
            start, end, _preset = _resolveRange(qs)
            # 20·60·120 이평을 채우려고 시작일 앞을 같이 적재
            from datetime import datetime as _dt
            from datetime import timedelta as _td

            try:
                look = (
                    _dt.strptime(start, "%Y%m%d") - _td(days=280)
                ).strftime("%Y%m%d")
            except ValueError:
                look = start
            loaded = {}
            try:
                from utils.dayFrgnSfLoad import loadFrgnSfRange
                from utils.dayFrgnSfSeries import buildFrgnSfSeries

                loaded = loadFrgnSfRange(look, end)
                data = buildFrgnSfSeries(start, end)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return
            if not data.get("ok"):
                data["load"] = loaded
                self._json(400, data)
                return
            data["load"] = loaded
            self._json(200, data)
            return

        if path == "/api/series":
            rawInds = (qs.get("inds") or ["001"])[0]
            meta = INDS_MAP.get(str(rawInds).lower()) or INDS_MAP.get(str(rawInds))
            if not meta:
                self._json(400, {"ok": False, "errMsg": "inds=001|101|kospi|kosdaq"})
                return

            start, end, _preset = _resolveRange(qs)
            sigAlg = resolveSigAlgId((qs.get("sigAlg") or [DEFAULT_SIG_ALG])[0])

            try:
                data = fetchSeries(meta["code"], start, end, sigAlgId=sigAlg)
            except Exception as err:
                self._json(500, {"ok": False, "errMsg": str(err)})
                return

            self._json(
                200,
                {
                    "ok": True,
                    "indsCd": meta["code"],
                    "indsName": meta["name"],
                    "indsLabel": meta["label"],
                    "startDt": start,
                    "endDt": end,
                    "startDate": _ymdToIso(start),
                    "endDate": _ymdToIso(end),
                    "points": data["points"],
                    "signals": data["signals"],
                    "lastSignalDate": data["lastSignalDate"],
                    "sigAlgId": data["sigAlgId"],
                    "sigAlgName": data["sigAlgName"],
                    "rowCnt": data["rowCnt"],
                },
            )
            return

        if path in ("/", ""):
            self.path = "/index.html"
        elif path.startswith("/api/"):
            # 구버전 서버/미등록 API가 HTML 404를 주면 화면이 JSON 파싱 오류를 냄
            self._json(404, {"ok": False, "errMsg": f"API 없음: {path}"})
            return
        return SimpleHTTPRequestHandler.do_GET(self)


def _portInUse(host=HOST, port=PORT, timeoutSec=0.6):
    """
    이미 서버가 떠 있는지 확인.
    윈도우는 SO_REUSEADDR로 같은 포트 중복 바인딩이 되어 옛 코드가 뜬 프로세스가
    요청을 가로챌 수 있다. 그래서 바인딩 전에 접속으로 직접 확인한다.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeoutSec)
        return sock.connect_ex((host, port)) == 0


def main():
    if _portInUse():
        # cp949 콘솔에서 깨지지 않게 특수문자(—) 대신 기본 문자만 쓴다
        print(f"이미 {HOST}:{PORT} 에 서버가 실행 중입니다. 중복 기동을 중단합니다.")
        print("코드를 반영하려면 기존 프로세스를 종료한 뒤 다시 실행하세요.")
        return 1

    server = ThreadingHTTPServer((HOST, PORT), IndsChartHandler)
    print(f"지수·종목 차트: http://{HOST}:{PORT}/")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
