"""외국인 현선물 일별 순매수 저장·조회."""

from utils.dayFrgnSfTables import ensureDayFrgnSfTable
from utils.dbConn import openDbConn
from utils.termLog import termLog


def _toInt(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(round(float(str(raw).replace(",", ""))))
    except (TypeError, ValueError):
        return None


def upsertFrgnSfRows(rows, src="krx_mdc"):
    """
    rows: [{tradeDt, spotNet, futNet, note?}, ...]
    금액은 원. 현선물 합은 적재 시 계산.
    """
    if not ensureDayFrgnSfTable():
        return {"ok": False, "errMsg": "AI_DAY_FRGN_SF 없음", "upsertCnt": 0}
    items = list(rows or [])
    if not items:
        return {"ok": True, "upsertCnt": 0}
    conn = None
    cnt = 0
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for row in items:
                ymd = str(row.get("tradeDt") or "").replace("-", "")[:8]
                if len(ymd) != 8 or not ymd.isdigit():
                    continue
                spot = _toInt(row.get("spotNet"))
                fut = _toInt(row.get("futNet"))
                total = None
                if spot is not None or fut is not None:
                    total = int(spot or 0) + int(fut or 0)
                cur.execute(
                    """
                    INSERT INTO AI_DAY_FRGN_SF (
                      TRADE_DT, SPOT_NET, FUT_NET, SPOT_FUT_NET, SRC, NOTE
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      SPOT_NET=VALUES(SPOT_NET),
                      FUT_NET=VALUES(FUT_NET),
                      SPOT_FUT_NET=VALUES(SPOT_FUT_NET),
                      SRC=VALUES(SRC),
                      NOTE=VALUES(NOTE)
                    """,
                    (
                        ymd,
                        spot,
                        fut,
                        total,
                        str(src or "krx_mdc")[:40],
                        (row.get("note") or None),
                    ),
                )
                cnt += 1
        conn.commit()
        return {"ok": True, "upsertCnt": cnt}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[frgnSf] 저장 실패: {err}")
        return {"ok": False, "errMsg": str(err), "upsertCnt": cnt}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listFrgnSf(startDt, endDt):
    """기간 일별 (시간순). 테이블 없으면 []."""
    if not ensureDayFrgnSfTable():
        return []
    start = str(startDt or "").replace("-", "")[:8]
    end = str(endDt or "").replace("-", "")[:8]
    if len(start) != 8 or len(end) != 8:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, SPOT_NET, FUT_NET, SPOT_FUT_NET, SRC, NOTE
                FROM AI_DAY_FRGN_SF
                WHERE TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (start, end),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[frgnSf] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
