# -*- coding: utf-8 -*-
"""MMG01 / MTS01 v64 단위·시뮬 테스트 (실주문·DB쓰기·텔레그램 없음).  python tests/testMmg01.py"""
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILS = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))
    if not cond:
        FAILS.append(name)


# ── 합성 일봉: 완만한 상승 → 박스 → 거래량 돌파 → 급등 → 급락
def mkBars():
    bars = []
    d0 = datetime(2025, 9, 1)
    px = 10000.0
    k = 0
    def add(c, vol=1000000, hi=None, lo=None, op=None):
        nonlocal k
        dt = d0 + timedelta(days=k)
        k += 1
        o = op if op is not None else c * 0.995
        bars.append({"dt": dt.strftime("%Y%m%d"), "open": o, "high": hi or max(o, c) * 1.005,
                     "low": lo or min(o, c) * 0.995, "close": c, "vol": vol})
    for i in range(240):
        px *= 1.0015
        add(px)
    box = px
    for i in range(15):
        add(box * (1 + 0.01 * math.sin(i)))
    return bars, add, box


def main():
    import trading.manualMgrLive as mm
    import utils.botOrdRegistry as reg
    from utils.mmgSignals import calcInd, updateStops, eodExitReason, addPlan, intradayStopHit, DEFAULTS

    # ── 1. 지표·신호
    bars, add, box = mkBars()
    add(box * 1.03, vol=4000000)  # 20일 고가 돌파 + 거래량 4배
    ind = calcInd(bars)
    check("signal E2/E4 on volume breakout", ind and ind["brk"] and bool(ind["sigTags"]), str(ind and ind["sigTags"]))
    check("stage2 true in uptrend", ind["stage2"])

    # ── 2. 래칫·청산 순수함수
    pos = {"avg": 10000.0, "qty": 10, "hi": 16000.0, "stop": 9200.0, "nAdds": 2}
    fake = {"close": 15600.0, "high": 16000.0, "s50": 14000.0, "s150": 12000.0, "atr": 300.0, "stage2": True}
    updateStops(pos, fake)
    check("ratchet stop = max(avg, hi*0.75) after +50%", abs(pos["stop"] - 12000.0) < 1e-6, f"{pos['stop']}")
    fake2 = dict(fake, close=15000.0)
    check("chandelier exit (close < hi-3ATR)", eodExitReason(pos, fake2) == "EXIT_CHAND")
    fake3 = dict(fake, close=13900.0)
    check("SMA50 break exit", eodExitReason(pos, fake3) == "EXIT_MA50")
    check("intraday stop only after add", intradayStopHit(pos, 11999) and not intradayStopHit(dict(pos, nAdds=0), 11999))
    pos2 = {"avg": 10000.0, "qty": 1, "cost": 10000.0, "nAdds": 0, "entryPx": 10000.0, "lastAddPx": 10000.0, "stop": 9200, "s2": True}
    ind2 = {"close": 10600.0, "s50": 9800.0, "s50Ago5": 9700.0, "brk": True, "pb": False, "atr": 200.0, "stage2": True}
    ctx = {"maxBuyAmt": 3_000_000, "equity": 10_000_000, "exposure": 0, "maxExposurePct": 60, "haltAdds": False, "barsSinceAdd": 3}
    ap, why = addPlan(pos2, ind2, ctx)
    # 트랜치 40% × 3,000,000 = 1,200,000 / 10,600 = 113주, 위험 0.5%×1천만/(10600−(10600−400)) = 125주 → 113주
    check("add tranche 40% of max", ap and ap["qty"] == 113, f"{ap} {why}")
    ap2, why2 = addPlan(dict(pos2), dict(ind2, close=10400.0), ctx)
    check("no add below +5%", ap2 is None and why2 == "GAIN_LT_MIN", why2)
    ap3, why3 = addPlan(dict(pos2), ind2, dict(ctx, haltAdds=True))
    check("daily loss halts adds", ap3 is None and why3 == "DAILY_LOSS_HALT")
    ap4, why4 = addPlan(dict(pos2), ind2, dict(ctx, maxBuyAmt=1_000_000))
    check("tranche scales with maxBuyAmt setting", ap4 and ap4["qty"] == int(400_000 // 10600), f"{ap4}")
    ap5, _ = addPlan(dict(pos2, nAdds=3), ind2, ctx)
    check("max 3 adds", ap5 is None)

    # ── 3. 시뮬: 수동정찰 편입 → 추가 → 래칫 → 청산 (모의 주문 경로)
    orders = []
    def fakeOrder(cd, side, qty, trdeTp, ordUv):
        orders.append((cd, side, qty, trdeTp, ordUv))
        return {"ok": True, "ordNo": f"9{len(orders):05d}"}
    clock = {"t": datetime(2026, 10, 12, 15, 15)}
    series = {"cd": "123450"}
    mm._injected.clear()
    mm._injected.update({
        "noDb": True, "noDisk": True, "noTelegram": True, "noSleep": True, "quietHold": True,
        "session": ("http://paper", "tok", "bearer"), "order": fakeOrder, "equity": 10_000_000,
        "regime": True, "earn": lambda cd: True, "now": lambda: clock["t"],
        "pool": {"123450": {"nm": "TEST", "src": "USER", "note": "", "expYmd": ""}},
        "params": {"_enabled": True, "maxBuyAmt": 3_000_000.0, "autoScout": False},
    })
    mm._state = mm._emptyState()
    mm._params = None
    reg.clearForTest()
    simBars = list(bars[:-1])  # 돌파 전까지
    def barsFn(cd):
        return [dict(b) for b in simBars]
    def liveFn(cd):
        b = simBars[-1]
        return {"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "qty": b["vol"]}
    mm._injected["bars"] = barsFn
    mm._injected["live"] = liveFn

    def newDay(c, vol=1000000, hi=None):
        clock["t"] = clock["t"] + timedelta(days=1)
        o = c * 0.995
        simBars.append({"dt": clock["t"].strftime("%Y%m%d"), "open": o, "high": hi or c * 1.005, "low": o * 0.995, "close": c, "vol": vol})
        mm._barMem.clear()
        mm._params = None

    # 3-1 봇 주문(타전략)은 편입 안 함
    reg.noteInflight("123450", "BUY")
    mm.onBrokerFill("매수", "123450", 1, 10000, "55555")
    check("bot-order fill not adopted by MMG01", not mm.holdsMmg("123450"))
    reg.clearForTest()
    # 3-2 풀 아닌 종목 수동매수 무시
    mm.onBrokerFill("매수", "999990", 1, 5000, "77777")
    check("non-pool manual fill ignored", not mm.holdsMmg("999990"))
    # 3-3 풀 종목 수동 1주 정찰 → 편입
    simBars[-1]["dt"] = (clock["t"]).strftime("%Y%m%d")
    scoutPx = simBars[-1]["close"]
    mm.onBrokerFill("매수", "123450", 1, scoutPx, "12345")
    check("manual scout adopted", mm.holdsMmg("123450") and mm.listMmgPositions()["123450"]["qty"] == 1)
    # 3-4 상승 → +5% 이상 돌파일에 ADD
    added = False
    c = scoutPx
    for i in range(12):
        c *= 1.012
        vol = 4000000 if i in (3, 8) else 1000000
        hh = max(b["high"] for b in simBars[-20:])
        if i in (3, 8):
            c = max(c, hh * 1.01)
        newDay(c, vol=vol)
        plan = mm.decideEod()
        if any(p["act"] == "ADD" for p in plan):
            done = mm.executePlan()
            for d in done:
                if d["act"] == "ADD":
                    mm.onBrokerFill("매수", "123450", d["qty"], c, d["ordNo"])
                    added = True
    pos = mm.listMmgPositions().get("123450", {})
    check("pyramid add executed (paper path, limit order)", added and pos.get("nAdds", 0) >= 1, f"nAdds={pos.get('nAdds')} qty={pos.get('qty')}")
    check("orders are limit (trde_tp 0) with ordUv", orders and all(o[3] == "0" and o[4] for o in orders), str(orders[:3]))
    check("position cost ≤ max buy amt", pos.get("cost", 0) <= 3_000_000 + c, f"{pos.get('cost')}")
    # 3-5 +50% 까지 상승 → 래칫 손절
    for i in range(40):
        c *= 1.015
        newDay(c)
        plan = mm.decideEod()
        done = mm.executePlan()
        for d in done:
            if d["act"] == "ADD":
                mm.onBrokerFill("매수", "123450", d["qty"], c, d["ordNo"])
        pos = mm.listMmgPositions().get("123450")
        if pos and c / pos["avg"] - 1 >= 0.5:
            break
    pos = mm.listMmgPositions().get("123450")
    if pos:
        check("ratchet engaged after +50%", pos["stop"] >= pos["hi"] * 0.75 - 1 and pos["stop"] >= pos["avg"], f"stop={pos['stop']:.0f} hi={pos['hi']:.0f} avg={pos['avg']:.0f}")
    # 3-6 급락 → EXIT 판단 → 매도체결 → 청산·재진입금지
    newDay(c * 0.80)
    plan = mm.decideEod()
    ex = [p for p in plan if p["act"] == "EXIT"]
    check("EOD exit decided on breakdown", bool(ex), str(plan))
    done = mm.executePlan()
    for d in done:
        if d["act"] == "EXIT":
            mm.onBrokerFill("매도", "123450", d["qty"], c * 0.80, d["ordNo"])
    check("position closed + round recorded", not mm.holdsMmg("123450") and mm._injected.get("closed"), str(mm._injected.get("closed", [])[:1])[:120])
    check("60-day re-entry ban recorded", mm._st()["lastExit"].get("123450") == clock["t"].strftime("%Y%m%d"))
    from utils.mmgSignals import entryAllowed
    okE, whyE = entryAllowed("123450", dict(ind, close=10000), {"regimeOk": True, "earnOk": True, "daysSinceExit": 5})
    check("re-entry banned within 60d", not okE and whyE == "REENTRY_BAN")
    okP, whyP = entryAllowed("123450", dict(ind, close=350000), {"regimeOk": True, "earnOk": True, "daysSinceExit": None})
    check("scout price cap 300,000", not okP and whyP == "PX_GT_CAP")

    # ── 4. 자동정찰 (하루 2종목 한도)
    mm._state = mm._emptyState()
    mm._params = None
    orders.clear()
    pool = {f"10000{i}": {"nm": f"T{i}", "src": "SCR", "note": "", "expYmd": ""} for i in range(4)}
    mm._injected["pool"] = pool
    mm._injected["params"]["autoScout"] = True
    full = list(bars)
    mm._injected["bars"] = lambda cd: [dict(b) for b in full[:-1]]
    mm._injected["live"] = lambda cd: {k: full[-1][k] for k in ("open", "high", "low", "close")} | {"qty": full[-1]["vol"]}
    clock["t"] = datetime.strptime(full[-1]["dt"], "%Y%m%d").replace(hour=15, minute=15)
    plan = mm.decideEod()
    sc = [p for p in plan if p["act"] == "SCOUT"]
    check("auto scout max 2/day, 1 share", len(sc) == 2 and all(p["qty"] == 1 for p in sc), str(sc))
    mm.executePlan()
    check("auto scout orders sent via paper path", len(orders) == 2 and all(o[1] == "BUY" and o[2] == 1 for o in orders), str(orders))
    mm._injected["regime"] = False
    mm._state = mm._emptyState()
    plan = mm.decideEod()
    check("index filter blocks auto scout", not [p for p in plan if p["act"] == "SCOUT"])

    # ── 5. MTS01 v64 대기복구 가드
    import trading.maTouchLive as mt
    mt._pos.pop("111110", None)
    check("v64: manual fill (unknown ordNo) not MTS01", not mt._mtsOwnsBuyFill("111110", "44444"))
    mt._noteMtsBuyOrd("0004321", "111110")
    check("v64: bot ordNo match → MTS01", mt._mtsOwnsBuyFill("111110", "4321"))
    mt._mtsOrdNos.clear()
    import time as _t
    mt._mtsBuyInflight["111110"] = _t.time()
    check("v64: recent MTS01 buy inflight → MTS01", mt._mtsOwnsBuyFill("111110", ""))
    mt._mtsBuyInflight.clear()
    mt._pos["111110"] = {"qty": 5, "tradeCd": "X"}
    check("v64: already-managed MTS01 stock → MTS01", mt._mtsOwnsBuyFill("111110", "1"))
    mt._pos.pop("111110", None)
    orig = mt._isEnabled
    mt._isEnabled = lambda: True
    calls = []
    origSave = mt._saveState
    mt._saveState = lambda *a, **k: None
    origReg = mt._registerOrdWait
    mt._registerOrdWait = lambda *a, **k: calls.append(a) or "k"
    try:
        mt.onBrokerFill("매수", "111110", 1, 10000, "44444")
    finally:
        mt._isEnabled = orig
        mt._registerOrdWait = origReg
        mt._saveState = origSave
    check("v64: onBrokerFill skips recovery for manual fill", not calls and "111110" not in mt._pos)

    # ── 6. 설정 필드
    import config.mmgSettings as ms
    import config.settingsStore as ss
    mem = {}
    o1, o2 = ss.loadSettings, ss.patchSettings
    ss.loadSettings = lambda: dict(mem)
    ss.patchSettings = lambda up: mem.update(up)
    try:
        check("settings default 3,000,000", ms.getMmgMaxBuyAmt() == 3_000_000)
        ok, v = ms.setMmgMaxBuyAmt("5,000,000원")
        check("settings save '5,000,000원'", ok and mem.get(ms.KEY_MAX_BUY_AMT) == 5_000_000 and ms.getMmgMaxBuyAmt() == 5_000_000)
        ok, v = ms.setMmgMaxBuyAmt("300만")
        check("settings save '300만'", ok and ms.getMmgMaxBuyAmt() == 3_000_000)
        for bad in ("abc", "-100", "0", ""):
            ok, v = ms.setMmgMaxBuyAmt(bad)
            check(f"settings reject {bad!r}", not ok and ms.getMmgMaxBuyAmt() == 3_000_000, str(v))
        check("fmtWon", ms.fmtWon(3000000) == "3,000,000원")
        mm._params = None
        mm._injected["params"].pop("maxBuyAmt", None)
        mem[ms.KEY_MAX_BUY_AMT] = 2_000_000
        check("MMG01 reads maxBuyAmt from settings", mm._loadParams()["maxBuyAmt"] == 2_000_000)
    finally:
        ss.loadSettings, ss.patchSettings = o1, o2
    real = ss.loadSettings()
    check("real settings.json loads (key optional)", isinstance(real, dict), f"value={real.get(ms.KEY_MAX_BUY_AMT, '(default 3,000,000)')}")

    # ── 7. 가드: STLS·교차전략
    import trading.crossAlgBuyGuard as cg
    mm._injected["pool"] = {"222220": {"nm": "P", "src": "USER", "note": "", "expYmd": ""}}
    mm._params = None
    b1 = cg.isNewBuyBlocked("MTS01", "222220")
    b2 = cg.isNewBuyBlocked("MSE01", "222220")
    check("MTS01/MSE01 skip MMG01 pool stock", b1 == (True, "MMG01_POOL") and b2 == (True, "MMG01_POOL"), f"{b1} {b2}")
    from telegram.commands import stlsCommand
    msg = stlsCommand.handleStls("start")
    check("STLS start blocked while MMG01 active", "MMG01" in msg, msg[:60])
    mm._injected.clear()
    print("\nRESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAIL: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
