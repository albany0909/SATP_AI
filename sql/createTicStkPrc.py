"""AI_TIC_STK_PRC 생성 + 스모크 적재"""
from pathlib import Path

import pymysql

from api.getTicStkPrc import fetchTicStkPrc
from api.getToken import issueAccToken
from config import dbConfig
from config.config import real_app_key, real_app_secret, real_host_url
from utils.ticStkPrcStore import upsertTicStkPrcRows

sqlPath = Path(__file__).resolve().parent / "ticStkPrc.sql"
sqlText = sqlPath.read_text(encoding="utf-8")
stmts = []
buf = []
for line in sqlText.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("--"):
        continue
    buf.append(line)
    if stripped.endswith(";"):
        stmts.append("\n".join(buf))
        buf = []

conn = pymysql.connect(
    host=dbConfig.dbHost,
    port=int(dbConfig.dbPort),
    user=dbConfig.dbUser,
    password=dbConfig.dbPassword,
    database=dbConfig.dbName,
    charset=dbConfig.dbCharset,
)
try:
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'AI_TIC_STK_PRC'")
        if cur.fetchone():
            print("ALREADY_EXISTS")
        else:
            for stmt in stmts:
                cur.execute(stmt)
            print("CREATED")
        cur.execute("DESCRIBE AI_TIC_STK_PRC")
        for row in cur.fetchall():
            print(row[0], row[1])
    conn.commit()
finally:
    conn.close()

issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
print("token", issued.get("ok"), issued.get("errMsg"))
if not issued.get("ok"):
    raise SystemExit(1)

fetched = fetchTicStkPrc(
    real_host_url,
    issued["token"],
    "005930",
    ticScope="1",
    startDt="20260731",
    endDt="20260731",
    tokenType=issued.get("tokenType") or "bearer",
    isPaper=False,
    maxPages=5,
)
print(
    "ok",
    fetched.get("ok"),
    "err",
    fetched.get("errMsg"),
    "cnt",
    fetched.get("rowCnt"),
    "page",
    fetched.get("pageUsed"),
)
rows = fetched.get("rows") or []
if rows:
    print(
        "first",
        rows[0]["CNTR_TM"],
        rows[0]["TIC_SEQ"],
        rows[0]["CLOSE_PRC"],
        rows[0]["TRDE_QTY"],
    )
    print("last", rows[-1]["CNTR_TM"], rows[-1]["TIC_SEQ"])
    print(upsertTicStkPrcRows(rows))
