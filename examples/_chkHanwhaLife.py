# -*- coding: utf-8 -*-
import json, re
from pathlib import Path
from utils.dbConn import openDbConn

# Find 한화생명 in day reviews / events
base = Path("config/data/algDayReview")
print("=== dayReview items mentioning 088350 ===")
for p in sorted(base.glob("*_MTS01.json")):
    dd = json.load(open(p, encoding="utf-8"))
    hits = []
    for it in dd.get("items") or []:
        if str(it.get("stkCd")) == "088350" or "한화생명" in str(it.get("stkNm") or ""):
            hits.append(it)
    for c in dd.get("closes") or []:
        if str(c.get("stkCd")) == "088350":
            hits.append({"CLOSE": c})
    if hits:
        print(p.name, len(hits))
        for h in hits[:15]:
            print(" ", h)

# AI_ALG_EVT
conn = openDbConn(); cur = conn.cursor()
cur.execute("SHOW TABLES LIKE '%ALG%EVT%'")
print("EVT TABLES", cur.fetchall())
cur.execute("SHOW TABLES LIKE '%ALG%'")
print("ALG TABLES", [r[list(r.keys())[0]] if isinstance(r, dict) else r[0] for r in cur.fetchall()])

# try AI_ALG_EVT
for tbl in ["AI_ALG_EVT", "AI_ALG_TRADE_HIST", "AI_ALG_STK"]:
    try:
        cur.execute(f"SHOW COLUMNS FROM {tbl}")
        cols = [r["Field"] for r in cur.fetchall()]
        print(f"\n{tbl} cols", cols[:30])
        if "STK_CD" in cols or "stk_cd" in [c.lower() for c in cols]:
            cur.execute(f"SELECT * FROM {tbl} WHERE STK_CD=%s ORDER BY 1 DESC LIMIT 20", ("088350",))
            rows = cur.fetchall()
            print(f"{tbl} 088350 rows", len(rows))
            for r in rows[:20]:
                print(r)
    except Exception as e:
        print(tbl, "ERR", e)

# log scrape for buy avg
log = Path("config/data/logs")
for lp in sorted(log.glob("SATP_202609*.log")) + sorted(log.glob("SATP_202608*.log")):
    try:
        text = lp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = lp.read_bytes().decode("cp949", errors="ignore")
    if "088350" in text or "한화생명" in text:
        lines = [ln for ln in text.splitlines() if "088350" in ln or "한화생명" in ln]
        if lines:
            print(f"\nLOG {lp.name} hits={len(lines)}")
            for ln in lines[:30]:
                print(ln[:220])

cur.close(); conn.close()
