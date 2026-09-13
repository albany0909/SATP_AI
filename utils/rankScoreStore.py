"""AI_RANK_SCORE 저장 / 조회 (일별 관심종목 순위 점수)"""

from utils.dbConn import openDbConn
from utils.rankScoreTables import ensureRankScoreTables
from utils.termLog import termLog

_INSERT_SQL = """
INSERT INTO AI_RANK_SCORE (
  TRADE_DT, COND_CD, STK_CD, STK_NM, SCORE, RANK_NO,
  HIT_CNT, HIT_ITEMS, DETAIL_JSON, IND_GRP, APPEAR_CNT
) VALUES (
  %(TRADE_DT)s, %(COND_CD)s, %(STK_CD)s, %(STK_NM)s, %(SCORE)s, %(RANK_NO)s,
  %(HIT_CNT)s, %(HIT_ITEMS)s, %(DETAIL_JSON)s, %(IND_GRP)s, %(APPEAR_CNT)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  SCORE = VALUES(SCORE),
  RANK_NO = VALUES(RANK_NO),
  HIT_CNT = VALUES(HIT_CNT),
  HIT_ITEMS = VALUES(HIT_ITEMS),
  DETAIL_JSON = VALUES(DETAIL_JSON),
  IND_GRP = VALUES(IND_GRP),
  APPEAR_CNT = VALUES(APPEAR_CNT)
"""


def replaceRankScoreRows(tradeDt, condCd, rows):
    """
    해당 일자·조건의 점수를 통째로 교체한다.
    조건을 바꿔 재계산하면 순위가 밀리므로, 기존 행을 지우고 새로 넣어야 잔여 순위가 남지 않는다.
    """
    if not ensureRankScoreTables():
        return {"ok": False, "errMsg": "AI_RANK_SCORE 테이블 생성 실패", "savedCnt": 0}

    ymd = str(tradeDt or "")[:8]
    code = str(condCd or "")
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM AI_RANK_SCORE WHERE TRADE_DT=%s AND COND_CD=%s",
                (ymd, code),
            )
            if rows:
                cur.executemany(_INSERT_SQL, rows)
        conn.commit()
        termLog(f"[AI_RANK_SCORE] {ymd} {code} 저장 {len(rows)}건")
        return {"ok": True, "savedCnt": len(rows)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"점수 저장 실패: {err}", "savedCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def hasRankScore(tradeDt, condCd):
    """해당 일자·조건 점수 적재 여부 (배치 중복 실행 방지용)."""
    if not ensureRankScoreTables():
        return False
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM AI_RANK_SCORE
                WHERE TRADE_DT=%s AND COND_CD=%s LIMIT 1
                """,
                (str(tradeDt or "")[:8], str(condCd or "")),
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


def listRankScore(condCd="001", tradeDt=None, limit=10):
    """
    점수 순위 목록. tradeDt 없으면 해당 조건의 최근 적재일.
    반환: {ok, tradeDt, condCd, cnt, items:[...]}
    """
    if not ensureRankScoreTables():
        return {"ok": False, "errMsg": "AI_RANK_SCORE 테이블 생성 실패", "items": []}

    code = str(condCd or "001")
    lim = max(1, min(int(limit or 10), 300))
    ymd = str(tradeDt or "").replace("-", "")[:8]
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if not (ymd.isdigit() and len(ymd) == 8):
                cur.execute(
                    "SELECT MAX(TRADE_DT) AS MAX_DT FROM AI_RANK_SCORE WHERE COND_CD=%s",
                    (code,),
                )
                meta = cur.fetchone() or {}
                ymd = str(meta.get("MAX_DT") or "")
            if not ymd:
                return {
                    "ok": True,
                    "tradeDt": "",
                    "condCd": code,
                    "cnt": 0,
                    "items": [],
                }
            cur.execute(
                """
                SELECT TRADE_DT, STK_CD, STK_NM, SCORE, RANK_NO, HIT_CNT,
                       HIT_ITEMS, DETAIL_JSON, IND_GRP, APPEAR_CNT, UPDATED_AT
                FROM AI_RANK_SCORE
                WHERE TRADE_DT=%s AND COND_CD=%s
                ORDER BY RANK_NO ASC, SCORE DESC, STK_CD ASC
                LIMIT %s
                """,
                (ymd, code, lim),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        return {"ok": False, "errMsg": f"점수 조회 실패: {err}", "items": []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    items = []
    updatedAt = ""
    for r in rows:
        updatedAt = str(r.get("UPDATED_AT") or updatedAt)
        items.append(
            {
                "tradeDt": str(r.get("TRADE_DT") or ""),
                "stkCd": str(r.get("STK_CD") or ""),
                "stkNm": r.get("STK_NM") or "",
                "score": float(r.get("SCORE") or 0),
                "rankNo": int(r.get("RANK_NO") or 0),
                "hitCnt": int(r.get("HIT_CNT") or 0),
                "hitItems": r.get("HIT_ITEMS") or "",
                "detail": r.get("DETAIL_JSON") or "",
                "indGrp": r.get("IND_GRP") or "",
                "appearCnt": int(r.get("APPEAR_CNT") or 0),
            }
        )
    return {
        "ok": True,
        "tradeDt": ymd,
        "condCd": code,
        "cnt": len(items),
        "updatedAt": updatedAt,
        "items": items,
    }
