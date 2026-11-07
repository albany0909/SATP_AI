# -*- coding: utf-8 -*-
"""Patch today's dayReview closes/summary/pnlSnap to match fixed ledger."""
import json
from pathlib import Path
from utils.algPnlDayStore import listAlgPnlDay
from utils.dbConn import openDbConn

path = Path("config/data/algDayReview/20260922_MTS01.json")
d = json.load(open(path, encoding="utf-8"))

# fix 한화 close
for c in d.get("closes") or []:
    if c.get("stkCd") == "088350":
        c["avgBuyPx"] = 5810.0
        c["exitPx"] = 5700.0
        c["pnlWon"] = -21170.0
        c["pnlPct"] = -2.118441
        c["exitReason"] = c.get("exitReason") or "STOP"
        print("patched close", c)

# recompute summary from closes
closes = d.get("closes") or []
dayPnl = sum(float(c.get("pnlWon") or 0) for c in closes)
wins = sum(1 for c in closes if float(c.get("pnlWon") or 0) > 0)
losses = sum(1 for c in closes if float(c.get("pnlWon") or 0) < 0)
buyCost = 0.0
for c in closes:
    avg = c.get("avgBuyPx")
    # approximate from pnl/pct if needed - use LIVE amounts
buyCost = 999320.0 + 998280.0
s = d.setdefault("summary", {})
s["dayPnlWon"] = dayPnl
s["dayRetPct"] = (dayPnl / buyCost * 100.0) if buyCost else 0.0
s["winCnt"] = wins
s["lossCnt"] = losses
s["winRate"] = (wins / len(closes) * 100.0) if closes else 0.0
s["closeCnt"] = len(closes)
print("summary", {k: s[k] for k in ["dayPnlWon","dayRetPct","winCnt","lossCnt","winRate","closeCnt"]})

# pnlSnap from DB
rows = listAlgPnlDay(mode="real", tradeDt="20260922", algCd="MTS01") or []
if rows:
    r = rows[0]
    d["pnlSnap"] = {
        "tdyPnl": float(r.get("TDY_PNL") or 0),
        "tdyPnlRt": float(r.get("TDY_PNL_RT") or 0),
        "tdyWin": int(r.get("TDY_WIN") or 0),
        "tdyLoss": int(r.get("TDY_LOSS") or 0),
        "monPnl": float(r.get("MON_PNL") or 0),
        "monPnlRt": float(r.get("MON_PNL_RT") or 0),
        "totPnl": float(r.get("TOT_PNL") or 0),
        "totPnlRt": float(r.get("TOT_PNL_RT") or 0),
    }
    print("pnlSnap", d["pnlSnap"])

path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", path)

# final verify LIVE + PNL
conn = openDbConn(); cur = conn.cursor()
cur.execute("SELECT STK_NM, PNL_WON, PNL_PCT, AVG_BUY_PX, BUY_QTY FROM AI_STRAT_LIVE_TRADE WHERE EXIT_DT='20260922' AND ALG_CD='MTS01' AND MODE='real'")
print("LIVE today", cur.fetchall())
cur.execute("SELECT TDY_PNL, MON_PNL, TOT_PNL, TDY_WIN, TDY_LOSS, TDY_CNT, MON_CNT FROM AI_ALG_PNL_DAY WHERE MODE='real' AND ALG_CD='MTS01' AND TRADE_DT='20260922'")
print("PNL_DAY", cur.fetchone())
cur.close(); conn.close()
