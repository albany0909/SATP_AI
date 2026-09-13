"""관심종목 순위 테이블 생성·시드 (AI_RANK_COND / AI_RANK_COND_ITEM / AI_RANK_SCORE)"""

from pathlib import Path

from utils.dbConn import openDbConn
from utils.termLog import termLog

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
_ensured = False


def _runSqlFile(path):
    """세미콜론으로 끝나는 줄 단위로 문장을 잘라 순차 실행 (프로젝트 공통 방식)."""
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


def ensureRankScoreTables():
    """
    순위 조건·점수 테이블 보장 (프로세스당 1회).
    조건셋 001(수급_이평_테마_RSI)은 INSERT IGNORE 시드라 기존 설정을 덮지 않는다.
    """
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "rankScore.sql")
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[rankScoreTables] 생성 실패: {err}")
        return False
