# -*- coding: utf-8 -*-
"""
이평터치 개선안 BT 검증 (1분봉만, 실주문 없음).

1) 현행 vs 15:18 무조건 전량(오버나잇 금지)
2) + 일 손실 서킷 (STOP≥3 또는 당일실현≤-20만 → 당일 신규매수 금지)
3) 유니버스 20∪대금30 vs 20∩대금100
4) 시장가전환: 무조건 vs 현재가>SMA20만

연속 분봉 + 종목별 전략 인스턴스, 시각순 포트폴리오 시뮬.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

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


class ImproveStrat(MaTouchScaleStrategy):
    """개선 옵션: 당일강제청산 · 시장가 SMA20위 · 유니버스/서킷 게이트."""

    def __init__(
        self,
        params,
        activeDts=None,
        eodFlatAlways=False,
        marketAboveMa=False,
        circuit=None,
        stkCd="",
    ):
        super().__init__(params)
        self.activeDts = set(activeDts or [])
        self.eodFlatAlways = bool(eodFlatAlways)
        self.marketAboveMa = bool(marketAboveMa)
        self.circuit = circuit
        self.stkCd = stkCd
        self._lots = []  # [qty, px] FIFO — 서킷 실현손익용

    def _inUniv(self, tradeDt):
        if not self.activeDts:
            return True
        return tradeDt in self.activeDts

    def _circuitOk(self, tradeDt):
        if self.circuit is None:
            return True
        return self.circuit.allowNew(tradeDt)

    def _feedCircuit(self, tradeDt, events):
        if self.circuit is None or not events:
            return
        for e in events:
            tp = e.get("type")
            px = float(e.get("price") or 0)
            qty = int(e.get("qty") or 0)
            if px <= 0 or qty <= 0:
                continue
            if tp in ("BUY", "ADD"):
                self._lots.append([qty, px])
            elif tp in ("SELL", "SELL_HALF"):
                left, cost, sold = qty, 0.0, 0
                while left > 0 and self._lots:
                    q, bp = self._lots[0]
                    take = min(q, left)
                    cost += take * bp
                    sold += take
                    left -= take
                    q -= take
                    if q <= 0:
                        self._lots.pop(0)
                    else:
                        self._lots[0][0] = q
                pnl = px * sold - cost
                self.circuit.noteRoundPnl(tradeDt, str(e.get("reason") or ""), pnl)

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
                try:
                    closePx = float(bar.get("close") or bar.get("c") or 0)
                except (TypeError, ValueError):
                    closePx = 0.0
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
        gateBan = (not self._inUniv(tradeDt)) or (not self._circuitOk(tradeDt))
        oldBan = self.buyBanDt
        if gateBan:
            self.buyBanDt = tradeDt
        try:
            ev = super().onBar(bars, idx)
        finally:
            if gateBan:
                self.buyBanDt = oldBan
        events = list(ev) if isinstance(ev, list) else ([ev] if ev else [])
        if (
            self.eodFlatAlways
            and self.pos is not None
            and barTm >= self.exitAfterTm
        ):
            qtyNow = int(self.pos.get("qty") or 0)
            if qtyNow > 0:
                close = float(bar.get("close") or 0)
                events.append(
                    {
                        "type": "SELL",
                        "price": close,
                        "reason": "eod_flat_always",
                        "cntrTm": bar.get("cntrTm") or tradeDt,
                        "qty": qtyNow,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
        self._feedCircuit(tradeDt, events)
        return events or None


class DayCircuit:
    def __init__(self, maxStops=CIRCUIT_STOPS, maxLoss=CIRCUIT_LOSS):
        self.maxStops = maxStops
        self.maxLoss = maxLoss
        self.stops = defaultdict(int)
        self.pnl = defaultdict(float)
        self.tripped = set()

    def allowNew(self, tradeDt):
        if tradeDt in self.tripped:
            return False
        if self.stops[tradeDt] >= self.maxStops:
            self.tripped.add(tradeDt)
            return False
        if self.pnl[tradeDt] <= self.maxLoss:
            self.tripped.add(tradeDt)
            return False
        return True

    def noteRoundPnl(self, tradeDt, reason, pnlWon):
        if str(reason).startswith("hard_stop"):
            self.stops[tradeDt] += 1
        self.pnl[tradeDt] += float(pnlWon or 0)
        self.allowNew(tradeDt)


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


def _trdeTop(ymd, n):
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


def _rankCds(ymd):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT=%s ORDER BY RANK_NO ASC
                """,
                (ymd,),
            )
            return [
                normStkCd(r["STK_CD"])
                for r in (cur.fetchall() or [])
                if normStkCd(r.get("STK_CD"))
            ]
    finally:
        conn.close()


def buildUnivMaps():
    """tradeDt → {union: set, inter: set, names: {cd:nm}}"""
    out = {}
    for rankDt in _rankDts():
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > END:
            continue
        trde30 = set(_trdeTop(rankDt, 30))
        trde100 = set(_trdeTop(rankDt, 100))
        rank = set(_rankCds(rankDt))
        gotU = screenUnivFromDb(rankDt, trdePrcaCds=trde30, rankMax=20)
        picksU = gotU.get("picks") or []
        union = {p["stkCd"] for p in picksU}
        names = {p["stkCd"]: p.get("stkNm") or p["stkCd"] for p in picksU}
        # 교집합: 순위∩대금100 후 동일 스크린 필터
        gotI = screenUnivFromDb(rankDt, trdePrcaCds=trde100, rankMax=20)
        picksI = [
            p
            for p in (gotI.get("picks") or [])
            if p.get("stkCd") in (rank & trde100)
        ]
        inter = {p["stkCd"] for p in picksI}
        for p in picksI:
            names[p["stkCd"]] = p.get("stkNm") or p["stkCd"]
        out[tradeDt] = {"union": union, "inter": inter, "names": names}
        print(
            f"univ {rankDt}→{tradeDt} ∪={len(union)} ∩={len(inter)}",
            flush=True,
        )
    return out


def _loadStamped(stkCd, startDt, endDt, cache):
    key = (stkCd, startDt, endDt)
    if key in cache:
        return cache[key]
    try:
        smaStart = (
            datetime.strptime(startDt, "%Y%m%d")
            - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
        ).strftime("%Y%m%d")
    except ValueError:
        smaStart = startDt
    dayBars = loadDayBars(stkCd, smaStart, endDt)
    minBars = loadMinBars(stkCd, "1", startDt, endDt)
    if not minBars or not dayBars:
        cache[key] = (None, 0)
        return cache[key]
    byDt = defaultdict(int)
    for b in minBars:
        byDt[b.get("tradeDt")] += 1
    okDts = {d for d, n in byDt.items() if n >= MIN_BARS}
    if not okDts:
        cache[key] = (None, 0)
        return cache[key]
    minBars = [b for b in minBars if b.get("tradeDt") in okDts]
    tmp = MaTouchScaleStrategy({})
    bars = stampMinBarsWithDaySma(minBars, dayBars, tmp._needPeriods())
    cache[key] = (bars, len(okDts))
    return cache[key]


def runPortfolio(
    univMap, univKey, params, eodFlatAlways, marketAboveMa, useCircuit, barCache
):
    """시각순 포트폴리오. 반환: 요약 dict."""
    active = defaultdict(set)
    for dt, u in univMap.items():
        for cd in u.get(univKey) or []:
            active[cd].add(dt)
    if not active:
        return {
            "pnl": 0,
            "buys": 0,
            "stops": 0,
            "takes": 0,
            "flats": 0,
            "mktAfter": 0,
            "stkN": 0,
            "trippedDays": 0,
            "worstDay": 0,
            "bestDay": 0,
        }

    tradeDts = sorted(univMap.keys())
    startDt, endDt = tradeDts[0], tradeDts[-1]
    circuit = DayCircuit() if useCircuit else None

    stratMap = {}
    barsMap = {}
    for cd, dts in active.items():
        bars, _ = _loadStamped(cd, startDt, endDt, barCache)
        if not bars:
            continue
        barDts = {b.get("tradeDt") for b in bars}
        act = dts & barDts
        if not act:
            continue
        stratMap[cd] = ImproveStrat(
            dict(params),
            activeDts=act,
            eodFlatAlways=eodFlatAlways,
            marketAboveMa=marketAboveMa,
            circuit=circuit,
            stkCd=cd,
        )
        barsMap[cd] = stratMap[cd].prepare(list(bars))

    idxMap = {cd: 0 for cd in barsMap}
    allTrades = []

    def peek(cd):
        bars = barsMap[cd]
        i = idxMap[cd]
        if i >= len(bars):
            return None
        b = bars[i]
        return (b.get("tradeDt") or "", b.get("cntrTm") or "", cd, i)

    alive = set(barsMap.keys())
    while alive:
        nxt = []
        for cd in list(alive):
            p = peek(cd)
            if p is None:
                alive.discard(cd)
                continue
            nxt.append(p)
        if not nxt:
            break
        nxt.sort()
        dt, tm, cd, i = nxt[0]
        bars = barsMap[cd]
        strat = stratMap[cd]
        ev = strat.onBar(bars, i)
        idxMap[cd] = i + 1
        if ev:
            for e in ev if isinstance(ev, list) else [ev]:
                e = dict(e)
                e["tradeDt"] = e.get("tradeDt") or dt
                e["_stk"] = cd
                allTrades.append(e)

    for cd, strat in stratMap.items():
        if strat.pos is None:
            continue
        bars = barsMap[cd]
        if not bars:
            continue
        last = bars[-1]
        qty = int(strat.pos.get("qty") or 0)
        if qty <= 0:
            continue
        allTrades.append(
            {
                "type": "SELL",
                "price": float(last.get("close") or 0),
                "reason": "force_close_eod",
                "cntrTm": last.get("cntrTm"),
                "tradeDt": last.get("tradeDt"),
                "qty": qty,
                "_stk": cd,
            }
        )
        strat.pos = None

    byStk = defaultdict(list)
    for t in allTrades:
        byStk[t.get("_stk")].append(t)

    totPnl = 0.0
    buys = 0
    stops = 0
    takes = 0
    flats = 0
    mktAfter = 0
    dayPnl = defaultdict(float)
    for cd, trades in byStk.items():
        scale = calcScalePnl(trades)
        totPnl += float(scale.get("pnlWon") or 0)
        for t in trades:
            if t.get("type") == "BUY":
                buys += 1
                if "mkt_after_limit" in str(t.get("reason") or ""):
                    mktAfter += 1
            if t.get("type") == "SELL":
                rsn = str(t.get("reason") or "")
                if rsn.startswith("hard_stop"):
                    stops += 1
                elif rsn.startswith("tp") or rsn.startswith("rsi"):
                    takes += 1
                elif "flat" in rsn:
                    flats += 1
        buysQ = []
        for t in trades:
            tp = t.get("type")
            px = float(t.get("price") or 0)
            qty = int(t.get("qty") or 0)
            dt = t.get("tradeDt") or ""
            if px <= 0 or qty <= 0:
                continue
            if tp in ("BUY", "ADD"):
                buysQ.append([qty, px])
            elif tp in ("SELL", "SELL_HALF"):
                left, cost, sold = qty, 0.0, 0
                while left > 0 and buysQ:
                    q, bp = buysQ[0]
                    take = min(q, left)
                    cost += take * bp
                    sold += take
                    left -= take
                    q -= take
                    if q <= 0:
                        buysQ.pop(0)
                    else:
                        buysQ[0][0] = q
                dayPnl[dt] += px * sold - cost

    tripped = len(circuit.tripped) if circuit else 0
    return {
        "pnl": totPnl,
        "buys": buys,
        "stops": stops,
        "takes": takes,
        "flats": flats,
        "mktAfter": mktAfter,
        "stkN": len(byStk),
        "trippedDays": tripped,
        "worstDay": min(dayPnl.values()) if dayPnl else 0,
        "bestDay": max(dayPnl.values()) if dayPnl else 0,
    }


def main():
    t0 = time.time()
    print("=== 이평터치 개선안 BT (1분봉≥60) ===", flush=True)
    print(f"기간 {START}~{END}", flush=True)
    univMap = buildUnivMaps()
    base = dict((loadStratSpec("maTouchScale_v1") or {}).get("params") or {})
    base["limitWaitSec"] = 180
    base["confirmMode"] = "bar3BreakLimit"
    barCache = {}

    scenarios = [
        ("1a 현행 ∪ 오버나잇허용", "union", False, False, False),
        ("1b ∪ 15:18무조건전량", "union", True, False, False),
        ("2  ∪ 전량+서킷", "union", True, False, True),
        ("3  ∩20·100 전량+서킷", "inter", True, False, True),
        ("4  ∩ 전량+서킷+시장가>SMA20", "inter", True, True, True),
    ]

    rows = []
    for label, ukey, flat, mktMa, circ in scenarios:
        print(f"\n▶ {label} …", flush=True)
        r = runPortfolio(univMap, ukey, base, flat, mktMa, circ, barCache)
        r["label"] = label
        rows.append(r)
        print(
            f"  손익 {r['pnl']:+,.0f} · 매수 {r['buys']} · STOP {r['stops']} · "
            f"TAKE {r['takes']} · flat {r['flats']} · mkt전환 {r['mktAfter']} · "
            f"서킷발동일 {r['trippedDays']} · 최악일 {r['worstDay']:+,.0f}",
            flush=True,
        )

    print("\n=== 요약 ===", flush=True)
    basePnl = rows[0]["pnl"] if rows else 0
    for r in rows:
        d = r["pnl"] - basePnl
        print(
            f"{r['label']}: {r['pnl']:+,.0f}원 (vs현행 {d:+,.0f}) · "
            f"매수{r['buys']} STOP{r['stops']} mkt{r['mktAfter']}",
            flush=True,
        )
    print(f"소요 {time.time() - t0:.0f}초 · 바캐시 {len(barCache)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
