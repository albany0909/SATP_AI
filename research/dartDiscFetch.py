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
from utils.dartSecret import maskDartSecret
from utils.stkCdUtil import normStkCd

ensureEnvLoaded()


def _maskedGet(url, params, timeout=25):
    """요청 실패 문구에 crtfc_key 가 남지 않게 한다."""
    key = str((params or {}).get("crtfc_key") or "")
    try:
        return requests.get(url, params=params, timeout=timeout)
    except Exception as err:
        raise RuntimeError(maskDartSecret(str(err), key)) from None


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
        res = _maskedGet(url, params, timeout=25)
        body = res.json()
    except Exception as err:
        return {
            "ok": False,
            "errMsg": maskDartSecret(str(err), key),
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


def fetchMarketDisclosures(bgnDe, endDe, corpCls="", pageCount=100, getter=None):
    """코스피·코스닥 공시 목록. corp_code 없이 날짜만 조회한다. 키는 결과에 넣지 않는다."""
    key = (dart_api_key or "").strip()
    if not key:
        return {"ok": False, "errMsg": "DART_API_KEY 없음", "items": []}
    url = "https://opendart.fss.or.kr/api/list.json"
    call = getter or _maskedGet
    items = []
    page = 1
    while page <= 40:
        params = {
            "crtfc_key": key,
            "bgn_de": str(bgnDe),
            "end_de": str(endDe),
            "page_no": str(page),
            "page_count": str(min(100, max(1, int(pageCount)))),
        }
        if corpCls:
            params["corp_cls"] = corpCls
        try:
            res = call(url, params, 25)
            body = res.json()
        except Exception as err:
            return {
                "ok": False,
                "errMsg": maskDartSecret(str(err), key),
                "items": items,
            }
        status = str(body.get("status") or "")
        if status == "013":
            break
        if status != "000":
            return {
                "ok": False,
                "errMsg": maskDartSecret(str(body.get("message") or status), key),
                "items": items,
            }
        rows = body.get("list") or []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            rcept = str(row.get("rcept_no") or "").strip()
            stock = "".join(ch for ch in str(row.get("stock_code") or "") if ch.isdigit())
            if len(stock) >= 6:
                stock = stock[-6:]
            if not rcept or len(stock) != 6:
                continue
            items.append({
                "rceptNo": rcept,
                "stkCd": stock,
                "reportNm": str(row.get("report_nm") or "").strip(),
                "rceptDt": str(row.get("rcept_dt") or "").replace("-", "")[:8],
            })
        if len(rows) < int(params["page_count"]):
            break
        page += 1
    return {"ok": True, "items": items, "pages": page}
