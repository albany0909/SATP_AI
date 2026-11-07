# -*- coding: utf-8 -*-
from utils.dbConn import openDbConn
from decimal import Decimal

conn = openDbConn()
cur = conn.cursor()

print("=== LIVE_TRADE 088350 한화생명 ===")
cur.execute(
    """
    SELECT TRADE_ID, MODE, STK_CD, STK_NM, ENTRY_DT, EXIT_DT, EXIT_TM, EXIT_REASON,
           BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, AVG_BUY_PX, EXIT_PX, PNL_WON, PNL_PCT,
           HALF_YN, STEPS, NOTE, CREATED_AT
    FROM AI_STRAT_LIVE_TRADE
    WHERE ALG_CD='MTS01' AND STK_CD='088350'
    ORDER BY TRADE_ID DESC LIMIT 10
    """
)
for r in cur.fetchall() or []:
    print(r)

print("\n=== LIVE_TRADE exits 20260922 ===")
cur.execute(
    """
    SELECT TRADE_ID, MODE, STK_CD, STK_NM, ENTRY_DT, EXIT_DT, EXIT_TM, EXIT_REASON,
           BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, AVG_BUY_PX, EXIT_PX, PNL_WON, PNL_PCT
    FROM AI_STRAT_LIVE_TRADE
    WHERE ALG_CD='MTS01' AND MODE='real' AND EXIT_DT='20260922'
    ORDER BY EXIT_TM
    """
)
for r in cur.fetchall() or []:
    print(r)

print("\n=== Recompute Sept MON from LIVE_TRADE round closes ===")
# Use project's sumRoundWl if possible
cur.execute(
    """
    SELECT EXIT_DT,
           COUNT(*) CNT,
           SUM(PNL_WON) PNL,
           SUM(CASE WHEN PNL_WON>0 THEN 1 ELSE 0 END) W,
           SUM(CASE WHEN PNL_WON<0 THEN 1 ELSE 0 END) L
    FROM AI_STRAT_LIVE_TRADE
    WHERE ALG_CD='MTS01' AND MODE='real'
      AND EXIT_DT BETWEEN '20260901' AND '20260930'
      AND SELL_QTY>0 AND BUY_QTY>0
      AND SELL_QTY >= BUY_QTY
    GROUP BY EXIT_DT
    ORDER BY EXIT_DT
    """
)
rows = cur.fetchall() or []
tot = Decimal("0")
for r in rows:
    print(r)
    tot += Decimal(str(r["PNL"] or 0))
print("SUM PNL Sept (SELL_QTY>=BUY_QTY):", tot)

print("\n=== All Sept exits detail with null/zero avg ===")
cur.execute(
    """
    SELECT EXIT_DT, STK_CD, STK_NM, EXIT_REASON, BUY_QTY, SELL_QTY,
           BUY_AMT, SELL_AMT, AVG_BUY_PX, EXIT_PX, PNL_WON, PNL_PCT, ENTRY_DT
    FROM AI_STRAT_LIVE_TRADE
    WHERE ALG_CD='MTS01' AND MODE='real'
      AND EXIT_DT BETWEEN '20260901' AND '20260930'
    ORDER BY EXIT_DT, EXIT_TM
    """
)
for r in cur.fetchall() or []:
    flag = ""
    if r["AVG_BUY_PX"] in (None, 0) or r["PNL_PCT"] in (None, 0) and float(r["PNL_WON"] or 0) > 100000:
        flag = " <<SUSPECT"
    print(dict(r), flag)

# open positions
print("\n=== Open / partial ===")
cur.execute(
    """
    SELECT STK_CD, STK_NM, ENTRY_DT, EXIT_DT, BUY_QTY, SELL_QTY, AVG_BUY_PX, PNL_WON, NOTE
    FROM AI_STRAT_LIVE_TRADE
    WHERE ALG_CD='MTS01' AND MODE='real'
      AND (EXIT_DT IS NULL OR EXIT_DT='' OR SELL_QTY < BUY_QTY)
    ORDER BY ENTRY_DT DESC LIMIT 20
    """
)
for r in cur.fetchall() or []:
    print(r)

cur.close(); conn.close()
