"""AI_ALG_VER_HIST — 알고리즘 버전 변경 이력."""

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


def ensureAlgVerHistTable():
    global _ensured
    if _ensured:
        return True
    try:
        _runSqlFile(_SQL_DIR / "algVerHist.sql")
        _ensured = True
        return True
    except Exception as err:
        termLog(f"[algVerHist] 테이블 생성 실패: {err}")
        return False


def upsertAlgVerHist(
    algCd,
    algVer,
    ymd,
    note,
    stratId=None,
    gitHash=None,
    src="manual",
):
    """버전 1건 upsert. (ALG_CD, ALG_VER) 유니크."""
    ensureAlgVerHistTable()
    cd = str(algCd or "").strip().upper()[:5]
    try:
        ver = int(algVer)
    except (TypeError, ValueError):
        return {"ok": False, "errMsg": "algVer 정수 필요"}
    day = str(ymd or "").strip()[:10]
    text = str(note or "").strip()[:500]
    if not cd or not day or not text:
        return {"ok": False, "errMsg": "algCd/ymd/note 필요"}
    sid = (str(stratId).strip()[:40] if stratId else None) or None
    gh = (str(gitHash).strip()[:16] if gitHash else None) or None
    srcV = str(src or "manual").strip()[:16] or "manual"
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_ALG_VER_HIST
                  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  STRAT_ID=COALESCE(VALUES(STRAT_ID), STRAT_ID),
                  YMD=VALUES(YMD),
                  NOTE=VALUES(NOTE),
                  GIT_HASH=COALESCE(VALUES(GIT_HASH), GIT_HASH),
                  SRC=VALUES(SRC)
                """,
                (cd, sid, ver, day, text, gh, srcV),
            )
        conn.commit()
        return {"ok": True, "algCd": cd, "algVer": ver}
    except Exception as err:
        try:
            conn.rollback()
        except Exception:
            pass
        termLog(f"[algVerHist] upsert 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def listAlgVerHistRows(algCd=None, limit=500):
    """DB 이력 최신 ver 우선. [{algCd,stratId,ver,ymd,note,gitHash,src}]"""
    ensureAlgVerHistTable()
    cd = str(algCd or "").strip().upper()[:5]
    lim = max(1, min(int(limit or 500), 2000))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            if cd:
                cur.execute(
                    """
                    SELECT ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC
                    FROM AI_ALG_VER_HIST
                    WHERE ALG_CD=%s
                    ORDER BY ALG_VER DESC
                    LIMIT %s
                    """,
                    (cd, lim),
                )
            else:
                cur.execute(
                    """
                    SELECT ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC
                    FROM AI_ALG_VER_HIST
                    ORDER BY ALG_CD ASC, ALG_VER DESC
                    LIMIT %s
                    """,
                    (lim,),
                )
            rows = cur.fetchall() or []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for r in rows:
        out.append(
            {
                "algCd": r.get("ALG_CD"),
                "stratId": r.get("STRAT_ID"),
                "ver": int(r["ALG_VER"]) if r.get("ALG_VER") is not None else None,
                "ymd": r.get("YMD") or "",
                "note": r.get("NOTE") or "",
                "gitHash": r.get("GIT_HASH") or "",
                "src": r.get("SRC") or "",
            }
        )
    return out


def syncAlgVerHistFromDoc(algCd=None):
    """문서 변경 이력 → DB upsert. 반환 {ok, upserted, algs}."""
    from strategies.specLoader import listStratSpecs, loadStratSpec, normAlgCd
    from utils.stratVerHist import _mdPath, parseDocVerHist

    ensureAlgVerHistTable()
    want = normAlgCd(algCd) if algCd else ""
    n = 0
    touched = []
    for s in listStratSpecs() or []:
        cd = s.get("algCd") or ""
        if not cd:
            continue
        if want and cd != want:
            continue
        sid = s.get("stratId") or ""
        spec = loadStratSpec(sid) or {}
        path = _mdPath(spec) or _mdPath(s)
        if path is None:
            continue
        try:
            hist = parseDocVerHist(path.read_text(encoding="utf-8"))
        except Exception as err:
            termLog(f"[algVerHist] doc parse 실패 {cd}: {err}")
            continue
        for row in hist:
            ver = row.get("ver")
            if ver is None:
                continue
            got = upsertAlgVerHist(
                cd,
                ver,
                row.get("ymd"),
                row.get("note"),
                stratId=sid,
                src="doc_sync",
            )
            if got.get("ok"):
                n += 1
        touched.append(cd)
    return {"ok": True, "upserted": n, "algs": touched}
