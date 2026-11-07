import json
from pathlib import Path
base = Path(r"config/data/algDayReview")
with open(base / "20260922_MTS01.json", "r", encoding="utf-8") as f:
    d = json.load(f)
print("=== TODAY summary ===")
print(json.dumps(d["summary"], ensure_ascii=False, indent=2))
print("=== TODAY pnlSnap ===")
print(json.dumps(d["pnlSnap"], ensure_ascii=False, indent=2))
print("=== TODAY closes ===")
for c in d["closes"]:
    keys = ["stkCd","stkNm","exitReason","pnlWon","pnlPct","avgBuyPx","exitPx","maxPnlPct","exitTm","halfYn"]
    print({k: c.get(k) for k in keys})

print("=== SEPT day-by-day ===")
sum_day = 0
sum_closes = 0
last_mon = None
for p in sorted(base.glob("202609*_MTS01.json")):
    dd = json.load(open(p, encoding="utf-8"))
    s = dd.get("summary") or {}
    day = s.get("dayPnlWon")
    snap = dd.get("pnlSnap") or {}
    closes = dd.get("closes") or []
    close_sum = sum((c.get("pnlWon") or 0) for c in closes)
    bad = [c for c in closes if c.get("avgBuyPx") in (None, 0)]
    print(f"{p.name}: dayPnlWon={day} closesSum={close_sum} nClose={len(closes)} snap.tdy={snap.get('tdyPnl')} snap.mon={snap.get('monPnl')} snap.tot={snap.get('totPnl')} badAvg={len(bad)}")
    if isinstance(day, (int, float)):
        sum_day += day
    sum_closes += close_sum
    last_mon = snap.get("monPnl")
    # print bad closes detail
    for c in bad:
        print("  BAD", c.get("stkNm"), c.get("exitReason"), "pnlWon", c.get("pnlWon"), "pnlPct", c.get("pnlPct"), "avg", c.get("avgBuyPx"), "exit", c.get("exitPx"))

print("SUM summary.dayPnlWon Sept=", sum_day)
print("SUM closes.pnlWon Sept=", sum_closes)
print("LAST snap monPnl=", last_mon)
