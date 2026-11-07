"""AI_ALG_STK 테이블 보장 + 기존 USE_TRADE=Y → MTS01 이관"""

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


def _migrateFromWatchTrade(cur):
    """
    이평터치 실매매 ON 종목을 MTS01 관리종목으로 복사.
    이미 있는 행은 덮어쓰지 않는다.
    """
    cur.execute(
        """
        INSERT INTO AI_ALG_STK (ALG_CD, STK_CD, STK_NM, STATUS, SRC, NOTE)
        SELECT 'MTS01', w.STK_CD, w.STK_NM, 'Y',
               COALESCE(NULLIF(w.SRC,''), 'watch'),
               'from USE_TRADE=Y'
        FROM AI_WATCH_STK w
        WHERE w.MKT_TP='KR' AND w.STATUS='Y' AND w.USE_TRADE='Y'
          AND CHAR_LENGTH(w.STK_CD)=6
          AND NOT EXISTS (
            SELECT 1 FROM AI_ALG_STK a
            WHERE a.ALG_CD='MTS01' AND a.STK_CD=w.STK_CD
          )
        """
    )


def ensureAlgStkTable():
    """프로세스당 1회 생성·이관. client 는 시도하지 않음."""
    global _ensured
    if _ensured:
        return True
    try:
        from utils.dbConn import isLocalDbEnabled

        if not isLocalDbEnabled():
            return False
    except Exception:
        pass
    try:
        _runSqlFile(_SQL_DIR / "algStk.sql")
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                _migrateFromWatchTrade(cur)
            conn.commit()
        finally:
            conn.close()
        _ensured = True
        return True
    except Exception as err:
        try:
            from utils.dbConn import DbDisabledError, DbDownError

            if isinstance(err, (DbDisabledError, DbDownError)):
                return False
        except Exception:
            pass
        termLog(f"[algStk] 테이블 생성/이관 실패: {err}")
        return False
