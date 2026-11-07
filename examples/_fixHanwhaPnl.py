# -*- coding: utf-8 -*-
"""Fix 한화생명 LIVE_TRADE and rebuild MTS01 PNL day."""
from utils.dbConn import openDbConn
from utils.algPnlDayStore import rebuildAlgPnlDay, listAlgPnlDay
from utils.heroPnl import heroRealizedPnl, loadDeskFeeBuckets, takeSellFees

AVG = 5810.0
QTY = 172
EXIT_PX = 5700.0
ENTRY_DT = "20260921"
EXIT_DT = "20260922"
BUY_AMT = AVG * QTY
SELL_AMT = EXIT_PX * QTY

feeBuckets = loadDeskFeeBuckets(EXIT_DT, mode="real") or {}
bucket = feeBuckets.get("088350")
print("fee bucket 088350:", bucket)
buyCmsn, sellCmsn, sellTax = 0.0, 0.0, 0.0
if bucket:
    buyCmsn, sellCmsn, sellTax = takeSellFees(bucket, QTY, sellPx=EXIT_PX, buyPx=AVG)
print("fees buyCmsn/sellCmsn/sellTax", buyCmsn, sellCmsn, sellTax)

detail = heroRealizedPnl(AVG, EXIT_PX, QTY, buyCmsn=buyCmsn, sellCmsn=sellCmsn, sellTax=sellTax)
print("hero detail", detail)
pnl_won = float(detail.get("netPnl"))
pnl_pct = float(detail.get("pnlRt") or 0)  # may already be ratio not pct
# check: pnlRt -1.89 looks like percent already from earlier wrong call; inspect
if abs(pnl_pct) < 1:
    pnl_pct = pnl_pct * 100.0
# Actually from try2 pnlRt was -1.89 as percent-like. From correct call:
print("netPnl", pnl_won, "pnlRt raw", detail.get("pnlRt"), "evalRt", detail.get("evalRt"))
# Use evalRt or pnlRt as stored elsewhere - check 파인엠텍 PNL_PCT was -1.053813 (percent)
rt = detail.get("pnlRt")
if rt is None:
    pnl_pct = pnl_won / BUY_AMT * 100.0
else:
    pnl_pct = float(rt)
    if abs(pnl_pct) <= 1.0 and abs(pnl_won) > 100:
        # could be fraction
        if abs(pnl_won / BUY_AMT - pnl_pct) < 0.01:
            pnl_pct = pnl_pct * 100.0
print(f"FINAL pnl_won={pnl_won} pnl_pct={pnl_pct}")

conn = openDbConn()
cur = conn.cursor()
cur.execute("SELECT TRADE_ID, BUY_QTY, BUY_AMT, AVG_BUY_PX, PNL_WON, PNL_PCT, ENTRY_DT, NOTE FROM AI_STRAT_LIVE_TRADE WHERE TRADE_ID=98")
print("BEFORE", cur.fetchone())
note = f"fixed carryover avg={AVG:g}; net={pnl_won:+.0f}"
cur.execute(
    """
    UPDATE AI_STRAT_LIVE_TRADE SET
      ENTRY_DT=%s, BUY_QTY=%s, SELL_QTY=%s, BUY_AMT=%s, SELL_AMT=%s,
      AVG_BUY_PX=%s, EXIT_PX=%s, PNL_WON=%s, PNL_PCT=%s, NOTE=%s
    WHERE TRADE_ID=98 AND STK_CD='088350' AND EXIT_DT='20260922'
    """,
    (ENTRY_DT, QTY, QTY, BUY_AMT, SELL_AMT, AVG, EXIT_PX, pnl_won, pnl_pct, note),
)
print("UPDATE rows", cur.rowcount)
conn.commit()
cur.execute("SELECT * FROM AI_STRAT_LIVE_TRADE WHERE TRADE_ID=98")
print("AFTER", cur.fetchone())

# ORD ledger
cur.execute(
    """
    SELECT TRADE_CD, STK_STS_CD, FIRST_BUY_DT, CLOSE_DT, BUY_QTY, SELL_QTY,
           BUY_AMT, SELL_AMT, BUY_PX, SELL_PX, CLOSE_PNL_PCT, MODE
    FROM AI_STRAT_ORD_LEDGER
    WHERE STK_CD='088350' AND ALG_CD='MTS01'
    ORDER BY TRADE_CD DESC LIMIT 5
    """
)
print("LEDGER:")
for r in cur.fetchall() or []:
    print(r)
cur.close(); conn.close()

print("\n=== rebuild ===")
print(rebuildAlgPnlDay(mode="real", tradeDt="20260922", algCd="MTS01"))
print("PNL_DAY", listAlgPnlDay(mode="real", tradeDt="20260922", algCd="MTS01"))

# Also verify sumTodaySellPnlDetail
from utils.algTradeHist import sumTodaySellPnlDetail
print("tdySellDetail", sumTodaySellPnlDetail("MTS01", "20260922", mode="real"))
