"""AI_MIN_STK_PRC 테이블 UPSERT"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_MIN_STK_PRC (
  STK_CD, TIC_SCOPE, CNTR_TM, TRADE_DT, BAR_TM,
  OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
  TRDE_QTY, ACC_TRDE_QTY, PRED_PRE, PRED_PRE_SIG,
  UPD_STKPC_TP, SRC_TR
) VALUES (
  %(STK_CD)s, %(TIC_SCOPE)s, %(CNTR_TM)s, %(TRADE_DT)s, %(BAR_TM)s,
  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s,
  %(TRDE_QTY)s, %(ACC_TRDE_QTY)s, %(PRED_PRE)s, %(PRED_PRE_SIG)s,
  %(UPD_STKPC_TP)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  TRADE_DT = VALUES(TRADE_DT),
  BAR_TM = VALUES(BAR_TM),
  OPEN_PRC = VALUES(OPEN_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  LOW_PRC = VALUES(LOW_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  TRDE_QTY = VALUES(TRDE_QTY),
  ACC_TRDE_QTY = VALUES(ACC_TRDE_QTY),
  PRED_PRE = VALUES(PRED_PRE),
  PRED_PRE_SIG = VALUES(PRED_PRE_SIG),
  UPD_STKPC_TP = VALUES(UPD_STKPC_TP),
  SRC_TR = VALUES(SRC_TR)
"""

# 대량 적재 시 배치 크기
_BATCH_SIZE = 500


def upsertMinStkPrcRows(rows):
    """
    분봉 row 목록을 AI_MIN_STK_PRC에 UPSERT.
    반환: {ok, upsertCnt, errMsg?}
    """
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    conn = None
    try:
        conn = openDbConn()
        affected = 0
        with conn.cursor() as cur:
            for i in range(0, len(rows), _BATCH_SIZE):
                chunk = rows[i : i + _BATCH_SIZE]
                cur.executemany(_UPSERT_SQL, chunk)
                affected += cur.rowcount
        conn.commit()
        upsertCnt = len(rows)
        termLog(f"[AI_MIN_STK_PRC] UPSERT {upsertCnt}건 (affected={affected})")
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
