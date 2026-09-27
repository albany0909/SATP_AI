"""전략 모의/실전 전량청산 라운드 저장·조회 (검증용)"""

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.stratLiveTradeTables import ensureStratLiveTradeTable
from utils.stratRoundFill import _estFee, calcRoundFill, calcRoundFromDeskFills
from utils.termLog import termLog


# 원장보강: 오늘 청산은 원장 UPDATED_AT 이 이 분수 이상 지난 것만
_BACKFILL_SETTLE_MIN = 5


def _resolveRowAlg(row):
    """저장 키는 알고리즘코드. 구 stratId 가 오면 코드로 바꾼다."""
    raw = row.get("algCd") or row.get("stratId") or ""
    try:
        from strategies.specLoader import resolveAlgRef

        got = resolveAlgRef(raw)
        if got.get("ok"):
            return got.get("algCd") or "", got.get("stratId") or ""
    except Exception:
        pass
    return str(raw or "").strip().upper()[:5], str(row.get("stratId") or "")


# 같은 청산 중복 판정: 매도금액 차이 허용 비율
_DUP_SELL_AMT_TOL = 0.005
# 손익 절대값이 매도금액의 이 비율을 넘으면 매수기준 누락으로 보고 거부
_GUARD_PNL_RATIO = 0.5
_BACKFILL_NOTE = "ledgerBackfill"


def _guardLiveRow(row):
    """
    저장 전 이상치 가드. 문제 있으면 사유 문자열, 없으면 None.
    매수 0·평단 0(=매도금액이 통째로 손익이 되는 이월 청산 버그)을 막는다.
    """
    buyQty = int(_flt(row.get("buyQty")) or 0)
    avgPx = _flt(row.get("avgBuyPx")) or 0.0
    sellAmt = abs(_flt(row.get("sellAmt")) or 0.0)
    pnl = abs(_flt(row.get("pnlWon")) or 0.0)
    if buyQty <= 0:
        return f"매수수량 {buyQty}"
    if avgPx <= 0:
        return f"평단 {avgPx}"
    if sellAmt > 0 and pnl > sellAmt * _GUARD_PNL_RATIO:
        return f"손익 {pnl:,.0f} > 매도금액 {sellAmt:,.0f}×{_GUARD_PNL_RATIO}"
    return None


def _findDupLiveTrade(cur, md, algCd, cd, exitDt, sellQty, sellAmt):
    """
    같은 청산이 이미 있으면 그 행. (MODE,ALG,STK,EXIT_DT) + 매도수량 동일 + 매도금액 근사.
    같은 날 재진입한 별도 라운드는 매도수량·금액이 달라 걸리지 않는다
    (DB 유니크 키로는 이 둘을 구분할 수 없어 앱에서 판정한다).
    """
    cur.execute(
        """
        SELECT TRADE_ID, SELL_QTY, SELL_AMT, NOTE FROM AI_STRAT_LIVE_TRADE
        WHERE MODE=%s AND ALG_CD=%s AND STK_CD=%s AND EXIT_DT=%s
        ORDER BY TRADE_ID
        """,
        (md, algCd, cd, exitDt),
    )
    for got in cur.fetchall() or []:
        if int(got.get("SELL_QTY") or 0) != int(sellQty or 0):
            continue
        oldAmt = float(got.get("SELL_AMT") or 0)
        base = max(abs(oldAmt), abs(float(sellAmt or 0)), 1.0)
        if abs(oldAmt - float(sellAmt or 0)) <= base * _DUP_SELL_AMT_TOL:
            return got
    return None


def insertLiveTrade(row, refreshPnl=True):
    """
    전량 청산 1라운드 저장.
    row: algCd(또는 구 stratId), mode, stkCd, stkNm, entryDt, exitDt, exitTm,
         exitReason, buyQty, sellQty, buyAmt, sellAmt, avgBuyPx, exitPx,
         pnlWon, pnlPct, halfYn, steps, note
    refreshPnl=False 이면 algPnl 스냅샷 재집계를 생략 (원장 일괄 보강용).
    같은 청산이 이미 있으면 새로 넣지 않는다 (dup=skip). 기존이 원장보강이고
    이번이 실시간 청산이면 실시간 값으로 덮는다 (dup=update).
    """
    if not ensureStratLiveTradeTable():
        return {"ok": False, "errMsg": "AI_STRAT_LIVE_TRADE 없음"}
    algCd, sid = _resolveRowAlg(row)
    if not algCd:
        return {"ok": False, "errMsg": "알고리즘코드 없음"}
    bad = _guardLiveRow(row)
    if bad:
        termLog(
            f"[stratLiveTrade] 저장 거부 {algCd} {normStkCd(row.get('stkCd'))} "
            f"{row.get('exitDt')} — {bad} (매수 {row.get('buyQty')}주 · "
            f"평단 {row.get('avgBuyPx')} · 매도 {row.get('sellAmt')} · "
            f"손익 {row.get('pnlWon')})"
        )
        return {"ok": False, "errMsg": f"guard:{bad}"}
    md = row.get("mode") or "paper"
    cd = normStkCd(row.get("stkCd"))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            dup = _findDupLiveTrade(
                cur, md, algCd, cd, row.get("exitDt"),
                int(row.get("sellQty") or 0), float(row.get("sellAmt") or 0),
            )
            newIsBackfill = str(row.get("note") or "").startswith(_BACKFILL_NOTE)
            if dup:
                oldIsBackfill = str(dup.get("NOTE") or "").startswith(_BACKFILL_NOTE)
                if not (oldIsBackfill and not newIsBackfill):
                    termLog(
                        f"[stratLiveTrade] 중복 청산 스킵 {algCd} {cd} "
                        f"{row.get('exitDt')} — 기존 TRADE_ID {dup.get('TRADE_ID')}"
                    )
                    return {"ok": True, "tradeId": dup.get("TRADE_ID"), "dup": "skip"}
                cur.execute(
                    """
                    UPDATE AI_STRAT_LIVE_TRADE SET
                      ENTRY_DT=%s, EXIT_TM=%s, EXIT_REASON=%s,
                      BUY_QTY=%s, SELL_QTY=%s, BUY_AMT=%s, SELL_AMT=%s,
                      AVG_BUY_PX=%s, EXIT_PX=%s, PNL_WON=%s, PNL_PCT=%s,
                      HALF_YN=%s, STEPS=%s, NOTE=%s, MAX_PNL_PCT=%s, MAX_PX=%s
                    WHERE TRADE_ID=%s
                    """,
                    (
                        row.get("entryDt") or None,
                        row.get("exitTm") or None,
                        row.get("exitReason"),
                        int(row.get("buyQty") or 0),
                        int(row.get("sellQty") or 0),
                        float(row.get("buyAmt") or 0),
                        float(row.get("sellAmt") or 0),
                        row.get("avgBuyPx"),
                        row.get("exitPx"),
                        row.get("pnlWon"),
                        row.get("pnlPct"),
                        row.get("halfYn") or "N",
                        row.get("steps") or None,
                        row.get("note") or None,
                        row.get("maxPnlPct"),
                        row.get("maxPx"),
                        dup.get("TRADE_ID"),
                    ),
                )
                conn.commit()
                termLog(
                    f"[stratLiveTrade] 원장보강 행을 실시간 청산으로 갱신 {algCd} {cd} "
                    f"TRADE_ID {dup.get('TRADE_ID')}"
                )
                if refreshPnl:
                    try:
                        from utils.algPnlDayStore import rebuildAlgPnlDay

                        rebuildAlgPnlDay(mode=md, tradeDt=row.get("exitDt"), algCd=algCd)
                    except Exception as snapErr:
                        termLog(f"[stratLiveTrade] algPnl 스냅샷 실패: {snapErr}")
                return {"ok": True, "tradeId": dup.get("TRADE_ID"), "dup": "update"}
            cur.execute(
                """
                INSERT INTO AI_STRAT_LIVE_TRADE (
                  ALG_CD, STRAT_ID, MODE, STK_CD, STK_NM,
                  ENTRY_DT, EXIT_DT, EXIT_TM, EXIT_REASON,
                  BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT,
                  AVG_BUY_PX, EXIT_PX, PNL_WON, PNL_PCT,
                  HALF_YN, STEPS, NOTE, MAX_PNL_PCT, MAX_PX
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    algCd,
                    sid or None,
                    md,
                    cd,
                    row.get("stkNm") or None,
                    row.get("entryDt") or None,
                    row.get("exitDt"),
                    row.get("exitTm") or None,
                    row.get("exitReason"),
                    int(row.get("buyQty") or 0),
                    int(row.get("sellQty") or 0),
                    float(row.get("buyAmt") or 0),
                    float(row.get("sellAmt") or 0),
                    row.get("avgBuyPx"),
                    row.get("exitPx"),
                    row.get("pnlWon"),
                    row.get("pnlPct"),
                    row.get("halfYn") or "N",
                    row.get("steps") or None,
                    row.get("note") or None,
                    row.get("maxPnlPct"),
                    row.get("maxPx"),
                ),
            )
            tradeId = cur.lastrowid
        conn.commit()
        # 알고리즘별 당일/월/누적 스냅샷 갱신 (웹·계좌탭)
        if refreshPnl:
            try:
                from utils.algPnlDayStore import rebuildAlgPnlDay

                rebuildAlgPnlDay(
                    mode=row.get("mode") or "paper",
                    tradeDt=row.get("exitDt"),
                    algCd=algCd,
                )
            except Exception as snapErr:
                termLog(f"[stratLiveTrade] algPnl 스냅샷 실패: {snapErr}")
        return {"ok": True, "tradeId": tradeId}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[stratLiveTrade] 저장 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _flt(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _normMode(mode):
    raw = str(mode or "").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    return "paper"


def backfillLiveTradeFromLedger(mode="paper", exitDt=None, algCd=None):
    """
    원장(CLOSE_DT)은 있는데 LIVE_TRADE 가 없는 전량청산을 보강한다.
    계좌탭 승패가 0으로 남는 누락을 복구한다.
    반환: {ok, inserted, skipped, tradeDt}
    """
    if not ensureStratLiveTradeTable():
        return {"ok": False, "errMsg": "AI_STRAT_LIVE_TRADE 없음", "inserted": 0}
    md = _normMode(mode)
    ymd = str(exitDt or "")[:8]
    if not ymd:
        from datetime import datetime

        ymd = datetime.now().strftime("%Y%m%d")
    wantAlg = str(algCd or "").strip().upper()[:5]
    from datetime import datetime, timedelta

    isToday = ymd == datetime.now().strftime("%Y%m%d")
    # 오늘 청산분은 실시간 저장(_onFullClose)이 먼저 하도록 몇 분 기다린다
    settleBefore = datetime.now() - timedelta(minutes=_BACKFILL_SETTLE_MIN)
    conn = None
    inserted = 0
    skipped = 0
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sql = """
                SELECT TRADE_CD, ALG_CD, STK_CD, STK_NM, FIRST_BUY_DT, CLOSE_DT,
                       BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, BUY_PX, SELL_PX,
                       CLOSE_PNL_PCT, HALF_YN, FILLED_STEPS, MODE,
                       MAX_PNL_PCT, MAX_PX, UPDATED_AT
                FROM AI_STRAT_ORD_LEDGER
                WHERE CLOSE_DT=%s AND STK_STS_CD='9'
                  AND (MODE=%s OR MODE IS NULL OR MODE='')
            """
            args = [ymd, md]
            if wantAlg:
                sql += " AND ALG_CD=%s"
                args.append(wantAlg)
            cur.execute(sql, args)
            rows = cur.fetchall() or []
        for row in rows:
            alg = str(row.get("ALG_CD") or "").strip().upper()[:5]
            cd = normStkCd(row.get("STK_CD"))
            if not alg or not cd:
                skipped += 1
                continue
            tradeCd = str(row.get("TRADE_CD") or "").strip()
            upd = row.get("UPDATED_AT")
            if isToday and isinstance(upd, datetime) and upd > settleBefore:
                skipped += 1
                continue
            # 이 청산일을 포함하는 LIVE 라운드(이월·병합 라운드 포함)가 있거나
            # NOTE 에 원장 TRADE_CD 가 남아 있으면 이미 반영된 것 → 스킵
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TRADE_ID FROM AI_STRAT_LIVE_TRADE
                    WHERE MODE=%s AND ALG_CD=%s AND STK_CD=%s
                      AND (EXIT_DT=%s
                           OR (ENTRY_DT IS NOT NULL AND ENTRY_DT<>''
                               AND ENTRY_DT<=%s AND EXIT_DT>=%s)
                           OR (%s<>'' AND NOTE LIKE %s))
                    LIMIT 1
                    """,
                    (md, alg, cd, ymd, ymd, ymd, tradeCd, f"%{tradeCd}%"),
                )
                if cur.fetchone():
                    skipped += 1
                    continue
            buyAmt = float(row.get("BUY_AMT") or 0)
            sellAmt = float(row.get("SELL_AMT") or 0)
            buyQty = int(row.get("BUY_QTY") or 0)
            sellQty = int(row.get("SELL_QTY") or 0)
            avgBuyPx = row.get("BUY_PX")
            exitPx = row.get("SELL_PX")
            netPnl = None
            # 1순위: 00 실체결 로그(단위 체결가·수수료·세금) 기준 순손익
            desk = calcRoundFromDeskFills(
                cd, row.get("FIRST_BUY_DT") or ymd, ymd, mode=md
            )
            if (
                desk.get("ok")
                and desk.get("closed")
                and str(desk.get("exitDt") or "")[:8] == ymd
                and int(desk.get("buyQty") or 0) > 0
            ):
                fill = {"ok": False}
                buyQty = int(desk.get("buyQty") or 0)
                sellQty = int(desk.get("sellQty") or 0)
                buyAmt = float(desk.get("buyAmt") or 0)
                sellAmt = float(desk.get("sellAmt") or 0)
                avgBuyPx = desk.get("avgBuyPx") or avgBuyPx
                exitPx = desk.get("exitPx") or exitPx
                netPnl = float(desk.get("pnlWon") or 0)
            else:
                # 원장 수량·금액은 주문 접수 시점 값이라 부분체결·미체결취소가 빠져 있다.
                # 체결 로그로 다시 세고, 실제로 전량청산된 라운드만 올린다.
                fill = calcRoundFill(
                    alg, cd, row.get("FIRST_BUY_DT") or ymd, ymd, mode=md
                )
            if netPnl is not None:
                pass
            elif fill.get("ok"):
                fBuy = int(fill.get("buyQty") or 0)
                fSell = int(fill.get("sellQty") or 0)
                if fill.get("closed"):
                    buyQty, sellQty = fBuy, fSell
                    buyAmt = float(fill.get("buyAmt") or 0)
                    sellAmt = float(fill.get("sellAmt") or 0)
                    avgBuyPx = fill.get("avgBuyPx") or avgBuyPx
                    exitPx = fill.get("exitPx") or exitPx
                elif fSell > 0:
                    # 원장은 전량청산(STS=9)인데 BUY 이벤트 주문수량 > 매도합이면
                    # 미체결 취소 누락이다. 매도체결 기준으로 라운드를 닫는다.
                    sellQty = fSell
                    sellAmt = float(fill.get("sellAmt") or 0)
                    avgBuyPx = fill.get("avgBuyPx") or avgBuyPx
                    exitPx = fill.get("exitPx") or exitPx
                    buyQty = sellQty
                    buyAmt = float(avgBuyPx or 0) * buyQty
                    termLog(
                        f"[stratLiveTrade] 원장보강 보정 {alg} {cd} — "
                        f"이벤트 매수 {fBuy}주 / 매도 {fSell}주 → 매도 기준으로 기록"
                    )
                else:
                    # 매수 체결 0(지정가 미체결 취소) — 라운드가 아니다
                    skipped += 1
                    termLog(
                        f"[stratLiveTrade] 원장보강 제외 {alg} {cd} — "
                        f"체결 매수 {fBuy}주 / 매도 {fSell}주 (전량청산 아님)"
                    )
                    continue
            elif buyQty > 0 and sellQty != buyQty:
                # 체결 로그를 못 읽은 경우만 옛 보정 유지
                avgPx = float(row.get("BUY_PX") or 0)
                lastPx = float(row.get("SELL_PX") or 0)
                if avgPx > 0 and lastPx > 0:
                    sellQty = buyQty
                    buyAmt = avgPx * buyQty
                    sellAmt = lastPx * buyQty
            if netPnl is None:
                # 00 로그가 없으면 추정 수수료·세금을 뺀 순손익 (LIVE 기준과 맞춤)
                bCm, _bTx = _estFee(buyAmt, False, md)
                sCm, sTx = _estFee(sellAmt, True, md)
                netPnl = sellAmt - buyAmt - bCm - sCm - sTx
            pnlWon = round(netPnl)
            pnlPct = (pnlWon / buyAmt * 100.0) if buyAmt > 0 else 0.0
            got = insertLiveTrade(
                {
                    "algCd": alg,
                    "mode": md,
                    "stkCd": cd,
                    "stkNm": row.get("STK_NM") or "",
                    "entryDt": row.get("FIRST_BUY_DT") or ymd,
                    "exitDt": ymd,
                    "exitTm": None,
                    "exitReason": "STOP",
                    "buyQty": buyQty,
                    "sellQty": sellQty if sellQty > 0 else buyQty,
                    "buyAmt": buyAmt,
                    "sellAmt": sellAmt,
                    "avgBuyPx": avgBuyPx,
                    "exitPx": exitPx,
                    "pnlWon": pnlWon,
                    "pnlPct": pnlPct,
                    "halfYn": str(row.get("HALF_YN") or "N"),
                    "steps": row.get("FILLED_STEPS") or "",
                    "note": f"{_BACKFILL_NOTE} {tradeCd}",
                    "maxPnlPct": row.get("MAX_PNL_PCT"),
                    "maxPx": row.get("MAX_PX"),
                },
                refreshPnl=False,
            )
            if got.get("ok") and not got.get("dup"):
                inserted += 1
            elif got.get("ok"):
                skipped += 1
            else:
                skipped += 1
                termLog(
                    f"[stratLiveTrade] 원장보강 실패 {alg} {cd}: {got.get('errMsg')}"
                )
        if inserted:
            termLog(f"[stratLiveTrade] 원장→LIVE 보강 {ymd} {inserted}건")
        return {
            "ok": True,
            "inserted": inserted,
            "skipped": skipped,
            "tradeDt": ymd,
            "mode": md,
        }
    except Exception as err:
        termLog(f"[stratLiveTrade] 원장보강 실패: {err}")
        return {"ok": False, "errMsg": str(err), "inserted": inserted}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listLiveTrades(
    stratId=None,
    algCd=None,
    mode=None,
    exitDt=None,
    exitDtFrom=None,
    entryDtFrom=None,
    limit=50,
):
    """최근 청산 라운드 (최신순). 알고리즘코드로 필터. mode/exitDt/exitDtFrom/entryDtFrom 로 일지 필터."""
    if not ensureStratLiveTradeTable():
        return []
    lim = max(1, min(int(limit or 50), 500))
    where = []
    args = []
    ref = algCd or stratId
    if ref:
        wantAlg = str(ref).strip()
        wantSid = ""
        try:
            from strategies.specLoader import resolveAlgRef

            got = resolveAlgRef(ref)
            if got.get("ok"):
                wantAlg = got.get("algCd") or wantAlg
                wantSid = got.get("stratId") or ""
        except Exception:
            pass
        # 신규는 ALG_CD, 마이그레이션 전 행은 구 STRAT_ID
        if wantSid:
            where.append("(ALG_CD=%s OR STRAT_ID=%s)")
            args.extend([wantAlg, wantSid])
        else:
            where.append("ALG_CD=%s")
            args.append(wantAlg)
    if mode:
        where.append("MODE=%s")
        args.append(_normMode(mode))
    if exitDt:
        where.append("EXIT_DT=%s")
        args.append(str(exitDt))
    if exitDtFrom:
        where.append("EXIT_DT>=%s")
        args.append(str(exitDtFrom).replace("-", "")[:8])
    if entryDtFrom:
        where.append("ENTRY_DT>=%s")
        args.append(str(entryDtFrom).replace("-", "")[:8])
    sql = """
        SELECT TRADE_ID, ALG_CD, STRAT_ID, MODE, STK_CD, STK_NM,
               ENTRY_DT, EXIT_DT, EXIT_TM, EXIT_REASON,
               BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT,
               AVG_BUY_PX, EXIT_PX, PNL_WON, PNL_PCT,
               HALF_YN, STEPS, MAX_PNL_PCT, MAX_PX, CREATED_AT
        FROM AI_STRAT_LIVE_TRADE
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY EXIT_DT DESC, EXIT_TM DESC, TRADE_ID DESC LIMIT %s"
    args.append(lim)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[stratLiveTrade] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listLiveTradeDays(mode=None, limit=90):
    """일자별 정산 집계 (청산일 기준)."""
    if not ensureStratLiveTradeTable():
        return []
    lim = max(1, min(int(limit or 90), 365))
    where = []
    args = []
    if mode:
        where.append("MODE=%s")
        args.append(_normMode(mode))
    sql = """
        SELECT EXIT_DT,
               COUNT(*) AS CNT,
               SUM(CASE WHEN PNL_WON > 0 THEN 1 ELSE 0 END) AS WIN_CNT,
               SUM(CASE WHEN PNL_WON < 0 THEN 1 ELSE 0 END) AS LOSS_CNT,
               SUM(PNL_WON) AS TOT_PNL_WON,
               SUM(BUY_AMT) AS TOT_BUY_AMT,
               SUM(SELL_AMT) AS TOT_SELL_AMT
        FROM AI_STRAT_LIVE_TRADE
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY EXIT_DT ORDER BY EXIT_DT DESC LIMIT %s"
    args.append(lim)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[stratLiveTrade] 일자집계 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def classifyRoundWl(pnlWon):
    """
    모든 알고리즘 공통 승패 판정 — 보유수량을 전량 청산했을 때의 최종 손익금 기준.
    1=승, -1=패, 0=본전(승패 미집계).
    분할익절(HALF_YN) 여부는 보지 않는다. 절반을 익절했어도 잔량에서 더 크게
    잃었으면 그 라운드는 패다. LIVE_TRADE 1행 = 전량청산 1라운드이고
    PNL_WON 이 분할분까지 합산한 최종 손익이므로 이 값만으로 판정한다.
    """
    try:
        pnl = float(pnlWon or 0)
    except (TypeError, ValueError):
        return 0
    if pnl > 0:
        return 1
    if pnl < 0:
        return -1
    return 0


def sumRoundWl(rows):
    """LIVE_TRADE 행 목록 → {win, loss, cnt}. cnt 는 승패 판정 건수(본전 제외)."""
    win = 0
    loss = 0
    for r in rows or []:
        wl = classifyRoundWl((r or {}).get("PNL_WON"))
        if wl > 0:
            win += 1
        elif wl < 0:
            loss += 1
    return {"win": win, "loss": loss, "cnt": win + loss}


def countRoundWl(algCd=None, mode="paper", dtFrom=None, dtTo=None):
    """
    EXIT_DT 구간의 전량청산 라운드 승패. 당일 승패도 이 함수로 맞춘다.
    부분청산만 하고 잔량을 남긴 종목은 아직 라운드가 안 닫혔으므로 잡히지 않고,
    전량청산이 끝나는 날에 승패가 확정된다.
    """
    if not ensureStratLiveTradeTable():
        return {"win": 0, "loss": 0, "cnt": 0}
    sql = "SELECT PNL_WON FROM AI_STRAT_LIVE_TRADE WHERE MODE=%s"
    args = [_normMode(mode)]
    if algCd:
        sql += " AND ALG_CD=%s"
        args.append(str(algCd).strip().upper()[:5])
    if dtFrom:
        sql += " AND EXIT_DT>=%s"
        args.append(str(dtFrom)[:8])
    if dtTo:
        sql += " AND EXIT_DT<=%s"
        args.append(str(dtTo)[:8])
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return sumRoundWl(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[stratLiveTrade] 라운드 승패 집계 실패: {err}")
        return {"win": 0, "loss": 0, "cnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _sumFromRows(rows):
    """라운드 목록 → 승률·합산 손익.
    승패는 라운드 최종 손익금 기준 (classifyRoundWl). 본전은 승패에서 뺀다.
    """
    if not rows:
        return {
            "cnt": 0,
            "winCnt": 0,
            "lossCnt": 0,
            "winRate": 0.0,
            "avgPnlPct": 0.0,
            "totPnlWon": 0.0,
            "totBuyAmt": 0.0,
            "totSellAmt": 0.0,
            "totRetPct": 0.0,
        }
    winCnt = 0
    lossCnt = 0
    wlCnt = 0
    pctSum = 0.0
    totPnl = 0.0
    totBuy = 0.0
    totSell = 0.0
    for r in rows:
        pnl = float(r.get("PNL_WON") or 0)
        pct = float(r.get("PNL_PCT") or 0)
        buy = float(r.get("BUY_AMT") or 0)
        sell = float(r.get("SELL_AMT") or 0)
        totPnl += pnl
        totBuy += buy
        totSell += sell
        pctSum += pct
        # 전량청산 라운드 최종 손익만 본다 (분할익절 여부와 무관)
        wl = classifyRoundWl(pnl)
        if wl > 0:
            winCnt += 1
            wlCnt += 1
        elif wl < 0:
            lossCnt += 1
            wlCnt += 1
    cnt = len(rows)
    return {
        "cnt": cnt,
        "winCnt": winCnt,
        "lossCnt": lossCnt,
        "winRate": (winCnt / wlCnt * 100.0) if wlCnt else 0.0,
        "avgPnlPct": (pctSum / cnt) if cnt else 0.0,
        "totPnlWon": totPnl,
        "totBuyAmt": totBuy,
        "totSellAmt": totSell,
        "totRetPct": (totPnl / totBuy * 100.0) if totBuy > 0 else 0.0,
    }


def _dayToJson(row):
    buy = _flt(row.get("TOT_BUY_AMT")) or 0.0
    pnl = _flt(row.get("TOT_PNL_WON")) or 0.0
    cnt = int(row.get("CNT") or 0)
    winCnt = int(row.get("WIN_CNT") or 0)
    return {
        "exitDt": row.get("EXIT_DT") or "",
        "cnt": cnt,
        "winCnt": winCnt,
        "lossCnt": int(row.get("LOSS_CNT") or 0),
        "winRate": (winCnt / cnt * 100.0) if cnt else 0.0,
        "totPnlWon": pnl,
        "totBuyAmt": buy,
        "totSellAmt": _flt(row.get("TOT_SELL_AMT")) or 0.0,
        "totRetPct": (pnl / buy * 100.0) if buy else 0.0,
    }


def _tradeToJson(row):
    created = row.get("CREATED_AT")
    if created is not None and hasattr(created, "strftime"):
        created = created.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "tradeId": row.get("TRADE_ID"),
        "algCd": row.get("ALG_CD") or "",
        "stratId": row.get("ALG_CD") or row.get("STRAT_ID") or "",
        "mode": row.get("MODE") or "",
        "stkCd": row.get("STK_CD") or "",
        "stkNm": row.get("STK_NM") or "",
        "entryDt": row.get("ENTRY_DT") or "",
        "exitDt": row.get("EXIT_DT") or "",
        "exitTm": row.get("EXIT_TM") or "",
        "exitReason": row.get("EXIT_REASON") or "",
        "buyQty": int(row.get("BUY_QTY") or 0),
        "sellQty": int(row.get("SELL_QTY") or 0),
        "buyAmt": _flt(row.get("BUY_AMT")) or 0.0,
        "sellAmt": _flt(row.get("SELL_AMT")) or 0.0,
        "avgBuyPx": _flt(row.get("AVG_BUY_PX")),
        "exitPx": _flt(row.get("EXIT_PX")),
        "pnlWon": _flt(row.get("PNL_WON")) or 0.0,
        "pnlPct": _flt(row.get("PNL_PCT")) or 0.0,
        "maxPnlPct": _flt(row.get("MAX_PNL_PCT")),
        "maxPx": _flt(row.get("MAX_PX")),
        "halfYn": row.get("HALF_YN") or "N",
        "steps": row.get("STEPS") or "",
        "createdAt": created or "",
    }


def listLiveTradeDaysInMonth(mode=None, year=None, month=None):
    """해당 월(EXIT_DT YYYYMM) 일자별 정산. year/month 없으면 당월."""
    if not ensureStratLiveTradeTable():
        return []
    from datetime import datetime

    now = datetime.now()
    try:
        yy = int(year or now.year)
        mm = int(month or now.month)
    except (TypeError, ValueError):
        yy, mm = now.year, now.month
    if mm < 1 or mm > 12:
        mm = now.month
    prefix = f"{yy:04d}{mm:02d}"
    where = ["EXIT_DT LIKE %s"]
    args = [prefix + "%"]
    if mode:
        where.append("MODE=%s")
        args.append(_normMode(mode))
    sql = """
        SELECT EXIT_DT,
               COUNT(*) AS CNT,
               SUM(CASE WHEN PNL_WON > 0 THEN 1 ELSE 0 END) AS WIN_CNT,
               SUM(CASE WHEN PNL_WON < 0 THEN 1 ELSE 0 END) AS LOSS_CNT,
               SUM(PNL_WON) AS TOT_PNL_WON,
               SUM(BUY_AMT) AS TOT_BUY_AMT,
               SUM(SELL_AMT) AS TOT_SELL_AMT
        FROM AI_STRAT_LIVE_TRADE
        WHERE """ + " AND ".join(where) + """
        GROUP BY EXIT_DT
        ORDER BY EXIT_DT ASC
    """
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[stratLiveTrade] 월별집계 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def buildTradeCalMonth(mode="paper", year=None, month=None):
    """
    웹 투자달력 — 한 달치 일자 요약.
    일자 손익은 매매창 계좌 당일손익과 같다 (당일 매도 실현).
    daysByDt: {YYYYMMDD: daySumJson}
    monthSum: 해당 월 당일손익 합 (계좌 월간=전량청산 합과는 다를 수 있음)
    """
    from calendar import monthrange
    from datetime import datetime

    md = _normMode(mode)
    now = datetime.now()
    try:
        yy = int(year or now.year)
        mm = int(month or now.month)
    except (TypeError, ValueError):
        yy, mm = now.year, now.month
    if mm < 1 or mm > 12:
        mm = now.month
        yy = now.year
    # 계좌 당일손익과 동일: 전량청산 라운드가 아니라 당일 매도 실현
    from utils.algTradeHist import listDeskSellDaysInMonth

    daysRaw = listDeskSellDaysInMonth(mode=md, year=yy, month=mm)
    days = [_dayToJson(d) for d in daysRaw]
    daysByDt = {d["exitDt"]: d for d in days if d.get("exitDt")}
    monthSum = {
        "cnt": sum(int(d.get("cnt") or 0) for d in days),
        "winCnt": sum(int(d.get("winCnt") or 0) for d in days),
        "lossCnt": sum(int(d.get("lossCnt") or 0) for d in days),
        "totPnlWon": sum(float(d.get("totPnlWon") or 0) for d in days),
        "totBuyAmt": sum(float(d.get("totBuyAmt") or 0) for d in days),
        "totSellAmt": sum(float(d.get("totSellAmt") or 0) for d in days),
    }
    buy = monthSum["totBuyAmt"]
    monthSum["winRate"] = (
        (monthSum["winCnt"] / monthSum["cnt"] * 100.0) if monthSum["cnt"] else 0.0
    )
    monthSum["totRetPct"] = (monthSum["totPnlWon"] / buy * 100.0) if buy else 0.0
    lastDay = monthrange(yy, mm)[1]
    # 달력 셀용: 1일 요일(월=0) — HTML 월~일 헤더와 맞춤
    firstWeekday = datetime(yy, mm, 1).weekday()  # Mon=0 .. Sun=6
    return {
        "ok": True,
        "mode": md,
        "year": yy,
        "month": mm,
        "ym": f"{yy:04d}{mm:02d}",
        "daysInMonth": lastDay,
        "firstWeekday": firstWeekday,
        "days": days,
        "daysByDt": daysByDt,
        "monthSum": monthSum,
    }


def buildTradeJournal(mode="paper", exitDt=None):
    """
    웹 투자일지.
    일자별 정산 + 선택일 종목 상세.
    exitDt 없으면 가장 최근 청산일.
    """
    md = _normMode(mode)
    daysRaw = listLiveTradeDays(mode=md, limit=180)
    days = [_dayToJson(d) for d in daysRaw]
    ymd = str(exitDt or "").strip()
    if ymd and len(ymd) != 8:
        ymd = ""
    if not ymd and days:
        ymd = days[0]["exitDt"]
    rowsRaw = listLiveTrades(mode=md, exitDt=ymd, limit=300) if ymd else []
    rows = [_tradeToJson(r) for r in rowsRaw]
    daySum = _sumFromRows(rowsRaw)
    allRowsHint = listLiveTrades(mode=md, limit=500)
    allSum = _sumFromRows(allRowsHint)
    dayReviews = []
    watchDay = {"rows": [], "cnt": 0, "tradeCnt": 0}
    try:
        from utils.algDayReview import (
            buildAlgDayReview,
            loadAlgDayReview,
            reviewNeedsRebuild,
            saveAlgDayReview,
        )
        from utils.algDayReviewStore import listAlgDayReviewSummaries
        from trading.deskLiveAlgs import listDeskLiveAlgs

        if ymd:
            codes = set()
            for row in listDeskLiveAlgs() or []:
                if row.get("algCd"):
                    codes.add(str(row.get("algCd")).strip().upper()[:5])
            for s in listAlgDayReviewSummaries(tradeDt=ymd, mode=md) or []:
                if s.get("algCd"):
                    codes.add(str(s.get("algCd")).strip().upper()[:5])
            for code in codes:
                existing = loadAlgDayReview(ymd, code, mode=md)
                if reviewNeedsRebuild(existing):
                    saveAlgDayReview(buildAlgDayReview(code, ymd), mode=md)
            dayReviews = listAlgDayReviewSummaries(tradeDt=ymd, mode=md)
    except Exception:
        dayReviews = []
    try:
        from utils.watchStkDayStore import buildWatchStkDayView

        if ymd:
            watchDay = buildWatchStkDayView(tradeDt=ymd, mktTp="KR", useTradeOnly=False)
    except Exception:
        watchDay = {"rows": [], "cnt": 0, "tradeCnt": 0}
    return {
        "ok": True,
        "mode": md,
        "exitDt": ymd or "",
        "days": days,
        "daySum": daySum,
        "allSum": allSum,
        "rows": rows,
        "dayReviews": dayReviews,
        "watchDay": watchDay,
    }


def summarizeLiveTrades(stratId=None, algCd=None, mode=None, exitDt=None):
    """
    검증용 집계.
    반환: {ok, cnt, winCnt, lossCnt, winRate, avgPnlPct, totPnlWon, totBuyAmt, totRetPct}
    """
    rows = listLiveTrades(
        stratId=stratId, algCd=algCd, mode=mode, exitDt=exitDt, limit=200
    )
    out = _sumFromRows(rows)
    out["ok"] = True
    out["rows"] = rows
    return out


def formatLiveTradeReport(stratId=None, limit=15):
    """텔레그램/터미널용 검증 리포트. stratId 인자는 코드/구파일명 모두 가능."""
    sumr = summarizeLiveTrades(stratId=stratId)
    title = stratId or "전체"
    if sumr["cnt"] <= 0:
        return f"[실전검증] {title}\n청산된 라운드가 없습니다."
    lines = [
        f"[실전검증] {title}",
        f"라운드 {sumr['cnt']} · 승 {sumr['winCnt']} / 패 {sumr['lossCnt']}"
        f" · 승률 {sumr['winRate']:.1f}%",
        f"평균수익률 {sumr['avgPnlPct']:.2f}% · 합산 {sumr['totPnlWon']:,.0f}원"
        f" ({sumr['totRetPct']:.2f}%)",
        "",
    ]
    for r in (sumr.get("rows") or [])[:limit]:
        pct = float(r.get("PNL_PCT") or 0)
        tag = "+" if pct >= 0 else ""
        reason = r.get("EXIT_REASON") or ""
        nm = r.get("STK_NM") or r.get("STK_CD")
        lines.append(
            f"{r.get('EXIT_DT')} {nm}({r.get('STK_CD')}) "
            f"{tag}{pct:.2f}% {reason} "
            f"{float(r.get('PNL_WON') or 0):,.0f}원"
            + (
                f" 고점 {float(r.get('MAX_PNL_PCT')):+.2f}%"
                if r.get("MAX_PNL_PCT") is not None
                else ""
            )
        )
    try:
        from strategies.specLoader import loadStratSpec, resolveAlgRef
        from utils.maTouchMfeStats import formatHalfTpStatsLines

        sid = stratId
        got = resolveAlgRef(stratId or "MTS01")
        if got.get("ok"):
            sid = got.get("stratId") or sid
        spec = loadStratSpec(sid) or {}
        halfNow = float((spec.get("params") or {}).get("halfTpPct") or 3)
        lines.extend(formatHalfTpStatsLines(sumr.get("rows") or [], halfNow))
    except Exception as err:
        termLog(f"[stratLiveTrade] 1차익절 통계 생략: {err}")
    return "\n".join(lines)
