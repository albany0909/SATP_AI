"""AI_STK_INFO UPSERT / 조회"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_STK_INFO (
  STK_CD, STK_NM, MRKT_TP, MARKET_CODE, MARKET_NAME,
  LIST_CNT, REG_DAY, LAST_PRICE, STATE, UP_NAME, NXT_ENABLE, SRC_TR
) VALUES (
  %(STK_CD)s, %(STK_NM)s, %(MRKT_TP)s, %(MARKET_CODE)s, %(MARKET_NAME)s,
  %(LIST_CNT)s, %(REG_DAY)s, %(LAST_PRICE)s, %(STATE)s, %(UP_NAME)s,
  %(NXT_ENABLE)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  MRKT_TP = VALUES(MRKT_TP),
  MARKET_CODE = VALUES(MARKET_CODE),
  MARKET_NAME = VALUES(MARKET_NAME),
  LIST_CNT = VALUES(LIST_CNT),
  REG_DAY = VALUES(REG_DAY),
  LAST_PRICE = VALUES(LAST_PRICE),
  STATE = VALUES(STATE),
  UP_NAME = VALUES(UP_NAME),
  NXT_ENABLE = VALUES(NXT_ENABLE),
  SRC_TR = VALUES(SRC_TR)
"""

_BATCH_SIZE = 500


def upsertStkInfoRows(rows):
    """종목 마스터 UPSERT. 반환: {ok, upsertCnt, errMsg?}"""
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
        termLog(f"[AI_STK_INFO] UPSERT {upsertCnt}건 (affected={affected})")
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


def countStkInfo():
    """캐시 건수."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM AI_STK_INFO")
            row = cur.fetchone() or {}
            return int(row.get("cnt") or 0)
    finally:
        conn.close()


_STK_META_COLS = (
    "STK_CD, STK_NM, MARKET_NAME, UP_NAME, MRKT_TP, LAST_PRICE, STATE, NXT_ENABLE"
)


def findStkByCd(stkCd):
    """코드 정확 조회 → dict or None."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STK_META_COLS} FROM AI_STK_INFO WHERE STK_CD=%s",
                (str(stkCd),),
            )
            return cur.fetchone()
    finally:
        conn.close()


def isNxtEnabled(stkCd):
    """종목마스터 NXT가능여부. Y 이면 True."""
    from utils.stkCdUtil import normStkCd

    cd = normStkCd(stkCd)
    if not cd:
        return False
    row = findStkByCd(cd) or {}
    return str(row.get("NXT_ENABLE") or "").strip().upper() == "Y"


def findStkByName(name, limit=20):
    """
    종목명 검색.
    1) 정확 일치 우선
    2) 부분 일치 (LIKE)
    반환: list[dict]
    """
    nm = str(name or "").strip()
    if not nm:
        return []

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_STK_META_COLS}
                FROM AI_STK_INFO
                WHERE STK_NM=%s
                ORDER BY STK_CD
                LIMIT %s
                """,
                (nm, int(limit)),
            )
            exact = cur.fetchall() or []
            if exact:
                return exact

            cur.execute(
                f"""
                SELECT {_STK_META_COLS}
                FROM AI_STK_INFO
                WHERE STK_NM LIKE %s
                ORDER BY
                  CASE WHEN STK_NM LIKE %s THEN 0 ELSE 1 END,
                  CHAR_LENGTH(STK_NM),
                  STK_CD
                LIMIT %s
                """,
                (f"%{nm}%", f"{nm}%", int(limit)),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def searchStkInfo(q, limit=25):
    """
    코드 정확 또는 종목명 LIKE 검색.
    반환: list[dict] (메타 컬럼 포함)
    """
    text = str(q or "").strip()
    if not text:
        return []
    lim = max(1, min(int(limit or 25), 50))

    # 숫자 코드 (6자리 패딩 포함) — 정확 매치 우선
    digits = text.replace(" ", "")
    if digits.isdigit() and len(digits) <= 6:
        row = findStkByCd(digits.zfill(6))
        if row:
            return [row]

    # 코드 형태로 보이는 경우
    row = findStkByCd(text)
    if row:
        return [row]

    # 종목명 부분 검색
    byNm = findStkByName(text, limit=lim)
    if byNm:
        return byNm

    # 코드 부분 LIKE
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_STK_META_COLS}
                FROM AI_STK_INFO
                WHERE STK_CD LIKE %s OR STK_NM LIKE %s
                ORDER BY
                  CASE WHEN STK_CD=%s THEN 0
                       WHEN STK_CD LIKE %s THEN 1
                       ELSE 2 END,
                  STK_CD
                LIMIT %s
                """,
                (f"%{text}%", f"%{text}%", text, f"{text}%", lim),
            )
            return cur.fetchall() or []
    finally:
        conn.close()
