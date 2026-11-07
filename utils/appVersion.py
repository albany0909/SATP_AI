"""
매매창 앱 버전 (알고리즘 strat version 과 별개).

표시: v{major}.{minor:03d}  예) v1.001
- major: 관리자 수동 상향
- minor: 소스 배포 시 bumpAppVersion 으로 자동 +1
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.appPaths import configDataDir

_PATH = configDataDir() / "appVersion.json"


def _candidateVersionPaths():
    """로그인 루트·tradeApp·개발 소스 등 버전 파일 후보."""
    out = []
    seen = set()

    def _add(p: Path):
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        out.append(rp)

    _add(_PATH)
    try:
        root = projectRoot()
        _add(root / "config" / "data" / "appVersion.json")
        _add(root / "tradeApp" / "config" / "data" / "appVersion.json")
        # Login.exe 가 설치루트에 있고 tradeApp 이 하위인 경우
        if root.name.lower() == "tradeapp":
            _add(root.parent / "config" / "data" / "appVersion.json")
    except Exception:
        pass
    return out


def _readRaw():
    """여러 후보 중 가장 높은 버전을 쓴다 (로그인 표시 = 매매창 업데이트 반영)."""
    best = None
    bestPair = (-1, -1)
    for path in _candidateVersionPaths():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            maj = int(data.get("major") or 0)
            mn = int(data.get("minor") or 0)
            if (maj, mn) > bestPair:
                bestPair = (maj, mn)
                best = data
        except Exception:
            continue
    if best is not None:
        return best
    return {"major": 1, "minor": 0}


def getAppVersionParts():
    """(major, minor) int."""
    data = _readRaw()
    try:
        major = max(0, int(data.get("major") or 1))
    except (TypeError, ValueError):
        major = 1
    try:
        minor = max(0, int(data.get("minor") or 0))
    except (TypeError, ValueError):
        minor = 0
    return major, minor


def formatAppVersion(major=None, minor=None):
    """표시 문자열 v1.001"""
    if major is None or minor is None:
        major, minor = getAppVersionParts()
    return f"v{int(major)}.{int(minor):03d}"


def saveAppVersion(major, minor):
    """로컬 버전 파일 저장."""
    payload = {"major": int(major), "minor": int(minor)}
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return formatAppVersion(payload["major"], payload["minor"])


def bumpMinor():
    """소스 포인트 배포용 — minor +1 (999 넘으면 major+1, minor=0)."""
    major, minor = getAppVersionParts()
    minor += 1
    if minor > 999:
        major += 1
        minor = 0
    return saveAppVersion(major, minor)


def bumpMajor():
    """메이저 업데이트 — major +1, minor=0."""
    major, _minor = getAppVersionParts()
    return saveAppVersion(major + 1, 0)


def parseVersionLabel(raw):
    """'v1.001' / '1.001' → (major, minor) 또는 None."""
    text = str(raw or "").strip().lower().lstrip("v")
    if "." not in text:
        return None
    a, b = text.split(".", 1)
    try:
        return int(a), int(b)
    except ValueError:
        return None


def isNewer(remoteMajor, remoteMinor, localMajor=None, localMinor=None):
    """원격이 로컬보다 새면 True."""
    if localMajor is None or localMinor is None:
        localMajor, localMinor = getAppVersionParts()
    if int(remoteMajor) > int(localMajor):
        return True
    if int(remoteMajor) == int(localMajor) and int(remoteMinor) > int(localMinor):
        return True
    return False
