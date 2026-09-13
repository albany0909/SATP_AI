"""ka10059 종목별투자자 → AI_DAY_INV_SR 적재.

대상: 관심종목(활성) ∪ 실시간조회순위(당일누적) ∪ 운영 알고리즘 관리종목(AI_ALG_STK).
기본 파라미터는 HTS 종목별투자자 화면과 동일 — 순매수·수량·단주.
"""

from __future__ import annotations

import time
from datetime import datetime

from api.getInqRank import QRY_TP_DAY
from api.getInvSrOrgn import (
    AMT_QTY_QTY,
    SRC_TR,
    TRDE_NET,
    UNIT_SHARE,
    fetchInvSrOrgn,
)
from utils.dayInvSrStore import (
    hasInvSrForStkDt,
    listLoadedStkCdsByDt,
    upsertDayInvSrRows,
)
from utils.dayInvSrTables import ensureDayInvSrTable
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog
from utils.watchStkStore import listActiveWatch

# 모의 TR 1초 1회 — 여유
_PAPER_WAIT_SEC = 1.05
_REAL_WAIT_SEC = 0.25


def _pickSession(preferMode=None):
    return acquireKiwoomSession(preferMode=preferMode)


def collectInvSrUniverse(tradeDt=None):
    """
    관심종목 ∪ 당일누적 순위 종목 (코드 중복 제거).
    반환: [{stkCd, stkNm, src}, ...] src=watch|rank|both
    """
    byCd = {}

    for w in listActiveWatch(mktTp="KR") or []:
        cd = normStkCd(w.get("STK_CD"))
        if not cd:
            continue
        byCd[cd] = {
            "stkCd": cd,
            "stkNm": w.get("STK_NM") or "",
            "src": "watch",
        }

    ymd = str(tradeDt or "").replace("-", "")[:8]
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if not (ymd.isdigit() and len(ymd) == 8):
                cur.execute(
                    """
                    SELECT MAX(TRADE_DT) AS MAX_DT
                    FROM AI_DAY_INQ_RANK
                    WHERE QRY_TP=%s
                    """,
                    (QRY_TP_DAY,),
                )
                meta = cur.fetchone() or {}
                ymd = str(meta.get("MAX_DT") or "")
            if ymd:
                from utils.dayInqRankStore import resolveInqRankTradeDt

                resolved = resolveInqRankTradeDt(ymd, QRY_TP_DAY)
                if resolved.get("ok") and resolved.get("tradeDt"):
                    ymd = resolved.get("tradeDt")
                cur.execute(
                    """
                    SELECT STK_CD, STK_NM
                    FROM AI_DAY_INQ_RANK
                    WHERE TRADE_DT=%s AND QRY_TP=%s
                    ORDER BY RANK_NO ASC
                    """,
                    (ymd, QRY_TP_DAY),
                )
                for r in cur.fetchall() or []:
                    cd = normStkCd(r.get("STK_CD"))
                    if not cd:
                        continue
                    if cd in byCd:
                        byCd[cd]["src"] = "both"
                        if not byCd[cd].get("stkNm"):
                            byCd[cd]["stkNm"] = r.get("STK_NM") or ""
                    else:
                        byCd[cd] = {
                            "stkCd": cd,
                            "stkNm": r.get("STK_NM") or "",
                            "src": "rank",
                        }
    except Exception as err:
        termLog(f"[invSrLoad] 순위 유니버스 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    _mergeAlgUniv(byCd)
    return list(byCd.values())


def _mergeAlgUniv(byCd):
    """이평지지 등 관리종목도 순매수 그리드 대상에 넣는다."""
    try:
        from trading.deskLiveAlgs import listDeskLiveAlgCds

        algs = listDeskLiveAlgCds(enabledOnly=False) or ["MTS01", "MSE01"]
    except Exception:
        algs = ["MTS01", "MSE01"]
    try:
        from utils.algStkStore import listAlgStk
    except Exception:
        return
    for alg in algs:
        try:
            rows = listAlgStk(alg, limit=2000) or []
        except Exception:
            continue
        for r in rows:
            cd = normStkCd(r.get("stkCd") or r.get("STK_CD"))
            if not cd:
                continue
            nm = (r.get("stkNm") or r.get("STK_NM") or "").strip()
            if cd in byCd:
                src = byCd[cd].get("src") or ""
                if "alg" not in src:
                    byCd[cd]["src"] = (src + "+alg").strip("+")
                if nm and not byCd[cd].get("stkNm"):
                    byCd[cd]["stkNm"] = nm
                continue
            byCd[cd] = {"stkCd": cd, "stkNm": nm, "src": "alg"}


def loadInvSrForStk(
    stkRef,
    tradeDt=None,
    amtQtyTp=AMT_QTY_QTY,
    trdeTp=TRDE_NET,
    unitTp=UNIT_SHARE,
    preferMode=None,
    skipIfToday=False,
):
    """
    단건 조회·적재. tradeDt 미지정 시 오늘.
    반환: ok, stkCd, upsertCnt, rowCnt, errMsg?
    """
    if not ensureDayInvSrTable():
        return {"ok": False, "errMsg": "AI_DAY_INV_SR 테이블 생성 실패"}

    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "errMsg": resolved.get("errMsg") or "종목 해석 실패",
            "matches": resolved.get("matches") or [],
        }

    stkCd = resolved["stkCd"]
    stkNm = resolved.get("stkNm") or ""
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    if skipIfToday and hasInvSrForStkDt(stkCd, ymd, amtQtyTp, trdeTp, unitTp):
        return {
            "ok": True,
            "skipped": True,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "upsertCnt": 0,
            "rowCnt": 0,
            "tradeDt": ymd,
        }

    sess = _pickSession(preferMode=preferMode)
    if not sess:
        return {
            "ok": False,
            "errMsg": "키움 토큰 없음 — login paper|real 또는 .env 앱키 확인",
        }
    mode, hostUrl, token, tokenType = sess
    if not token:
        return {"ok": False, "errMsg": "토큰 없음"}

    fetched = fetchInvSrOrgn(
        hostUrl,
        token,
        stkCd,
        ymd,
        amtQtyTp=amtQtyTp,
        trdeTp=trdeTp,
        unitTp=unitTp,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    if not fetched.get("ok"):
        return {
            "ok": False,
            "errMsg": fetched.get("errMsg") or "조회 실패",
            "stkCd": stkCd,
            "stkNm": stkNm,
        }

    rows = fetched.get("rows") or []
    saved = upsertDayInvSrRows(rows)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg") or "DB 적재 실패",
            "stkCd": stkCd,
        }
    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "tradeDt": ymd,
        "upsertCnt": saved.get("upsertCnt") or 0,
        "rowCnt": fetched.get("rowCnt") or 0,
        "mode": mode,
        "srcTr": SRC_TR,
        "amtQtyTp": str(amtQtyTp),
        "trdeTp": str(trdeTp),
        "unitTp": str(unitTp),
    }


def loadInvSrForStkCds(
    stkCds,
    tradeDt=None,
    minDt=None,
    amtQtyTp=AMT_QTY_QTY,
    trdeTp=TRDE_NET,
    unitTp=UNIT_SHARE,
    preferMode=None,
    skipIfToday=True,
):
    """지정 종목 ka10059 일괄 적재. minDt 있으면 그 일자 미만은 버린다."""
    if not ensureDayInvSrTable():
        return {"ok": False, "errMsg": "AI_DAY_INV_SR 테이블 생성 실패"}
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {"ok": False, "errMsg": "종목 없음", "okCnt": 0, "failCnt": 0}

    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    sess = _pickSession(preferMode=preferMode)
    if not sess:
        return {"ok": False, "errMsg": "키움 토큰 없음"}
    mode, hostUrl, token, tokenType = sess
    isPaper = mode == "paper"
    waitSec = _PAPER_WAIT_SEC if isPaper else _REAL_WAIT_SEC

    okCnt = 0
    failCnt = 0
    skipCnt = 0
    upsertCnt = 0
    failMsgs = []
    called = 0
    for cd in codes:
        if skipIfToday and hasInvSrForStkDt(cd, ymd, amtQtyTp, trdeTp, unitTp):
            skipCnt += 1
            continue
        if called > 0:
            time.sleep(waitSec)
        called += 1
        fetched = fetchInvSrOrgn(
            hostUrl,
            token,
            cd,
            ymd,
            amtQtyTp=amtQtyTp,
            trdeTp=trdeTp,
            unitTp=unitTp,
            tokenType=tokenType,
            isPaper=isPaper,
            minDt=minDt,
        )
        if not fetched.get("ok"):
            failCnt += 1
            failMsgs.append(f"{cd}: {fetched.get('errMsg')}")
            termLog(f"[invSrLoad] 실패 {cd} {fetched.get('errMsg')}")
            continue
        saved = upsertDayInvSrRows(fetched.get("rows") or [])
        if not saved.get("ok"):
            failCnt += 1
            failMsgs.append(f"{cd}: {saved.get('errMsg')}")
            continue
        okCnt += 1
        upsertCnt += int(saved.get("upsertCnt") or 0)
        termLog(
            f"[invSrLoad] {okCnt}/{len(codes)} {cd} "
            f"{fetched.get('rowCnt') or 0}일"
        )
    return {
        "ok": failCnt == 0,
        "tradeDt": ymd,
        "okCnt": okCnt,
        "failCnt": failCnt,
        "skipCnt": skipCnt,
        "upsertCnt": upsertCnt,
        "failMsgs": failMsgs,
        "mode": mode,
    }


def loadInvSrUniverse(
    tradeDt=None,
    amtQtyTp=AMT_QTY_QTY,
    trdeTp=TRDE_NET,
    unitTp=UNIT_SHARE,
    preferMode=None,
    skipLoaded=True,
    onProgress=None,
):
    """
    관심+순위+알고리즘 관리종목 일괄 적재.
    skipLoaded=True 이면 당일 이미 있는 종목은 API 생략 (재시도 시 잔여만).
    """
    if not ensureDayInvSrTable():
        return {"ok": False, "errMsg": "AI_DAY_INV_SR 테이블 생성 실패"}

    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    items = collectInvSrUniverse(ymd)
    if not items:
        return {
            "ok": False,
            "errMsg": "적재 대상 없음 — 관심종목 또는 당일누적 순위를 확인하세요.",
            "tradeDt": ymd,
        }

    sess = _pickSession(preferMode=preferMode)
    if not sess:
        return {
            "ok": False,
            "errMsg": "키움 토큰 없음 — login paper|real 또는 .env 앱키 확인",
            "tradeDt": ymd,
        }
    mode, hostUrl, token, tokenType = sess
    isPaper = mode == "paper"
    waitSec = _PAPER_WAIT_SEC if isPaper else _REAL_WAIT_SEC
    modeLabel = "모의투자" if isPaper else "실투자"

    already = listLoadedStkCdsByDt(ymd, amtQtyTp, trdeTp, unitTp) if skipLoaded else set()
    targets = [x for x in items if x["stkCd"] not in already]
    skipped = len(items) - len(targets)

    termLog(
        f"[invSrLoad] ({modeLabel}) 시작 baseDt={ymd} "
        f"대상 {len(items)} (스킵 {skipped}) 순매수·수량·단주"
    )

    okCnt = 0
    failCnt = 0
    upsertCnt = 0
    failMsgs = []
    failCds = []

    for i, it in enumerate(targets):
        stkCd = it["stkCd"]
        if i > 0:
            time.sleep(waitSec)
        if onProgress:
            try:
                onProgress(i + 1, len(targets), stkCd)
            except Exception:
                pass
        fetched = fetchInvSrOrgn(
            hostUrl,
            token,
            stkCd,
            ymd,
            amtQtyTp=amtQtyTp,
            trdeTp=trdeTp,
            unitTp=unitTp,
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not fetched.get("ok"):
            failCnt += 1
            err = fetched.get("errMsg") or "조회실패"
            failCds.append(stkCd)
            failMsgs.append(f"{stkCd}: {err}")
            termLog(f"[invSrLoad] 실패 {stkCd} {err}")
            continue
        rows = fetched.get("rows") or []
        saved = upsertDayInvSrRows(rows)
        if not saved.get("ok"):
            failCnt += 1
            failCds.append(stkCd)
            failMsgs.append(f"{stkCd}: {saved.get('errMsg')}")
            continue
        okCnt += 1
        upsertCnt += int(saved.get("upsertCnt") or 0)
        termLog(
            f"[invSrLoad] {i + 1}/{len(targets)} {stkCd} "
            f"{it.get('stkNm') or ''} {fetched.get('rowCnt') or 0}일"
        )

    summary = (
        f"[종목별투자자적재] ({modeLabel}) {ymd}\n"
        f"유니버스 {len(items)} · 호출 {len(targets)} · "
        f"성공 {okCnt} · 실패 {failCnt} · 기적재스킵 {skipped}\n"
        f"UPSERT {upsertCnt}행 · {SRC_TR} 순매수/수량/단주"
    )
    if failMsgs:
        summary += "\n실패: " + " / ".join(failMsgs[:8])
        if len(failMsgs) > 8:
            summary += f" 외 {len(failMsgs) - 8}건"

    return {
        "ok": failCnt == 0 and okCnt + skipped > 0,
        "partial": failCnt > 0 and okCnt > 0,
        "tradeDt": ymd,
        "univCnt": len(items),
        "callCnt": len(targets),
        "okCnt": okCnt,
        "failCnt": failCnt,
        "failCds": failCds,
        "skipped": skipped,
        "upsertCnt": upsertCnt,
        "summary": summary,
        "errMsg": None if failCnt == 0 else f"{failCnt}종목 실패",
        "mode": mode,
        "modeLabel": modeLabel,
    }


def formatInvSrLabel(stkCd, stkNm=""):
    return formatStkLabel(stkCd, stkNm)
