"""
리서치 AI 에이전트·개발 검증용 참고 소스 설정.

- 설정: config/data/researchSources.json
- 네이버 증권, Open DART, FnGuide(CompanyGuide·WiseReport·API·지수) 등
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "researchSources.json"
_cache = None


def _loadRaw():
    global _cache
    if _cache is not None:
        return _cache
    if not _DATA_PATH.is_file():
        _cache = {"version": 0, "sources": [], "defaults": {}}
        return _cache
    with open(_DATA_PATH, "r", encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


def reloadResearchSources():
    """파일 변경 후 캐시 갱신."""
    global _cache
    _cache = None
    return _loadRaw()


def listResearchSources(enabledOnly=True, providerGroup=None, devOnly=False):
    """
    소스 메타 목록 (priority 오름차순).
    providerGroup: 예) fnGuide, naver
    devOnly: devReference=true 만
    """
    raw = _loadRaw()
    items = list(raw.get("sources") or [])
    if enabledOnly:
        items = [s for s in items if s.get("enabled", True)]
    if providerGroup:
        pg = str(providerGroup).strip()
        items = [s for s in items if str(s.get("providerGroup") or "") == pg]
    if devOnly:
        items = [s for s in items if s.get("devReference")]
    items.sort(key=lambda s: int(s.get("priority") or 99))
    return items


def getResearchSource(sourceId):
    """id → 소스 dict. 없으면 None."""
    sid = str(sourceId or "").strip()
    for s in listResearchSources(enabledOnly=False):
        if s.get("id") == sid:
            return s
    return None


def getResearchDefaults():
    return dict((_loadRaw().get("defaults") or {}))


def _fmtUrl(template, **kwargs):
    if not template:
        return ""
    try:
        return str(template).format(**kwargs)
    except (KeyError, ValueError):
        return str(template)


def _normStkCd(stkCd):
    code = str(stkCd or "").strip()
    if code.isdigit() and len(code) <= 6:
        return code.zfill(6)
    return code


def buildSourceUrls(sourceId, stkCd="", rceptNo="", nid="", page=1, **extra):
    """
    소스 endpoints 템플릿에 종목코드 등을 채워 dict 반환.
    """
    src = getResearchSource(sourceId)
    if not src:
        return {}
    code = _normStkCd(stkCd)
    params = {
        "stkCd": code,
        "rceptNo": str(rceptNo or ""),
        "nid": str(nid or ""),
        "page": page,
        **extra,
    }
    out = {}
    for key, tmpl in (src.get("endpoints") or {}).items():
        out[key] = _fmtUrl(tmpl, **params)
    for key, tmpl in (src.get("linkTemplates") or {}).items():
        out[key] = _fmtUrl(tmpl, **params)
    out["homeUrl"] = src.get("homeUrl") or ""
    out["sourceId"] = src.get("id")
    out["name"] = src.get("name")
    out["providerGroup"] = src.get("providerGroup") or ""
    return out


def naverFinanceUrls(stkCd=""):
    """네이버 페이 증권 참고 링크."""
    return buildSourceUrls("naverFinance", stkCd=stkCd)


def openDartUrls(stkCd="", rceptNo=""):
    """Open DART / DART 공시 뷰어 링크."""
    return buildSourceUrls("openDart", stkCd=stkCd, rceptNo=rceptNo)


def fnGuideUrls(stkCd=""):
    """
    FnGuide 계열 개발·분석 참고 URL 묶음.
    Company Guide Snapshot 포함.
    """
    code = _normStkCd(stkCd)
    out = {
        "portal": buildSourceUrls("fnGuidePortal", stkCd=code),
        "companyGuide": buildSourceUrls("fnCompanyGuide", stkCd=code),
        "corp": buildSourceUrls("fnGuideCorp", stkCd=code),
        "companyIrEdit": buildSourceUrls("fnCompanyIrEdit", stkCd=code),
        "wiseReport": buildSourceUrls("wiseReport", stkCd=code),
        "fnIndex": buildSourceUrls("fnIndex", stkCd=code),
        "wiseIndex": buildSourceUrls("wiseIndex", stkCd=code),
        "fnSpaceApi": buildSourceUrls("fnSpaceApi", stkCd=code),
        "dataGuide": buildSourceUrls("fnDataGuide", stkCd=code),
    }
    return out


def listDevReferenceSites(providerGroup=None):
    """
    개발·검증 시 참고할 사이트 평탄 목록.
    반환: [{id, name, homeUrl, providerGroup, roles, accessNote, disclaimer}]
    """
    rows = []
    for s in listResearchSources(
        enabledOnly=True, providerGroup=providerGroup, devOnly=False
    ):
        if not s.get("devReference") and not (
            s.get("useFor") or {}
        ).get("devValidation"):
            continue
        rows.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "homeUrl": s.get("homeUrl") or "",
                "providerGroup": s.get("providerGroup") or "",
                "roles": list(s.get("roles") or []),
                "accessNote": s.get("accessNote") or "",
                "disclaimer": s.get("disclaimer") or "",
                "providerNote": s.get("providerNote") or "",
            }
        )
    return rows


def listFnGuideReferenceSites():
    """FnGuide 패밀리만."""
    return listDevReferenceSites(providerGroup="fnGuide")


def referenceLinksForStk(stkCd):
    """
    종목 기준 1차 참고 링크 (에이전트 리포트 하단·개발 검증용).
    """
    code = _normStkCd(stkCd)
    links = []

    nv = naverFinanceUrls(code)
    if nv:
        pairs = [
            ("itemMain", "종목 종합", "quote"),
            ("itemNews", "종목 뉴스", "news"),
            ("itemNotice", "공시·알림(포털)", "disclosure"),
            ("itemResearch", "리포트·리서치", "research"),
            ("itemCoinfo", "기업현황", "finance"),
            ("itemFrgn", "외국인·기관", "flow"),
        ]
        for key, label, role in pairs:
            url = nv.get(key)
            if url:
                links.append(
                    {
                        "sourceId": "naverFinance",
                        "name": "네이버 페이 증권",
                        "roleHint": role,
                        "label": label,
                        "url": url,
                    }
                )

    dart = openDartUrls(code)
    if dart.get("home"):
        links.append(
            {
                "sourceId": "openDart",
                "name": "Open DART",
                "roleHint": "disclosure",
                "label": "공시·재무 1차(Open DART)",
                "url": dart["home"],
            }
        )

    # FnGuide Company Guide Snapshot (개발 검증·수동 대조)
    cg = buildSourceUrls("fnCompanyGuide", stkCd=code)
    if cg.get("snapshot") or cg.get("stkByCd"):
        links.append(
            {
                "sourceId": "fnCompanyGuide",
                "name": "Company Guide",
                "roleHint": "finance",
                "label": "FnGuide Snapshot",
                "url": cg.get("snapshot") or cg.get("stkByCd"),
            }
        )
    if cg.get("home"):
        links.append(
            {
                "sourceId": "fnCompanyGuide",
                "name": "Company Guide",
                "roleHint": "home",
                "label": "Company Guide 홈",
                "url": cg["home"],
            }
        )

    wr = buildSourceUrls("wiseReport")
    if wr.get("home"):
        links.append(
            {
                "sourceId": "wiseReport",
                "name": "와이즈리포트",
                "roleHint": "research",
                "label": "WiseReport (계약/로그인)",
                "url": wr["home"],
            }
        )

    return links


def sourcesForRole(role):
    """역할(news, disclosure, theme 등)을 담당하는 활성 소스 id 목록."""
    want = str(role or "").strip()
    out = []
    for s in listResearchSources(enabledOnly=True):
        roles = s.get("roles") or []
        useFor = s.get("useFor") or {}
        if want in roles or useFor.get(want):
            out.append(s.get("id"))
    return out
