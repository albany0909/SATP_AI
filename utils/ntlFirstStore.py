"""AI_DAY_NTL_FIRST UPSERT · 조회"""

from utils.dbConn import openDbConn
from utils.ntlFirstTables import ensureNtlFirstTable
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_DAY_NTL_FIRST (
  TRADE_DT, STK_CD, STK_NM, MARKET_NAME,
  FLU_RT, CUR_PRC, HIGH_PRC, PREV_MAX_HIGH,
  TRDE_QTY, AVG_VOL_20, VOL_INC_RT,
  FOR_NETPRPS, ORGN_NETPRPS, FOR_NET_AMT, ORGN_NET_AMT,
  USE_CLOSE, SRC_TR
) VALUES (
  %(TRADE_DT)s, %(STK_CD)s, %(STK_NM)s, %(MARKET_NAME)s,
  %(FLU_RT)s, %(CUR_PRC)s, %(HIGH_PRC)s, %(PREV_MAX_HIGH)s,
  %(TRDE_QTY)s, %(AVG_VOL_20)s, %(VOL_INC_RT)s,
  %(FOR_NETPRPS)s, %(ORGN_NETPRPS)s, %(FOR_NET_AMT)s, %(ORGN_NET_AMT)s,
  %(USE_CLOSE)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  MARKET_NAME = VALUES(MARKET_NAME),
  FLU_RT = VALUES(FLU_RT),
  CUR_PRC = VALUES(CUR_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  PREV_MAX_HIGH = VALUES(PREV_MAX_HIGH),
  TRDE_QTY = VALUES(TRDE_QTY),
  AVG_VOL_20 = VALUES(AVG_VOL_20),
  VOL_INC_RT = VALUES(VOL_INC_RT),
  FOR_NETPRPS = VALUES(FOR_NETPRPS),
  ORGN_NETPRPS = VALUES(ORGN_NETPRPS),
  FOR_NET_AMT = VALUES(FOR_NET_AMT),
  ORGN_NET_AMT = VALUES(ORGN_NET_AMT),
  USE_CLOSE = VALUES(USE_CLOSE),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertNtlFirstRows(rows):
    """
    최초52 진입 row UPSERT.
    반환: {ok, upsertCnt, errMsg?}
    """
    if not ensureNtlFirstTable():
        return {"ok": False, "errMsg": "AI_DAY_NTL_FIRST 테이블 생성 실패", "upsertCnt": 0}
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        upsertCnt = len(rows)
        termLog(f"[AI_DAY_NTL_FIRST] UPSERT {upsertCnt}건 (affected={affected})")
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


def hasNtlFirst(tradeDt):
    """해당 TRADE_DT 적재 1건 이상이면 True."""
    if not ensureNtlFirstTable():
        return False
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM AI_DAY_NTL_FIRST
                WHERE TRADE_DT=%s
                LIMIT 1
                """,
                (str(tradeDt),),
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


def countNtlFirst(tradeDt):
    """일자별 건수."""
    if not ensureNtlFirstTable():
        return 0
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS CNT
                FROM AI_DAY_NTL_FIRST
                WHERE TRADE_DT=%s
                """,
                (str(tradeDt),),
            )
            row = cur.fetchone() or {}
            return int(row.get("CNT") or 0)
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listNtlFirstByDt(tradeDt, limit=100):
    """
    일자 스냅샷 조회 (등락률 큰 순).
    반환: list[dict]
    """
    if not ensureNtlFirstTable():
        return []
    lim = max(1, min(int(limit or 100), 500))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, STK_CD, STK_NM, MARKET_NAME,
                       FLU_RT, CUR_PRC, HIGH_PRC, PREV_MAX_HIGH,
                       TRDE_QTY, AVG_VOL_20, VOL_INC_RT,
                       FOR_NETPRPS, ORGN_NETPRPS, FOR_NET_AMT, ORGN_NET_AMT,
                       USE_CLOSE, SRC_TR
                FROM AI_DAY_NTL_FIRST
                WHERE TRADE_DT=%s
                ORDER BY (FLU_RT IS NULL), FLU_RT DESC, STK_CD
                LIMIT %s
                """,
                (str(tradeDt), lim),
            )
            return list(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[AI_DAY_NTL_FIRST] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def latestNtlFirstDt():
    """가장 최근 적재 TRADE_DT."""
    if not ensureNtlFirstTable():
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(TRADE_DT) AS MAX_DT FROM AI_DAY_NTL_FIRST
                """
            )
            row = cur.fetchone() or {}
            return row.get("MAX_DT")
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
