"""AI_DESK_LOG 테이블 보장"""

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


def ensureDeskLogTable():
    """프로세스당 1회 생성·컬럼 보강."""
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "deskLog.sql")
        _ensureFeeCols()
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[deskLog] 테이블 생성 실패: {err}")
        return False


def _ensureFeeCols():
    """기존 DB에 CMSN/TAX 컬럼 추가."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for col, ddl in (
                (
                    "CMSN",
                    "ADD COLUMN CMSN INT NULL COMMENT '수수료(원) 00 FID 938' AFTER ORD_NO",
                ),
                (
                    "TAX",
                    "ADD COLUMN TAX INT NULL COMMENT '세금(원) 00 FID 939' AFTER CMSN",
                ),
            ):
                cur.execute(
                    """
                    SELECT COUNT(*) AS CNT
                      FROM information_schema.COLUMNS
                     WHERE TABLE_SCHEMA = DATABASE()
                       AND TABLE_NAME = 'AI_DESK_LOG'
                       AND COLUMN_NAME = %s
                    """,
                    (col,),
                )
                row = cur.fetchone() or {}
                cnt = int(row.get("CNT") or row.get("cnt") or 0)
                if cnt <= 0:
                    cur.execute(f"ALTER TABLE AI_DESK_LOG {ddl}")
        conn.commit()
    except Exception as err:
        termLog(f"[deskLog] CMSN/TAX 컬럼 보강 실패: {err}")
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
