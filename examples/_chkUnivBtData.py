# -*- coding: utf-8 -*-
"""데이터 가용성 + 라이브 손익 스냅샷."""
from utils.dbConn import openDbConn

conn = openDbConn()
cur = conn.cursor()
cur.execute("SHOW COLUMNS FROM AI_DAY_STK_PRC")
cols = [r["Field"] for r in cur.fetchall()]
print("DAY_STK_PRC cols with AMT/TRDE:", [c for c in cols if "AMT" in c or "TRDE" in c or "PRICA" in c])

cur.execute(
    """
    SELECT TRADE_DT, COUNT(*) AS n
    FROM AI_DAY_STK_PRC
    WHERE INDC_TP='0' AND TRADE_DT BETWEEN %s AND %s
    GROUP BY TRADE_DT ORDER BY TRADE_DT
    """,
    ("20260801", "20260910"),
)
rows = cur.fetchall() or []
print(f"dayBar days={len(rows)} minN={min(r['n'] for r in rows) if rows else 0} maxN={max(r['n'] for r in rows) if rows else 0}")
print("first3", rows[:3], "last3", rows[-3:])

cur.execute(
    """
    SELECT TRADE_DT, COUNT(*) AS n, MAX(RANK_NO) AS mx
    FROM AI_DAY_INQ_RANK
    WHERE TRADE_DT BETWEEN %s AND %s
    GROUP BY TRADE_DT ORDER BY TRADE_DT
    """,
    ("20260801", "20260910"),
)
inq = cur.fetchall() or []
print(f"inq days={len(inq)}")
print("inq sample", [(r["TRADE_DT"], r["n"], r["mx"]) for r in inq[:5]], "...", [(r["TRADE_DT"], r["n"], r["mx"]) for r in inq[-3:]])

for mode in ("paper", "real"):
    cur.execute(
        """
        SELECT COUNT(*) AS n,
               SUM(IFNULL(PNL_WON,0)) AS pnl,
               SUM(CASE WHEN IFNULL(PNL_WON,0)>0 THEN 1 ELSE 0 END) AS w
        FROM AI_STRAT_LIVE_TRADE
        WHERE ALG_CD=%s AND MODE=%s AND EXIT_DT BETWEEN %s AND %s
        """,
        ("MTS01", mode, "20260801", "20260910"),
    )
    print(f"LIVE {mode}", cur.fetchone())

conn.close()
