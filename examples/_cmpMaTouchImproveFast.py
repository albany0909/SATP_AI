# -*- coding: utf-8 -*-
"""
이평터치 개선안 BT (빠름, 1분봉≥60).

1) 종목 연속: 현행 vs 15:18 무조건 전량
2) 일별 포트폴리오: 전량 + 서킷(STOP≥3 또는 당일≤-20만)
3) 유니버스 ∪20·30 vs ∩20·100 (전량+서킷)
4) 시장가 전환 무조건 vs >SMA20 (∩+전량+서킷)
"""
from __future__ import annotations

import heapq
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import _MA_TOUCH_SMA_WARM_DAYS, calcScalePnl, loadDayBars, loadMinBars
from config.krHoliday import isTradingDay, nextTradingDay
from strategies.impl.maTouchScale import (
    MaTouchScaleStrategy,
    resolveBar3LimitFill,
    stampMinBarsWithDaySma,
)
from strategies.specLoader import loadStratSpec
from trading.maTouchUnivLoad import _isKospiKosdaq, _stkMktMap, screenUnivFromDb
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd

START = "20260801"
END = datetime.now().strftime("%Y%m%d")
MIN_BARS = 60
CIRCUIT_STOPS = 3
CIRCUIT_LOSS = -200_000.0


class StratOpt(MaTouchScaleStrategy):
    def __init__(self, params, eodFlatAlways=False, marketAboveMa=False, allowBuyFn=None):
        super().__init__(params)
        self.eodFlatAlways = eodFlatAlways
        self.marketAboveMa = marketAboveMa
        self.allowBuyFn = allowBuyFn  # tradeDt -> bool
        self._lots = []

    def _resolveBar3LimitOnMin(self, bar, tradeDt, cntrTm, events):
        if not bar or not self._limitWait:
            return
        barTm = self._barTm(bar)
        for step in list(self._limitWait.keys()):
            pend = self._limitWait.get(step) or {}
            fromTm = str(pend.get("fromTm") or "")
            expireTm = str(pend.get("expireTm") or "")
            if fromTm and barTm <= fromTm:
                continue
            limitPx = float(pend.get("limitPx") or 0)
            ordQty = int(pend.get("qty") or 0)
            kind, fillPx = resolveBar3LimitFill(bar, pend.get("maPx"), limitPx)
            if kind == "limit" and fillPx and fillPx > 0:
                self._limitWait.pop(int(step), None)
                ev = self._buyStep(
                    step,
                    float(fillPx),
                    tradeDt,
                    cntrTm,
                    f"ma{step}_bar3_limit",
                    ordQty=ordQty if ordQty > 0 else None,
                )
                if ev:
                    events.append(ev)
                continue
            if expireTm and barTm >= expireTm:
                self._limitWait.pop(int(step), None)
                closePx = float(bar.get("close") or 0)
                if closePx <= 0:
                    continue
                maPx = float(pend.get("maPx") or 0)
                if self.marketAboveMa and maPx > 0 and closePx <= maPx:
                    continue
                ev = self._buyStep(
                    step,
                    closePx,
                    tradeDt,
                    cntrTm,
                    f"ma{step}_bar3_mkt_after_limit",
                    ordQty=ordQty if ordQty > 0 else None,
                )
                if ev:
                    events.append(ev)

    def onBar(self, bars, idx):
        bar = bars[idx]
        tradeDt = bar.get("tradeDt")
        barTm = self._barTm(bar)
        block = self.allowBuyFn is not None and (not self.allowBuyFn(tradeDt))
        old = self.buyBanDt
        if block:
            self.buyBanDt = tradeDt
        try:
            ev = super().onBar(bars, idx)
        finally:
            if block:
                self.buyBanDt = old
        events = list(ev) if isinstance(ev, list) else ([ev] if ev else [])
        if self.eodFlatAlways and self.pos is not None and barTm >= self.exitAfterTm:
            qty = int(self.pos.get("qty") or 0)
            if qty > 0:
                events.append(
                    {
                        "type": "SELL",
                        "price": float(bar.get("close") or 0),
                        "reason": "eod_flat_always",
                        "cntrTm": bar.get("cntrTm") or tradeDt,
                        "qty": qty,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
        return events or None


def _rankDts():
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT TRADE_DT FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT BETWEEN %s AND %s ORDER BY TRADE_DT
                """,
                (START, END),
            )
            return [r["TRADE_DT"] for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _trde(ymd, n):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD FROM AI_DAY_STK_PRC
                WHERE TRADE_DT=%s AND INDC_TP='0'
                ORDER BY IFNULL(AMT_MN,0) DESC LIMIT %s
                """,
                (ymd, max(n * 4, 200)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    cds, seen = [], set()
    mkt = _stkMktMap([normStkCd(r.get("STK_CD")) for r in rows])
    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if not cd or cd in seen:
            continue
        meta = mkt.get(cd) or {}
        if meta and not _isKospiKosdaq(meta.get("mrktTp"), meta.get("marketName")):
            continue
        seen.add(cd)
        cds.append(cd)
        if len(cds) >= n:
            break
    return cds


def _rank(ymd):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT STK_CD FROM AI_DAY_INQ_RANK WHERE TRADE_DT=%s ORDER BY RANK_NO",
                (ymd,),
            )
            return [normStkCd(r["STK_CD"]) for r in cur.fetchall() or [] if normStkCd(r.get("STK_CD"))]
    finally:
        conn.close()


def buildUniv():
    days = []
    for rankDt in _rankDts():
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > END:
            continue
        t30 = set(_trde(rankDt, 30))
        t100 = set(_trde(rankDt, 100))
        rk = set(_rank(rankDt))
        pu = screenUnivFromDb(rankDt, trdePrcaCds=t30, rankMax=20).get("picks") or []
        pi = [
            p
            for p in (
                screenUnivFromDb(rankDt, trdePrcaCds=t100, rankMax=20).get("picks") or []
            )
            if p.get("stkCd") in (rk & t100)
        ]
        days.append(
            {
                "tradeDt": tradeDt,
                "union": [(p["stkCd"], p.get("stkNm") or p["stkCd"]) for p in pu],
                "inter": [(p["stkCd"], p.get("stkNm") or p["stkCd"]) for p in pi],
            }
        )
        print(
            f"univ {rankDt}→{tradeDt} ∪{len(pu)} ∩{len(pi)}",
            flush=True,
        )
    return days


_barCache = {}


def loadDayMin(stkCd, tradeDt):
    key = (stkCd, tradeDt)
    if key in _barCache:
        return _barCache[key]
    try:
        smaStart = (
            datetime.strptime(tradeDt, "%Y%m%d") - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
        ).strftime("%Y%m%d")
    except ValueError:
        smaStart = tradeDt
    dayBars = loadDayBars(stkCd, smaStart, tradeDt)
    minBars = loadMinBars(stkCd, "1", tradeDt, tradeDt)
    if not minBars or len(minBars) < MIN_BARS or not dayBars:
        _barCache[key] = None
        return None
    tmp = MaTouchScaleStrategy({})
    bars = stampMinBarsWithDaySma(minBars, dayBars, tmp._needPeriods())
    _barCache[key] = bars
    return bars


def loadCont(stkCd, dts):
    """연속 분봉 (정렬된 매매일 목록)."""
    parts = []
    for d in dts:
        b = loadDayMin(stkCd, d)
        if b:
            parts.extend(b)
    return parts or None


def simBars(bars, params, eodFlatAlways=False, marketAboveMa=False, allowBuyFn=None):
    if not bars:
        return []
    strat = StratOpt(params, eodFlatAlways, marketAboveMa, allowBuyFn)
    bars = strat.prepare(list(bars))
    trades = []
    for i in range(len(bars)):
        ev = strat.onBar(bars, i)
        if not ev:
            continue
        for e in ev if isinstance(ev, list) else [ev]:
            e = dict(e)
            e["tradeDt"] = e.get("tradeDt") or bars[i].get("tradeDt")
            trades.append(e)
    if strat.pos is not None and bars:
        last = bars[-1]
        q = int(strat.pos.get("qty") or 0)
        if q > 0:
            trades.append(
                {
                    "type": "SELL",
                    "price": float(last.get("close") or 0),
                    "reason": "force_close_eod",
                    "cntrTm": last.get("cntrTm"),
                    "tradeDt": last.get("tradeDt"),
                    "qty": q,
                }
            )
    return trades


def summarize(trades):
    scale = calcScalePnl(trades)
    buys = sum(1 for t in trades if t.get("type") == "BUY")
    stops = sum(
        1
        for t in trades
        if t.get("type") == "SELL" and str(t.get("reason") or "").startswith("hard_stop")
    )
    mkt = sum(
        1
        for t in trades
        if t.get("type") == "BUY" and "mkt_after_limit" in str(t.get("reason") or "")
    )
    flats = sum(
        1
        for t in trades
        if t.get("type") == "SELL" and "flat" in str(t.get("reason") or "")
    )
    return {
        "pnl": float(scale.get("pnlWon") or 0),
        "buys": buys,
        "stops": stops,
        "mkt": mkt,
        "flats": flats,
    }


def fifoPnlEvents(trades):
    """각 SELL/SELL_HALF 에 (dt, reason, pnl) 부여."""
    lots = []
    out = []
    for t in trades:
        tp = t.get("type")
        px = float(t.get("price") or 0)
        qty = int(t.get("qty") or 0)
        if px <= 0 or qty <= 0:
            continue
        if tp in ("BUY", "ADD"):
            lots.append([qty, px])
        elif tp in ("SELL", "SELL_HALF"):
            left, cost, sold = qty, 0.0, 0
            while left > 0 and lots:
                q, bp = lots[0]
                take = min(q, left)
                cost += take * bp
                sold += take
                left -= take
                q -= take
                if q <= 0:
                    lots.pop(0)
                else:
                    lots[0][0] = q
            out.append(
                (
                    t.get("tradeDt") or "",
                    str(t.get("reason") or ""),
                    px * sold - cost,
                )
            )
    return out


def runContCompare(univDays, params):
    """1번: 종목별 연속 — 현행 vs 당일전량."""
    # stk -> sorted tradeDts in union
    active = defaultdict(set)
    for d in univDays:
        for cd, _ in d["union"]:
            active[cd].add(d["tradeDt"])
    aTrades, bTrades = [], []
    n = 0
    for cd, dts in active.items():
        ordered = sorted(dts)
        bars = loadCont(cd, ordered)
        if not bars:
            continue
        n += 1
        aTrades.extend(simBars(bars, params, False, False))
        bTrades.extend(simBars(bars, params, True, False))
        if n % 20 == 0:
            print(f"  cont {n}/{len(active)}", flush=True)
    return summarize(aTrades), summarize(bTrades), n


def runDayPortfolio(univDays, univKey, params, eodFlatAlways, marketAboveMa, useCircuit):
    """일별 heap 병합 포트폴리오."""
    allTrades = []
    tripped = 0
    for day in univDays:
        tradeDt = day["tradeDt"]
        picks = day[univKey]
        strats = {}
        barsMap = {}
        for cd, _nm in picks:
            bars = loadDayMin(cd, tradeDt)
            if not bars:
                continue
            st = StratOpt(dict(params), eodFlatAlways, marketAboveMa, None)
            barsMap[cd] = st.prepare(list(bars))
            strats[cd] = st
        if not strats:
            continue
        # circuit state for this day
        stopN = 0
        dayPnl = 0.0
        blocked = False

        def allowBuy(_dt):
            return not blocked

        for st in strats.values():
            st.allowBuyFn = allowBuy
            st.eodFlatAlways = eodFlatAlways
            st.marketAboveMa = marketAboveMa

        heap = []
        idx = {cd: 0 for cd in barsMap}
        for cd, bars in barsMap.items():
            b0 = bars[0]
            heapq.heappush(heap, (b0.get("cntrTm") or "", cd, 0))
        dayEvents = []
        while heap:
            tm, cd, i = heapq.heappop(heap)
            bars = barsMap[cd]
            if i != idx[cd]:
                continue
            st = strats[cd]
            # refresh block
            blocked = useCircuit and (
                stopN >= CIRCUIT_STOPS or dayPnl <= CIRCUIT_LOSS
            )
            ev = st.onBar(bars, i)
            idx[cd] = i + 1
            if ev:
                for e in ev if isinstance(ev, list) else [ev]:
                    e = dict(e)
                    e["tradeDt"] = tradeDt
                    e["_stk"] = cd
                    dayEvents.append(e)
            ni = idx[cd]
            if ni < len(bars):
                heapq.heappush(heap, (bars[ni].get("cntrTm") or "", cd, ni))
        # force close leftover
        for cd, st in strats.items():
            if st.pos is None:
                continue
            bars = barsMap[cd]
            q = int(st.pos.get("qty") or 0)
            if q <= 0:
                continue
            dayEvents.append(
                {
                    "type": "SELL",
                    "price": float(bars[-1].get("close") or 0),
                    "reason": "force_close_eod",
                    "cntrTm": bars[-1].get("cntrTm"),
                    "tradeDt": tradeDt,
                    "qty": q,
                    "_stk": cd,
                }
            )
            st.pos = None
        # circuit stats from this day's events (for reporting tripped)
        # recompute stop/pnl chronologically for tripped flag accuracy already applied live
        for dt, rsn, pnl in fifoPnlEvents(dayEvents):
            dayPnl += pnl  # already accumulated during? we blocked live with running — recount
        # recount for tripped reporting
        stopN2 = sum(
            1
            for t in dayEvents
            if t.get("type") == "SELL" and str(t.get("reason") or "").startswith("hard_stop")
        )
        pnl2 = sum(p for _, _, p in fifoPnlEvents(dayEvents))
        if useCircuit and (stopN2 >= CIRCUIT_STOPS or pnl2 <= CIRCUIT_LOSS):
            tripped += 1
        # BUG: circuit during sim used stopN/dayPnl but never updated them in the loop!
        allTrades.extend(dayEvents)
        print(f"  day {tradeDt} {univKey} picks={len(picks)} barsOK={len(strats)}", flush=True)

    # Fix: re-run day portfolio WITH live circuit update — rewrite inner loop properly
    return summarize(allTrades), tripped


def runDayPortfolioFixed(
    univDays, univKey, params, eodFlatAlways, marketAboveMa, useCircuit
):
    allTrades = []
    trippedDays = 0
    for day in univDays:
        tradeDt = day["tradeDt"]
        picks = day[univKey]
        strats = {}
        barsMap = {}
        for cd, _nm in picks:
            bars = loadDayMin(cd, tradeDt)
            if not bars:
                continue
            st = StratOpt(dict(params), eodFlatAlways, marketAboveMa, None)
            barsMap[cd] = st.prepare(list(bars))
            strats[cd] = st
        if not strats:
            continue

        state = {"stopN": 0, "pnl": 0.0, "blocked": False}

        def allowBuy(_dt):
            return not state["blocked"]

        for st in strats.values():
            st.allowBuyFn = allowBuy

        # per-stk lots for live circuit pnl
        lots = defaultdict(list)
        heap = []
        idx = {cd: 0 for cd in barsMap}
        for cd, bars in barsMap.items():
            heapq.heappush(heap, (bars[0].get("cntrTm") or "", cd, 0))
        dayEvents = []
        while heap:
            _tm, cd, i = heapq.heappop(heap)
            if i != idx[cd]:
                continue
            if useCircuit:
                state["blocked"] = (
                    state["stopN"] >= CIRCUIT_STOPS or state["pnl"] <= CIRCUIT_LOSS
                )
            st = strats[cd]
            ev = st.onBar(barsMap[cd], i)
            idx[cd] = i + 1
            batch = []
            if ev:
                for e in ev if isinstance(ev, list) else [ev]:
                    e = dict(e)
                    e["tradeDt"] = tradeDt
                    e["_stk"] = cd
                    batch.append(e)
                    dayEvents.append(e)
            # update circuit from batch
            for e in batch:
                tp = e.get("type")
                px = float(e.get("price") or 0)
                qty = int(e.get("qty") or 0)
                if px <= 0 or qty <= 0:
                    continue
                if tp in ("BUY", "ADD"):
                    lots[cd].append([qty, px])
                elif tp in ("SELL", "SELL_HALF"):
                    left, cost, sold = qty, 0.0, 0
                    while left > 0 and lots[cd]:
                        q, bp = lots[cd][0]
                        take = min(q, left)
                        cost += take * bp
                        sold += take
                        left -= take
                        q -= take
                        if q <= 0:
                            lots[cd].pop(0)
                        else:
                            lots[cd][0][0] = q
                    pnl = px * sold - cost
                    state["pnl"] += pnl
                    rsn = str(e.get("reason") or "")
                    if rsn.startswith("hard_stop"):
                        state["stopN"] += 1
            ni = idx[cd]
            if ni < len(barsMap[cd]):
                heapq.heappush(
                    heap, (barsMap[cd][ni].get("cntrTm") or "", cd, ni)
                )
        for cd, st in strats.items():
            if st.pos is None:
                continue
            q = int(st.pos.get("qty") or 0)
            if q <= 0:
                continue
            dayEvents.append(
                {
                    "type": "SELL",
                    "price": float(barsMap[cd][-1].get("close") or 0),
                    "reason": "force_close_eod",
                    "cntrTm": barsMap[cd][-1].get("cntrTm"),
                    "tradeDt": tradeDt,
                    "qty": q,
                    "_stk": cd,
                }
            )
            st.pos = None
        if useCircuit and (
            state["stopN"] >= CIRCUIT_STOPS or state["pnl"] <= CIRCUIT_LOSS
        ):
            trippedDays += 1
        allTrades.extend(dayEvents)
        print(
            f"  {tradeDt} {univKey} ok={len(strats)} "
            f"stop={state['stopN']} dayPnl={state['pnl']:+,.0f} "
            f"block={state['blocked']}",
            flush=True,
        )
    return summarize(allTrades), trippedDays


def main():
    t0 = time.time()
    print("=== 개선안 BT ===", flush=True)
    univDays = buildUniv()
    params = dict((loadStratSpec("maTouchScale_v1") or {}).get("params") or {})
    params["limitWaitSec"] = 180
    params["confirmMode"] = "bar3BreakLimit"

    print("\n[1] 연속 오버나잇 vs 당일전량 …", flush=True)
    s1a, s1b, nStk = runContCompare(univDays, params)
    print(f"  종목 {nStk}", flush=True)
    print(f"  1a 현행: {s1a}", flush=True)
    print(f"  1b 당일전량: {s1b}", flush=True)

    print("\n[2] ∪ 당일전량+서킷 …", flush=True)
    s2, trip2 = runDayPortfolioFixed(univDays, "union", params, True, False, True)
    print(f"  2: {s2} trippedDays={trip2}", flush=True)

    print("\n[2ref] ∪ 당일전량 무서킷 …", flush=True)
    s2r, _ = runDayPortfolioFixed(univDays, "union", params, True, False, False)
    print(f"  2ref: {s2r}", flush=True)

    print("\n[3] ∩20·100 당일전량+서킷 …", flush=True)
    s3, trip3 = runDayPortfolioFixed(univDays, "inter", params, True, False, True)
    print(f"  3: {s3} trippedDays={trip3}", flush=True)

    print("\n[4] ∩ 당일전량+서킷+시장가>SMA20 …", flush=True)
    s4, trip4 = runDayPortfolioFixed(univDays, "inter", params, True, True, True)
    print(f"  4: {s4} trippedDays={trip4}", flush=True)

    print("\n=== 요약 ===", flush=True)
    rows = [
        ("1a 연속 현행(오버나잇)", s1a),
        ("1b 연속 당일전량", s1b),
        ("2ref 일별∪ 전량", s2r),
        ("2  일별∪ 전량+서킷", s2),
        ("3  일별∩ 전량+서킷", s3),
        ("4  일별∩ 전량+서킷+mkt>MA", s4),
    ]
    base = s1a["pnl"]
    for label, s in rows:
        print(
            f"{label}: {s['pnl']:+,.0f} (vs1a {s['pnl']-base:+,.0f}) · "
            f"매수{s['buys']} STOP{s['stops']} mkt{s['mkt']} flat{s['flats']}",
            flush=True,
        )
    print(f"소요 {time.time()-t0:.0f}초 바캐시 {len(_barCache)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
