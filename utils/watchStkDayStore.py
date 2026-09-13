"""관심종목 일별 스냅샷 — 적재·조회 (매매일지·과거 검증)"""

from __future__ import annotations

from datetime import datetime

from utils.dbConn import openDbConn
from utils.termLog import termLog
from utils.watchStkDayTables import ensureWatchStkDayTable
from utils.watchStkTables import ensureWatchStkTable


def _normYmd(raw):
    return str(raw or "").replace("-", "").strip()[:8]


def _rowToJson(row):
    return {
        "tradeDt": row.get("TRADE_DT") or "",
        "stkCd": row.get("STK_CD") or "",
        "stkNm": row.get("STK_NM") or "",
        "marketName": row.get("MARKET_NAME") or "",
        "indGrp": row.get("IND_GRP") or "",
        "grpCd": row.get("GRP_CD") or "",
        "useBt": row.get("USE_BT") or "N",
        "useTrade": row.get("USE_TRADE") or "N",
        "priority": int(row.get("PRIORITY") or 0),
        "rankNo": row.get("RANK_NO"),
        "refTradeDt": row.get("REF_TRADE_DT") or "",
        "loadDt": row.get("LOAD_DT") or "",
        "openPrc": row.get("OPEN_PRC"),
        "closePrc": row.get("CLOSE_PRC"),
        "src": row.get("SRC") or "",
        "note": row.get("NOTE") or "",
        "snapshotAt": (
            row.get("SNAPSHOT_AT").strftime("%Y-%m-%d %H:%M:%S")
            if row.get("SNAPSHOT_AT") is not None
            and hasattr(row.get("SNAPSHOT_AT"), "strftime")
            else str(row.get("SNAPSHOT_AT") or "")
        ),
    }


def snapshotWatchStkDay(tradeDt=None, mktTp="KR"):
    """
    당일 활성 관심종목(STATUS=Y)을 AI_WATCH_STK_DAY 에 저장.
    같은 TRADE_DT 재실행 시 UPSERT 갱신.
    """
    if not ensureWatchStkDayTable():
        return {"ok": False, "errMsg": "AI_WATCH_STK_DAY 없음"}

    ymd = _normYmd(tradeDt or datetime.now().strftime("%Y%m%d"))
    ensureWatchStkTable()
    connRd = openDbConn()
    try:
        with connRd.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
                       USE_BT, USE_TRADE, PRIORITY, RANK_NO, REF_TRADE_DT, LOAD_DT,
                       OPEN_PRC, CLOSE_PRC, SRC, NOTE
                FROM AI_WATCH_STK
                WHERE STATUS='Y' AND MKT_TP=%s
                ORDER BY PRIORITY ASC, RANK_NO ASC, STK_CD ASC
                """,
                (str(mktTp),),
            )
            rows = cur.fetchall() or []
    finally:
        connRd.close()
    snapAt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 비활성화된 종목은 당일 스냅샷에서 제거 (재실행 시 정합)
            cur.execute(
                """
                DELETE FROM AI_WATCH_STK_DAY
                WHERE TRADE_DT=%s AND MKT_TP=%s
                """,
                (ymd, str(mktTp)),
            )
            if rows:
                payload = []
                for r in rows:
                    payload.append(
                        (
                            ymd,
                            r.get("STK_CD"),
                            str(mktTp),
                            r.get("STK_NM"),
                            r.get("MARKET_NAME"),
                            r.get("IND_GRP"),
                            r.get("GRP_CD"),
                            r.get("USE_BT") or "Y",
                            r.get("USE_TRADE") or "N",
                            int(r.get("PRIORITY") or 100),
                            r.get("RANK_NO"),
                            r.get("REF_TRADE_DT"),
                            r.get("LOAD_DT"),
                            r.get("OPEN_PRC"),
                            r.get("CLOSE_PRC"),
                            r.get("SRC"),
                            r.get("NOTE"),
                            snapAt,
                        )
                    )
                cur.executemany(
                    """
                    INSERT INTO AI_WATCH_STK_DAY (
                      TRADE_DT, STK_CD, MKT_TP, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
                      USE_BT, USE_TRADE, PRIORITY, RANK_NO, REF_TRADE_DT, LOAD_DT,
                      OPEN_PRC, CLOSE_PRC, SRC, NOTE, SNAPSHOT_AT
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                      STK_NM=VALUES(STK_NM),
                      MARKET_NAME=VALUES(MARKET_NAME),
                      IND_GRP=VALUES(IND_GRP),
                      GRP_CD=VALUES(GRP_CD),
                      USE_BT=VALUES(USE_BT),
                      USE_TRADE=VALUES(USE_TRADE),
                      PRIORITY=VALUES(PRIORITY),
                      RANK_NO=VALUES(RANK_NO),
                      REF_TRADE_DT=VALUES(REF_TRADE_DT),
                      LOAD_DT=VALUES(LOAD_DT),
                      SRC=VALUES(SRC),
                      NOTE=VALUES(NOTE),
                      SNAPSHOT_AT=VALUES(SNAPSHOT_AT)
                    """,
                    payload,
                )
        conn.commit()
        tradeCnt = sum(1 for r in rows if str(r.get("USE_TRADE") or "") == "Y")
        termLog(
            f"[watchStkDay] {ymd} 스냅샷 {len(rows)}종 "
            f"(매매ON {tradeCnt})"
        )
        return {
            "ok": True,
            "tradeDt": ymd,
            "cnt": len(rows),
            "tradeCnt": tradeCnt,
            "snapshotAt": snapAt,
        }
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[watchStkDay] 스냅샷 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listWatchStkDay(tradeDt, mktTp="KR", useTradeOnly=False):
    """일자별 관심종목 스냅샷 목록."""
    if not ensureWatchStkDayTable():
        return []
    ymd = _normYmd(tradeDt)
    if not ymd:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sql = """
                SELECT TRADE_DT, STK_CD, MKT_TP, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
                       USE_BT, USE_TRADE, PRIORITY, RANK_NO, REF_TRADE_DT, LOAD_DT,
                       OPEN_PRC, CLOSE_PRC, SRC, NOTE, SNAPSHOT_AT
                FROM AI_WATCH_STK_DAY
                WHERE TRADE_DT=%s AND MKT_TP=%s
            """
            args = [ymd, str(mktTp)]
            if useTradeOnly:
                sql += " AND USE_TRADE='Y'"
            sql += " ORDER BY PRIORITY ASC, RANK_NO ASC, STK_CD ASC"
            cur.execute(sql, args)
            rows = cur.fetchall() or []
        return [_rowToJson(r) for r in rows]
    except Exception as err:
        termLog(f"[watchStkDay] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listWatchStkDayDates(mktTp="KR", limit=180):
    """스냅샷이 있는 거래일 목록 (신→구)."""
    if not ensureWatchStkDayTable():
        return []
    lim = max(1, min(int(limit or 180), 365))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT,
                       COUNT(*) AS CNT,
                       SUM(CASE WHEN USE_TRADE='Y' THEN 1 ELSE 0 END) AS TRADE_CNT,
                       MAX(SNAPSHOT_AT) AS SNAPSHOT_AT
                FROM AI_WATCH_STK_DAY
                WHERE MKT_TP=%s
                GROUP BY TRADE_DT
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (str(mktTp), lim),
            )
            rows = cur.fetchall() or []
        out = []
        for r in rows:
            out.append(
                {
                    "tradeDt": r.get("TRADE_DT") or "",
                    "cnt": int(r.get("CNT") or 0),
                    "tradeCnt": int(r.get("TRADE_CNT") or 0),
                    "snapshotAt": (
                        r.get("SNAPSHOT_AT").strftime("%Y-%m-%d %H:%M:%S")
                        if r.get("SNAPSHOT_AT") is not None
                        and hasattr(r.get("SNAPSHOT_AT"), "strftime")
                        else str(r.get("SNAPSHOT_AT") or "")
                    ),
                }
            )
        return out
    except Exception as err:
        termLog(f"[watchStkDay] 일자목록 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def buildWatchStkDayView(tradeDt, mktTp="KR", useTradeOnly=False):
    """웹·매매일지용."""
    rows = listWatchStkDay(tradeDt, mktTp=mktTp, useTradeOnly=useTradeOnly)
    ymd = _normYmd(tradeDt)
    tradeCnt = sum(1 for r in rows if r.get("useTrade") == "Y")
    return {
        "ok": True,
        "tradeDt": ymd,
        "cnt": len(rows),
        "tradeCnt": tradeCnt if not useTradeOnly else len(rows),
        "rows": rows,
    }
