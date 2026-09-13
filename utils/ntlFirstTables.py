"""AI_DAY_NTL_FIRST 테이블 생성 보장"""

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


def ensureNtlFirstTable():
    """최초52신고 적재 테이블 없으면 생성 (프로세스당 1회)."""
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "dayNtlFirst.sql")
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[ntlFirstTables] 생성 실패: {err}")
        return False
