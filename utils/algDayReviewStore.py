"""알고리즘 당일 복기 DB 적재·조회 (매매일지)"""

from __future__ import annotations

import json

from utils.algDayReviewTables import ensureAlgDayReviewTable
from utils.dbConn import openDbConn
from utils.termLog import termLog


def _normMode(mode):
    raw = str(mode or "").strip().lower()
    if raw in ("real", "live", "실전"):
        return "real"
    return "paper"


def _normYmd(raw):
    return str(raw or "").replace("-", "").strip()[:8]


def _normAlg(raw):
    return str(raw or "").strip().upper()[:5]


def _parseGenAt(report):
    text = str((report or {}).get("generatedAt") or "").strip()
    if not text:
        return None
    return text.replace(".", "-")[:19]


def _rowToSummary(row):
    out = {
        "mode": row.get("MODE") or "paper",
        "tradeDt": row.get("TRADE_DT") or "",
        "algCd": row.get("ALG_CD") or "",
        "algVer": row.get("ALG_VER") or "",
        "name": row.get("ALG_NM") or "",
        "closeCnt": int(row.get("CLOSE_CNT") or 0),
        "winCnt": int(row.get("WIN_CNT") or 0),
        "lossCnt": int(row.get("LOSS_CNT") or 0),
        "winRate": float(row.get("WIN_RATE") or 0),
        "dayPnlWon": float(row.get("DAY_PNL_WON") or 0),
        "dayRetPct": float(row.get("DAY_RET_PCT") or 0),
        "buyCnt": int(row.get("BUY_CNT") or 0),
        "skipCnt": int(row.get("SKIP_CNT") or 0),
        "sellCnt": int(row.get("SELL_CNT") or 0),
        "generatedAt": (
            row.get("GENERATED_AT").strftime("%Y-%m-%d %H:%M:%S")
            if row.get("GENERATED_AT") is not None
            and hasattr(row.get("GENERATED_AT"), "strftime")
            else str(row.get("GENERATED_AT") or "")
        ),
    }
    # 매매일지·달력 팝업에서 문제/개선·MFE 요약을 바로 보여 주기 위함
    try:
        rep = json.loads(row.get("REPORT_JSON") or "{}")
    except Exception:
        rep = {}
    if isinstance(rep, dict):
        ins = rep.get("insights") or {}
        ht = rep.get("halfTp") or {}
        out["insights"] = {
            "problems": ins.get("problems") or [],
            "keeps": ins.get("keeps") or [],
            "improves": ins.get("improves") or [],
        }
        out["halfTp"] = {
            "applicable": bool(ht.get("applicable")),
            "halfTpNow": ht.get("halfTpNow"),
            "dayLines": ht.get("dayLines") or [],
            "histLines": ht.get("histLines") or [],
            "today": ht.get("today") or {},
            "hist": {
                "n": (ht.get("hist") or {}).get("n"),
                "suggestPct": (ht.get("hist") or {}).get("suggestPct"),
                "sampleOk": (ht.get("hist") or {}).get("sampleOk"),
            },
        }
    return out


def _rowToReport(row):
    if not row:
        return None
    try:
        rep = json.loads(row.get("REPORT_JSON") or "{}")
    except Exception:
        rep = {}
    if not isinstance(rep, dict):
        rep = {}
    rep.setdefault("ok", True)
    rep.setdefault("algCd", row.get("ALG_CD") or "")
    rep.setdefault("tradeDt", row.get("TRADE_DT") or "")
    rep.setdefault("name", row.get("ALG_NM") or "")
    rep.setdefault("algVer", row.get("ALG_VER") or "")
    return rep


def upsertAlgDayReview(report, mode="paper"):
    """복기 JSON 1건 upsert. {ok, tradeDt, algCd} 반환."""
    if not report or not report.get("ok"):
        return {"ok": False, "errMsg": "invalid report"}
    if not ensureAlgDayReviewTable():
        return {"ok": False, "errMsg": "AI_ALG_DAY_REVIEW 없음"}

    md = _normMode(mode)
    ymd = _normYmd(report.get("tradeDt"))
    alg = _normAlg(report.get("algCd"))
    if not ymd or not alg:
        return {"ok": False, "errMsg": "tradeDt/algCd 없음"}

    s = report.get("summary") or {}
    body = json.dumps(report, ensure_ascii=False)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_ALG_DAY_REVIEW (
                  MODE, TRADE_DT, ALG_CD, ALG_VER, ALG_NM,
                  CLOSE_CNT, WIN_CNT, LOSS_CNT, WIN_RATE,
                  DAY_PNL_WON, DAY_RET_PCT, BUY_CNT, SKIP_CNT, SELL_CNT,
                  REPORT_JSON, GENERATED_AT
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s
                )
                ON DUPLICATE KEY UPDATE
                  ALG_VER=VALUES(ALG_VER),
                  ALG_NM=VALUES(ALG_NM),
                  CLOSE_CNT=VALUES(CLOSE_CNT),
                  WIN_CNT=VALUES(WIN_CNT),
                  LOSS_CNT=VALUES(LOSS_CNT),
                  WIN_RATE=VALUES(WIN_RATE),
                  DAY_PNL_WON=VALUES(DAY_PNL_WON),
                  DAY_RET_PCT=VALUES(DAY_RET_PCT),
                  BUY_CNT=VALUES(BUY_CNT),
                  SKIP_CNT=VALUES(SKIP_CNT),
                  SELL_CNT=VALUES(SELL_CNT),
                  REPORT_JSON=VALUES(REPORT_JSON),
                  GENERATED_AT=VALUES(GENERATED_AT)
                """,
                (
                    md,
                    ymd,
                    alg,
                    report.get("algVer") or None,
                    report.get("name") or None,
                    int(s.get("closeCnt") or 0),
                    int(s.get("winCnt") or 0),
                    int(s.get("lossCnt") or 0),
                    float(s.get("winRate") or 0),
                    float(s.get("dayPnlWon") or 0),
                    float(s.get("dayRetPct") or 0),
                    int(s.get("buyCnt") or 0),
                    int(s.get("skipCnt") or 0),
                    int(s.get("sellCnt") or 0),
                    body,
                    _parseGenAt(report),
                ),
            )
        conn.commit()
        return {"ok": True, "mode": md, "tradeDt": ymd, "algCd": alg}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algDayReview] DB 저장 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def getAlgDayReview(tradeDt, algCd, mode="paper"):
    """일자·알고리즘 복기 전문. 없으면 None."""
    if not ensureAlgDayReviewTable():
        return None
    ymd = _normYmd(tradeDt)
    alg = _normAlg(algCd)
    if not ymd or not alg:
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MODE, TRADE_DT, ALG_CD, ALG_VER, ALG_NM,
                       CLOSE_CNT, WIN_CNT, LOSS_CNT, WIN_RATE,
                       DAY_PNL_WON, DAY_RET_PCT, BUY_CNT, SKIP_CNT, SELL_CNT,
                       REPORT_JSON, GENERATED_AT, CREATED_AT
                FROM AI_ALG_DAY_REVIEW
                WHERE MODE=%s AND TRADE_DT=%s AND ALG_CD=%s
                """,
                (_normMode(mode), ymd, alg),
            )
            row = cur.fetchone()
        return _rowToReport(row)
    except Exception as err:
        termLog(f"[algDayReview] DB 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listAlgDayReviewSummaries(tradeDt=None, algCd=None, mode="paper", limit=120):
    """매매일지용 요약 목록 (신→구)."""
    if not ensureAlgDayReviewTable():
        return []
    lim = max(1, min(int(limit or 120), 500))
    where = ["MODE=%s"]
    args = [_normMode(mode)]
    if tradeDt:
        where.append("TRADE_DT=%s")
        args.append(_normYmd(tradeDt))
    if algCd:
        where.append("ALG_CD=%s")
        args.append(_normAlg(algCd))
    sql = f"""
        SELECT MODE, TRADE_DT, ALG_CD, ALG_VER, ALG_NM,
               CLOSE_CNT, WIN_CNT, LOSS_CNT, WIN_RATE,
               DAY_PNL_WON, DAY_RET_PCT, BUY_CNT, SKIP_CNT, SELL_CNT,
               REPORT_JSON, GENERATED_AT
        FROM AI_ALG_DAY_REVIEW
        WHERE {' AND '.join(where)}
        ORDER BY TRADE_DT DESC, ALG_CD ASC
        LIMIT %s
    """
    args.append(lim)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() or []
        return [_rowToSummary(r) for r in rows]
    except Exception as err:
        termLog(f"[algDayReview] 목록 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
