"""AI_DAY_STK_PRC 테이블 UPSERT"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_DAY_STK_PRC (
  STK_CD, TRADE_DT, INDC_TP,
  OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
  PRED_RT, FLU_RT, TRDE_QTY, AMT_MN,
  CRD_RT, IND_VAL, ORGN_VAL, FOR_QTY, FRGN_VAL, PRM_VAL,
  FOR_RT, FOR_POSS, FOR_WGHT,
  FOR_NETPRPS, ORGN_NETPRPS, IND_NETPRPS, CRD_REMN_RT
) VALUES (
  %(STK_CD)s, %(TRADE_DT)s, %(INDC_TP)s,
  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s,
  %(PRED_RT)s, %(FLU_RT)s, %(TRDE_QTY)s, %(AMT_MN)s,
  %(CRD_RT)s, %(IND_VAL)s, %(ORGN_VAL)s, %(FOR_QTY)s, %(FRGN_VAL)s, %(PRM_VAL)s,
  %(FOR_RT)s, %(FOR_POSS)s, %(FOR_WGHT)s,
  %(FOR_NETPRPS)s, %(ORGN_NETPRPS)s, %(IND_NETPRPS)s, %(CRD_REMN_RT)s
)
ON DUPLICATE KEY UPDATE
  OPEN_PRC = VALUES(OPEN_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  LOW_PRC = VALUES(LOW_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  PRED_RT = VALUES(PRED_RT),
  FLU_RT = VALUES(FLU_RT),
  TRDE_QTY = VALUES(TRDE_QTY),
  AMT_MN = VALUES(AMT_MN),
  CRD_RT = VALUES(CRD_RT),
  IND_VAL = VALUES(IND_VAL),
  ORGN_VAL = VALUES(ORGN_VAL),
  FOR_QTY = VALUES(FOR_QTY),
  FRGN_VAL = VALUES(FRGN_VAL),
  PRM_VAL = VALUES(PRM_VAL),
  FOR_RT = VALUES(FOR_RT),
  FOR_POSS = VALUES(FOR_POSS),
  FOR_WGHT = VALUES(FOR_WGHT),
  FOR_NETPRPS = VALUES(FOR_NETPRPS),
  ORGN_NETPRPS = VALUES(ORGN_NETPRPS),
  IND_NETPRPS = VALUES(IND_NETPRPS),
  CRD_REMN_RT = VALUES(CRD_REMN_RT)
"""


def upsertDayStkPrcRows(rows):
    """
    일별주가 row 목록을 AI_DAY_STK_PRC에 UPSERT.
    반환: {ok, upsertCnt, errMsg?}
    """
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # executemany — 동일 PK는 ON DUPLICATE KEY UPDATE
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        # MySQL rowcount: insert=1, update=2 합산일 수 있어 입력 건수를 기준으로 안내
        upsertCnt = len(rows)
        termLog(f"[AI_DAY_STK_PRC] UPSERT {upsertCnt}건 (affected={affected})")
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
