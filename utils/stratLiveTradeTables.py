"""AI_STRAT_LIVE_TRADE 테이블 보장"""

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


def _colNames(cur):
    cur.execute("SHOW COLUMNS FROM AI_STRAT_LIVE_TRADE")
    return {str(row.get("Field") or "") for row in (cur.fetchall() or [])}


def _idxNames(cur):
    cur.execute("SHOW INDEX FROM AI_STRAT_LIVE_TRADE")
    return {str(row.get("Key_name") or "") for row in (cur.fetchall() or [])}


def _sidToAlgMap():
    """구 STRAT_ID(파일명) → 알고리즘코드."""
    try:
        from strategies.specLoader import listStratSpecs

        return {
            s.get("stratId"): s.get("algCd")
            for s in listStratSpecs()
            if s.get("stratId") and s.get("algCd")
        }
    except Exception:
        return {"maTouchScale_v1": "MTS01"}


def _migrateAlgCd(cur):
    """기존 테이블에 ALG_CD 를 넣고 파일명 행을 코드로 채운다."""
    cols = _colNames(cur)
    if "ALG_CD" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_LIVE_TRADE "
            "ADD COLUMN ALG_CD CHAR(5) NOT NULL DEFAULT '' "
            "COMMENT '알고리즘코드 (MTS01)' AFTER TRADE_ID"
        )
    for sid, alg in _sidToAlgMap().items():
        cur.execute(
            "UPDATE AI_STRAT_LIVE_TRADE SET ALG_CD=%s "
            "WHERE (ALG_CD='' OR ALG_CD IS NULL) AND STRAT_ID=%s",
            (alg, sid),
        )
    # 공백/변형 파일명 잔여
    cur.execute(
        "UPDATE AI_STRAT_LIVE_TRADE SET ALG_CD='MTS01' "
        "WHERE (ALG_CD='' OR ALG_CD IS NULL) AND STRAT_ID LIKE 'maTouchScale%'"
    )
    idxs = _idxNames(cur)
    if "IDX_AI_STRAT_LIVE_ALG_DT" not in idxs:
        cur.execute(
            "ALTER TABLE AI_STRAT_LIVE_TRADE "
            "ADD KEY IDX_AI_STRAT_LIVE_ALG_DT (ALG_CD, EXIT_DT)"
        )
    cols = _colNames(cur)
    if "MAX_PNL_PCT" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_LIVE_TRADE "
            "ADD COLUMN MAX_PNL_PCT DECIMAL(12,6) NULL "
            "COMMENT '매수후 청산전 최대수익률%(고가/평단)'"
        )
    if "MAX_PX" not in cols:
        cur.execute(
            "ALTER TABLE AI_STRAT_LIVE_TRADE "
            "ADD COLUMN MAX_PX DECIMAL(18,4) NULL "
            "COMMENT '보유기간 고가'"
        )


def ensureStratLiveTradeTable():
    """프로세스당 1회 생성·ALG_CD 보정."""
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "stratLiveTrade.sql")
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                _migrateAlgCd(cur)
            conn.commit()
        finally:
            conn.close()
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[stratLiveTrade] 테이블 생성/변경 실패: {err}")
        return False
