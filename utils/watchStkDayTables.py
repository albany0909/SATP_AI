"""AI_WATCH_STK_DAY 테이블 보장"""

from pathlib import Path

from utils.dbConn import openDbConn
from utils.termLog import termLog

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_ensured = False


def _runSqlFile(path):
    text = path.read_text(encoding="utf-8")
    stmts = []
    buf = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("--") or s == "":
            continue
        buf.append(line)
        if s.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    if buf:
        stmts.append("\n".join(buf))

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def ensureWatchStkDayTable():
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "watchStkDay.sql")
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[watchStkDay] 테이블 생성 실패: {err}")
        return False
