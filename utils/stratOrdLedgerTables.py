"""AI_STRAT_ORD_LEDGER 테이블 보장 + 자리수 마이그레이션"""

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


def _colMap(cur):
    cur.execute("SHOW COLUMNS FROM AI_STRAT_ORD_LEDGER")
    out = {}
    for row in cur.fetchall() or []:
        out[str(row.get("Field") or "")] = str(row.get("Type") or "").lower()
    return out


def _idxNames(cur):
    cur.execute("SHOW INDEX FROM AI_STRAT_ORD_LEDGER")
    names = set()
    for row in cur.fetchall() or []:
        names.add(str(row.get("Key_name") or ""))
    return names


def _migrateLedgerWidth(cur):
    """
    기존 테이블을 5/6/25 자리로 맞춘다.
    CREATE IF NOT EXISTS 는 이미 있는 테이블을 바꾸지 않는다.
    """
    cols = _colMap(cur)
    if "ALG_CD" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_ORD_LEDGER "
            "ADD COLUMN ALG_CD CHAR(5) NOT NULL DEFAULT '' "
            "COMMENT '알고리즘코드 영문3+버전2 (MTS01)' AFTER TRADE_CD"
        )
        cols = _colMap(cur)
    if "char(25)" not in (cols.get("TRADE_CD") or ""):
        cur.execute(
            "ALTER TABLE AI_STRAT_ORD_LEDGER "
            "MODIFY TRADE_CD CHAR(25) NOT NULL "
            "COMMENT '거래코드=ALG_CD5+종목6+일시분초14'"
        )
    if "char(6)" not in (cols.get("STK_CD") or ""):
        cur.execute(
            "ALTER TABLE AI_STRAT_ORD_LEDGER "
            "MODIFY STK_CD CHAR(6) NOT NULL COMMENT '종목코드 6자리'"
        )
    idxs = _idxNames(cur)
    if "IDX_AI_STRAT_ORD_OPEN" in idxs:
        cur.execute("ALTER TABLE AI_STRAT_ORD_LEDGER DROP INDEX IDX_AI_STRAT_ORD_OPEN")
    if "IDX_AI_STRAT_ORD_STS" in idxs:
        cur.execute("ALTER TABLE AI_STRAT_ORD_LEDGER DROP INDEX IDX_AI_STRAT_ORD_STS")
    cur.execute(
        "ALTER TABLE AI_STRAT_ORD_LEDGER "
        "ADD KEY IDX_AI_STRAT_ORD_OPEN (ALG_CD, STK_CD, STK_STS_CD)"
    )
    cur.execute(
        "ALTER TABLE AI_STRAT_ORD_LEDGER "
        "ADD KEY IDX_AI_STRAT_ORD_STS (STK_STS_CD, ALG_CD)"
    )
    cols = _colMap(cur)
    if "MAX_PNL_PCT" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_ORD_LEDGER "
            "ADD COLUMN MAX_PNL_PCT DECIMAL(12,6) NULL "
            "COMMENT '매수후 청산전 최대수익률%(고가/평단)'"
        )
    if "MAX_PX" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_ORD_LEDGER "
            "ADD COLUMN MAX_PX DECIMAL(18,4) NULL "
            "COMMENT '보유기간 고가'"
        )


def ensureStratOrdLedgerTable():
    """프로세스당 1회 생성·자리수 보정."""
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
        _runSqlFile(_SQL_DIR / "stratOrdLedger.sql")
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                _migrateLedgerWidth(cur)
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
        termLog(f"[stratOrdLedger] 테이블 생성/변경 실패: {err}")
        return False
