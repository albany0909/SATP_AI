"""차트 전용 통합봉. AI_DAY_STK_PRC 에는 쓰지 않는다."""

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_TP = {"day": "D", "D": "D", "week": "W", "W": "W", "month": "M", "M": "M"}


def _code(stkCd):
    from utils.chartBarPlan import unifiedStkCd

    text = unifiedStkCd(stkCd)
    return text[:-3] if text.endswith("_AL") else normStkCd(stkCd)


def rowsFromBars(stkCd, barTp, bars):
    code = _code(stkCd)
    tp = _TP.get(str(barTp or ""))
    out = []
    if not code or not tp:
        return out
    for bar in bars or []:
        dt = str((bar or {}).get("tradeDt") or "")[:8]
        if len(dt) != 8:
            continue
        out.append(
            {
                "STK_CD": code,
                "BAR_TP": tp,
                "TRADE_DT": dt,
                "OPEN_PRC": int(float(bar.get("open") or 0)),
                "HIGH_PRC": int(float(bar.get("high") or 0)),
                "LOW_PRC": int(float(bar.get("low") or 0)),
                "CLOSE_PRC": int(float(bar.get("close") or 0)),
                "TRDE_QTY": int(float(bar.get("qty") or 0)),
            }
        )
    return out


def upsertUnifBars(stkCd, barTp, bars):
    rows = rowsFromBars(stkCd, barTp, bars)
    if not rows:
        return {"ok": True, "upsertCnt": 0}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO AI_CHART_UNIF_PRC (
                  STK_CD, BAR_TP, TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                ) VALUES (
                  %(STK_CD)s, %(BAR_TP)s, %(TRADE_DT)s,
                  %(OPEN_PRC)s, %(HIGH_PRC)s, %(LOW_PRC)s, %(CLOSE_PRC)s, %(TRDE_QTY)s
                )
                ON DUPLICATE KEY UPDATE
                  OPEN_PRC=VALUES(OPEN_PRC),
                  HIGH_PRC=VALUES(HIGH_PRC),
                  LOW_PRC=VALUES(LOW_PRC),
                  CLOSE_PRC=VALUES(CLOSE_PRC),
                  TRDE_QTY=VALUES(TRDE_QTY)
                """,
                rows,
            )
        conn.commit()
        return {"ok": True, "upsertCnt": len(rows)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[deskChart] 통합봉 저장 실패 {stkCd} {barTp}: {err}")
        return {"ok": False, "errMsg": str(err), "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def loadUnifBars(stkCd, barTp, startDt="19900101", endDt="29991231"):
    code = _code(stkCd)
    tp = _TP.get(str(barTp or ""))
    if not code or not tp:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_CHART_UNIF_PRC
                WHERE STK_CD=%s AND BAR_TP=%s AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (code, tp, str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[deskChart] 통합봉 조회 실패 {stkCd} {barTp}: {err}")
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


def unifBarStat(stkCd, barTp):
    code = _code(stkCd)
    tp = _TP.get(str(barTp or ""))
    if not code or not tp:
        return {"cnt": 0, "minDt": "", "maxDt": ""}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt, MIN(TRADE_DT) AS minDt, MAX(TRADE_DT) AS maxDt
                FROM AI_CHART_UNIF_PRC
                WHERE STK_CD=%s AND BAR_TP=%s
                """,
                (code, tp),
            )
            row = cur.fetchone() or {}
    except Exception as err:
        termLog(f"[deskChart] 통합봉 건수 실패 {stkCd} {barTp}: {err}")
        return {"cnt": 0, "minDt": "", "maxDt": ""}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {
        "cnt": int(row.get("cnt") or 0),
        "minDt": str(row.get("minDt") or ""),
        "maxDt": str(row.get("maxDt") or ""),
    }
