"""
증권사 리포트 수집 → 가이던스·요약 분석 → DB 적재.
FnSpace 예상실적은 유료 스킵. 리포트 파생 목표가/의견은 AI_CNS_EST 적재.
"""

from __future__ import annotations

from research.guideParse import (
    extractGuideText,
    extractTargetAndOpinion,
    summarizeContent,
)
from research.naverCorpReport import enrichReportDetail, fetchCorpReportList
from research.reportAnalyze import _toneScore, formatCorpReportMsg, analyzeReportItems
from utils.rptAnalStore import listCnsEst, listRptAnal, upsertCnsEstRows, upsertRptAnalRows
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog


def _itemToRptRow(stkCd, stkNm, item):
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    opinion, target = extractTargetAndOpinion(
        title,
        summary,
        opinionHint=item.get("opinion") or "",
        targetHint=item.get("targetPrc"),
    )
    hasGuide, guideTxt = extractGuideText(title, summary)
    sumTxt = summarizeContent(title, summary)
    tone, _, _ = _toneScore(" ".join([title, summary, opinion]))
    nid = str(item.get("nid") or "").strip()
    if not nid:
        return None, None
    pubDt = str(item.get("pubDt") or "")[:8]
    row = {
        "STK_CD": str(stkCd),
        "NID": nid,
        "STK_NM": stkNm or item.get("stkNm") or "",
        "TITLE": title[:300],
        "BROKER": (item.get("broker") or "")[:80],
        "PUB_DT": pubDt or None,
        "PUB_DT_RAW": (item.get("pubDtRaw") or "")[:20],
        "OPINION": (opinion or "")[:40] or None,
        "TARGET_PRC": int(target) if target else None,
        "TONE_SCORE": int(tone),
        "HAS_GUIDE": hasGuide,
        "GUIDE_TXT": (guideTxt or "")[:800] or None,
        "SUM_TXT": (sumTxt or "")[:2000] or None,
        "DETAIL_URL": (item.get("detailUrl") or "")[:400] or None,
        "PDF_URL": (item.get("pdfUrl") or "")[:400] or None,
        "VIEWS": int(item.get("views") or 0),
        "SRC": "NAVER",
        "PUSHED_YN": "N",
    }
    cnsRows = []
    baseDt = pubDt or "19700101"
    if target:
        cnsRows.append(
            {
                "STK_CD": str(stkCd),
                "BASE_DT": baseDt,
                "EST_TP": "TARGET_PRC",
                "PERIOD_CD": "RPT",
                "EST_VAL": float(target),
                "EST_TXT": None,
                "SRC": "NAVER_RPT",
                "SRC_REF": nid,
                "NOTE": (item.get("broker") or "")[:200],
            }
        )
    if opinion:
        cnsRows.append(
            {
                "STK_CD": str(stkCd),
                "BASE_DT": baseDt,
                "EST_TP": "OPINION",
                "PERIOD_CD": "RPT",
                "EST_VAL": None,
                "EST_TXT": opinion[:200],
                "SRC": "NAVER_RPT",
                "SRC_REF": nid,
                "NOTE": (item.get("broker") or "")[:200],
            }
        )
    return row, cnsRows


def loadAndStoreCorpReports(
    stkRef,
    maxPages=2,
    maxItems=15,
    detailTopN=8,
):
    """
    리포트 목록·상세 수집 후 AI_RPT_ANAL / AI_CNS_EST(리포트 파생) 적재.
    """
    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "errMsg": resolved.get("errMsg") or "종목 해석 실패",
            "matches": resolved.get("matches") or [],
        }
    stkCd = resolved["stkCd"]
    stkNm = resolved.get("stkNm") or ""
    listed = fetchCorpReportList(stkCd, maxPages=maxPages, maxItems=maxItems)
    items = listed.get("items") or []
    if not items and not listed.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": listed.get("errMsg") or "리포트 없음",
        }

    # 상세 보강 (요약·의견)
    topN = max(0, int(detailTopN or 0))
    enriched = []
    for i, it in enumerate(items):
        row = dict(it)
        if i < topN:
            row = enrichReportDetail(row, stkCd)
        enriched.append(row)

    rptRows = []
    cnsRows = []
    guideCnt = 0
    for it in enriched:
        r, c = _itemToRptRow(stkCd, stkNm, it)
        if not r:
            continue
        if r.get("HAS_GUIDE") == "Y":
            guideCnt += 1
        rptRows.append(r)
        cnsRows.extend(c or [])

    savedR = upsertRptAnalRows(rptRows)
    if not savedR.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": savedR.get("errMsg") or "리포트 DB 적재 실패",
        }
    savedC = upsertCnsEstRows(cnsRows)
    # 톤 요약 (표시용)
    analysis = analyzeReportItems(enriched, detailTopN=0)

    label = formatStkLabel(stkCd, stkNm)
    msg = (
        f"[리포트 적재] {label}\n"
        f"수집 {len(enriched)}건 · DB 리포트 {savedR.get('upsertCnt') or 0}건 · "
        f"가이던스표시 {guideCnt}건\n"
        f"리포트 파생 목표가/의견 {savedC.get('upsertCnt') or 0}건 → AI_CNS_EST\n"
        f"FnSpace 예상실적: 유료 스킵\n"
        f"※ PDF 전문 미저장 · 투자 권유 아님"
    )
    # 분석 텍스트 추가(짧게)
    brief = formatCorpReportMsg(label, listed, analysis)
    if brief:
        # 앞 15줄만
        lines = brief.splitlines()
        msg = msg + "\n\n" + "\n".join(lines[:18])

    termLog(f"[rptLoad] {stkCd} rpt={savedR.get('upsertCnt')} cns={savedC.get('upsertCnt')}")
    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "listUrl": listed.get("listUrl"),
        "fetchCnt": len(enriched),
        "rptUpsertCnt": savedR.get("upsertCnt") or 0,
        "cnsUpsertCnt": savedC.get("upsertCnt") or 0,
        "guideCnt": guideCnt,
        "fnSpaceSkipped": True,
        "msgText": msg,
        "analysis": analysis,
    }


def formatRptLastMsg(stkCd, guideOnly=False, limit=10):
    """DB 최근 리포트 목록 텍스트."""
    rows = listRptAnal(stkCd, limit=limit, guideOnly=guideOnly)
    if not rows:
        return f"[리포트DB] {stkCd} 적재 없음. creport load {stkCd} 실행 후 조회."
    lines = [
        f"[리포트DB] {stkCd} "
        + ("(가이던스만) " if guideOnly else "")
        + f"{len(rows)}건"
    ]
    for r in rows:
        g = " [G]" if r.get("HAS_GUIDE") == "Y" else ""
        op = r.get("OPINION") or ""
        tp = r.get("TARGET_PRC")
        extra = ""
        if op or tp:
            extra = f" | {op}" + (f" 목표 {int(tp):,}" if tp else "")
        lines.append(
            f"· {r.get('PUB_DT') or ''} {r.get('BROKER') or ''}{g} | "
            f"{(r.get('TITLE') or '')[:50]}{extra}"
        )
        guide = (r.get("GUIDE_TXT") or "").strip()
        if guide and guideOnly:
            lines.append(f"  가이던스: {guide[:160]}")
        sumt = (r.get("SUM_TXT") or "").strip()
        if sumt and not guideOnly:
            lines.append(f"  요약: {sumt[:120]}")
    return "\n".join(lines)


def formatCnsLastMsg(stkCd, limit=15):
    rows = listCnsEst(stkCd, limit=limit)
    if not rows:
        return (
            f"[예상/목표가DB] {stkCd} 없음.\n"
            f"FnSpace 유료 스킵. creport load 로 리포트 파생 목표가·의견만 적재."
        )
    lines = [f"[예상/목표가DB] {stkCd} {len(rows)}건 (소스 확인)"]
    for r in rows:
        src = r.get("SRC") or ""
        val = r.get("EST_VAL")
        txt = r.get("EST_TXT") or ""
        valS = f"{float(val):,.0f}" if val is not None else txt
        lines.append(
            f"· {r.get('BASE_DT')} {r.get('EST_TP')}={valS} "
            f"({src}/{r.get('SRC_REF') or '-'}) {r.get('NOTE') or ''}"
        )
    lines.append("※ FnSpace 컨센서스 아님(유료 스킵). 리포트 파생 메타.")
    return "\n".join(lines)
