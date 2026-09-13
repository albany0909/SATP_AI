"""AI_WATCH_STK UPSERT / 비활성 / 조회"""

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.watchStkTables import ensureWatchStkTable

_UPSERT_SQL = """
INSERT INTO AI_WATCH_STK (
  STK_CD, MKT_TP, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
  STATUS, USE_BT, USE_TRADE, PRIORITY,
  RANK_NO, REF_TRADE_DT, LOAD_DT, OPEN_PRC, CLOSE_PRC, SRC, NOTE
) VALUES (
  %(STK_CD)s, %(MKT_TP)s, %(STK_NM)s, %(MARKET_NAME)s, %(IND_GRP)s, %(GRP_CD)s,
  %(STATUS)s, %(USE_BT)s, %(USE_TRADE)s, %(PRIORITY)s,
  %(RANK_NO)s, %(REF_TRADE_DT)s, %(LOAD_DT)s, %(OPEN_PRC)s, %(CLOSE_PRC)s, %(SRC)s, %(NOTE)s
)
ON DUPLICATE KEY UPDATE
  STK_NM = VALUES(STK_NM),
  MARKET_NAME = VALUES(MARKET_NAME),
  IND_GRP = VALUES(IND_GRP),
  GRP_CD = VALUES(GRP_CD),
  STATUS = VALUES(STATUS),
  USE_BT = VALUES(USE_BT),
  PRIORITY = VALUES(PRIORITY),
  RANK_NO = VALUES(RANK_NO),
  REF_TRADE_DT = VALUES(REF_TRADE_DT),
  LOAD_DT = VALUES(LOAD_DT),
  OPEN_PRC = VALUES(OPEN_PRC),
  CLOSE_PRC = VALUES(CLOSE_PRC),
  SRC = VALUES(SRC),
  NOTE = VALUES(NOTE),
  -- 청산 후 순위 재진입 등 Y 요청만 반영. 기존 Y는 N으로 내리지 않는다.
  USE_TRADE = IF(VALUES(USE_TRADE)='Y', 'Y', USE_TRADE)
"""


def upsertWatchStkRows(rows):
    """관심종목 UPSERT. 반환: {ok, upsertCnt, errMsg?}"""
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    ensureWatchStkTable()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        termLog(f"[AI_WATCH_STK] UPSERT {len(rows)}건 (affected={affected})")
        return {"ok": True, "upsertCnt": len(rows), "affected": affected}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"관심종목 적재 실패: {err}", "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def deactivateWatchByGrp(grpCd, exceptStkCds=None, mktTp="KR"):
    """
    해당 그룹 종목을 STATUS=N.
    exceptStkCds에 있는 종목은 유지(활성 유지 — 비활성 대상에서 제외).
    """
    ensureWatchStkTable()
    codes = []
    for c in exceptStkCds or []:
        cd = normStkCd(c)
        if cd:
            codes.append(cd)

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if codes:
                placeholders = ",".join(["%s"] * len(codes))
                cur.execute(
                    f"""
                    UPDATE AI_WATCH_STK
                    SET STATUS='N'
                    WHERE GRP_CD=%s AND MKT_TP=%s AND STATUS='Y'
                      AND STK_CD NOT IN ({placeholders})
                    """,
                    [str(grpCd), str(mktTp)] + codes,
                )
            else:
                cur.execute(
                    """
                    UPDATE AI_WATCH_STK
                    SET STATUS='N'
                    WHERE GRP_CD=%s AND MKT_TP=%s AND STATUS='Y'
                    """,
                    (str(grpCd), str(mktTp)),
                )
            n = cur.rowcount
        conn.commit()
        return {"ok": True, "deactCnt": int(n or 0)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err), "deactCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def addWatchForMon(stkCd, stkNm="", marketName="", note=""):
    """
    웹/수동 관심 추가. STATUS=Y · USE_TRADE=Y.
    이미 RANK 그룹이면 그룹은 유지하고 감시만 켠다.
    실시간 구독은 장 시작 시 watchRtMonitor 가 수행 (추가 즉시 REG 하지 않음).
    """
    cd = normStkCd(stkCd)
    if not cd:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}

    from datetime import datetime

    ensureWatchStkTable()
    today = datetime.now().strftime("%Y%m%d")
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, GRP_CD, STATUS, USE_TRADE
                FROM AI_WATCH_STK
                WHERE STK_CD=%s AND MKT_TP='KR'
                """,
                (cd,),
            )
            exist = cur.fetchone()
            if exist:
                # 수동 재등록이면 청산잠금(exitClosed) 메모를 지운다
                cur.execute(
                    """
                    UPDATE AI_WATCH_STK
                    SET STATUS='Y',
                        USE_TRADE='Y',
                        STK_NM=COALESCE(NULLIF(%s,''), STK_NM),
                        MARKET_NAME=COALESCE(NULLIF(%s,''), MARKET_NAME),
                        NOTE=CASE
                          WHEN %s <> '' THEN %s
                          WHEN NOTE LIKE 'exitClosed:%%' THEN NULL
                          ELSE NOTE
                        END,
                        LOAD_DT=%s
                    WHERE STK_CD=%s AND MKT_TP='KR'
                    """,
                    (
                        stkNm or "",
                        marketName or "",
                        note or "",
                        note or "",
                        today,
                        cd,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO AI_WATCH_STK (
                      STK_CD, MKT_TP, STK_NM, MARKET_NAME, GRP_CD,
                      STATUS, USE_BT, USE_TRADE, PRIORITY,
                      LOAD_DT, SRC, NOTE
                    ) VALUES (
                      %s, 'KR', %s, %s, 'WEB',
                      'Y', 'Y', 'Y', 50,
                      %s, 'web', %s
                    )
                    """,
                    (cd, stkNm or None, marketName or None, today, note or None),
                )
        conn.commit()
        termLog(f"[AI_WATCH_STK] 감시 추가 {cd} {stkNm}")
        # 실매매 ON 은 이평터치(MTS01) 관리종목과 같이 둔다
        try:
            from utils.algStkStore import upsertAlgStk

            upsertAlgStk("MTS01", cd, stkNm=stkNm, src="watch", note=note or "watch")
        except Exception:
            pass
        return {"ok": True, "stkCd": cd, "stkNm": stkNm or "", "added": not bool(exist)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def deactivateWatchStk(stkCd, mktTp="KR", note=None):
    """
    관심 비활성 + 실시간 감시 해제 (STATUS=N, USE_TRADE=N).
    전량 청산 시 note='exitClosed:STOP|TAKE:YYYYMMDD' 를 남긴다.
    다음날 장마감 순위에 다시 뜨고 선정 조건(양봉·EMA정배열)을 통과하면
    순위 동기화가 STATUS=Y · USE_TRADE=Y 로 재진입시킨다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}
    ensureWatchStkTable()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if note:
                cur.execute(
                    """
                    UPDATE AI_WATCH_STK
                    SET STATUS='N', USE_TRADE='N', NOTE=%s
                    WHERE STK_CD=%s AND MKT_TP=%s
                    """,
                    (str(note)[:200], cd, str(mktTp)),
                )
            else:
                cur.execute(
                    """
                    UPDATE AI_WATCH_STK
                    SET STATUS='N', USE_TRADE='N'
                    WHERE STK_CD=%s AND MKT_TP=%s
                    """,
                    (cd, str(mktTp)),
                )
            n = cur.rowcount
        conn.commit()
        if not n:
            return {"ok": False, "errMsg": f"관심종목에 없음: {cd}"}
        termLog(f"[AI_WATCH_STK] 비활성 {cd}")
        try:
            from utils.algStkStore import deactivateAlgStk

            deactivateAlgStk("MTS01", cd)
        except Exception:
            pass
        return {"ok": True, "stkCd": cd, "deactCnt": int(n)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listExitClosedStkCds(mktTp="KR"):
    """
    손절·익절 전량청산으로 잠긴 종목.
    STATUS=N 이고 NOTE 가 exitClosed: 로 시작하는 코드만.
    """
    ensureWatchStkTable()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD
                FROM AI_WATCH_STK
                WHERE MKT_TP=%s AND STATUS='N' AND NOTE LIKE 'exitClosed:%%'
                """,
                (str(mktTp),),
            )
            rows = cur.fetchall() or []
        return [normStkCd(r.get("STK_CD")) for r in rows if r.get("STK_CD")]
    except Exception as err:
        termLog(f"[AI_WATCH_STK] 청산잠금 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listTradeWatch(mktTp="KR", limit=180):
    """실시간 감시 대상 (STATUS=Y · USE_TRADE=Y)."""
    ensureWatchStkTable()
    lim = max(1, min(int(limit or 180), 200))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, STK_NM, MARKET_NAME, GRP_CD, PRIORITY, LOAD_DT
                FROM AI_WATCH_STK
                WHERE STATUS='Y' AND USE_TRADE='Y' AND MKT_TP=%s
                ORDER BY PRIORITY ASC, STK_CD ASC
                LIMIT %s
                """,
                (str(mktTp), lim),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_WATCH_STK] 감시목록 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listActiveWatch(grpCd=None, useBtOnly=False, mktTp="KR"):
    """활성 관심종목 목록."""
    ensureWatchStkTable()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sql = """
                SELECT STK_CD, STK_NM, MARKET_NAME, IND_GRP, GRP_CD,
                       RANK_NO, REF_TRADE_DT, LOAD_DT, USE_BT, USE_TRADE, PRIORITY
                FROM AI_WATCH_STK
                WHERE STATUS='Y' AND MKT_TP=%s
            """
            args = [str(mktTp)]
            if grpCd:
                sql += " AND GRP_CD=%s"
                args.append(str(grpCd))
            if useBtOnly:
                sql += " AND USE_BT='Y'"
            sql += " ORDER BY PRIORITY ASC, RANK_NO ASC, STK_CD ASC"
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[AI_WATCH_STK] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
