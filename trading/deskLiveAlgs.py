"""매매창·실시간에서 쓰는 운영 알고리즘 목록 (탭·하단 칩)."""

from strategies.specLoader import listStratSpecs, loadStratSpec, normAlgCd

# 디폴트 운영 순서 (settings deskAlgTabs 없을 때)
_DEFAULT_ALG_TABS = ("MTS01", "MSE01")

# 보유 행번호·하단 칩 공통 바탕색 (가독성 있는 파스텔)
_ALG_BG = {
    "MTS01": "#90CAF9",  # 이평터치 — 파랑
    "MSE01": "#A5D6A7",  # 이평지지 — 초록
    "MMG01": "#FFCC80",
    "LDR01": "#FFAB91",  # 수동정찰 피라미딩 — 주황
    "RAM01": "#CE93D8",  # 실계좌관리 — 보라
    "SRG01": "#80DEEA",  # 급등주매매 — 청록
}
# 거래로그 글자색 — 탭 파스텔과 같은 계열, 흰 배경에서 읽히게 진하게
_ALG_FG = {
    "MTS01": "#1565C0",
    "MSE01": "#2E7D32",
    "MMG01": "#E65100",
    "LDR01": "#BF360C",
    "RAM01": "#6A1B9A",
    "SRG01": "#006064",
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

        settings = loadSettings() or {}
        if "deskAlgTabs" in settings and isinstance(settings.get("deskAlgTabs"), list):
            out = []
            seen = set()
            for item in settings.get("deskAlgTabs") or []:
                alg = normAlgCd(item)
                if alg and alg not in seen:
                    seen.add(alg)
                    out.append(alg)
            return out
    except Exception:
        pass
    return list(_DEFAULT_ALG_TABS)


def setDeskAlgTabs(algCds):
    """탭 순서 저장. 빈 목록이면 매매창에 전략 탭을 두지 않는다."""
    from config.settingsStore import loadSettings, saveSettings

    out = []
    seen = set()
    for item in algCds or []:
        alg = normAlgCd(item)
        if alg and alg not in seen:
            seen.add(alg)
            out.append(alg)
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


def standaloneMonitorTabs():
    """
    공유·조회 탭. 알고리즘 비활성과 무관하게 유지한다.
    급등주는 조회 탭이다. SRG01 관리 탭(전략 서브탭)과는 따로 둔다.
    """
    return (
        {"key": "acnt", "name": "실계좌", "bg": "#CE93D8", "kind": "shared"},
        {"key": "leader", "name": "주도주", "bg": "#FFCC80", "kind": "shared"},
        {"key": "surge", "name": "급등주", "bg": "#80DEEA", "kind": "monitor", "algCd": "SRG01"},
    )


def algWatchSubtabVisible(enabled, evidence=None):
    """
    알고리즘 전용 관심 서브탭.
    비활성이고 잔고·미체결이 없다고 확인된 경우에만 숨긴다.
    확인이 안 되면 숨기지 않는다. 탭 목록 자체를 삭제하지는 않는다.
    """
    if enabled:
        return True
    if not isinstance(evidence, dict) or evidence.get("known") is not True:
        return True
    try:
        openPos = int(evidence.get("openPos") or 0)
        openOrders = int(evidence.get("openOrders") or 0)
    except (TypeError, ValueError):
        return True
    if openPos > 0 or openOrders > 0:
        return True
    return False


def filterVisibleAlgTabs(rows, evidenceByAlg=None):
    """전략 탭 표시용. 공유 조회 탭에는 적용하지 않는다."""
    evidenceByAlg = evidenceByAlg or {}
    out = []
    for row in rows or []:
        alg = row.get("algCd")
        if algWatchSubtabVisible(bool(row.get("enabled")), evidenceByAlg.get(alg)):
            out.append(row)
    return out


def _walkOpenState(node):
    if isinstance(node, dict):
        try:
            if int(node.get("qty") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        pending = node.get("pending")
        if pending not in (None, "", [], {}):
            return True
        return any(_walkOpenState(v) for v in node.values())
    if isinstance(node, list):
        return any(_walkOpenState(v) for v in node)
    return False


def _defaultStatePath(algCd):
    names = {
        "MMG01": "manualMgrState.json",
        "MTS01": "maTouchState.json",
        "MSE01": "maSupportEodState.json",
        "SRG01": "surgeTradeState.json",
    }
    name = names.get(normAlgCd(algCd))
    if not name:
        return None
    try:
        from utils.appPaths import configDataDir

        return configDataDir() / name
    except Exception:
        return None


def _stateFileEvidence(path):
    filePath = path if hasattr(path, "is_file") else None
    if filePath is None or not filePath.is_file():
        return {"known": True, "openPos": 0, "openOrders": 0}
    try:
        import json

        data = json.loads(filePath.read_text(encoding="utf-8"))
    except Exception:
        return {"known": False, "reason": "state_unreadable"}
    if _walkOpenState(data):
        return {"known": True, "openPos": 1, "openOrders": 0}
    return {"known": True, "openPos": 0, "openOrders": 0}


def collectWatchEvidence(algCd, ledgerCount=None, statePath=None):
    """
    ledgerCount 를 넘기면 DB 를 열지 않는다 (테스트).
    넘기지 않으면 원장 COUNT 가 성공할 때만 known 이다.
    """
    alg = normAlgCd(algCd)
    if ledgerCount is None:
        ledger = _ledgerOpenCount(alg)
    else:
        try:
            ledger = {"known": True, "openPos": int(ledgerCount), "openOrders": 0}
        except (TypeError, ValueError):
            ledger = {"known": False, "reason": "bad_count"}
    if not ledger.get("known"):
        return ledger
    if statePath is None:
        statePath = _defaultStatePath(alg)
    state = (
        _stateFileEvidence(statePath)
        if statePath is not None
        else {"known": True, "openPos": 0, "openOrders": 0}
    )
    if not state.get("known"):
        return state
    return {
        "known": True,
        "openPos": int(ledger.get("openPos") or 0) + int(state.get("openPos") or 0),
        "openOrders": int(ledger.get("openOrders") or 0) + int(state.get("openOrders") or 0),
    }


def _ledgerOpenCount(algCd):
    conn = None
    try:
        from config.dbConfig import openDbConn

        conn = openDbConn()
        if conn is None:
            return {"known": False, "reason": "no_conn"}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS N FROM AI_STRAT_ORD_LEDGER "
                "WHERE ALG_CD=%s AND STK_STS_CD=%s",
                (algCd, "3"),
            )
            row = cur.fetchone()
        if isinstance(row, dict):
            n = int(row.get("N") or 0)
        elif isinstance(row, (list, tuple)):
            n = int(row[0] or 0)
        else:
            return {"known": False, "reason": "bad_row"}
        return {"known": True, "openPos": n, "openOrders": 0}
    except Exception:
        return {"known": False, "reason": "query"}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


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
