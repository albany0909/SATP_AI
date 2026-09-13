"""AI_BT_RUN / AI_BT_TRADE 이력 적재·조회"""

import json

from utils.dbConn import openDbConn
from utils.termLog import termLog

_algColReady = False


def _algCdOf(stratId):
    try:
        from strategies.specLoader import getAlgCd, resolveAlgRef

        got = resolveAlgRef(stratId)
        if got.get("ok") and got.get("algCd"):
            return got["algCd"]
        return getAlgCd(stratId) or ""
    except Exception:
        return ""


def _ensureBtAlgCol(cur):
    """기존 AI_BT_RUN 에 ALG_CD 를 넣고 파일명 행을 코드로 채운다."""
    global _algColReady
    if _algColReady:
        return
    cur.execute("SHOW COLUMNS FROM AI_BT_RUN LIKE 'ALG_CD'")
    if not cur.fetchone():
        cur.execute(
            "ALTER TABLE AI_BT_RUN "
            "ADD COLUMN ALG_CD CHAR(5) NOT NULL DEFAULT '' "
            "COMMENT '알고리즘코드 (MTS01)' AFTER STRAT_ID"
        )
    cur.execute("SHOW INDEX FROM AI_BT_RUN")
    idxs = {str(row.get("Key_name") or "") for row in (cur.fetchall() or [])}
    if "IDX_AI_BT_RUN_ALG_DT" not in idxs:
        cur.execute(
            "ALTER TABLE AI_BT_RUN "
            "ADD KEY IDX_AI_BT_RUN_ALG_DT (ALG_CD, START_DT, END_DT)"
        )
    try:
        from strategies.specLoader import listStratSpecs

        for spec in listStratSpecs():
            sid = spec.get("stratId")
            alg = spec.get("algCd")
            if sid and alg:
                cur.execute(
                    "UPDATE AI_BT_RUN SET ALG_CD=%s "
                    "WHERE (ALG_CD='' OR ALG_CD IS NULL) AND STRAT_ID=%s",
                    (alg, sid),
                )
    except Exception:
        pass
    _algColReady = True


def saveBtRun(result, resultMsg="", initCash=None):
    """
    백테스트 결과 dict를 AI_BT_RUN(+TRADE)에 저장.
    result: runBacktest 반환값 (ok True/False 모두)
    반환: {ok, btRunId?, errMsg?}
    """
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            ok = bool(result.get("ok"))
            status = "OK" if ok else "FAIL"
            stats = result.get("stats") or {}
            params = result.get("params")
            paramsJson = None
            if params is not None:
                try:
                    paramsJson = json.dumps(params, ensure_ascii=False)
                except Exception:
                    paramsJson = str(params)

            cash = initCash
            if cash is None and stats.get("equityEnd") is not None:
                # stats만으로는 초기자금 복원이 어려워 호출측 값을 우선
                cash = None
            if cash is None:
                cash = 100_000_000.0

            _ensureBtAlgCol(cur)
            sid = result.get("stratId") or ""
            algCd = result.get("algCd") or _algCdOf(sid)
            cur.execute(
                """
                INSERT INTO AI_BT_RUN (
                  STRAT_ID, ALG_CD, STRAT_NAME, STK_CD,
                  START_DT, END_DT, WARMUP_DT,
                  INIT_CASH, TIC_SCOPE, BAR_SOURCE,
                  STATUS, ERR_MSG,
                  BAR_CNT, TRADE_CNT, WIN_CNT, LOSS_CNT,
                  WIN_RATE, AVG_RET_PCT, FINAL_RET_PCT, MAX_DD_PCT, EQUITY_END,
                  PARAMS_JSON, RESULT_MSG
                ) VALUES (
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s,
                  %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s
                )
                """,
                (
                    sid,
                    algCd,
                    result.get("name"),
                    result.get("stkCd") or "",
                    result.get("startDt") or "",
                    result.get("endDt") or "",
                    result.get("warmupStartDt"),
                    float(cash),
                    str(result.get("ticScope") or "1"),
                    str(result.get("barSource") or "min"),
                    status,
                    (result.get("errMsg") or "")[:1000] or None,
                    result.get("barCnt"),
                    stats.get("tradeCnt"),
                    stats.get("winCnt"),
                    stats.get("lossCnt"),
                    stats.get("winRate"),
                    stats.get("avgRetPct"),
                    stats.get("finalRetPct"),
                    stats.get("maxDdPct"),
                    stats.get("equityEnd"),
                    paramsJson,
                    (resultMsg or "")[:65000] or None,
                ),
            )
            btRunId = cur.lastrowid

            trades = result.get("trades") or []
            if ok and trades:
                rows = []
                for i, t in enumerate(trades, start=1):
                    rows.append(
                        (
                            btRunId,
                            i,
                            t.get("type") or "",
                            str(t.get("cntrTm") or "")[:32] or None,
                            t.get("tradeDt"),
                            t.get("price"),
                            str(t.get("reason") or "")[:64] or None,
                        )
                    )
                cur.executemany(
                    """
                    INSERT INTO AI_BT_TRADE (
                      BT_RUN_ID, SEQ, SIDE, CNTR_TM, TRADE_DT, PRICE, REASON
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )

        conn.commit()
        termLog(f"[AI_BT_RUN] 저장 BT_RUN_ID={btRunId} status={status}")
        return {"ok": True, "btRunId": btRunId}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"BT 이력 저장 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listBtHist(limit=5):
    """
    최근 실행 이력 조회.
    반환: {ok, rows:[{...}], errMsg?}
    """
    lim = max(1, min(int(limit or 5), 50))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            _ensureBtAlgCol(cur)
            conn.commit()
            cur.execute(
                """
                SELECT BT_RUN_ID, STRAT_ID, ALG_CD, STRAT_NAME, STK_CD,
                       START_DT, END_DT, INIT_CASH, STATUS,
                       TRADE_CNT, WIN_RATE, FINAL_RET_PCT, MAX_DD_PCT,
                       ERR_MSG, CREATED_AT
                FROM AI_BT_RUN
                ORDER BY BT_RUN_ID DESC
                LIMIT %s
                """,
                (lim,),
            )
            rows = cur.fetchall() or []
        return {"ok": True, "rows": rows}
    except Exception as err:
        return {"ok": False, "errMsg": f"BT 이력 조회 실패: {err}", "rows": []}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
