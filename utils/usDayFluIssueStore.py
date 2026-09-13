"""AI_US_DAY_FLU_ISSUE UPSERT / 조회"""

from __future__ import annotations

from utils.dbConn import openDbConn
from utils.termLog import termLog
from utils.usDayFluIssueTables import ensureUsFluIssueTable

_UPSERT_SQL = """
INSERT INTO AI_US_DAY_FLU_ISSUE (
  TRADE_DT, FLU_TP, RANK_NO,
  STK_CD, STK_NM, STK_ENM, STEX_TP, IND_GRP,
  FLU_RT, CUR_PRC, PRED_PRE, BASE_PRIC,
  ACC_TRDE_QTY, SDNIN_RT, PRED_PRE_SIG, SRC_TR
) VALUES (
  %(TRADE_DT)s, %(FLU_TP)s, %(RANK_NO)s,
  %(STK_CD)s, %(STK_NM)s, %(STK_ENM)s, %(STEX_TP)s, %(IND_GRP)s,
  %(FLU_RT)s, %(CUR_PRC)s, %(PRED_PRE)s, %(BASE_PRIC)s,
  %(ACC_TRDE_QTY)s, %(SDNIN_RT)s, %(PRED_PRE_SIG)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STK_CD = VALUES(STK_CD),
  STK_NM = VALUES(STK_NM),
  STK_ENM = VALUES(STK_ENM),
  STEX_TP = VALUES(STEX_TP),
  IND_GRP = VALUES(IND_GRP),
  FLU_RT = VALUES(FLU_RT),
  CUR_PRC = VALUES(CUR_PRC),
  PRED_PRE = VALUES(PRED_PRE),
  BASE_PRIC = VALUES(BASE_PRIC),
  ACC_TRDE_QTY = VALUES(ACC_TRDE_QTY),
  SDNIN_RT = VALUES(SDNIN_RT),
  PRED_PRE_SIG = VALUES(PRED_PRE_SIG),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertUsFluIssueRows(rows):
    if not rows:
        return {"ok": True, "upsertCnt": 0}
    ensureUsFluIssueTable()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        termLog(f"[AI_US_DAY_FLU_ISSUE] UPSERT {len(rows)}건 (affected={affected})")
        return {"ok": True, "upsertCnt": len(rows), "affected": affected}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[AI_US_DAY_FLU_ISSUE] UPSERT 실패: {err}")
        return {"ok": False, "upsertCnt": 0, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def deleteUsFluIssueByDt(tradeDt, fluTp=None):
    """재적재 전 해당일(·구분) 삭제."""
    ensureUsFluIssueTable()
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            if fluTp:
                cur.execute(
                    "DELETE FROM AI_US_DAY_FLU_ISSUE WHERE TRADE_DT=%s AND FLU_TP=%s",
                    (str(tradeDt), str(fluTp)),
                )
            else:
                cur.execute(
                    "DELETE FROM AI_US_DAY_FLU_ISSUE WHERE TRADE_DT=%s",
                    (str(tradeDt),),
                )
            deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def listUsFluIssue(tradeDt, fluTp=None, limit=100):
    """일자별 급등/급락 목록."""
    ensureUsFluIssueTable()
    lim = max(1, min(int(limit or 100), 500))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            if fluTp in ("1", "2"):
                cur.execute(
                    """
                    SELECT *
                    FROM AI_US_DAY_FLU_ISSUE
                    WHERE TRADE_DT=%s AND FLU_TP=%s
                    ORDER BY RANK_NO ASC
                    LIMIT %s
                    """,
                    (str(tradeDt), str(fluTp), lim),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM AI_US_DAY_FLU_ISSUE
                    WHERE TRADE_DT=%s
                    ORDER BY FLU_TP ASC, RANK_NO ASC
                    LIMIT %s
                    """,
                    (str(tradeDt), lim),
                )
            return cur.fetchall() or []
    finally:
        conn.close()


def listUsFluThemeAgg(tradeDt, fluTp=None, topN=15):
    """테마별 종목수·평균등락률."""
    ensureUsFluIssueTable()
    top = max(1, min(int(topN or 15), 50))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT FLU_TP, IFNULL(IND_GRP,'기타') AS IND_GRP,
                       COUNT(*) AS CNT,
                       ROUND(AVG(FLU_RT), 2) AS AVG_FLU,
                       ROUND(MAX(FLU_RT), 2) AS MAX_FLU,
                       ROUND(MIN(FLU_RT), 2) AS MIN_FLU
                FROM AI_US_DAY_FLU_ISSUE
                WHERE TRADE_DT=%s
            """
            params = [str(tradeDt)]
            if fluTp in ("1", "2"):
                sql += " AND FLU_TP=%s"
                params.append(str(fluTp))
            sql += """
                GROUP BY FLU_TP, IFNULL(IND_GRP,'기타')
                ORDER BY FLU_TP ASC, ABS(AVG(FLU_RT)) DESC, CNT DESC
                LIMIT %s
            """
            params.append(top * 2)
            cur.execute(sql, tuple(params))
            return cur.fetchall() or []
    finally:
        conn.close()


def listUsFluTradeDts(limit=30):
    """적재된 일자 목록 (최신순)."""
    ensureUsFluIssueTable()
    lim = max(1, min(int(limit or 30), 90))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, COUNT(*) AS CNT,
                       SUM(FLU_TP='1') AS UP_CNT,
                       SUM(FLU_TP='2') AS DN_CNT
                FROM AI_US_DAY_FLU_ISSUE
                GROUP BY TRADE_DT
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (lim,),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def hasUsFluIssue(tradeDt):
    ensureUsFluIssueTable()
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM AI_US_DAY_FLU_ISSUE WHERE TRADE_DT=%s LIMIT 1",
                (str(tradeDt),),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()
