"""
당일 거래내역 (시간순).

AI_ALG_EVT 의 BUY/SELL 만 뽑아 시간순으로 세운다.
매도 수익률은 그 시점 평단 대비로 계산한다. 전일 이월 보유는 당일 이벤트만으로는
평단을 알 수 없어 원장(AI_STRAT_ORD_LEDGER)의 BUY_PX 를 평단으로 쓴다.
원장 BUY_PX 는 매수 때만 갱신되므로 매도 시점의 평단과 같다.
"""

from datetime import datetime

from utils.algEvtStore import listAlgEvt
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_SIDE_LABEL = {"BUY": "매수", "SELL": "매도"}


def _flt(val):
    try:
        f = float(str(val).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return abs(f)


def _int(val):
    try:
        return int(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _ledgerAvgMap(algCd, ymd, mode=None):
    """당일 손댄 원장 행의 평단·첫매수일. {stkCd: {avgPx, firstBuyDt}}"""
    out = {}
    md = None
    if mode is not None:
        raw = str(mode or "").strip().lower()
        md = "real" if raw in ("real", "live", "실전") else "paper"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            where = [
                "ALG_CD=%s",
                "(LAST_BUY_DT=%s OR CLOSE_DT=%s OR STK_STS_CD='3')",
            ]
            args = [str(algCd or "")[:5], ymd, ymd]
            if md:
                if md == "paper":
                    where.append("(MODE=%s OR MODE IS NULL OR MODE='')")
                else:
                    where.append("MODE=%s")
                args.append(md)
            cur.execute(
                f"""
                SELECT STK_CD, BUY_PX, FIRST_BUY_DT, LAST_BUY_DT, CLOSE_DT
                FROM AI_STRAT_ORD_LEDGER
                WHERE {' AND '.join(where)}
                ORDER BY CREATED_AT ASC, TRADE_CD ASC
                """,
                tuple(args),
            )
            for row in cur.fetchall() or []:
                cd = normStkCd(row.get("STK_CD"))
                if not cd:
                    continue
                out[cd] = {
                    "avgPx": _flt(row.get("BUY_PX")),
                    "firstBuyDt": str(row.get("FIRST_BUY_DT") or ""),
                }
    except Exception as err:
        termLog(f"[tradeHist] 원장 평단 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def resolveSellAvgPx(stkCd, tradeDt, dayAvgPx, ledger=None):
    """
    매도 수익률·수익금용 평단.
    당일 신규 매수분은 당일 이벤트 평단, 전일 이월은 원장 BUY_PX.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return 0.0
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    led = (ledger or {}).get(cd) or {}
    openedToday = str(led.get("firstBuyDt") or "") == ymd
    dayAvg = float(dayAvgPx or 0)
    if openedToday and dayAvg > 0:
        return dayAvg
    return float(led.get("avgPx") or dayAvg or 0)


def ledgerAvgMap(algCd, tradeDt, mode=None):
    """거래탭·계좌손익용 원장 평단 맵."""
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    return _ledgerAvgMap(algCd, ymd, mode=mode)

    """
    시간순 거래내역.
    반환: [{tm, stkNm, stkCd, side, sideCd, px, qty, pnlPct, reason, mode}]
    pnlPct 는 매도 행만 채운다 (매수는 None).
    """
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    rows = listAlgEvt(algCd, ymd, limit=limit) or []
    ledger = _ledgerAvgMap(algCd, ymd)

    pos = {}
    out = []
    for r in rows:
        tp = str(r.get("EVT_TP") or "").strip().upper()
        if tp not in ("BUY", "SELL"):
            continue
        cd = normStkCd(r.get("STK_CD"))
        px = _flt(r.get("PX"))
        qty = _int(r.get("QTY"))
        if not cd or not px:
            continue
        st = pos.setdefault(cd, {"qty": 0, "avgPx": 0.0})
        pnlPct = None
        if tp == "BUY":
            newQty = st["qty"] + qty
            if newQty > 0:
                st["avgPx"] = (st["avgPx"] * st["qty"] + px * qty) / float(newQty)
            st["qty"] = newQty
        else:
            led = ledger.get(cd) or {}
            openedToday = str(led.get("firstBuyDt") or "") == ymd
            base = resolveSellAvgPx(cd, ymd, st["avgPx"], ledger)
            if base and base > 0:
                pnlPct = (px - base) / base * 100.0
            st["qty"] = max(0, st["qty"] - qty)
            if st["qty"] == 0:
                st["avgPx"] = 0.0
        out.append(
            {
                "tm": str(r.get("EVT_TM") or "").zfill(6)[-6:],
                "stkNm": str(r.get("STK_NM") or "").strip(),
                "stkCd": cd,
                "sideCd": tp,
                "side": _SIDE_LABEL.get(tp, tp),
                "px": px,
                "qty": qty,
                "pnlPct": pnlPct,
                "reason": str(r.get("REASON") or "").strip(),
                "mode": str(r.get("MODE") or "").strip(),
            }
        )
    return out


def sumTodaySellPnl(algCd="MTS01", tradeDt=None, mode=None):
    """
    거래탭 수익금과 같은 식. 당일 BUY 평단을 누적하고
    매도 (매도가-평단)*수량 합. 전일 이월 보유는 원장 평단을 쓴다.
    """
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    rows = listAlgEvt(algCd, ymd, limit=2000) or []
    ledger = _ledgerAvgMap(algCd, ymd)
    modeKey = str(mode or "").strip().lower()
    pos = {}
    tot = 0.0
    for r in rows:
        tp = str(r.get("EVT_TP") or "").strip().upper()
        if tp not in ("BUY", "SELL"):
            continue
        if modeKey and str(r.get("MODE") or "").strip().lower() not in ("", modeKey):
            continue
        cd = normStkCd(r.get("STK_CD"))
        px = _flt(r.get("PX"))
        qty = _int(r.get("QTY"))
        if not cd or not px:
            continue
        if qty < 1:
            qty = 1
        st = pos.setdefault(cd, {"qty": 0, "avgPx": 0.0})
        if tp == "BUY":
            newQty = st["qty"] + qty
            if newQty > 0:
                st["avgPx"] = (st["avgPx"] * st["qty"] + px * qty) / float(newQty)
            st["qty"] = newQty
            continue
        # 매도: 거래탭과 동일 — 당일 신규면 이벤트 평단, 이월이면 원장 평단
        avgPx = resolveSellAvgPx(cd, ymd, st.get("avgPx"), ledger)
        if avgPx > 0:
            tot += (px - avgPx) * qty
        st["qty"] = max(0, st["qty"] - qty)
        if st["qty"] == 0:
            st["avgPx"] = 0.0
    return tot


def sumTodaySellPnlDetail(algCd="MTS01", tradeDt=None, mode=None):
    """
    당일 매도 실현손익 + 매도분 매입원가(수익률 분모).
    영웅문식: 체결차액 − 매수/매도수수료 − 매도세금.
    승패는 전량청산 라운드 최종 손익(LIVE_TRADE) 기준.
    """
    from utils.heroPnl import heroRealizedPnl, loadDeskFeeBuckets, takeSellFees

    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    modeKey = str(mode or "").strip().lower()
    if modeKey not in ("paper", "real"):
        modeKey = ""
    rows = listAlgEvt(algCd, ymd, limit=2000, mode=modeKey or None) or []
    ledger = _ledgerAvgMap(algCd, ymd, mode=modeKey or None)
    feeBuckets = loadDeskFeeBuckets(ymd, mode=modeKey or None)
    pos = {}
    totPnl = 0.0
    totCost = 0.0
    sellCnt = 0
    totSellAmt = 0.0
    stkPnl = {}
    for r in rows:
        tp = str(r.get("EVT_TP") or "").strip().upper()
        if tp not in ("BUY", "SELL"):
            continue
        cd = normStkCd(r.get("STK_CD"))
        px = _flt(r.get("PX"))
        qty = _int(r.get("QTY"))
        if not cd or not px:
            continue
        if qty < 1:
            qty = 1
        st = pos.setdefault(cd, {"qty": 0, "avgPx": 0.0})
        if tp == "BUY":
            newQty = st["qty"] + qty
            if newQty > 0:
                st["avgPx"] = (st["avgPx"] * st["qty"] + px * qty) / float(newQty)
            st["qty"] = newQty
            continue
        avgPx = resolveSellAvgPx(cd, ymd, st.get("avgPx"), ledger)
        if avgPx > 0:
            bucket = feeBuckets.get(cd)
            buyCmsn, sellCmsn, sellTax = takeSellFees(
                bucket,
                qty,
                sellPx=px,
                buyPx=avgPx,
                mode=modeKey or "real",
            )
            got = heroRealizedPnl(
                avgPx, px, qty, buyCmsn=buyCmsn, sellCmsn=sellCmsn, sellTax=sellTax
            )
            fillPnl = float(got.get("netPnl") or 0)
            totPnl += fillPnl
            totCost += avgPx * qty
            stkPnl[cd] = float(stkPnl.get(cd) or 0) + fillPnl
        st["qty"] = max(0, st["qty"] - qty)
        if st["qty"] == 0:
            st["avgPx"] = 0.0
        sellCnt += 1
        totSellAmt += px * qty
    winCnt = 0
    lossCnt = 0
    try:
        from utils.stratLiveTradeStore import countRoundWl

        wl = countRoundWl(algCd=algCd, mode=modeKey or "paper", dtFrom=ymd, dtTo=ymd)
        winCnt = int(wl.get("win") or 0)
        lossCnt = int(wl.get("loss") or 0)
    except Exception as err:
        termLog(f"[tradeHist] {algCd} 라운드 승패 집계 실패: {err}")
    pnlRt = (totPnl / totCost * 100.0) if totCost > 0 else 0.0
    return {
        "pnl": totPnl,
        "buyCost": totCost,
        "pnlRt": pnlRt,
        "sellCnt": sellCnt,
        "winCnt": winCnt,
        "lossCnt": lossCnt,
        "sellAmt": totSellAmt,
        "stkCnt": len(stkPnl),
    }


def _deskAlgCds(algCds=None):
    cds = list(algCds or [])
    if not cds:
        try:
            from trading.deskLiveAlgs import listDeskLiveAlgCds

            cds = list(listDeskLiveAlgCds(enabledOnly=False) or [])
        except Exception:
            cds = []
    return cds or ["MTS01"]


def sumDeskSellPnlDetail(tradeDt=None, mode=None, algCds=None):
    """
    운영 알고리즘 당일 매도분만 합산.
    반환: {pnl, buyCost, pnlRt, sellCnt, winCnt, lossCnt, sellAmt}
    수익률 분모는 당일 매도분 매입원가. 매매창 계좌 당일손익과 동일.
    """
    totPnl = 0.0
    totCost = 0.0
    sellCnt = 0
    winCnt = 0
    lossCnt = 0
    totSellAmt = 0.0
    for alg in _deskAlgCds(algCds):
        try:
            d = sumTodaySellPnlDetail(alg, tradeDt=tradeDt, mode=mode)
            totPnl += float(d.get("pnl") or 0)
            totCost += float(d.get("buyCost") or 0)
            sellCnt += int(d.get("sellCnt") or 0)
            winCnt += int(d.get("winCnt") or 0)
            lossCnt += int(d.get("lossCnt") or 0)
            totSellAmt += float(d.get("sellAmt") or 0)
        except Exception as err:
            termLog(f"[tradeHist] {alg} 매도실현 합 실패: {err}")
    pnlRt = (totPnl / totCost * 100.0) if totCost > 0 else 0.0
    return {
        "pnl": totPnl,
        "buyCost": totCost,
        "pnlRt": pnlRt,
        "sellCnt": sellCnt,
        "winCnt": winCnt,
        "lossCnt": lossCnt,
        "sellAmt": totSellAmt,
    }


def _listSellYmdInMonth(mode, year, month, algCds):
    """해당 월에 매도 이벤트가 있는 일자."""
    prefix = f"{int(year):04d}{int(month):02d}"
    algs = [str(a).strip().upper()[:5] for a in (algCds or []) if a]
    if not algs:
        algs = ["MTS01"]
    modeKey = str(mode or "").strip().lower()
    ph = ",".join(["%s"] * len(algs))
    sql = f"""
        SELECT DISTINCT TRADE_DT
        FROM AI_ALG_EVT
        WHERE EVT_TP='SELL'
          AND TRADE_DT LIKE %s
          AND ALG_CD IN ({ph})
    """
    args = [prefix + "%"] + algs
    if modeKey in ("paper", "real"):
        sql += " AND (MODE=%s OR MODE IS NULL OR MODE='')"
        args.append(modeKey)
    sql += " ORDER BY TRADE_DT"
    conn = None
    try:
        from utils.algEvtTables import ensureAlgEvtTable

        if not ensureAlgEvtTable():
            return []
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() or []
        return [str(r.get("TRADE_DT") or "")[:8] for r in rows if r.get("TRADE_DT")]
    except Exception as err:
        termLog(f"[tradeHist] 월별 매도일 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listDeskSellDaysInMonth(mode=None, year=None, month=None, algCds=None):
    """
    투자달력용 일자별 매도실현.
    매매창 계좌 당일손익과 같은 함수(sumDeskSellPnlDetail)로 집계한다.
    """
    now = datetime.now()
    try:
        yy = int(year or now.year)
        mm = int(month or now.month)
    except (TypeError, ValueError):
        yy, mm = now.year, now.month
    if mm < 1 or mm > 12:
        mm = now.month
        yy = now.year
    cds = _deskAlgCds(algCds)
    out = []
    for ymd in _listSellYmdInMonth(mode, yy, mm, cds):
        d = sumDeskSellPnlDetail(tradeDt=ymd, mode=mode, algCds=cds)
        sellCnt = int(d.get("sellCnt") or 0)
        pnl = float(d.get("pnl") or 0)
        if sellCnt <= 0 and abs(pnl) < 0.5:
            continue
        out.append(
            {
                "EXIT_DT": ymd,
                "CNT": sellCnt,
                "WIN_CNT": int(d.get("winCnt") or 0),
                "LOSS_CNT": int(d.get("lossCnt") or 0),
                "TOT_PNL_WON": pnl,
                "TOT_BUY_AMT": float(d.get("buyCost") or 0),
                "TOT_SELL_AMT": float(d.get("sellAmt") or 0),
            }
        )
    return out


def sumDeskSellPnl(tradeDt=None, mode=None, algCds=None):
    """매매창 운영 알고리즘(MTS01·MSE01 등) 당일 매도실현 합."""
    return float(sumDeskSellPnlDetail(tradeDt=tradeDt, mode=mode, algCds=algCds).get("pnl") or 0)
