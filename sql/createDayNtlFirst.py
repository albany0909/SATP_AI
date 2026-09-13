"""AI_DAY_NTL_FIRST 테이블 생성"""

from pathlib import Path

import pymysql

from config import dbConfig

sqlPath = Path(__file__).resolve().parent / "dayNtlFirst.sql"
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
        cur.execute("SHOW TABLES LIKE %s", ("AI_DAY_NTL_FIRST",))
        if cur.fetchone():
            print("AI_DAY_NTL_FIRST: ALREADY_EXISTS")
        else:
            for stmt in stmts:
                cur.execute(stmt)
            print("AI_DAY_NTL_FIRST: CREATED")
        cur.execute("DESCRIBE AI_DAY_NTL_FIRST")
        cols = [r[0] for r in cur.fetchall()]
        print("columns:", ", ".join(cols))
    conn.commit()
    print("COMMIT_OK")
finally:
    conn.close()
