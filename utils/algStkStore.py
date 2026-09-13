"""알고리즘별 관리종목(AI_ALG_STK) 조회·추가·삭제"""

from strategies.specLoader import normAlgCd, resolveAlgRef
from utils.algStkTables import ensureAlgStkTable
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 실매매 엔진이 아직 AI_WATCH_STK.USE_TRADE 를 읽는 코드
_LIVE_ALG = "MTS01"


def _resolveAlg(raw):
    text = str(raw or "").strip() or _LIVE_ALG
    try:
        got = resolveAlgRef(text)
        if got.get("ok") and got.get("algCd"):
            return got["algCd"]
    except Exception:
        pass
    return normAlgCd(text)


def listAlgStk(algCd, limit=300):
    """활성 관리종목. 없으면 []."""
    if not ensureAlgStkTable():
        return []
    alg = _resolveAlg(algCd)
    if not alg:
        return []
    # MSE01 ka10016 52주 신고가는 수백 종까지 올 수 있어 상한을 넉넉히 둔다
    lim = max(1, min(int(limit or 300), 2000))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ALG_CD, STK_CD, STK_NM, STATUS, SRC, NOTE
                FROM AI_ALG_STK
                WHERE ALG_CD=%s AND STATUS='Y'
                ORDER BY STK_NM ASC, STK_CD ASC
                LIMIT %s
                """,
                (alg, lim),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[algStk] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    items = []
    for r in rows:
        items.append(
            {
                "algCd": str(r.get("ALG_CD") or ""),
                "stkCd": str(r.get("STK_CD") or ""),
                "stkNm": r.get("STK_NM") or "",
                "src": r.get("SRC") or "",
                "note": r.get("NOTE") or "",
            }
        )
    return items


def upsertAlgStk(algCd, stkCd, stkNm="", src="", note=""):
    """관리종목 1건 활성 UPSERT. 반환: {ok, algCd, stkCd, errMsg?}"""
    if not ensureAlgStkTable():
        return {"ok": False, "errMsg": "AI_ALG_STK 없음"}
    alg = _resolveAlg(algCd)
    cd = normStkCd(stkCd)
    if not alg or not cd:
        return {"ok": False, "errMsg": "알고리즘코드/종목코드 필수"}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_ALG_STK (ALG_CD, STK_CD, STK_NM, STATUS, SRC, NOTE)
                VALUES (%s, %s, %s, 'Y', %s, %s)
                ON DUPLICATE KEY UPDATE
                  STK_NM = COALESCE(NULLIF(VALUES(STK_NM), ''), STK_NM),
                  STATUS = 'Y',
                  SRC = COALESCE(NULLIF(VALUES(SRC), ''), SRC),
                  NOTE = COALESCE(NULLIF(VALUES(NOTE), ''), NOTE)
                """,
                (alg, cd, stkNm or None, src or None, note or None),
            )
        conn.commit()
        return {"ok": True, "algCd": alg, "stkCd": cd, "stkNm": stkNm or ""}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algStk] 저장 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsertAlgStkRows(rows):
    """다건 UPSERT. rows: [{algCd, stkCd, stkNm, src, note}, ...]"""
    okCnt = 0
    for row in rows or []:
        got = upsertAlgStk(
            row.get("algCd"),
            row.get("stkCd"),
            stkNm=row.get("stkNm") or "",
            src=row.get("src") or "",
            note=row.get("note") or "",
        )
        if got.get("ok"):
            okCnt += 1
    return {"ok": True, "upsertCnt": okCnt}


def deactivateAlgStkExcept(algCd, keepCds):
    """keepCds 외 활성 관리종목을 전부 비활성. keep 이 비면 해당 ALG 전체 해제."""
    if not ensureAlgStkTable():
        return {"ok": False, "errMsg": "AI_ALG_STK 없음"}
    alg = _resolveAlg(algCd)
    if not alg:
        return {"ok": False, "errMsg": "알고리즘코드 필수"}
    keep = []
    seen = set()
    for raw in keepCds or []:
        cd = normStkCd(raw)
        if not cd or cd in seen:
            continue
        seen.add(cd)
        keep.append(cd)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if keep:
                placeholders = ",".join(["%s"] * len(keep))
                cur.execute(
                    f"""
                    UPDATE AI_ALG_STK SET STATUS='N'
                    WHERE ALG_CD=%s AND STATUS='Y'
                      AND STK_CD NOT IN ({placeholders})
                    """,
                    [alg] + keep,
                )
            else:
                cur.execute(
                    """
                    UPDATE AI_ALG_STK SET STATUS='N'
                    WHERE ALG_CD=%s AND STATUS='Y'
                    """,
                    (alg,),
                )
            n = cur.rowcount
        conn.commit()
        return {"ok": True, "algCd": alg, "deactCnt": n, "keepCnt": len(keep)}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algStk] 일괄 해제 실패 {alg}: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def deactivateAlgStk(algCd, stkCd):
    """관리종목 비활성."""
    if not ensureAlgStkTable():
        return {"ok": False, "errMsg": "AI_ALG_STK 없음"}
    alg = _resolveAlg(algCd)
    cd = normStkCd(stkCd)
    if not alg or not cd:
        return {"ok": False, "errMsg": "알고리즘코드/종목코드 필수"}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE AI_ALG_STK SET STATUS='N'
                WHERE ALG_CD=%s AND STK_CD=%s
                """,
                (alg, cd),
            )
            n = cur.rowcount
        conn.commit()
        return {"ok": True, "algCd": alg, "stkCd": cd, "deactCnt": n}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def addAlgManaged(algCd, stkCd, stkNm="", note="web"):
    """
    화면에서 관리종목 추가.
    MTS01 은 기존 실매매(USE_TRADE=Y)와도 맞춰 둔다.
    """
    alg = _resolveAlg(algCd)
    cd = normStkCd(stkCd)
    if not alg or not cd:
        return {"ok": False, "errMsg": "알고리즘코드/종목코드 필수"}
    got = upsertAlgStk(alg, cd, stkNm=stkNm, src="web", note=note)
    if not got.get("ok"):
        return got
    if alg == _LIVE_ALG:
        try:
            from utils.watchStkStore import addWatchForMon

            addWatchForMon(cd, stkNm=stkNm, note=note or "algStk")
        except Exception as err:
            termLog(f"[algStk] 관심 동기화 실패 {cd}: {err}")
    got["hint"] = f"{alg} 관리종목 추가"
    return got


def delAlgManaged(algCd, stkCd):
    """화면에서 관리종목 삭제. MTS01 은 관심 감시도 끈다."""
    alg = _resolveAlg(algCd)
    cd = normStkCd(stkCd)
    if not alg or not cd:
        return {"ok": False, "errMsg": "알고리즘코드/종목코드 필수"}
    got = deactivateAlgStk(alg, cd)
    if not got.get("ok"):
        return got
    if alg == _LIVE_ALG:
        try:
            from utils.watchStkStore import deactivateWatchStk

            deactivateWatchStk(cd, note="algStkDel")
        except Exception as err:
            termLog(f"[algStk] 관심 해제 실패 {cd}: {err}")
    return got
