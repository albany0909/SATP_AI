"""AI_BT_RUN / AI_BT_TRADE 테이블 생성"""

from pathlib import Path

import pymysql

from config import dbConfig

sqlPath = Path(__file__).resolve().parent / "btRun.sql"
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
        for name in ("AI_BT_RUN", "AI_BT_TRADE"):
            cur.execute("SHOW TABLES LIKE %s", (name,))
            exists = cur.fetchone() is not None
            print(f"{name}: {'EXISTS' if exists else 'MISSING'}")

        for stmt in stmts:
            # 이미 있으면 CREATE 실패 — 존재 시 스킵
            tableName = None
            if "CREATE TABLE AI_BT_RUN" in stmt:
                tableName = "AI_BT_RUN"
            elif "CREATE TABLE AI_BT_TRADE" in stmt:
                tableName = "AI_BT_TRADE"
            if tableName:
                cur.execute("SHOW TABLES LIKE %s", (tableName,))
                if cur.fetchone():
                    print(f"SKIP create {tableName}")
                    continue
            cur.execute(stmt)
            print(f"CREATED via stmt ({tableName or 'SQL'})")

        for name in ("AI_BT_RUN", "AI_BT_TRADE"):
            cur.execute(f"DESCRIBE {name}")
            cols = [r[0] for r in cur.fetchall()]
            print(f"{name} columns({len(cols)}): {', '.join(cols)}")
    conn.commit()
    print("COMMIT_OK")
finally:
    conn.close()
