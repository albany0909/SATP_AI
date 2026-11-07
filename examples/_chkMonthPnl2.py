# -*- coding: utf-8 -*-
import json
from pathlib import Path
from utils.dbConn import openDbConn

ALG = "MTS01"
MODE = "real"
base = Path("config/data/algDayReview")

print("=== dayReview closes Sept ===")
sum_all = sum_ok = 0
for p in sorted(base.glob("202609*_MTS01.json")):
    dd = json.load(open(p, encoding="utf-8"))
    closes = dd.get("closes") or []
    day_all = day_ok = 0
    for c in closes:
        pw = float(c.get("pnlWon") or 0)
        day_all += pw
        if c.get("avgBuyPx") not in (None, 0, ""):
            day_ok += pw
        else:
            print(f"  EXCLUDE {p.name} {c.get('stkNm')} pnlWon={pw} avg={c.get('avgBuyPx')} exit={c.get('exitPx')} pct={c.get('pnlPct')}")
    sum_all += day_all
    sum_ok += day_ok
    print(f"{p.name}: all={day_all:,.0f} okAvg={day_ok:,.0f} n={len(closes)}")
print(f"SEPT dayReview all={sum_all:,.0f} okAvgOnly={sum_ok:,.0f}")

conn = openDbConn()
cur = conn.cursor(dictionary=True)
print("\n=== AI_ALG_PNL_DAY Sept ===")
cur.execute(
    """
    SELECT TRADE_DT, TDY_PNL, TDY_WIN, TDY_LOSS, TDY_CNT,
           MON_PNL, TOT_PNL, UPD_DT
    FROM AI_ALG_PNL_DAY
    WHERE MODE=%s AND ALG_CD=%s AND TRADE_DT BETWEEN '20260901' AND '20260930'
    ORDER BY TRADE_DT
    """,
    (MODE, ALG),
)
rows = cur.fetchall() or []
for r in rows:
    print(r)
print("count", len(rows))

cur.execute(
    """
    SELECT TRADE_DT, TDY_PNL, MON_PNL, TOT_PNL, UPD_DT
    FROM AI_ALG_PNL_DAY
    WHERE MODE=%s AND ALG_CD=%s
    ORDER BY TRADE_DT DESC LIMIT 8
    """,
    (MODE, ALG),
)
print("LATEST", cur.fetchall())

print("\n=== LIVE_TRADE columns ===")
cur.execute("SHOW COLUMNS FROM AI_STRAT_LIVE_TRADE")
cols = [c["Field"] for c in cur.fetchall()]
print(cols)

cur.close()
conn.close()
