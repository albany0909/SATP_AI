"""AI_RPT_ANAL / AI_CNS_EST UPSERT·조회"""

from utils.dbConn import openDbConn
from utils.rptAnalTables import ensureRptAnalTables
from utils.termLog import termLog

_UPSERT_RPT = """
INSERT INTO AI_RPT_ANAL (
  STK_CD, NID, STK_NM, TITLE, BROKER, PUB_DT, PUB_DT_RAW,
  OPINION, TARGET_PRC, TONE_SCORE, HAS_GUIDE, GUIDE_TXT, SUM_TXT,
  DETAIL_URL, PDF_URL, VIEWS, SRC, PUSHED_YN
) VALUES (
  %(STK_CD)s, %(NID)s, %(STK_NM)s, %(TITLE)s, %(BROKER)s, %(PUB_DT)s, %(PUB_DT_RAW)s,
  %(OPINION)s, %(TARGET_PRC)s, %(TONE_SCORE)s, %(HAS_GUIDE)s, %(GUIDE_TXT)s, %(SUM_TXT)s,
  %(DETAIL_URL)s, %(PDF_URL)s, %(VIEWS)s, %(SRC)s, %(PUSHED_YN)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  TITLE = VALUES(TITLE),
  BROKER = VALUES(BROKER),
  PUB_DT = VALUES(PUB_DT),
  PUB_DT_RAW = VALUES(PUB_DT_RAW),
  OPINION = VALUES(OPINION),
  TARGET_PRC = VALUES(TARGET_PRC),
  TONE_SCORE = VALUES(TONE_SCORE),
  HAS_GUIDE = VALUES(HAS_GUIDE),
  GUIDE_TXT = VALUES(GUIDE_TXT),
  SUM_TXT = VALUES(SUM_TXT),
  DETAIL_URL = VALUES(DETAIL_URL),
  PDF_URL = VALUES(PDF_URL),
  VIEWS = VALUES(VIEWS)
"""

_UPSERT_CNS = """
INSERT INTO AI_CNS_EST (
  STK_CD, BASE_DT, EST_TP, PERIOD_CD, EST_VAL, EST_TXT, SRC, SRC_REF, NOTE
) VALUES (
  %(STK_CD)s, %(BASE_DT)s, %(EST_TP)s, %(PERIOD_CD)s, %(EST_VAL)s, %(EST_TXT)s,
  %(SRC)s, %(SRC_REF)s, %(NOTE)s
)
ON DUPLICATE KEY UPDATE
  EST_VAL = VALUES(EST_VAL),
  EST_TXT = VALUES(EST_TXT),
  NOTE = VALUES(NOTE)
"""


def upsertRptAnalRows(rows):
    """리포트 분석 row UPSERT. 반환 {ok, upsertCnt, errMsg?}"""
    if not rows:
        return {"ok": True, "upsertCnt": 0}
    if not ensureRptAnalTables():
        return {"ok": False, "errMsg": "AI_RPT_ANAL 테이블 생성 실패", "upsertCnt": 0}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_RPT, rows)
        conn.commit()
        return {"ok": True, "upsertCnt": len(rows)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err), "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsertCnsEstRows(rows):
    if not rows:
        return {"ok": True, "upsertCnt": 0}
    if not ensureRptAnalTables():
        return {"ok": False, "errMsg": "AI_CNS_EST 테이블 생성 실패", "upsertCnt": 0}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_CNS, rows)
        conn.commit()
        return {"ok": True, "upsertCnt": len(rows)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err), "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listRptAnal(stkCd, limit=20, guideOnly=False):
    """최근 리포트 분석 목록."""
    if not ensureRptAnalTables():
        return []
    lim = max(1, min(int(limit or 20), 100))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sql = """
                SELECT STK_CD, NID, STK_NM, TITLE, BROKER, PUB_DT, PUB_DT_RAW,
                       OPINION, TARGET_PRC, TONE_SCORE, HAS_GUIDE, GUIDE_TXT,
                       SUM_TXT, DETAIL_URL, PDF_URL, VIEWS, SRC, PUSHED_YN,
                       CREATED_AT, UPDATED_AT
                FROM AI_RPT_ANAL
                WHERE STK_CD=%s
            """
            args = [str(stkCd)]
            if guideOnly:
                sql += " AND HAS_GUIDE='Y'"
            sql += " ORDER BY PUB_DT DESC, NID DESC LIMIT %s"
            args.append(lim)
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_RPT_ANAL] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listCnsEst(stkCd, limit=20):
    if not ensureRptAnalTables():
        return []
    lim = max(1, min(int(limit or 20), 100))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, BASE_DT, EST_TP, PERIOD_CD, EST_VAL, EST_TXT,
                       SRC, SRC_REF, NOTE, UPDATED_AT
                FROM AI_CNS_EST
                WHERE STK_CD=%s
                ORDER BY BASE_DT DESC, EST_TP
                LIMIT %s
                """,
                (str(stkCd), lim),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_CNS_EST] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def markRptPushed(stkCd, nid):
    if not ensureRptAnalTables():
        return False
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE AI_RPT_ANAL
                SET PUSHED_YN='Y', PUSHED_AT=NOW()
                WHERE STK_CD=%s AND NID=%s
                """,
                (str(stkCd), str(nid)),
            )
        conn.commit()
        return True
    except Exception as err:
        termLog(f"[AI_RPT_ANAL] push mark fail: {err}")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listUnpushedRpt(limit=30):
    """미푸시 신규 리포트."""
    if not ensureRptAnalTables():
        return []
    lim = max(1, min(int(limit or 30), 100))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, NID, STK_NM, TITLE, BROKER, PUB_DT, OPINION,
                       TARGET_PRC, HAS_GUIDE, GUIDE_TXT, SUM_TXT, DETAIL_URL
                FROM AI_RPT_ANAL
                WHERE PUSHED_YN='N'
                ORDER BY CREATED_AT DESC
                LIMIT %s
                """,
                (lim,),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_RPT_ANAL] unpushed fail: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
