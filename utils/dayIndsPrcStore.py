"""AI_DAY_INDS_PRC 테이블 UPSERT"""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_DAY_INDS_PRC (
  MKT_TP, INDS_CD, TRADE_DT,
  OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
  TRDE_QTY, TRDE_PRICA, INDS_NM, SRC_TR
) VALUES (
  %(MKT_TP)s, %(INDS_CD)s, %(TRADE_DT)s,
  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s,
  %(TRDE_QTY)s, %(TRDE_PRICA)s, %(INDS_NM)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  OPEN_PRC = VALUES(OPEN_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  LOW_PRC = VALUES(LOW_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  TRDE_QTY = VALUES(TRDE_QTY),
  TRDE_PRICA = VALUES(TRDE_PRICA),
  INDS_NM = VALUES(INDS_NM),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertDayIndsPrcRows(rows):
    """
    업종일봉 row 목록을 AI_DAY_INDS_PRC에 UPSERT.
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
        termLog(f"[AI_DAY_INDS_PRC] UPSERT {upsertCnt}건 (affected={affected})")
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


_INSERT_IGNORE_SQL = """
INSERT IGNORE INTO AI_DAY_INDS_PRC (
  MKT_TP, INDS_CD, TRADE_DT,
  OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
  TRDE_QTY, TRDE_PRICA, INDS_NM, SRC_TR
) VALUES (
  %(MKT_TP)s, %(INDS_CD)s, %(TRADE_DT)s,
  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s,
  %(TRDE_QTY)s, %(TRDE_PRICA)s, %(INDS_NM)s, %(SRC_TR)s
)
"""

KOSPI_MKT = "KR"
KOSPI_CD = "001"


def _missingTable(err):
    text = str(err)
    return "1146" in text or "doesn't exist" in text.lower() or "존재하지" in text


def insertIgnoreDayIndsPrcRows(rows):
    """없는 날짜만 넣는다. 이미 있는 AI_DAY_INDS_PRC 행은 고치거나 지우지 않는다."""
    if not rows:
        return {"ok": True, "upsertCnt": 0, "insertOnly": True}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_INSERT_IGNORE_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        termLog(f"[AI_DAY_INDS_PRC] INSERT IGNORE {len(rows)}건 (affected={affected})")
        return {
            "ok": True,
            "upsertCnt": int(affected or 0),
            "insertOnly": True,
            "affected": affected,
            "store": "AI_DAY_INDS_PRC",
        }
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {
            "ok": False,
            "errMsg": f"DB 적재 실패: {err}",
            "upsertCnt": 0,
            "missingTable": _missingTable(err),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listKospiTradeDates(startDt, endDt, indsCd=KOSPI_CD):
    """기간 안에 이미 있는 코스피 일자. 테이블이 없으면 missingTable."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT
                FROM AI_DAY_INDS_PRC
                WHERE MKT_TP=%s AND INDS_CD=%s AND TRADE_DT BETWEEN %s AND %s
                """,
                (KOSPI_MKT, str(indsCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
        dates = {str(row.get("TRADE_DT") or "")[:8] for row in rows}
        dates.discard("")
        return {"ok": True, "dates": dates, "store": "AI_DAY_INDS_PRC"}
    except Exception as err:
        return {
            "ok": False,
            "errMsg": str(err),
            "missingTable": _missingTable(err),
            "dates": set(),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def loadKospiDayBars(asOf=None, indsCd=KOSPI_CD):
    """저장된 코스피 일봉 종가. 판정일 이후는 빼고, 테이블이 없으면 missingTable."""
    conn = None
    params = [KOSPI_MKT, str(indsCd)]
    where = "WHERE MKT_TP=%s AND INDS_CD=%s"
    day = str(asOf or "").replace("-", "")[:8]
    if len(day) == 8 and day.isdigit():
        where += " AND TRADE_DT<=%s"
        params.append(day)
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT TRADE_DT, CLOSE_PRC
                FROM AI_DAY_INDS_PRC
                {where}
                ORDER BY TRADE_DT
                """,
                params,
            )
            rows = cur.fetchall() or []
        bars = []
        for row in rows:
            ymd = str(row.get("TRADE_DT") or "")[:8]
            if len(ymd) != 8:
                continue
            try:
                close = float(row["CLOSE_PRC"]) if row.get("CLOSE_PRC") is not None else None
            except (TypeError, ValueError):
                close = None
            if close is None:
                continue
            bars.append({"tradeDt": ymd, "close": close})
        return {"ok": True, "bars": bars, "store": "AI_DAY_INDS_PRC"}
    except Exception as err:
        return {
            "ok": False,
            "errMsg": str(err),
            "missingTable": _missingTable(err),
            "bars": [],
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _normIndsCd3(raw):
    """업종코드 3자리. DB에 1 로 들어가 있어도 001 로 맞춘다."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(3)[-3:]


def loadIndsLastCloseMap(indsCds=None):
    """
    업종별 가장 최근 일봉 종가. 실시간(0J)이 없을 때 매매창 폴백용.
    반환: {indsCd: {prc, fluRt, tradeDt, src}}
    fluRt 는 직전 거래일 종가 대비 % (없으면 None).
    """
    want = [_normIndsCd3(c) for c in (indsCds or ("001", "101", "201"))]
    want = [c for c in want if c]
    if not want:
        return {}
    placeholders = ",".join(["%s"] * len(want))
    sql = f"""
    SELECT a.INDS_CD, a.TRADE_DT, a.CLOSE_PRC,
           (
             SELECT p.CLOSE_PRC
             FROM AI_DAY_INDS_PRC p
             WHERE p.MKT_TP = a.MKT_TP
               AND p.INDS_CD = a.INDS_CD
               AND p.TRADE_DT < a.TRADE_DT
             ORDER BY p.TRADE_DT DESC
             LIMIT 1
           ) AS PREV_CLOSE
    FROM AI_DAY_INDS_PRC a
    INNER JOIN (
      SELECT INDS_CD, MAX(TRADE_DT) AS TRADE_DT
      FROM AI_DAY_INDS_PRC
      WHERE MKT_TP = 'KR' AND INDS_CD IN ({placeholders})
      GROUP BY INDS_CD
    ) b
      ON a.INDS_CD = b.INDS_CD
     AND a.TRADE_DT = b.TRADE_DT
     AND a.MKT_TP = 'KR'
    """
    conn = None
    out = {}
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, want)
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_DAY_INDS_PRC] 최근종가 조회 실패: {err}")
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    for row in rows:
        cd = _normIndsCd3(row.get("INDS_CD"))
        if cd not in want:
            continue
        try:
            prc = float(row.get("CLOSE_PRC")) if row.get("CLOSE_PRC") is not None else None
        except (TypeError, ValueError):
            prc = None
        if prc is None:
            continue
        fluRt = None
        try:
            prev = row.get("PREV_CLOSE")
            prevN = float(prev) if prev is not None else None
            if prevN and prevN > 0:
                fluRt = (prc - prevN) / prevN * 100.0
        except (TypeError, ValueError):
            fluRt = None
        out[cd] = {
            "prc": prc,
            "fluRt": fluRt,
            "tradeDt": str(row.get("TRADE_DT") or ""),
            "src": "db",
        }
    return out
