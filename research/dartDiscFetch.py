"""
Open DART 공시 목록 조회 (list.json) — 메타만 저장용.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

from config.config import dart_api_key
from config.loadEnv import ensureEnvLoaded
from config.researchSources import openDartUrls
from research.dartCorpCode import resolveCorpCode
from utils.stkCdUtil import normStkCd

ensureEnvLoaded()


def _ymdPlus(daysBack):
    end = datetime.now()
    start = end - timedelta(days=int(daysBack))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetchDartDiscList(stkCd, daysBack=3, pageCount=50):
    """
    종목별 최근 공시 목록.
    반환: {ok, stkCd, corpCode, items, errMsg?}
    item: rceptNo, reportNm, rceptDt, flrNm, corpName, rm, viewUrl
    """
    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드 없음", "items": []}

    key = (dart_api_key or "").strip()
    if not key:
        return {"ok": False, "errMsg": "DART_API_KEY 없음", "items": [], "stkCd": code}

    corp = resolveCorpCode(code)
    if not corp.get("ok"):
        return {
            "ok": False,
            "errMsg": corp.get("errMsg") or "법인코드 실패",
            "items": [],
            "stkCd": code,
        }

    bgn, end = _ymdPlus(daysBack)
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": key,
        "corp_code": corp["corpCode"],
        "bgn_de": bgn,
        "end_de": end,
        "page_no": "1",
        "page_count": str(min(100, max(1, int(pageCount)))),
    }
    try:
        res = requests.get(url, params=params, timeout=25)
        body = res.json()
    except Exception as err:
        return {
            "ok": False,
            "errMsg": str(err),
            "items": [],
            "stkCd": code,
            "corpCode": corp["corpCode"],
        }

    st = str(body.get("status") or "")
    # 013: 조회된 데이타가 없습니다 → 성공 빈 목록
    if st == "013":
        return {
            "ok": True,
            "stkCd": code,
            "corpCode": corp["corpCode"],
            "corpNm": corp.get("corpNm") or "",
            "items": [],
            "bgnDe": bgn,
            "endDe": end,
        }
    if st != "000":
        return {
            "ok": False,
            "errMsg": body.get("message") or f"status={st}",
            "items": [],
            "stkCd": code,
            "corpCode": corp["corpCode"],
            "status": st,
        }

    items = []
    for r in body.get("list") or []:
        if not isinstance(r, dict):
            continue
        rcept = str(r.get("rcept_no") or "").strip()
        if not rcept:
            continue
        urls = openDartUrls(code, rceptNo=rcept)
        items.append(
            {
                "rceptNo": rcept,
                "reportNm": str(r.get("report_nm") or "").strip(),
                "rceptDt": str(r.get("rcept_dt") or "").strip(),
                "flrNm": str(r.get("flr_nm") or "").strip(),
                "corpName": str(r.get("corp_name") or "").strip(),
                "rm": str(r.get("rm") or "").strip(),
                "viewUrl": urls.get("documentViewer")
                or f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
            }
        )

    return {
        "ok": True,
        "stkCd": code,
        "corpCode": corp["corpCode"],
        "corpNm": corp.get("corpNm") or "",
        "items": items,
        "bgnDe": bgn,
        "endDe": end,
    }
