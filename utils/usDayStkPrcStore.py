"""AI_US_DAY_STK_PRC UPSERT"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_US_DAY_STK_PRC (
  STK_CD, TRADE_DT, STEX_TP,
  OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
  PRED_PRE, FLU_RT, TRDE_QTY, TRDE_PRICA, SRC_TR
) VALUES (
  %(STK_CD)s, %(TRADE_DT)s, %(STEX_TP)s,
  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s,
  %(PRED_PRE)s, %(FLU_RT)s, %(TRDE_QTY)s, %(TRDE_PRICA)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STEX_TP = VALUES(STEX_TP),
  OPEN_PRC = VALUES(OPEN_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  LOW_PRC = VALUES(LOW_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  PRED_PRE = VALUES(PRED_PRE),
  FLU_RT = VALUES(FLU_RT),
  TRDE_QTY = VALUES(TRDE_QTY),
  TRDE_PRICA = VALUES(TRDE_PRICA),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertUsDayStkPrcRows(rows):
    """미국 일봉 UPSERT."""
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
        termLog(f"[AI_US_DAY_STK_PRC] UPSERT {upsertCnt}건 (affected={affected})")
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
