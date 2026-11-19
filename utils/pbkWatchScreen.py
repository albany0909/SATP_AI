"""관심종목 화면은 PBK01 후보와 모의 보유만 보여 준다.

수집(일봉·주봉·월봉·국면·지수)은 이 목록을 쓰지 않는다.
AI_WATCH_STK 행은 지우지 않는다. 예전 목록은 json 으로 한 번만 백업한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

COLLECTION_EXTRAS = ("069500", "001", "101", "201")


def watchScreenOn(settings=None):
    """모의이고 watchScreen 이 false 가 아니면 PBK 화면을 쓴다."""
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    mode = str(settings.get("deskTradeMode") or "paper").strip().lower()
    if mode in ("real", "live", "실전"):
        return False
    row = ((settings.get("stratLive") or {}).get("PBK01") or {})
    if isinstance(row, dict) and row.get("watchScreen") is False:
        return False
    return True


def _code(raw):
    from utils.stkCdUtil import normStkCd

    return normStkCd(raw) or ""


def _held(row):
    if not isinstance(row, dict):
        return False
    try:
        if int(row.get("qty") or row.get("qtyNum") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(row.get("leg1"))


def unionRows(candidates, positions, accountHolds):
    """후보와 보유(슬롯·모의 잔고)의 합집합. 구분 칸은 후보/보유/후보·보유."""
    by = {}

    def slot(code):
        row = by.get(code)
        if row is None:
            row = {
                "stkCd": code,
                "stkNm": code,
                "role": "",
                "candidate": False,
                "holding": False,
                "signalDt": "",
                "expireDt": "",
                "grpCd": "",
            }
            by[code] = row
        return row

    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        code = _code(item.get("stkCd"))
        if not code:
            continue
        row = slot(code)
        row["candidate"] = True
        name = str(item.get("stkNm") or item.get("name") or "").strip()
        if name:
            row["stkNm"] = name
        row["signalDt"] = str(item.get("signalDt") or "")
        row["expireDt"] = str(item.get("expireDt") or "")
        row["grpCd"] = row["grpCd"] or "PBK01"
    for item in list(positions or []) + list(accountHolds or []):
        if not _held(item):
            continue
        code = _code(item.get("stkCd") or item.get("STK_CD"))
        if not code:
            continue
        row = slot(code)
        row["holding"] = True
        name = str(item.get("stkNm") or item.get("STK_NM") or item.get("name") or "").strip()
        if name:
            row["stkNm"] = name
        if not row["grpCd"]:
            row["grpCd"] = "PAPER" if item.get("leg1") is None and item.get("signalDt") is None else "PBK01"
    out = []
    for row in by.values():
        if row["candidate"] and row["holding"]:
            row["role"] = "후보·보유"
        elif row["holding"]:
            row["role"] = "보유"
        else:
            row["role"] = "후보"
        out.append(row)
    order = {"보유": 0, "후보·보유": 1, "후보": 2}
    out.sort(key=lambda row: (order.get(row["role"], 9), row["stkCd"]))
    return out


def loadScreenRows(statePath=None, accountHolds=None):
    """저장된 PBK 후보·슬롯과 넘긴 모의 잔고로 화면 행을 만든다."""
    candidates = []
    positions = []
    try:
        from utils.pbk01Store import latestByKind, pbkPath

        row = latestByKind(statePath or pbkPath("pbk01_state.jsonl"), "state") or {}
        candidates = row.get("candidates") or []
        positions = row.get("positions") or []
    except Exception:
        candidates = []
        positions = []
    return unionRows(candidates, positions, accountHolds)


def screenCodes(rows):
    return [row["stkCd"] for row in rows or [] if row.get("stkCd")]


def backupWatchOnce(rows, folder, now=None):
    """현재 저장 형식 그대로 한 번만 백업한다. 파일이 있으면 덮어쓰지 않는다."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    found = sorted(folder.glob("watchlist_backup_*.json"))
    if found:
        return {"ok": True, "skipped": "exists", "path": str(found[0]), "cnt": 0}
    stamp = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    path = folder / f"watchlist_backup_{stamp}.json"
    payload = list(rows or [])
    from config.settingsStore import _writeJson

    _writeJson(path, payload)
    return {"ok": True, "skipped": "", "path": str(path), "cnt": len(payload)}


def readWatchBackup(folder):
    folder = Path(folder)
    found = sorted(folder.glob("watchlist_backup_*.json"))
    if not found:
        return []
    try:
        data = json.loads(found[0].read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def legacyWatchRows(reader=None):
    """DB 관심종목을 읽기만 한다. 행은 지우지 않는다."""
    if reader is not None:
        return list(reader() or [])
    try:
        from utils.watchStkStore import listActiveWatch

        return list(listActiveWatch(mktTp="KR") or [])
    except Exception:
        return []


def ensureWatchBackup(folder, reader=None):
    """목록을 읽었고 1건 이상일 때만 백업한다. 빈 결과로 파일을 만들지 않는다."""
    folder = Path(folder)
    if list(folder.glob("watchlist_backup_*.json")):
        return {"ok": True, "skipped": "exists"}
    try:
        rows = legacyWatchRows(reader)
    except Exception:
        return {"ok": False, "skipped": "unread"}
    if not rows:
        return {"ok": True, "skipped": "empty"}
    return backupWatchOnce(rows, folder)


def collectionCodes(backupRows, extras=COLLECTION_EXTRAS):
    """화면과 분리된 수집 대상. 백업된 관심종목, 069500, 지수."""
    out = []
    seen = set()
    for row in backupRows or []:
        if isinstance(row, str):
            code = _code(row)
        else:
            code = _code((row or {}).get("stkCd") or (row or {}).get("STK_CD"))
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    for raw in extras or []:
        code = str(raw or "").strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def touchDelta(previous, screen, keep):
    """화면 목록에 맞춰 넣을 코드와 뺄 코드. keep 은 다른 수집·탭이 쓰는 구독이다."""
    prev = {_code(code) for code in (previous or []) if _code(code)}
    want = []
    seen = set()
    for code in screen or []:
        cd = _code(code)
        if cd and cd not in seen:
            seen.add(cd)
            want.append(cd)
    wantSet = set(want)
    held = {_code(code) for code in (keep or []) if _code(code)}
    added = [code for code in want if code not in prev and code not in held]
    removed = [code for code in prev if code not in wantSet and code not in held]
    return {"added": added, "removed": removed, "screen": want}


PBK_TAB = {"key": "pbk", "name": "PBK", "bg": "#C5E1A5", "kind": "monitor", "algCd": "PBK01"}


def ensurePbkTab(tabs, settings=None):
    """모의 PBK 화면이면 저장된 탭 목록에 PBK 가 없어도 앞에 넣는다."""
    rows = []
    for item in tabs or []:
        if isinstance(item, dict):
            rows.append(dict(item))
        else:
            rows.append({"key": str(item or ""), "name": str(item or ""), "bg": ""})
    if not watchScreenOn(settings):
        return rows
    if any(str(row.get("key") or "") == "pbk" for row in rows):
        return rows
    return [dict(PBK_TAB)] + rows


def bootWatchSelection(current, visibleKeys, settings=None):
    """부팅 때는 저장된 탭보다 PBK 를 먼저 연다."""
    keys = []
    for item in visibleKeys or []:
        key = item.get("key") if isinstance(item, dict) else item
        key = str(key or "").strip()
        if key and key not in keys:
            keys.append(key)
    if watchScreenOn(settings) and "pbk" in keys:
        return "pbk"
    from trading.deskLiveAlgs import resolveMonitorSelection

    return resolveMonitorSelection(current, keys)


def setWatchScreen(on):
    """화면 스위치. DB 관심종목은 건드리지 않는다."""
    from config.settingsStore import loadSettings, patchSettings

    settings = loadSettings() or {}
    live = dict(settings.get("stratLive") or {})
    row = dict(live.get("PBK01") or {})
    row["watchScreen"] = bool(on)
    row.setdefault("paperOnly", True)
    live["PBK01"] = row
    patchSettings({"stratLive": live})
    return {"ok": True, "watchScreen": bool(on)}


def restoreLegacyWatch():
    """관심종목 화면을 백업·DB 목록으로 되돌린다."""
    return setWatchScreen(False)


def noteScreenRefresh(reason, codes=None, accountHolds=None):
    """스캔·백필·체결 뒤 터치 시세만 화면 목록에 맞춘다. 실전 모드에서는 하지 않는다."""
    if not watchScreenOn():
        return {"ok": True, "skipped": "off"}
    if codes is None:
        codes = screenCodes(loadScreenRows(accountHolds=accountHolds))
    try:
        from trading.watchRtMonitor import syncPbkTouch

        return syncPbkTouch(codes, reason or "PBK화면")
    except Exception as err:
        return {"ok": False, "errMsg": str(err), "screen": list(codes or [])}


def notePaperFill(sideLabel, stkCd):
    """모의 매수·매도 체결 뒤에만 화면 시세를 갱신한다."""
    if not watchScreenOn():
        return {"ok": True, "skipped": "off"}
    return noteScreenRefresh(f"체결 {sideLabel} {stkCd}")
