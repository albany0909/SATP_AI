# -*- coding: utf-8 -*-
"""Compare stop schemes on MTS01 BT: A hard-2 | B 1.2/1.6/2 | C 1.5/50 then 2 | D 1.5 flat.
막날 강제청산은 그 봉의 장중 종가다. 장마감 뒤에야 아는 신호의 당일 종가 체결은 쓰지 않는다.
Also report winners missed / flipped vs A per symbol.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\dev-workspace\workspace\SATP_AI")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import calcScalePnl
from strategies.impl.maTouchScale import MaTouchScaleStrategy, scaleOutQty
from strategies.specLoader import loadStratSpec
from utils.stkCdUtil import normStkCd

START = "20260818"
END = "20260921"
MIN_BARS = 40


class LadderStrat(MaTouchScaleStrategy):
    """ladder: list of (pct, ratio); last often 1.0 flatten. None = baseline hard."""

    def __init__(self, params, ladder=None):
        super().__init__(params)
        self.ladder = list(ladder) if ladder else None

    def _applyLadderStop(self, bar, tradeDt, cntrTm):
        if self.pos is None or self._scaleStopOn() or not self.ladder:
            return []
        avgPx = float(self.pos.get("avgPx") or 0)
        qtyNow = int(self.pos.get("qty") or 0)
        if avgPx <= 0 or qtyNow < 1:
            return []
        low = float(bar.get("low") or 0)
        if low <= 0:
            return []
        events = []
        for i, (pct, ratio) in enumerate(self.ladder):
            flag = f"stopL{i+1}"
            if self.pos is None:
                break
            if self.pos.get(flag):
                continue
            trigger = avgPx * (1.0 - float(pct) / 100.0)
            if low > trigger + 1e-9:
                break
            qtyNow = int(self.pos.get("qty") or 0)
            if qtyNow < 1:
                break
            if float(ratio) >= 0.999 or qtyNow == 1:
                sellQty = qtyNow
            else:
                sellQty = scaleOutQty(qtyNow, float(ratio))
                if sellQty < 1:
                    sellQty = qtyNow
            self.pos[flag] = True
            if sellQty >= qtyNow:
                events.append(
                    {
                        "type": "SELL",
                        "price": trigger,
                        "reason": f"ladder_stop_{pct:g}pct_flat",
                        "cntrTm": cntrTm,
                        "qty": qtyNow,
                        "tradeDt": tradeDt,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
                break
            self._cutQty(sellQty)
            events.append(
                {
                    "type": "SELL_HALF",
                    "price": trigger,
                    "reason": f"ladder_stop_{pct:g}pct",
                    "cntrTm": cntrTm,
                    "qty": sellQty,
                    "tradeDt": tradeDt,
                }
            )
            if self.pos is None:
                break
        return events

    def onBar(self, bars, idx):
        bar = bars[idx]
        tradeDt = bar.get("tradeDt")
        cntrTm = str(bar.get("cntrTm") or "")
        if self.ladder and self.pos is not None and (not self._scaleStopOn()):
            ev = self._applyLadderStop(bar, tradeDt, cntrTm)
            if ev:
                return ev
        old = self.hardStopPct
        if self.ladder:
            self.hardStopPct = 99.0
        try:
            return super().onBar(bars, idx)
        finally:
            self.hardStopPct = old


def buildUniv():
    import examples._cmpMaTouchImproveFast as m

    old_s, old_e = m.START, m.END
    m.START, m.END = START, END
    try:
        return m.buildUniv()
    finally:
        m.START, m.END = old_s, old_e


_barCache = {}


def loadCont(stkCd, dts):
    key = (stkCd, tuple(dts))
    if key in _barCache:
        return _barCache[key]
    import examples._cmpMaTouchImproveFast as m

    bars = m.loadCont(stkCd, dts)
    _barCache[key] = bars
    return bars


def sim(bars, params, ladder):
    if not bars:
        return []
    strat = LadderStrat(params, ladder=ladder)
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
    hard = sum(
        1
        for t in trades
        if t.get("type") in ("SELL", "SELL_HALF")
        and "hard_stop" in str(t.get("reason") or "")
    )
    ladder = sum(
        1
        for t in trades
        if t.get("type") in ("SELL", "SELL_HALF")
        and "ladder_stop" in str(t.get("reason") or "")
    )
    wins = 0
    losses = 0
    for r in scale.get("rounds") or []:
        p = float(r.get("pnlWon") or 0)
        if p > 0:
            wins += 1
        elif p < 0:
            losses += 1
    return {
        "pnl": float(scale.get("pnlWon") or 0),
        "buys": buys,
        "hardStops": hard,
        "ladderStops": ladder,
        "rounds": int(scale.get("roundsN") or len(scale.get("rounds") or [])),
        "wins": wins,
        "losses": losses,
    }


def perStkPnl(trades):
    """Group trades into rounds via calcScalePnl then sum by stk if present; else one bucket."""
    scale = calcScalePnl(trades)
    by = defaultdict(float)
    rounds = scale.get("rounds") or []
    if not rounds:
        by["_all"] = float(scale.get("pnlWon") or 0)
        return by
    # calcScalePnl rounds may not carry stkCd; tag from buy events order fallback
    buys = [t for t in trades if t.get("type") == "BUY"]
    bi = 0
    for r in rounds:
        cd = r.get("stkCd") or r.get("code")
        if not cd and bi < len(buys):
            cd = buys[bi].get("stkCd") or buys[bi].get("code")
            bi += 1
        elif bi < len(buys):
            bi += 1
        cd = normStkCd(cd) if cd else "_unk"
        by[cd] += float(r.get("pnlWon") or 0)
    return by


def main():
    t0 = time.time()
    print(f"=== MTS01 stop compare BT {START}~{END} ===", flush=True)
    params = dict((loadStratSpec("maTouchScale_v1") or {}).get("params") or {})
    schemes = [
        ("A_hard-2flat", None),
        ("B_ladder-1.2/30-1.6/30-2flat", [(1.2, 0.30), (1.6, 0.30), (2.0, 1.0)]),
        ("C_ladder-1.5/50-2flat", [(1.5, 0.50), (2.0, 1.0)]),
        ("D_hard-1.5flat", [(1.5, 1.0)]),
    ]
    print("schemes:", ", ".join(n for n, _ in schemes), flush=True)
    print("loading univ…", flush=True)
    univDays = buildUniv()
    print(f"univ days {len(univDays)}", flush=True)

    active = defaultdict(set)
    for d in univDays:
        for cd, _ in d["union"]:
            active[normStkCd(cd)].add(d["tradeDt"])

    # per scheme: all trades + per-stock pnl map
    buckets = {name: [] for name, _ in schemes}
    stkPnl = {name: {} for name, _ in schemes}
    n = 0
    total = len(active)
    for cd, dts in sorted(active.items()):
        ordered = sorted(dts)
        bars = loadCont(cd, ordered)
        if not bars or len(bars) < MIN_BARS:
            continue
        n += 1
        for name, ladder in schemes:
            tr = sim(bars, params, ladder)
            buckets[name].extend(tr)
            s = calcScalePnl(tr)
            stkPnl[name][cd] = float(s.get("pnlWon") or 0)
        if n % 15 == 0:
            print(f"  {n}/{total}", flush=True)

    print("\n=== TOTAL ===", flush=True)
    print(f"종목수 {n}", flush=True)
    baseName = schemes[0][0]
    basePnl = None
    rows = []
    for name, _ in schemes:
        s = summarize(buckets[name])
        rows.append((name, s))
        if basePnl is None:
            basePnl = s["pnl"]
        print(
            f"{name}: pnl={s['pnl']:+,.0f} (vsA {s['pnl']-basePnl:+,.0f}) | "
            f"매수{s['buys']} rounds{s['rounds']} W{s['wins']}/L{s['losses']} "
            f"hard{s['hardStops']} ladder{s['ladderStops']}",
            flush=True,
        )

    # Missed winners vs A: A>0 but scheme<=0; also profit cut on A winners
    print("\n=== 수익종목 놓침 (vs A) ===", flush=True)
    aMap = stkPnl[baseName]
    missLines = []
    for name, _ in schemes:
        if name == baseName:
            continue
        m = stkPnl[name]
        # A winner -> other loser
        flipped = []
        # A winner but other made less (profit cut), still may be >0
        cut = []
        # A loser -> other winner (rescued)
        rescued = []
        for cd, ap in aMap.items():
            op = m.get(cd, 0.0)
            if ap > 0 and op <= 0:
                flipped.append((cd, ap, op, op - ap))
            elif ap > 0 and op < ap - 1:
                cut.append((cd, ap, op, op - ap))
            if ap <= 0 and op > 0:
                rescued.append((cd, ap, op, op - ap))
        flipped.sort(key=lambda x: x[3])  # worst delta first
        cut.sort(key=lambda x: x[3])
        rescued.sort(key=lambda x: -x[3])
        flipLoss = sum(x[3] for x in flipped)
        cutLoss = sum(x[3] for x in cut)
        rescueGain = sum(x[3] for x in rescued)
        line = (
            f"{name}: A수익→손실 {len(flipped)}종 (Δ {flipLoss:+,.0f}) | "
            f"A수익축소 {len(cut)}종 (Δ {cutLoss:+,.0f}) | "
            f"A손실→수익 {len(rescued)}종 (Δ {rescueGain:+,.0f})"
        )
        print(line, flush=True)
        missLines.append(line)
        # top examples
        if flipped:
            top = ", ".join(f"{c}(A{a:+.0f}→{o:+.0f})" for c, a, o, _ in flipped[:8])
            print(f"  놓침예시: {top}", flush=True)
            missLines.append(f"  missEx: {top}")
        if rescued:
            top = ", ".join(f"{c}(A{a:+.0f}→{o:+.0f})" for c, a, o, _ in rescued[:5])
            print(f"  구제예시: {top}", flush=True)
            missLines.append(f"  rescueEx: {top}")

    print(f"\n소요 {time.time()-t0:.0f}s", flush=True)
    out = ROOT / "examples" / "_cmpStopLadder_out.txt"
    with out.open("w", encoding="utf-8") as f:
        f.write(f"range {START}-{END} stks {n}\n")
        for name, s in rows:
            f.write(
                f"{name}\tpnl={s['pnl']:.0f}\tdeltaA={s['pnl']-basePnl:.0f}\t"
                f"buys={s['buys']}\trounds={s['rounds']}\twins={s['wins']}\tlosses={s['losses']}\t"
                f"hard={s['hardStops']}\tladder={s['ladderStops']}\n"
            )
        f.write("--- miss vs A ---\n")
        for ln in missLines:
            f.write(ln + "\n")
    print("wrote", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
