# -*- coding: utf-8 -*-
"""Inspect why TDY excludes 한화; fix ORD_LEDGER; rebuild."""
from utils.dbConn import openDbConn
from utils.algTradeHist import sumTodaySellPnlDetail, resolveSellAvgPx, _ledgerAvgMap, listAlgEvt
from utils.algPnlDayStore import rebuildAlgPnlDay, listAlgPnlDay
import inspect

# debug ledger map
m = _ledgerAvgMap("MTS01", "20260922", mode="real")
print("ledgerAvgMap keys sample", list(m.items())[:20] if isinstance(m, dict) else m)
print("088350 in map", m.get("088350") if isinstance(m, dict) else None)
print("resolveSellAvgPx", resolveSellAvgPx("088350", "20260922", 0, m))

# show sell path manually via events
ev = listAlgEvt("MTS01", "20260922", limit=2000, mode="real") or []
for r in ev:
    if str(r.get("STK_CD")) == "088350" or str(r.get("EVT_TP")) in ("BUY","SELL"):
        if str(r.get("STK_CD")) in ("088350", "441270") or str(r.get("EVT_TP")) in ("BUY","SELL"):
            pass
sells = [r for r in ev if str(r.get("EVT_TP","")).upper()=="SELL"]
print("SELL events", len(sells))
for r in sells:
    print(r.get("STK_CD"), r.get("PX"), r.get("QTY"), r.get("REASON"))

print("sumToday", sumTodaySellPnlDetail("MTS01", "20260922", mode="real"))

# Fix ORD_LEDGER for 한화
conn = openDbConn(); cur = conn.cursor()
cur.execute(
    """
    SELECT TRADE_CD, STK_STS_CD, FIRST_BUY_DT, CLOSE_DT, BUY_QTY, SELL_QTY,
           BUY_AMT, SELL_AMT, BUY_PX, SELL_PX, CLOSE_PNL_PCT, MODE, FILLED_STEPS
    FROM AI_STRAT_ORD_LEDGER
    WHERE TRADE_CD='MTS0108835020260921092555'
    """
)
print("LEDGER before", cur.fetchone())

# Correct: buy 172 @ 5810, sell 172 @ 5700, closed
AVG, QTY, EXIT_PX = 5810.0, 172, 5700.0
BUY_AMT, SELL_AMT = AVG*QTY, EXIT_PX*QTY
PNL_PCT = -21170.0 / BUY_AMT * 100.0  # match LIVE net? or price-only
# Use net pct from LIVE
PNL_PCT = -2.118441

cur.execute(
    """
    UPDATE AI_STRAT_ORD_LEDGER SET
      STK_STS_CD='9',
      CLOSE_DT='20260922',
      BUY_QTY=%s,
      SELL_QTY=%s,
      BUY_AMT=%s,
      SELL_AMT=%s,
      BUY_PX=%s,
      SELL_PX=%s,
      CLOSE_PNL_PCT=%s
    WHERE TRADE_CD='MTS0108835020260921092555' AND STK_CD='088350'
    """,
    (QTY, QTY, BUY_AMT, SELL_AMT, AVG, EXIT_PX, PNL_PCT),
)
print("ledger update", cur.rowcount)
conn.commit()
cur.execute("SELECT TRADE_CD, STK_STS_CD, FIRST_BUY_DT, CLOSE_DT, BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, BUY_PX, SELL_PX, CLOSE_PNL_PCT FROM AI_STRAT_ORD_LEDGER WHERE TRADE_CD='MTS0108835020260921092555'")
print("LEDGER after", cur.fetchone())
cur.close(); conn.close()

print("sumToday after ledger fix", sumTodaySellPnlDetail("MTS01", "20260922", mode="real"))
print("rebuild", rebuildAlgPnlDay(mode="real", tradeDt="20260922", algCd="MTS01"))
print("PNL_DAY", listAlgPnlDay(mode="real", tradeDt="20260922", algCd="MTS01"))
