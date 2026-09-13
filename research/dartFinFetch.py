"""
Open DART 단일회사 주요계정 조회 (재무 요약, 표시·검증용).
"""

from __future__ import annotations

from datetime import datetime

import requests

from config.config import dart_api_key
from config.loadEnv import ensureEnvLoaded
from research.dartCorpCode import resolveCorpCode

ensureEnvLoaded()

# 관심 계정 키워드 (계정명 포함 매칭)
_WANT = (
    "매출액",
    "영업이익",
    "당기순이익",
    "자산총계",
    "부채총계",
    "자본총계",
)


def _reprtLabel(code):
    return {
        "11011": "사업보고서",
        "11012": "반기보고서",
        "11013": "1분기보고서",
        "11014": "3분기보고서",
    }.get(str(code), str(code))


def fetchFnlttSinglAcnt(corpCode, bsnsYear, reprtCode="11011"):
    """
    fnlttSinglAcnt.json 호출.
    반환: {ok, items, errMsg?, rawStatus}
    """
    key = (dart_api_key or "").strip()
    if not key:
        return {"ok": False, "errMsg": "DART_API_KEY 없음", "items": []}
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    params = {
        "crtfc_key": key,
        "corp_code": str(corpCode),
        "bsns_year": str(bsnsYear),
        "reprt_code": str(reprtCode),
    }
    try:
        res = requests.get(url, params=params, timeout=30)
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": str(err), "items": []}

    st = str(body.get("status") or "")
    if st != "000":
        return {
            "ok": False,
            "errMsg": body.get("message") or f"status={st}",
            "items": [],
            "status": st,
        }
    rows = body.get("list") or []
    items = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("account_nm") or "").strip()
        items.append(
            {
                "accountNm": nm,
                "fsDiv": r.get("fs_div") or "",
                "fsNm": r.get("fs_nm") or "",
                "sjDiv": r.get("sj_div") or "",
                "sjNm": r.get("sj_nm") or "",
                "thstrmNm": r.get("thstrm_nm") or "",
                "thstrmAmount": r.get("thstrm_amount") or "",
                "frmtrmNm": r.get("frmtrm_nm") or "",
                "frmtrmAmount": r.get("frmtrm_amount") or "",
                "bfefrmtrmAmount": r.get("bfefrmtrm_amount") or "",
                "currency": r.get("currency") or "",
            }
        )
    return {"ok": True, "items": items, "status": st}


def pickHighlightAccounts(items):
    """핵심 계정만 추림 (연결 우선)."""
    # fs_div: CFS 연결, OFS 별도
    scored = []
    for it in items:
        nm = it.get("accountNm") or ""
        if not any(w in nm for w in _WANT):
            continue
        # 정확 매칭 우선
        exact = 0
        for w in _WANT:
            if nm == w or nm.replace(" ", "") == w:
                exact = 2
                break
            if w in nm:
                exact = 1
        cfs = 1 if (it.get("fsDiv") or "") == "CFS" else 0
        scored.append((exact, cfs, it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    seen = set()
    out = []
    for _, _, it in scored:
        key = (it.get("fsDiv"), it.get("accountNm"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= 16:
            break
    return out


def fetchStkFinSummary(stkCd, bsnsYear=None):
    """
    종목 재무 요약 (DART 주요계정).
    최근 연도부터 사업보고서 → 반기 순으로 시도.
    """
    code = str(stkCd or "").strip()
    if code.isdigit() and len(code) <= 6:
        code = code.zfill(6)

    corp = resolveCorpCode(code)
    if not corp.get("ok"):
        return {
            "ok": False,
            "stkCd": code,
            "errMsg": corp.get("errMsg") or "법인코드 조회 실패",
            "highlights": [],
            "src": "DART",
        }

    year = int(bsnsYear or datetime.now().year)
    tried = []
    for y in (year, year - 1, year - 2):
        for rc in ("11011", "11012", "11014", "11013"):
            tried.append(f"{y}/{rc}")
            r = fetchFnlttSinglAcnt(corp["corpCode"], y, rc)
            if r.get("ok") and r.get("items"):
                hi = pickHighlightAccounts(r["items"])
                return {
                    "ok": True,
                    "stkCd": code,
                    "corpCode": corp["corpCode"],
                    "corpNm": corp.get("corpNm") or "",
                    "bsnsYear": str(y),
                    "reprtCode": rc,
                    "reprtLabel": _reprtLabel(rc),
                    "highlights": hi,
                    "accountCnt": len(r["items"]),
                    "src": "DART",
                    "srcLabel": "Open DART 주요계정",
                }
            # 데이터 없음이면 다음
            if r.get("status") in ("013",):  # 조회된 데이타가 없습니다
                continue
    return {
        "ok": False,
        "stkCd": code,
        "corpCode": corp.get("corpCode"),
        "corpNm": corp.get("corpNm"),
        "errMsg": "최근 연도 재무 계정 없음 (" + ", ".join(tried[:6]) + "…)",
        "highlights": [],
        "src": "DART",
    }
