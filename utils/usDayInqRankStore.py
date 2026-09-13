"""AI_US_DAY_INQ_RANK UPSERT / 조회"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_US_DAY_INQ_RANK (
  TRADE_DT, QRY_TP, RANK_NO,
  STK_CD, STK_NM, STEX_TP, IND_GRP,
  RANK_CHG, RANK_CHG_SIGN,
  CUR_PRC, BASE_COMP_SIGN, BASE_COMP_CHGR,
  PREV_BASE_SIGN, PREV_BASE_CHGR, BASE_TM, SRC_TR
) VALUES (
  %(TRADE_DT)s, %(QRY_TP)s, %(RANK_NO)s,
  %(STK_CD)s, %(STK_NM)s, %(STEX_TP)s, %(IND_GRP)s,
  %(RANK_CHG)s, %(RANK_CHG_SIGN)s,
  %(CUR_PRC)s, %(BASE_COMP_SIGN)s, %(BASE_COMP_CHGR)s,
  %(PREV_BASE_SIGN)s, %(PREV_BASE_CHGR)s, %(BASE_TM)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STK_CD = VALUES(STK_CD),
  STK_NM = VALUES(STK_NM),
  STEX_TP = VALUES(STEX_TP),
  IND_GRP = VALUES(IND_GRP),
  RANK_CHG = VALUES(RANK_CHG),
  RANK_CHG_SIGN = VALUES(RANK_CHG_SIGN),
  CUR_PRC = VALUES(CUR_PRC),
  BASE_COMP_SIGN = VALUES(BASE_COMP_SIGN),
  BASE_COMP_CHGR = VALUES(BASE_COMP_CHGR),
  PREV_BASE_SIGN = VALUES(PREV_BASE_SIGN),
  PREV_BASE_CHGR = VALUES(PREV_BASE_CHGR),
  BASE_TM = VALUES(BASE_TM),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertUsDayInqRankRows(rows):
    """순위 스냅샷 UPSERT. 반환: {ok, upsertCnt, errMsg?}"""
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    from utils.usRankTables import ensureUsRankTables

    ensureUsRankTables()

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        upsertCnt = len(rows)
        termLog(f"[AI_US_DAY_INQ_RANK] UPSERT {upsertCnt}건 (affected={affected})")
        return {"ok": True, "upsertCnt": upsertCnt, "affected": affected}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"DB 적재 실패: {err}", "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def hasUsDayInqRank(tradeDt, qryTp):
    """해당 일자·구분 적재 여부."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 AS ok
                FROM AI_US_DAY_INQ_RANK
                WHERE TRADE_DT=%s AND QRY_TP=%s
                LIMIT 1
                """,
                (str(tradeDt), str(qryTp)),
            )
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def findLatestUsDayInqRankMeta(qryTp=None):
    """가장 최근 적재 스냅샷 메타."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if qryTp is not None and str(qryTp).strip() != "":
                cur.execute(
                    """
                    SELECT TRADE_DT, QRY_TP,
                           MAX(UPDATED_AT) AS UPDATED_AT,
                           COUNT(*) AS ROW_CNT
                    FROM AI_US_DAY_INQ_RANK
                    WHERE QRY_TP=%s
                    GROUP BY TRADE_DT, QRY_TP
                    ORDER BY MAX(UPDATED_AT) DESC, TRADE_DT DESC
                    LIMIT 1
                    """,
                    (str(qryTp).strip(),),
                )
            else:
                cur.execute(
                    """
                    SELECT TRADE_DT, QRY_TP,
                           MAX(UPDATED_AT) AS UPDATED_AT,
                           COUNT(*) AS ROW_CNT
                    FROM AI_US_DAY_INQ_RANK
                    GROUP BY TRADE_DT, QRY_TP
                    ORDER BY MAX(UPDATED_AT) DESC, TRADE_DT DESC
                    LIMIT 1
                    """
                )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "tradeDt": row.get("TRADE_DT"),
                "qryTp": str(row.get("QRY_TP") or ""),
                "updatedAt": row.get("UPDATED_AT"),
                "rowCnt": int(row.get("ROW_CNT") or 0),
            }
    except Exception as err:
        termLog(f"[AI_US_DAY_INQ_RANK] 최근 적재 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
