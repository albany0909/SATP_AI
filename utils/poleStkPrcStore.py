"""주봉 AI_WK_STK_PRC · 월봉 AI_MON_STK_PRC UPSERT. 일봉 테이블은 건드리지 않는다."""

from utils.dbConn import openDbConn
from utils.termLog import termLog

_TABLE = {"week": "AI_WK_STK_PRC", "month": "AI_MON_STK_PRC"}


def _sql(table):
    return f"""
INSERT INTO {table} (
  STK_CD, TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
) VALUES (
  %(STK_CD)s, %(TRADE_DT)s, %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s, %(TRDE_QTY)s
)
ON DUPLICATE KEY UPDATE
  OPEN_PRC = VALUES(OPEN_PRC),
  HIGH_PRC = VALUES(HIGH_PRC),
  LOW_PRC = VALUES(LOW_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  TRDE_QTY = VALUES(TRDE_QTY)
"""


def rowsFromBars(stkCd, bars):
    out = []
    code = str(stkCd or "")
    for bar in bars or []:
        dt = str((bar or {}).get("tradeDt") or "")[:8]
        if len(code) < 6 or len(dt) != 8:
            continue
        out.append(
            {
                "STK_CD": code,
                "TRADE_DT": dt,
                "OPEN_PRC": int(bar.get("open") or 0),
                "HIGH_PRC": int(bar.get("high") or 0),
                "LOW_PRC": int(bar.get("low") or 0),
                "CLOSE_PRC": int(bar.get("close") or 0),
                "TRDE_QTY": int(bar.get("qty") or 0),
            }
        )
    return out


def upsertPoleRows(barTp, rows):
    table = _TABLE.get(str(barTp or ""))
    if not table:
        return {"ok": False, "errMsg": "주기 없음", "upsertCnt": 0}
    if not rows:
        return {"ok": True, "upsertCnt": 0}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_sql(table), rows)
            affected = cur.rowcount
        conn.commit()
        termLog(f"[{table}] UPSERT {len(rows)}건 (affected={affected})")
        return {"ok": True, "upsertCnt": len(rows), "affected": affected}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[{table}] UPSERT 실패: {err}")
        return {"ok": False, "errMsg": f"DB 적재 실패: {err}", "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def loadPoleBars(stkCd, barTp, startDt="19900101", endDt="29991231"):
    """저장된 주봉·월봉. 실패하면 로그를 남기고 빈 목록."""
    table = _TABLE.get(str(barTp or ""))
    if not table or not stkCd:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM {table}
                WHERE STK_CD=%s AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[{table}] 조회 실패 {stkCd}: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    bars = []
    for row in rows:
        if row.get("CLOSE_PRC") is None:
            continue
        bars.append(
            {
                "tradeDt": str(row.get("TRADE_DT") or "")[:8],
                "open": float(row.get("OPEN_PRC") or 0),
                "high": float(row.get("HIGH_PRC") or 0),
                "low": float(row.get("LOW_PRC") or 0),
                "close": float(row.get("CLOSE_PRC") or 0),
                "qty": int(row.get("TRDE_QTY") or 0),
            }
        )
    return bars


def poleBarStat(stkCd, barTp):
    table = _TABLE.get(str(barTp or ""))
    if not table or not stkCd:
        return {"cnt": 0, "maxDt": ""}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS cnt, MAX(TRADE_DT) AS maxDt
                FROM {table}
                WHERE STK_CD=%s
                """,
                (str(stkCd),),
            )
            row = cur.fetchone() or {}
    except Exception as err:
        termLog(f"[{table}] 건수 실패 {stkCd}: {err}")
        return {"cnt": 0, "maxDt": ""}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {"cnt": int(row.get("cnt") or 0), "maxDt": str(row.get("maxDt") or "")}
