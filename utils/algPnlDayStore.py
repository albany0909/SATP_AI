"""
알고리즘별 당일·월간·누적 손익 스냅샷 (AI_ALG_PNL_DAY).

- 당일 수익금·수익률: 당일 매도된 거래만 (평단대비, 분모=매도분 매입원가)
- 승패(당일·월·누적 공통): 보유수량을 전량 청산한 라운드의 최종 손익금.
  · PNL_WON > 0 → 승 / < 0 → 패 / = 0 → 본전(승패 미집계)
  · 분할익절을 했더라도 잔량까지 청산한 최종 손익으로만 판정한다.
  · 부분청산만 하고 잔량이 남은 종목은 아직 판정하지 않고,
    전량청산이 끝나는 날에 그날 승패로 잡힌다.
  · 판정 규칙 원본: utils.stratLiveTradeStore.classifyRoundWl
- 월·누적 손익금: LIVE_TRADE 라운드 합
"""

from datetime import datetime

from strategies.specLoader import normAlgCd
from utils.algPnlDayTables import ensureAlgPnlDayTable
from utils.dbConn import openDbConn
from utils.stratLiveTradeStore import sumRoundWl
from utils.stratLiveTradeTables import ensureStratLiveTradeTable
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_ALG_PNL_DAY (
  MODE, ALG_CD, TRADE_DT,
  TDY_PNL, TDY_PNL_RT, TDY_WIN, TDY_LOSS, TDY_CNT, TDY_BUY_AMT,
  MON_PNL, MON_PNL_RT, MON_WIN, MON_LOSS, MON_CNT, MON_BUY_AMT,
  TOT_PNL, TOT_PNL_RT, TOT_WIN, TOT_LOSS, TOT_CNT, TOT_BUY_AMT
) VALUES (
  %s, %s, %s,
  %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
  TDY_PNL = VALUES(TDY_PNL),
  TDY_PNL_RT = VALUES(TDY_PNL_RT),
  TDY_WIN = VALUES(TDY_WIN),
  TDY_LOSS = VALUES(TDY_LOSS),
  TDY_CNT = VALUES(TDY_CNT),
  TDY_BUY_AMT = VALUES(TDY_BUY_AMT),
  MON_PNL = VALUES(MON_PNL),
  MON_PNL_RT = VALUES(MON_PNL_RT),
  MON_WIN = VALUES(MON_WIN),
  MON_LOSS = VALUES(MON_LOSS),
  MON_CNT = VALUES(MON_CNT),
  MON_BUY_AMT = VALUES(MON_BUY_AMT),
  TOT_PNL = VALUES(TOT_PNL),
  TOT_PNL_RT = VALUES(TOT_PNL_RT),
  TOT_WIN = VALUES(TOT_WIN),
  TOT_LOSS = VALUES(TOT_LOSS),
  TOT_CNT = VALUES(TOT_CNT),
  TOT_BUY_AMT = VALUES(TOT_BUY_AMT)
"""


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _normMode(mode):
    raw = str(mode or "").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    return "paper"


def _emptyBucket():
    return {
        "pnl": 0.0,
        "pnlRt": 0.0,
        "win": 0,
        "loss": 0,
        "cnt": 0,
        "buyAmt": 0.0,
    }


def _aggRows(rows):
    """손익금·매수금액 합. 승패는 _countWl 사용."""
    b = _emptyBucket()
    for r in rows or []:
        pnl = float(r.get("PNL_WON") or 0)
        buy = float(r.get("BUY_AMT") or 0)
        b["pnl"] += pnl
        b["buyAmt"] += buy
        b["cnt"] += 1
    if b["buyAmt"] > 0:
        b["pnlRt"] = b["pnl"] / b["buyAmt"] * 100.0
    else:
        b["pnlRt"] = 0.0
    return b


def _fetchWl(cur, mode, algCd, dtFrom=None, dtTo=None):
    """
    EXIT_DT 구간의 전량청산 라운드 승패.
    LIVE_TRADE 1행 = 전량청산 1라운드이고 PNL_WON 이 분할분까지 합산한
    최종 손익이므로, 분할익절 여부와 무관하게 이 값만으로 승/패를 가린다.
    """
    sql = """
        SELECT PNL_WON
        FROM AI_STRAT_LIVE_TRADE
        WHERE MODE=%s AND ALG_CD=%s
    """
    args = [mode, algCd]
    if dtFrom:
        sql += " AND EXIT_DT>=%s"
        args.append(dtFrom)
    if dtTo:
        sql += " AND EXIT_DT<=%s"
        args.append(dtTo)
    try:
        cur.execute(sql, args)
        return sumRoundWl(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[algPnlDay] 라운드 승패 집계 실패: {err}")
        return {"win": 0, "loss": 0, "cnt": 0}


def _fetchAgg(cur, mode, algCd, dtFrom=None, dtTo=None):
    """EXIT_DT 구간 손익금 합 + 승패."""
    sql = """
        SELECT PNL_WON, BUY_AMT, HALF_YN
        FROM AI_STRAT_LIVE_TRADE
        WHERE MODE=%s AND ALG_CD=%s
    """
    args = [mode, algCd]
    if dtFrom:
        sql += " AND EXIT_DT>=%s"
        args.append(dtFrom)
    if dtTo:
        sql += " AND EXIT_DT<=%s"
        args.append(dtTo)
    cur.execute(sql, args)
    b = _aggRows(cur.fetchall() or [])
    wl = _fetchWl(cur, mode, algCd, dtFrom=dtFrom, dtTo=dtTo)
    b["win"] = wl["win"]
    b["loss"] = wl["loss"]
    # cnt 는 라운드 수가 아니라 승패 판정 건수
    b["cnt"] = wl["cnt"]
    return b


def _listAlgCds(cur, mode):
    """운영 alg + 거래이력 alg."""
    algs = []
    seen = set()
    try:
        from trading.deskLiveAlgs import listDeskLiveAlgCds

        for a in listDeskLiveAlgCds(enabledOnly=False) or []:
            cd = normAlgCd(a)
            if cd and cd not in seen:
                seen.add(cd)
                algs.append(cd)
    except Exception:
        pass
    cur.execute(
        """
        SELECT DISTINCT ALG_CD
        FROM AI_STRAT_LIVE_TRADE
        WHERE MODE=%s AND ALG_CD IS NOT NULL AND ALG_CD<>''
        """,
        (mode,),
    )
    for row in cur.fetchall() or []:
        cd = normAlgCd(row.get("ALG_CD"))
        if cd and cd not in seen:
            seen.add(cd)
            algs.append(cd)
    return algs


def _fetchRoundWl(cur, mode, algCd, dtFrom=None, dtTo=None):
    """구 호출부 호환용 별칭 — 승패는 전량청산 라운드 최종 손익 기준."""
    return _fetchWl(cur, mode, algCd, dtFrom=dtFrom, dtTo=dtTo)


def rebuildAlgPnlDay(mode="paper", tradeDt=None, algCd=None):
    """
    LIVE_TRADE 로 당일 스냅샷을 재계산·UPSERT.
    algCd 있으면 해당만, 없으면 운영+이력 전체.
    """
    if not ensureStratLiveTradeTable() or not ensureAlgPnlDayTable():
        return {"ok": False, "errMsg": "테이블 없음"}
    md = _normMode(mode)
    ymd = str(tradeDt or _todayYmd())[:8]
    monFrom = ymd[:6] + "01"
    want = normAlgCd(algCd) if algCd else ""
    # 원장만 닫히고 LIVE_TRADE 가 빠진 건을 먼저 보강 (당일 승패 0 방지)
    try:
        from utils.stratLiveTradeStore import backfillLiveTradeFromLedger

        backfillLiveTradeFromLedger(mode=md, exitDt=ymd, algCd=want or None)
    except Exception as err:
        termLog(f"[algPnlDay] LIVE 보강 실패: {err}")
    conn = None
    saved = 0
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            algs = [want] if want else _listAlgCds(cur, md)
            for alg in algs:
                if not alg:
                    continue
                from utils.algTradeHist import sumTodaySellPnlDetail

                # 당일 금액·수익률 = 당일 매도 거래만 (계좌·거래탭과 동일)
                tdySell = sumTodaySellPnlDetail(alg, ymd, mode=md)
                # 당일 승패도 월·누적과 같은 기준 — 오늘 전량청산이 끝난 라운드만.
                # 부분청산만 하고 잔량을 넘긴 종목은 청산 완료일에 판정된다.
                tdyWl = _fetchWl(cur, md, alg, dtFrom=ymd, dtTo=ymd)
                tdy = {
                    "pnl": float(tdySell.get("pnl") or 0),
                    "pnlRt": float(tdySell.get("pnlRt") or 0),
                    "buyAmt": float(tdySell.get("buyCost") or 0),
                    "win": int(tdyWl.get("win") or 0),
                    "loss": int(tdyWl.get("loss") or 0),
                    "cnt": int(tdyWl.get("cnt") or 0),
                }
                mon = _fetchAgg(cur, md, alg, dtFrom=monFrom, dtTo=ymd)
                tot = _fetchAgg(cur, md, alg, dtFrom=None, dtTo=ymd)
                cur.execute(
                    _UPSERT_SQL,
                    (
                        md,
                        alg,
                        ymd,
                        tdy["pnl"],
                        tdy["pnlRt"],
                        tdy["win"],
                        tdy["loss"],
                        tdy["cnt"],
                        tdy["buyAmt"],
                        mon["pnl"],
                        mon["pnlRt"],
                        mon["win"],
                        mon["loss"],
                        mon["cnt"],
                        mon["buyAmt"],
                        tot["pnl"],
                        tot["pnlRt"],
                        tot["win"],
                        tot["loss"],
                        tot["cnt"],
                        tot["buyAmt"],
                    ),
                )
                saved += 1
        conn.commit()
        return {"ok": True, "saved": saved, "tradeDt": ymd, "mode": md}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algPnlDay] 적재 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listAlgPnlDay(mode="paper", tradeDt=None, algCd=None):
    """스냅샷 조회. 없으면 빈 목록."""
    if not ensureAlgPnlDayTable():
        return []
    md = _normMode(mode)
    ymd = str(tradeDt or _todayYmd())[:8]
    where = ["MODE=%s", "TRADE_DT=%s"]
    args = [md, ymd]
    if algCd:
        where.append("ALG_CD=%s")
        args.append(normAlgCd(algCd))
    sql = f"""
        SELECT *
        FROM AI_ALG_PNL_DAY
        WHERE {' AND '.join(where)}
        ORDER BY ALG_CD
    """
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[algPnlDay] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listAlgPnlHistory(mode="paper", algCd=None, limit=90):
    """웹용 일자 히스토리 (최신순)."""
    if not ensureAlgPnlDayTable():
        return []
    md = _normMode(mode)
    lim = max(1, min(int(limit or 90), 365))
    where = ["MODE=%s"]
    args = [md]
    if algCd:
        where.append("ALG_CD=%s")
        args.append(normAlgCd(algCd))
    sql = f"""
        SELECT *
        FROM AI_ALG_PNL_DAY
        WHERE {' AND '.join(where)}
        ORDER BY TRADE_DT DESC, ALG_CD
        LIMIT %s
    """
    args.append(lim)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[algPnlDay] 이력조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def rowToJson(row):
    if not row:
        return {}
    return {
        "mode": row.get("MODE") or "",
        "algCd": row.get("ALG_CD") or "",
        "tradeDt": row.get("TRADE_DT") or "",
        "tdyPnl": float(row.get("TDY_PNL") or 0),
        "tdyPnlRt": float(row.get("TDY_PNL_RT") or 0),
        "tdyWin": int(row.get("TDY_WIN") or 0),
        "tdyLoss": int(row.get("TDY_LOSS") or 0),
        "tdyCnt": int(row.get("TDY_CNT") or 0),
        "monPnl": float(row.get("MON_PNL") or 0),
        "monPnlRt": float(row.get("MON_PNL_RT") or 0),
        "monWin": int(row.get("MON_WIN") or 0),
        "monLoss": int(row.get("MON_LOSS") or 0),
        "monCnt": int(row.get("MON_CNT") or 0),
        "totPnl": float(row.get("TOT_PNL") or 0),
        "totPnlRt": float(row.get("TOT_PNL_RT") or 0),
        "totWin": int(row.get("TOT_WIN") or 0),
        "totLoss": int(row.get("TOT_LOSS") or 0),
        "totCnt": int(row.get("TOT_CNT") or 0),
    }


def _rowToJson(row):
    return rowToJson(row)


def buildAlgPnlView(mode="paper", tradeDt=None, refresh=True):
    """
    매매창·웹용.
    refresh=True 이면 LIVE_TRADE 재집계 후 스냅샷 반환.
    """
    md = _normMode(mode)
    ymd = str(tradeDt or _todayYmd())[:8]
    if refresh:
        rebuildAlgPnlDay(mode=md, tradeDt=ymd)
    rows = [_rowToJson(r) for r in listAlgPnlDay(mode=md, tradeDt=ymd)]
    # 표시명
    nameMap = {}
    try:
        from trading.deskLiveAlgs import algDisplayName, listDeskLiveAlgCds

        for a in listDeskLiveAlgCds(enabledOnly=False) or []:
            nameMap[a] = algDisplayName(a)
    except Exception:
        pass
    for r in rows:
        r["algNm"] = nameMap.get(r["algCd"]) or r["algCd"]
    return {"ok": True, "mode": md, "tradeDt": ymd, "rows": rows}


def formatWl(win, loss):
    """승패 표시. 당일·월·누적 모두 전량청산 라운드의 최종 손익 기준."""
    return f"{int(win or 0)}승 {int(loss or 0)}패"


def formatAlgPnlCell(pnl, pnlRt, win, loss):
    """한 칸 텍스트: 금액 / 수익률 / 승패."""
    try:
        p = float(pnl or 0)
    except (TypeError, ValueError):
        p = 0.0
    try:
        rt = float(pnlRt or 0)
    except (TypeError, ValueError):
        rt = 0.0
    return f"{p:+,.0f}원\n{rt:+.2f}%\n{formatWl(win, loss)}"
