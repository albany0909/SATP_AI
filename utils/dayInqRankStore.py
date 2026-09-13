"""AI_DAY_INQ_RANK 테이블 UPSERT"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_DAY_INQ_RANK (
  TRADE_DT, QRY_TP, RANK_NO,
  STK_CD, STK_NM, IND_GRP, UP_NAME, RANK_CHG, RANK_CHG_SIGN,
  CUR_PRC, BASE_COMP_SIGN, BASE_COMP_CHGR,
  PREV_BASE_SIGN, PREV_BASE_CHGR, BASE_TM, SRC_TR
) VALUES (
  %(TRADE_DT)s, %(QRY_TP)s, %(RANK_NO)s,
  %(STK_CD)s, %(STK_NM)s, %(IND_GRP)s, %(UP_NAME)s, %(RANK_CHG)s, %(RANK_CHG_SIGN)s,
  %(CUR_PRC)s, %(BASE_COMP_SIGN)s, %(BASE_COMP_CHGR)s,
  %(PREV_BASE_SIGN)s, %(PREV_BASE_CHGR)s, %(BASE_TM)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  STK_CD = VALUES(STK_CD),
  STK_NM = VALUES(STK_NM),
  IND_GRP = VALUES(IND_GRP),
  UP_NAME = VALUES(UP_NAME),
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


def upsertDayInqRankRows(rows):
    """
    순위 스냅샷 row 목록을 AI_DAY_INQ_RANK에 UPSERT.
    반환: {ok, upsertCnt, errMsg?}
    """
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
        termLog(f"[AI_DAY_INQ_RANK] UPSERT {upsertCnt}건 (affected={affected})")
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


def findLatestDayInqRankOnOrBefore(tradeDt, qryTp):
    """tradeDt 이하에서 가장 최근 적재일. 없으면 None."""
    ymd = str(tradeDt or "").replace("-", "").strip()[:8]
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT
                FROM AI_DAY_INQ_RANK
                WHERE QRY_TP=%s AND TRADE_DT<=%s
                GROUP BY TRADE_DT
                ORDER BY TRADE_DT DESC
                LIMIT 1
                """,
                (str(qryTp), ymd),
            )
            row = cur.fetchone()
            if not row:
                return None
            return str(row.get("TRADE_DT") or "") or None
    except Exception as err:
        termLog(f"[AI_DAY_INQ_RANK] 직전 적재일 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def resolveInqRankTradeDt(tradeDt=None, qryTp="4"):
    """
    순위 기준일 (DB만, API 없음).
    1) 요청일에 적재가 있으면 그 날 (휴일 순위 포함)
    2) 없으면 요청일~직전영업일 사이 휴일 적재분
    3) 없으면 직전 영업일 적재분
    4) 그것도 없으면 ok=False — 호출측에서 실시간 조회
    반환: {ok, tradeDt, reqDt, fallback, source, needLive}
    source: req | holiday | prevBiz
    """
    from datetime import datetime, timedelta

    from config.krHoliday import isKrHoliday, prevTradingDay

    req = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    if len(req) != 8 or not req.isdigit():
        req = datetime.now().strftime("%Y%m%d")
    qt = str(qryTp)

    def _hit(ymd, source, fallback):
        return {
            "ok": True,
            "tradeDt": ymd,
            "reqDt": req,
            "fallback": fallback,
            "source": source,
            "needLive": False,
        }

    if hasDayInqRank(req, qt):
        src = "holiday" if isKrHoliday(req) else "req"
        return _hit(req, src, src == "holiday")

    prevBiz = prevTradingDay(req)
    # 직전 영업일 다음날 ~ 요청 전날 사이 휴일 적재 (최근일 우선)
    day = datetime.strptime(req, "%Y%m%d") - timedelta(days=1)
    stop = datetime.strptime(prevBiz, "%Y%m%d") if prevBiz else None
    while stop is None or day > stop:
        ymd = day.strftime("%Y%m%d")
        if isKrHoliday(ymd) and hasDayInqRank(ymd, qt):
            return _hit(ymd, "holiday", True)
        day = day - timedelta(days=1)
        if (datetime.now() - day).days > 40:
            break

    if prevBiz and hasDayInqRank(prevBiz, qt):
        return _hit(prevBiz, "prevBiz", True)

    return {
        "ok": False,
        "tradeDt": "",
        "reqDt": req,
        "fallback": False,
        "source": "",
        "needLive": True,
    }


def hasDayInqRank(tradeDt, qryTp):
    """해당 일자·구분 적재 여부."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 AS ok
                FROM AI_DAY_INQ_RANK
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


def findLatestDayInqRankMeta(qryTp=None):
    """
    가장 최근 적재된 순위 스냅샷 메타.
    qryTp 지정 시 해당 구분만, 없으면 전체에서 UPDATED_AT 최신.
    반환: {tradeDt, qryTp, updatedAt, rowCnt} 또는 None
    """
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
                    FROM AI_DAY_INQ_RANK
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
                    FROM AI_DAY_INQ_RANK
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
        termLog(f"[AI_DAY_INQ_RANK] 최근 적재 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
