"""AI_WATCH_STK 테이블 생성·컬럼 보장"""

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


def _ensureLoadDtColumn():
    """기존 테이블에 LOAD_DT 없으면 추가."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM AI_WATCH_STK LIKE 'LOAD_DT'")
            if cur.fetchone():
                return
            cur.execute(
                """
                ALTER TABLE AI_WATCH_STK
                  ADD COLUMN LOAD_DT CHAR(8) NULL
                    COMMENT '관심종목 적재일 YYYYMMDD'
                    AFTER REF_TRADE_DT
                """
            )
            cur.execute(
                """
                ALTER TABLE AI_WATCH_STK
                  ADD KEY IDX_AI_WATCH_STK_LOAD_DT (LOAD_DT)
                """
            )
            # 기존 행: 순위 기준일로 백필
            cur.execute(
                """
                UPDATE AI_WATCH_STK
                SET LOAD_DT = REF_TRADE_DT
                WHERE LOAD_DT IS NULL AND REF_TRADE_DT IS NOT NULL
                """
            )
        conn.commit()
        termLog("[watchStkTables] LOAD_DT 컬럼 추가 완료")
    finally:
        conn.close()


def ensureWatchStkTable():
    """관심종목 테이블 없으면 생성, LOAD_DT 보장 (프로세스당 1회)."""
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "watchStk.sql")
        _ensureLoadDtColumn()
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[watchStkTables] 생성 실패: {err}")
        return False
