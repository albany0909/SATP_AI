"""웹·공통: 종목 재무·리포트 통합 조회"""

from __future__ import annotations

from research.dartFinFetch import fetchStkFinSummary
from research.finSummaryMerge import buildFinSummaryTable
from research.rptLoadService import loadAndStoreCorpReports
from utils.rptAnalStore import listCnsEst, listRptAnal
from utils.stkResolve import resolveStkRef


def _slimRpt(rows):
    out = []
    for r in rows or []:
        out.append(
            {
                "nid": r.get("NID"),
                "title": r.get("TITLE"),
                "broker": r.get("BROKER"),
                "pubDt": r.get("PUB_DT"),
                "opinion": r.get("OPINION"),
                "targetPrc": r.get("TARGET_PRC"),
                "toneScore": r.get("TONE_SCORE"),
                "hasGuide": r.get("HAS_GUIDE"),
                "guideTxt": r.get("GUIDE_TXT"),
                "sumTxt": r.get("SUM_TXT"),
                "detailUrl": r.get("DETAIL_URL"),
                "pdfUrl": r.get("PDF_URL"),
            }
        )
    return out


def _slimCns(rows):
    out = []
    for r in rows or []:
        out.append(
            {
                "baseDt": r.get("BASE_DT"),
                "estTp": r.get("EST_TP"),
                "periodCd": r.get("PERIOD_CD"),
                "estVal": float(r["EST_VAL"]) if r.get("EST_VAL") is not None else None,
                "estTxt": r.get("EST_TXT"),
                "src": r.get("SRC"),
                "srcRef": r.get("SRC_REF"),
                "note": r.get("NOTE"),
            }
        )
    return out


def getStkFinanceBundle(stkRef, loadRpt=False, finYear=None, rptLimit=15):
    """
    종목 재무(DART) + 리포트/가이던스 DB + (옵션) 리포트 재적재.
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

    loadRes = None
    if loadRpt:
        loadRes = loadAndStoreCorpReports(
            stkCd, maxPages=2, maxItems=12, detailTopN=6
        )

    reports = _slimRpt(listRptAnal(stkCd, limit=rptLimit, guideOnly=False))
    guides = _slimRpt(listRptAnal(stkCd, limit=10, guideOnly=True))
    cns = _slimCns(listCnsEst(stkCd, limit=20))

    fin = fetchStkFinSummary(stkCd, bsnsYear=finYear)
    finTable = buildFinSummaryTable(stkCd)

    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm or fin.get("corpNm") or "",
        "fin": {
            "ok": fin.get("ok"),
            "errMsg": fin.get("errMsg") or "",
            "src": fin.get("src"),
            "srcLabel": fin.get("srcLabel") or "Open DART",
            "bsnsYear": fin.get("bsnsYear"),
            "reprtLabel": fin.get("reprtLabel"),
            "corpCode": fin.get("corpCode"),
            "highlights": fin.get("highlights") or [],
            "accountCnt": fin.get("accountCnt") or 0,
        },
        "finTable": finTable,
        "reports": reports,
        "guides": guides,
        "cns": cns,
        "loadRpt": loadRes,
        "notes": [
            "주요재무정보 표: 연간 핵심계정 Open DART(연결→억원) + 예상(E)·분기·비율 네이버",
            "목표가·의견·가이던스: 증권사 리포트 메타 (creport load)",
            "FnSpace 컨센서스 유료 스킵",
            "투자 권유 아님 · PDF 전문 미저장",
        ],
    }
