"""
이평 터치 일별 요약 적재·조회 (AI_ALG_TOUCH_SUM).

터치는 틱 이벤트라 건마다 남기면 양이 커진다. 종목·매차별로 하루 1행을 두고
횟수·첫/마지막 시각만 누적한다. 매수까지 간 매차는 BUY_YN·STEP_SEQ 로 몇 차수를
채웠는지 함께 관리한다.
"""

from utils.algTouchSumTables import ensureAlgTouchSumTable
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 터치했지만 매수까지 못 간 사유
SKIP_BELOW_SMA20 = "BELOW_SMA20"
SKIP_WAIT_SMA20 = "WAIT_SMA20"
SKIP_AFTER_CUTOFF = "AFTER_1400"
SKIP_QTY0 = "QTY0"
SKIP_FILLED = "FILLED"

_UPSERT_SQL = """
INSERT INTO AI_ALG_TOUCH_SUM (
  ALG_CD, TRADE_DT, STK_CD, MA_STEP, STK_NM, MODE,
  MA_PX, SMA20_PX,
  TOUCH_CNT, FIRST_TM, LAST_TM, FIRST_PX, LAST_PX,
  SKIP_CNT, LAST_SKIP_RSN
) VALUES (
  %s, %s, %s, %s, %s, %s,
  %s, %s,
  1, %s, %s, %s, %s,
  %s, %s
)
ON DUPLICATE KEY UPDATE
  STK_NM = COALESCE(NULLIF(VALUES(STK_NM), ''), STK_NM),
  MA_PX = COALESCE(VALUES(MA_PX), MA_PX),
  SMA20_PX = COALESCE(VALUES(SMA20_PX), SMA20_PX),
  TOUCH_CNT = TOUCH_CNT + 1,
  LAST_TM = VALUES(LAST_TM),
  LAST_PX = VALUES(LAST_PX),
  SKIP_CNT = SKIP_CNT + VALUES(SKIP_CNT),
  LAST_SKIP_RSN = COALESCE(VALUES(LAST_SKIP_RSN), LAST_SKIP_RSN)
"""

_MARK_BUY_SQL = """
UPDATE AI_ALG_TOUCH_SUM
   SET BUY_YN='Y',
       BUY_TM=%s,
       BUY_PX=%s,
       BUY_QTY=%s,
       STEP_SEQ=%s,
       STK_NM=COALESCE(NULLIF(%s, ''), STK_NM)
 WHERE ALG_CD=%s AND TRADE_DT=%s AND STK_CD=%s AND MA_STEP=%s
"""


def _num(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def upsertTouch(
    algCd,
    tradeDt,
    stkCd,
    maStep,
    px,
    evtTm,
    maPx=None,
    sma20=None,
    stkNm="",
    mode="paper",
    skipRsn=None,
):
    """
    터치 1건 반영. skipRsn 이 있으면 미매수 터치로 센다.
    첫 터치 시각·가격은 INSERT 때만 들어가고 이후에는 마지막 값만 갱신한다.
    """
    if not ensureAlgTouchSumTable():
        return {"ok": False, "errMsg": "AI_ALG_TOUCH_SUM 없음"}
    cd = normStkCd(stkCd)
    ymd = str(tradeDt or "")[:8]
    if not cd or len(ymd) != 8:
        return {"ok": False, "errMsg": "종목·거래일 오류"}
    tm = str(evtTm or "")[:6]
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_SQL,
                (
                    str(algCd or "")[:5],
                    ymd,
                    cd,
                    int(maStep),
                    stkNm or None,
                    mode or "paper",
                    _num(maPx),
                    _num(sma20),
                    tm,
                    tm,
                    _num(px),
                    _num(px),
                    1 if skipRsn else 0,
                    str(skipRsn)[:20] if skipRsn else None,
                ),
            )
        conn.commit()
        return {"ok": True}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algTouchSum] 터치 적재 실패 {cd}/{maStep}: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def markTouchBuy(
    algCd, tradeDt, stkCd, maStep, buyTm, buyPx, buyQty, stepSeq=None, stkNm=""
):
    """해당 매차가 실제 매수까지 갔음을 기록. 몇 번째 차수인지 STEP_SEQ 로 남긴다."""
    if not ensureAlgTouchSumTable():
        return {"ok": False, "errMsg": "AI_ALG_TOUCH_SUM 없음"}
    cd = normStkCd(stkCd)
    ymd = str(tradeDt or "")[:8]
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                _MARK_BUY_SQL,
                (
                    str(buyTm or "")[:6],
                    _num(buyPx),
                    int(buyQty or 0),
                    int(stepSeq) if stepSeq else None,
                    stkNm or "",
                    str(algCd or "")[:5],
                    ymd,
                    cd,
                    int(maStep),
                ),
            )
            hit = cur.rowcount
        conn.commit()
        return {"ok": True, "updated": hit}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[algTouchSum] 매수표시 실패 {cd}/{maStep}: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listTouchSum(tradeDt, algCd="MTS01", stkCd=None):
    """해당일 터치 요약 (종목·매차 순)."""
    if not ensureAlgTouchSumTable():
        return []
    ymd = str(tradeDt or "")[:8]
    args = [str(algCd or "")[:5], ymd]
    where = "ALG_CD=%s AND TRADE_DT=%s"
    if stkCd:
        where += " AND STK_CD=%s"
        args.append(normStkCd(stkCd))
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT ALG_CD, TRADE_DT, STK_CD, STK_NM, MA_STEP, MODE,
                       MA_PX, SMA20_PX, TOUCH_CNT, FIRST_TM, LAST_TM,
                       FIRST_PX, LAST_PX, BUY_YN, BUY_TM, BUY_PX, BUY_QTY,
                       STEP_SEQ, SKIP_CNT, LAST_SKIP_RSN
                FROM AI_ALG_TOUCH_SUM
                WHERE {where}
                ORDER BY STK_CD ASC, MA_STEP ASC
                """,
                tuple(args),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def summarizeTouchByStk(tradeDt, algCd="MTS01"):
    """
    종목별 롤업: 터치한 매차 · 매수까지 간 매차 · 최대 차수.
    반환: [{stkCd, stkNm, touchSteps, buySteps, maxBuyStep, touchCnt, buyCnt}]
    """
    rows = listTouchSum(tradeDt, algCd=algCd)
    byStk = {}
    for r in rows:
        cd = r.get("STK_CD")
        item = byStk.setdefault(
            cd,
            {
                "stkCd": cd,
                "stkNm": r.get("STK_NM") or "",
                "touchSteps": [],
                "buySteps": [],
                "touchCnt": 0,
                "skipCnt": 0,
            },
        )
        if not item["stkNm"] and r.get("STK_NM"):
            item["stkNm"] = r.get("STK_NM")
        step = int(r.get("MA_STEP") or 0)
        item["touchSteps"].append(step)
        item["touchCnt"] += int(r.get("TOUCH_CNT") or 0)
        item["skipCnt"] += int(r.get("SKIP_CNT") or 0)
        if str(r.get("BUY_YN") or "N") == "Y":
            item["buySteps"].append(step)
    out = []
    for item in byStk.values():
        item["touchSteps"].sort()
        item["buySteps"].sort()
        item["maxBuyStep"] = item["buySteps"][-1] if item["buySteps"] else None
        item["buyCnt"] = len(item["buySteps"])
        out.append(item)
    out.sort(key=lambda x: (-x["buyCnt"], x["stkCd"]))
    return out


def formatTouchSumReport(tradeDt, algCd="MTS01", limit=30):
    """터미널·텔레그램용 요약."""
    rows = listTouchSum(tradeDt, algCd=algCd)
    if not rows:
        return f"[터치요약] {tradeDt} {algCd} — 기록 없음"
    stks = summarizeTouchByStk(tradeDt, algCd=algCd)
    lines = [
        f"[터치요약] {tradeDt} {algCd}",
        f"종목 {len(stks)} · 매차행 {len(rows)}",
        "",
    ]
    for s in stks[:limit]:
        buyTxt = (
            "/".join(f"{x}일" for x in s["buySteps"]) if s["buySteps"] else "매수없음"
        )
        lines.append(
            f"{s['stkNm'] or s['stkCd']}({s['stkCd']}) "
            f"터치 {s['touchCnt']}회 · 매수 {s['buyCnt']}차 [{buyTxt}]"
        )
        for r in rows:
            if r.get("STK_CD") != s["stkCd"]:
                continue
            mark = "매수" if str(r.get("BUY_YN") or "N") == "Y" else (
                r.get("LAST_SKIP_RSN") or "-"
            )
            lines.append(
                f"   SMA{int(r.get('MA_STEP') or 0)} "
                f"{float(r.get('MA_PX') or 0):,.0f} · "
                f"터치 {int(r.get('TOUCH_CNT') or 0)}회 "
                f"({r.get('FIRST_TM') or '-'}~{r.get('LAST_TM') or '-'}) "
                f"· {mark}"
            )
    return "\n".join(lines)
