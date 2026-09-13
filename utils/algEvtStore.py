"""알고리즘 복기 이벤트. 항상 상용 ALG_CD(+버전)에 붙인다."""

from strategies.specLoader import getAlgCd, loadStratSpec, resolveAlgRef
from utils.algEvtTables import ensureAlgEvtTable
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog


def _resolveEvtMode(mode=None):
    """paper|real. 미지정이면 deskTradeMode."""
    raw = str(mode or "").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    if raw in ("paper", "mock", "모의"):
        return "paper"
    try:
        from utils.tradeSession import getDeskTradeMode

        return getDeskTradeMode()
    except Exception:
        return "paper"


def resolveAlgMeta(ref):
    """
    참조 → 상용 키 {algCd, algVer, name}.
    규칙이 바뀌면 스펙 version 을 올리고 새 이벤트만 새 버전에 쌓인다.
    """
    got = resolveAlgRef(ref or "MTS01")
    if not got.get("ok"):
        return {"ok": False, "errMsg": got.get("errMsg") or "알고리즘 없음"}
    sid = got.get("stratId") or ""
    spec = loadStratSpec(sid) or {}
    algCd = got.get("algCd") or getAlgCd(sid)
    ver = str(spec.get("version") or "1").strip() or "1"
    return {
        "ok": True,
        "algCd": algCd,
        "algVer": ver[:8],
        "name": spec.get("name") or got.get("name") or algCd,
        "stratId": sid,
    }


def insertAlgEvt(row):
    """
    복기 1행. 주문 경로를 막지 않도록 실패해도 예외를 올리지 않는다.
    row: algCd(또는 구 파일명), evtTp, stkCd, reason, px, sma{5,10,15,20},
         qty, tradeCd, note, mode, tradeDt, evtTm
    """
    if not ensureAlgEvtTable():
        return {"ok": False, "errMsg": "AI_ALG_EVT 없음"}
    meta = resolveAlgMeta(row.get("algCd") or row.get("stratId") or "MTS01")
    if not meta.get("ok"):
        return meta
    cd = normStkCd(row.get("stkCd"))
    if not cd:
        return {"ok": False, "errMsg": "종목코드 없음"}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_ALG_EVT (
                  ALG_CD, ALG_VER, TRADE_DT, EVT_TM, EVT_TP, REASON,
                  STK_CD, STK_NM, TRADE_CD, MODE,
                  PX, SMA5, SMA10, SMA15, SMA20, QTY, NOTE
                ) VALUES (
                  %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    meta["algCd"],
                    row.get("algVer") or meta["algVer"],
                    str(row.get("tradeDt") or "")[:8],
                    str(row.get("evtTm") or "000000").zfill(6)[-6:],
                    str(row.get("evtTp") or "")[:16],
                    (row.get("reason") or None),
                    cd,
                    row.get("stkNm") or None,
                    row.get("tradeCd") or None,
                    _resolveEvtMode(row.get("mode")),
                    row.get("px"),
                    row.get("sma5"),
                    row.get("sma10"),
                    row.get("sma15"),
                    row.get("sma20"),
                    row.get("qty"),
                    row.get("note") or None,
                ),
            )
            evtId = cur.lastrowid
        conn.commit()
        try:
            from utils.tradeLogHook import emitTradeLogRow

            payload = dict(row or {})
            payload["algCd"] = meta["algCd"]
            payload["stkCd"] = cd
            emitTradeLogRow(payload)
        except Exception:
            pass
        return {"ok": True, "evtId": evtId, "algCd": meta["algCd"]}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algEvt] 저장 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listAlgEvt(algCd, tradeDt, limit=500, mode=None, evtTps=None):
    """
    하루·알고리즘코드 이벤트 (시간순). mode 지정 시 paper|real 만.
    evtTps: 이벤트 타입 목록이면 해당 타입만 (거래탭 주문정보용).
    """
    if not ensureAlgEvtTable():
        return []
    meta = resolveAlgMeta(algCd)
    code = meta.get("algCd") if meta.get("ok") else str(algCd or "").strip().upper()
    ymd = str(tradeDt or "").replace("-", "")[:8]
    if not code or len(ymd) != 8:
        return []
    lim = max(1, min(int(limit or 500), 2000))
    md = None
    if mode is not None:
        md = _resolveEvtMode(mode)
    tpList = []
    if evtTps:
        for t in evtTps:
            s = str(t or "").strip().upper()
            if s:
                tpList.append(s)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            where = ["ALG_CD=%s", "TRADE_DT=%s"]
            args = [code, ymd]
            if md:
                if md == "paper":
                    where.append("(MODE=%s OR MODE IS NULL OR MODE='')")
                else:
                    where.append("MODE=%s")
                args.append(md)
            if tpList:
                ph = ",".join(["%s"] * len(tpList))
                where.append(f"EVT_TP IN ({ph})")
                args.extend(tpList)
            args.append(lim)
            cur.execute(
                f"""
                SELECT EVT_ID, ALG_CD, ALG_VER, TRADE_DT, EVT_TM, EVT_TP, REASON,
                       STK_CD, STK_NM, TRADE_CD, MODE,
                       PX, SMA5, SMA10, SMA15, SMA20, QTY, NOTE
                FROM AI_ALG_EVT
                WHERE {' AND '.join(where)}
                ORDER BY EVT_TM ASC, EVT_ID ASC
                LIMIT %s
                """,
                tuple(args),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[algEvt] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listBuyAlgMap(tradeDt, algCds, mode=None):
    """
    당일 BUY 종목 → [algCd].
    원장 INSERT 가 체결보다 늦거나 빠진 날, 매매창 행번호 색을 보강한다.
    """
    from utils.stkCdUtil import normStkCd

    ymd = str(tradeDt or "").replace("-", "")[:8]
    out = {}
    if len(ymd) != 8:
        return out
    for alg in algCds or []:
        code = str(alg or "").strip().upper()
        if not code:
            continue
        try:
            rows = listAlgEvt(code, ymd, limit=500, mode=mode) or []
        except Exception:
            rows = []
        for row in rows:
            tp = str(row.get("EVT_TP") or row.get("evtTp") or "").upper()
            if tp != "BUY":
                continue
            cd = normStkCd(row.get("STK_CD") or row.get("stkCd"))
            algCd = str(row.get("ALG_CD") or row.get("algCd") or code).strip().upper()
            if not cd or not algCd:
                continue
            lst = out.setdefault(cd, [])
            if algCd not in lst:
                lst.append(algCd)
    return out


def listAlgEvtDts(algCd, limit=60):
    """해당 알고리즘에 이벤트가 있는 일자 (최신순)."""
    if not ensureAlgEvtTable():
        return []
    meta = resolveAlgMeta(algCd)
    code = meta.get("algCd") if meta.get("ok") else str(algCd or "").strip().upper()
    if not code:
        return []
    lim = max(1, min(int(limit or 60), 180))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, COUNT(*) AS CNT
                FROM AI_ALG_EVT
                WHERE ALG_CD=%s
                GROUP BY TRADE_DT
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (code, lim),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[algEvt] 일자 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listAlgCdsWithEvt():
    """이력이 있는 알고리즘코드."""
    if not ensureAlgEvtTable():
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ALG_CD, MAX(TRADE_DT) AS LAST_DT, COUNT(*) AS CNT
                FROM AI_ALG_EVT
                GROUP BY ALG_CD
                ORDER BY LAST_DT DESC
                """
            )
            return cur.fetchall() or []
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
