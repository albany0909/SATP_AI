"""매매창·실시간에서 쓰는 운영 알고리즘 목록 (탭·하단 칩)."""

from strategies.specLoader import listStratSpecs, loadStratSpec, normAlgCd

# 디폴트 운영 순서 (settings deskAlgTabs 없을 때)
_DEFAULT_ALG_TABS = ("MTS01", "MSE01")

# 보유 행번호·하단 칩 공통 바탕색 (가독성 있는 파스텔)
_ALG_BG = {
    "MTS01": "#90CAF9",  # 이평터치 — 파랑
    "MSE01": "#A5D6A7",  # 이평지지 — 초록
    "MMG01": "#FFCC80",  # 수동정찰 피라미딩 — 주황
}
# 거래로그 글자색 — 탭 파스텔과 같은 계열, 흰 배경에서 읽히게 진하게
_ALG_FG = {
    "MTS01": "#1565C0",
    "MSE01": "#2E7D32",
    "MMG01": "#E65100",
}
_ALG_BG_FALLBACK = ("#FFCC80", "#CE93D8", "#80DEEA", "#EF9A9A", "#B0BEC5")
_UNK_BG = "#E0E0E0"
_UNK_FG = "#000000"


def stripAlgTradeSuffix(name):
    """표시명에서 끝의 '매매' 제거 (이평선터치 매매 → 이평선터치)."""
    text = str(name or "").strip()
    if text.endswith(" 매매"):
        return text[:-3].strip() or text
    if text.endswith("매매") and len(text) > 2:
        return text[:-2].strip() or text
    return text


def getDeskAlgTabs():
    """매매창 전략 탭·운영 표시 순서 (settings deskAlgTabs)."""
    try:
        from config.settingsStore import loadSettings

        raw = (loadSettings() or {}).get("deskAlgTabs")
        if isinstance(raw, list) and raw:
            out = []
            seen = set()
            for item in raw:
                alg = normAlgCd(item)
                if alg and alg not in seen:
                    seen.add(alg)
                    out.append(alg)
            if out:
                return out
    except Exception:
        pass
    return list(_DEFAULT_ALG_TABS)


def setDeskAlgTabs(algCds):
    """탭 순서 저장. 최소 1개 유지."""
    from config.settingsStore import loadSettings, saveSettings

    out = []
    seen = set()
    for item in algCds or []:
        alg = normAlgCd(item)
        if alg and alg not in seen:
            seen.add(alg)
            out.append(alg)
    if not out:
        out = list(_DEFAULT_ALG_TABS)
    settings = loadSettings() or {}
    settings["deskAlgTabs"] = out
    saveSettings(settings)
    return out


def addDeskAlgTab(algCd):
    """탭 끝에 추가. 이미 있으면 그대로."""
    alg = normAlgCd(algCd)
    if not alg:
        return getDeskAlgTabs()
    tabs = getDeskAlgTabs()
    if alg in tabs:
        return tabs
    tabs.append(alg)
    return setDeskAlgTabs(tabs)


def removeDeskAlgTab(algCd):
    """탭 제거. 마지막 1개는 남긴다."""
    alg = normAlgCd(algCd)
    tabs = getDeskAlgTabs()
    if not alg or alg not in tabs:
        return tabs
    if len(tabs) <= 1:
        return tabs
    return setDeskAlgTabs([a for a in tabs if a != alg])


def listAllStratAlgs():
    """등록된 전체 전략 (추가 메뉴용)."""
    out = []
    seen = set()
    for s in listStratSpecs() or []:
        alg = normAlgCd(s.get("algCd"))
        if not alg or alg in seen:
            continue
        seen.add(alg)
        out.append(
            {
                "algCd": alg,
                "stratId": s.get("stratId") or "",
                "name": stripAlgTradeSuffix(s.get("name") or alg),
                "status": s.get("status") or "",
                "enabled": bool(s.get("enabled", True)),
            }
        )
    return out


def _algMeta(algCd, byAlg):
    """스펙·live 설정으로 한 행."""
    want = normAlgCd(algCd)
    row = dict(byAlg.get(want) or {})
    if not row:
        row = {
            "algCd": want,
            "stratId": "",
            "name": want,
            "status": "",
            "enabled": True,
            "paperOnly": True,
        }
    row["name"] = stripAlgTradeSuffix(row.get("name") or want)
    # live 오버라이드
    try:
        from config.settingsStore import getMaTouchLive, loadSettings

        if want == "MTS01":
            mt = getMaTouchLive() or {}
            row["enabled"] = bool(mt.get("enabled", row.get("enabled", True)))
            row["paperOnly"] = bool(mt.get("paperOnly", True))
        else:
            settings = loadSettings() or {}
            liveAll = settings.get("stratLive") or {}
            liveRow = liveAll.get(want) if isinstance(liveAll, dict) else None
            if isinstance(liveRow, dict) and "enabled" in liveRow:
                row["enabled"] = bool(liveRow.get("enabled"))
            if row.get("stratId"):
                spec = loadStratSpec(row["stratId"]) or {}
                row["paperOnly"] = bool(
                    (spec.get("params") or {}).get("paperOnly", True)
                )
    except Exception:
        pass
    return row


def listDeskLiveAlgs():
    """
    운영 표시용 [{algCd, stratId, name, enabled, paperOnly, status}, ...]
    deskAlgTabs 순서. name 은 '매매' 제외.
    """
    byAlg = {}
    for s in listStratSpecs() or []:
        alg = normAlgCd(s.get("algCd"))
        if not alg:
            continue
        byAlg[alg] = {
            "algCd": alg,
            "stratId": s.get("stratId") or "",
            "name": s.get("name") or alg,
            "status": s.get("status") or "",
            "enabled": bool(s.get("enabled", True)),
            "paperOnly": True,
        }
    out = []
    for alg in getDeskAlgTabs():
        out.append(_algMeta(alg, byAlg))
    return out


def listDeskLiveAlgCds(enabledOnly=False):
    """algCd 목록."""
    rows = listDeskLiveAlgs()
    if enabledOnly:
        rows = [r for r in rows if r.get("enabled")]
    return [r["algCd"] for r in rows if r.get("algCd")]


def algDisplayName(algCd):
    """짧은 표시명 ('매매' 제외)."""
    want = normAlgCd(algCd)
    for r in listDeskLiveAlgs():
        if r.get("algCd") == want:
            return stripAlgTradeSuffix(r.get("name") or want)
    for r in listAllStratAlgs():
        if r.get("algCd") == want:
            return r.get("name") or want
    return want or "-"


def stratIdForAlg(algCd):
    want = normAlgCd(algCd)
    for r in listDeskLiveAlgs():
        if r.get("algCd") == want:
            return r.get("stratId") or ""
    for r in listAllStratAlgs():
        if r.get("algCd") == want:
            return r.get("stratId") or ""
    return ""


def algBgColor(algCd):
    """보유 행번호·하단 칩 공통 바탕색 (#RRGGBB)."""
    want = normAlgCd(algCd)
    if not want:
        return _UNK_BG
    if want in _ALG_BG:
        return _ALG_BG[want]
    try:
        idx = abs(hash(want)) % len(_ALG_BG_FALLBACK)
        return _ALG_BG_FALLBACK[idx]
    except Exception:
        return _UNK_BG


def _darkenHex(hexColor, factor=0.45):
    """#RRGGBB 를 같은 색상으로 진하게 (글자색용)."""
    raw = str(hexColor or "").lstrip("#")
    if len(raw) != 6:
        return _UNK_FG
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return _UNK_FG
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return f"#{r:02X}{g:02X}{b:02X}"


def algFgColor(algCd):
    """거래로그 등 글자색. 탭 바탕과 같은 계열 (#RRGGBB)."""
    want = normAlgCd(algCd)
    if not want:
        return _UNK_FG
    if want in _ALG_FG:
        return _ALG_FG[want]
    return _darkenHex(algBgColor(want))


def isKnownAlgCd(algCd):
    """운영·스펙에 있는 알고리즘인지 (00 체결 등 공통 태그와 구분)."""
    want = normAlgCd(algCd)
    if not want or want == "00":
        return False
    if want in _ALG_BG:
        return True
    if want in (getDeskAlgTabs() or []):
        return True
    try:
        for row in listDeskLiveAlgs() or []:
            if row.get("algCd") == want:
                return True
    except Exception:
        pass
    return False
