"""관심종목 공시·뉴스 DB UPSERT / 신규 조회 / 푸시 마킹"""

from __future__ import annotations

from utils.dbConn import openDbConn
from utils.termLog import termLog
from utils.watchNewsDiscTables import ensureWatchNewsDiscTables

_DISC_UPSERT = """
INSERT INTO AI_STK_DISC (
  STK_CD, RCEPT_NO, STK_NM, CORP_CODE, CORP_NAME,
  REPORT_NM, RCEPT_DT, FLR_NM, RM, VIEW_URL, SRC
) VALUES (
  %(STK_CD)s, %(RCEPT_NO)s, %(STK_NM)s, %(CORP_CODE)s, %(CORP_NAME)s,
  %(REPORT_NM)s, %(RCEPT_DT)s, %(FLR_NM)s, %(RM)s, %(VIEW_URL)s, %(SRC)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  REPORT_NM = VALUES(REPORT_NM),
  RCEPT_DT = VALUES(RCEPT_DT),
  FLR_NM = VALUES(FLR_NM),
  RM = VALUES(RM),
  VIEW_URL = VALUES(VIEW_URL),
  UPDATED_AT = CURRENT_TIMESTAMP
"""

_NEWS_UPSERT = """
INSERT INTO AI_STK_NEWS (
  STK_CD, NEWS_ID, STK_NM, TITLE, OFFICE_NM,
  PUB_DT, PUB_TM, NEWS_URL, SRC, ISSUE_YN
) VALUES (
  %(STK_CD)s, %(NEWS_ID)s, %(STK_NM)s, %(TITLE)s, %(OFFICE_NM)s,
  %(PUB_DT)s, %(PUB_TM)s, %(NEWS_URL)s, %(SRC)s, %(ISSUE_YN)s
)
ON DUPLICATE KEY UPDATE
  TITLE = VALUES(TITLE),
  OFFICE_NM = VALUES(OFFICE_NM),
  PUB_DT = VALUES(PUB_DT),
  PUB_TM = VALUES(PUB_TM),
  NEWS_URL = VALUES(NEWS_URL),
  ISSUE_YN = VALUES(ISSUE_YN),
  UPDATED_AT = CURRENT_TIMESTAMP
"""


def _existingDiscKeys(rceptNos):
    if not rceptNos:
        return set()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(rceptNos))
            cur.execute(
                f"SELECT RCEPT_NO FROM AI_STK_DISC WHERE RCEPT_NO IN ({ph})",
                list(rceptNos),
            )
            return {str(r["RCEPT_NO"]) for r in (cur.fetchall() or [])}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _existingNewsKeys(pairs):
    """pairs: list of (stkCd, newsId)"""
    if not pairs:
        return set()
    conn = None
    try:
        conn = openDbConn()
        keys = set()
        with conn.cursor() as cur:
            # 배치 IN 은 복합키 불편 → 종목별 조회
            byStk = {}
            for stk, nid in pairs:
                byStk.setdefault(stk, []).append(nid)
            for stk, nids in byStk.items():
                ph = ",".join(["%s"] * len(nids))
                cur.execute(
                    f"""
                    SELECT STK_CD, NEWS_ID FROM AI_STK_NEWS
                    WHERE STK_CD=%s AND NEWS_ID IN ({ph})
                    """,
                    [stk] + list(nids),
                )
                for r in cur.fetchall() or []:
                    keys.add((str(r["STK_CD"]), str(r["NEWS_ID"])))
        return keys
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsertDiscRows(rows):
    """
    공시 UPSERT.
    반환: {ok, upsertCnt, newRows, errMsg?}
    """
    ensureWatchNewsDiscTables()
    if not rows:
        return {"ok": True, "upsertCnt": 0, "newRows": []}

    nos = [str(r.get("RCEPT_NO") or "") for r in rows if r.get("RCEPT_NO")]
    before = _existingDiscKeys(nos)
    newRows = [r for r in rows if str(r.get("RCEPT_NO") or "") not in before]

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_DISC_UPSERT, rows)
        conn.commit()
        termLog(f"[AI_STK_DISC] UPSERT {len(rows)}건 (new={len(newRows)})")
        return {"ok": True, "upsertCnt": len(rows), "newRows": newRows}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err), "upsertCnt": 0, "newRows": []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsertNewsRows(rows):
    """뉴스 UPSERT. 반환에 newRows 포함."""
    ensureWatchNewsDiscTables()
    if not rows:
        return {"ok": True, "upsertCnt": 0, "newRows": []}

    pairs = [
        (str(r.get("STK_CD")), str(r.get("NEWS_ID")))
        for r in rows
        if r.get("STK_CD") and r.get("NEWS_ID")
    ]
    before = _existingNewsKeys(pairs)
    newRows = [
        r
        for r in rows
        if (str(r.get("STK_CD")), str(r.get("NEWS_ID"))) not in before
    ]

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_NEWS_UPSERT, rows)
        conn.commit()
        termLog(f"[AI_STK_NEWS] UPSERT {len(rows)}건 (new={len(newRows)})")
        return {"ok": True, "upsertCnt": len(rows), "newRows": newRows}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err), "upsertCnt": 0, "newRows": []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def markDiscPushed(rceptNos):
    if not rceptNos:
        return
    ensureWatchNewsDiscTables()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(rceptNos))
            cur.execute(
                f"""
                UPDATE AI_STK_DISC
                SET PUSHED_YN='Y', PUSHED_AT=NOW()
                WHERE RCEPT_NO IN ({ph})
                """,
                list(rceptNos),
            )
        conn.commit()
    except Exception as err:
        termLog(f"[AI_STK_DISC] push mark 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def markNewsPushed(pairs):
    """pairs: [(stkCd, newsId), ...]"""
    if not pairs:
        return
    ensureWatchNewsDiscTables()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for stk, nid in pairs:
                cur.execute(
                    """
                    UPDATE AI_STK_NEWS
                    SET PUSHED_YN='Y', PUSHED_AT=NOW()
                    WHERE STK_CD=%s AND NEWS_ID=%s
                    """,
                    (stk, nid),
                )
        conn.commit()
    except Exception as err:
        termLog(f"[AI_STK_NEWS] push mark 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listRecentDisc(stkCd=None, limit=20):
    ensureWatchNewsDiscTables()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if stkCd:
                cur.execute(
                    """
                    SELECT * FROM AI_STK_DISC
                    WHERE STK_CD=%s
                    ORDER BY RCEPT_DT DESC, RCEPT_NO DESC
                    LIMIT %s
                    """,
                    (stkCd, int(limit)),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM AI_STK_DISC
                    ORDER BY CREATED_AT DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_STK_DISC] list 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listRecentNews(stkCd=None, limit=20, issueOnly=False):
    ensureWatchNewsDiscTables()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            where = []
            args = []
            if stkCd:
                where.append("STK_CD=%s")
                args.append(stkCd)
            if issueOnly:
                where.append("ISSUE_YN='Y'")
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            cur.execute(
                f"""
                SELECT * FROM AI_STK_NEWS
                {wsql}
                ORDER BY PUB_DT DESC, CREATED_AT DESC
                LIMIT %s
                """,
                args + [int(limit)],
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_STK_NEWS] list 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
