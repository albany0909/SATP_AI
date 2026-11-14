"""알고리즘 사용/중지. 매매 규칙은 바꾸지 않는다.

가동 여부는 기존 settings.stratLive.enabled 에 둔다.
MTS01 은 화면이 읽는 maTouchLive.enabled 도 같이 맞춘다.
모의/실전(paperOnly) 은 이 화면에서 바꾸지 않는다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

KEEP_POSITION_NOTE = (
    "보유 수량은 그대로 둔다. 중단해도 자동으로 팔지 않는다. "
    "신규 진입만 막히고, 이미 가진 종목의 손절·익절 감시는 계속한다."
)

ENTRY_ACTS = {"BUY", "ADD", "SCOUT"}


def actionsWhenStopped(enabled, actions):
    """중지면 새 매수·추가·정찰만 뺀다. 매도·청산은 남긴다."""
    kept = []
    for act in actions or []:
        if not isinstance(act, dict):
            continue
        kind = str(act.get("side") or act.get("act") or "").upper()
        if kind in ENTRY_ACTS or kind.startswith("BUY"):
            if enabled:
                kept.append(act)
            continue
        kept.append(act)
    return kept


def _norm(algCd):
    from strategies.specLoader import normAlgCd

    return normAlgCd(algCd)


def applyAlgRunToggle(settings, algCd, turnOn, reason="", actor="desk", now=""):
    """설정 사본과 변경 로그를 돌려준다. paperOnly 는 그대로다."""
    code = _norm(algCd)
    base = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    if not code:
        return {"ok": False, "err": "알고리즘 코드 없음", "settings": base, "log": None}
    live = dict(base.get("stratLive") or {})
    row = dict(live.get(code) or {})
    oldPaper = row.get("paperOnly")
    old = row.get("enabled")
    row["enabled"] = bool(turnOn)
    if oldPaper is not None:
        row["paperOnly"] = oldPaper
    live[code] = row
    base["stratLive"] = live
    if code == "MTS01":
        mt = dict(base.get("maTouchLive") or {})
        oldMtPaper = mt.get("paperOnly")
        mt["enabled"] = bool(turnOn)
        if oldMtPaper is not None:
            mt["paperOnly"] = oldMtPaper
        base["maTouchLive"] = mt
    log = {
        "algCd": code,
        "at": str(now or ""),
        "actor": str(actor or "desk"),
        "old": None if old is None else bool(old),
        "new": bool(turnOn),
        "reason": str(reason or ("다시 사용" if turnOn else "중단")),
        "positions": KEEP_POSITION_NOTE,
    }
    return {"ok": True, "settings": base, "log": log}


def appendAlgMgrLog(entry, path):
    """변경 한 줄을 JSON 목록에 붙인다. 호출한 경로만 쓴다."""
    filePath = Path(path)
    rows = []
    if filePath.is_file():
        try:
            loaded = json.loads(filePath.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                rows = loaded
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append(dict(entry or {}))
    filePath.parent.mkdir(parents=True, exist_ok=True)
    filePath.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def modeLabel(spec, settings, algCd):
    """모의/실전. 설정에 paperOnly 가 있으면 그것을 본다. 없으면 불명확."""
    code = _norm(algCd)
    settings = settings if isinstance(settings, dict) else {}
    if code == "MTS01":
        mt = settings.get("maTouchLive")
        if isinstance(mt, dict) and "paperOnly" in mt:
            return "모의" if mt.get("paperOnly") else "실전"
    row = (settings.get("stratLive") or {}).get(code)
    if isinstance(row, dict) and "paperOnly" in row:
        return "모의" if row.get("paperOnly") else "실전"
    params = (spec or {}).get("params") or {}
    if "paperOnly" in params:
        return "모의" if params.get("paperOnly") else "실전"
    return "불명확"


def classifyAlgRun(configuredOn, runnerStarted, tradingDay, nowHm, sessionEnd="153000", specBlocked=False, openCount=0):
    """운행중·대기·중지·중지(청산만)·미기동."""
    if not configuredOn and int(openCount or 0) > 0:
        return "중지(청산만)"
    if specBlocked and not runnerStarted:
        return "미기동"
    if not configuredOn:
        return "중지"
    if not runnerStarted:
        return "미기동"
    hm = str(nowHm or "000000").zfill(6)[-6:]
    end = str(sessionEnd or "153000").zfill(6)[-6:]
    if not tradingDay or hm >= end:
        return "대기"
    return "운행중"


def algMgrRows(specs, settings, versions=None, counts=None, pnl=None, runs=None, nowHm="120000", tradingDay=True, holds=None):
    """조회 행. DB 가 없으면 수량·손익은 0 이나 '-'."""
    from trading.deskLiveAlgs import effectiveAlgEnabled

    versions = versions or {}
    counts = counts or {}
    pnl = pnl or {}
    if holds is None:
        holds = readOpenCounts()
    out = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        code = _norm(spec.get("algCd"))
        if not code:
            continue
        from utils.algSpecDoc import easyFirstSentence, easyText

        on = effectiveAlgEnabled(code, spec, settings)
        configured = _configuredOn(code, settings, spec)
        blocked = code == "SRG01" and spec.get("enabled") is not True
        started = bool((runs or {}).get(code))
        runState = classifyAlgRun(
            configured, started, tradingDay, nowHm,
            specBlocked=blocked, openCount=int((holds or {}).get(code) or 0),
        )
        ver = versions.get(code) or {}
        money = pnl.get(code) or {}
        out.append({
            "algCd": code,
            "name": str(spec.get("name") or code),
            "summary": easyFirstSentence(code),
            "easy": easyText(code),
            "runState": runState,
            "version": spec.get("version") or "-",
            "status": "사용" if on else "중지",
            "enabled": bool(on),
            "mode": modeLabel(spec, settings, code),
            "verYmd": str(ver.get("ymd") or "-"),
            "stkCnt": int(counts.get(code) or 0),
            "todayPnl": money.get("today", "-"),
            "cumPnl": money.get("cum", "-"),
            "specGate": code == "SRG01" and spec.get("enabled") is not True,
        })
    return out


def _configuredOn(code, settings, spec):
    """저장 스위치. SRG01 은 스펙 enabled 가 꺼지면 저장값과 상관없이 막힌다."""
    settings = settings if isinstance(settings, dict) else {}
    if code == "MTS01":
        mt = settings.get("maTouchLive")
        if isinstance(mt, dict) and "enabled" in mt:
            return bool(mt.get("enabled"))
    row = (settings.get("stratLive") or {}).get(code)
    if isinstance(row, dict) and "enabled" in row:
        return bool(row.get("enabled"))
    return bool((spec or {}).get("enabled"))


def readOpenCounts():
    """메모리에 있는 보유 종목 수. 조회가 실패하면 0."""
    out = {}

    def _take(code, fn):
        try:
            out[code] = int(fn() or 0)
        except Exception:
            out[code] = 0

    try:
        from trading.surgeTradeLive import heldPositionCount as surgeHeld

        _take("SRG01", surgeHeld)
    except Exception:
        out["SRG01"] = 0
    try:
        from trading.maTouchLive import heldPositionCount as mtsHeld

        _take("MTS01", mtsHeld)
    except Exception:
        out["MTS01"] = 0
    try:
        from trading.manualMgrLive import listMmgPositions

        _take("MMG01", lambda: len(listMmgPositions() or {}))
    except Exception:
        out["MMG01"] = 0
    try:
        from trading.maSupportEodLive import heldPositionCount as mseHeld

        _take("MSE01", mseHeld)
    except Exception:
        out["MSE01"] = 0
    return out


def readRunnerStarted():
    """이 프로세스에서 기동 스레드가 떠 있는지. 없으면 False."""
    flags = {}

    def _flag(mod, name, code):
        try:
            flags[code] = bool(getattr(mod, name))
        except Exception:
            flags[code] = False

    try:
        import trading.surgeTradeLive as surge

        _flag(surge, "_started", "SRG01")
    except Exception:
        flags["SRG01"] = False
    try:
        import trading.maTouchLive as touch

        _flag(touch, "_workerStarted", "MTS01")
    except Exception:
        flags["MTS01"] = False
    try:
        import trading.manualMgrLive as mmg

        _flag(mmg, "_workerStarted", "MMG01")
    except Exception:
        flags["MMG01"] = False
    try:
        import trading.maSupportEodLive as mse

        _flag(mse, "_workerStarted", "MSE01")
    except Exception:
        flags["MSE01"] = False
    return flags


def _oneLine(spec):
    rules = (spec or {}).get("rules") or {}
    text = str(rules.get("entry") or rules.get("scope") or (spec or {}).get("notes") or "")
    text = " ".join(text.split())
    if not text:
        return "한 줄 요약 없음"
    return text[:80]
