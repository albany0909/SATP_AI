# -*- coding: utf-8 -*-
from utils.dbConn import openDbConn

ALG, MODE = "MTS01", "real"
conn = openDbConn()
cur = conn.cursor()

cur.execute("SHOW COLUMNS FROM AI_ALG_PNL_DAY")
print("PNL_DAY COLS:")
for r in cur.fetchall():
    print(r)

cur.execute(
    """
    SELECT *
    FROM AI_ALG_PNL_DAY
    WHERE MODE=%s AND ALG_CD=%s AND TRADE_DT BETWEEN '20260901' AND '20260930'
    ORDER BY TRADE_DT
    """,
    (MODE, ALG),
)
rows = cur.fetchall() or []
print("SEPT ROWS", len(rows))
for r in rows:
    print({k: r[k] for k in r if k in (
        "TRADE_DT","TDY_PNL","TDY_PNL_RT","TDY_WIN","TDY_LOSS","TDY_CNT",
        "MON_PNL","MON_PNL_RT","TOT_PNL","TOT_PNL_RT","REG_DT","MOD_DT","UPD_TM"
    ) or True})

cur.execute("SHOW COLUMNS FROM AI_STRAT_LIVE_TRADE")
print("\nLIVE_TRADE COLS:")
for r in cur.fetchall():
    print(r[0] if not isinstance(r, dict) else r.get("Field"), r[1] if not isinstance(r, dict) else r.get("Type"))

cur.close(); conn.close()
