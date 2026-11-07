"""
로그인 런처 → 매매창 실행 세션.

매매창(main)은 이 세션이 있을 때만 기동한다.
비밀번호는 저장하지 않으며, 토큰만 짧게 둔다.

세션 파일은 로그인 루트·tradeApp 양쪽에 기록해
설치 레이아웃 차이와 무관하게 매매창이 찾도록 한다.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from utils.appPaths import configDataDir, projectRoot

_PATH = configDataDir() / "deskLoginSession.json"
_ENV_TOKEN = "SATP_DESK_SESSION"
_TTL_SEC = 16 * 3600


def sessionEnvName():
    return _ENV_TOKEN


def sessionPath(rootDir=None):
    if rootDir:
        return Path(rootDir) / "config" / "data" / "deskLoginSession.json"
    return _PATH


def _sessionCandidates(rootDir=None):
    """매매창/로그인 이 읽을 수 있는 세션 경로 후보."""
    cands = []
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
        cands.append(rp)

    if rootDir:
        root = Path(rootDir)
        _add(root / "config" / "data" / "deskLoginSession.json")
        if root.name.lower() != "tradeapp":
            _add(root / "tradeApp" / "config" / "data" / "deskLoginSession.json")
        else:
            _add(root.parent / "config" / "data" / "deskLoginSession.json")

    envRoot = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if envRoot:
        er = Path(envRoot)
        _add(er / "config" / "data" / "deskLoginSession.json")

    _add(configDataDir() / "deskLoginSession.json")
    _add(projectRoot() / "config" / "data" / "deskLoginSession.json")
    _add(projectRoot() / "tradeApp" / "config" / "data" / "deskLoginSession.json")

    # main.py 기준 (실행 중 매매창)
    try:
        mainFile = Path(sys.argv[0]).resolve()
        if mainFile.name.lower() == "main.py":
            _add(mainFile.parent / "config" / "data" / "deskLoginSession.json")
    except Exception:
        pass

    return cands


def writeLoginSession(loginInfo: dict, rootDir=None):
    """로그인 성공 직후 세션 파일·환경변수 기록 (여러 후보 경로에 동일 기록)."""
    info = loginInfo or {}
    token = secrets.token_hex(16)
    now = int(time.time())
    payload = {
        "token": token,
        "sessToken": str(info.get("sessToken") or "").strip(),
        "logId": str(info.get("logId") or "").strip(),
        "csmno": str(info.get("csmno") or "").strip(),
        "custNm": str(info.get("custNm") or "").strip(),
        "deskGrade": str(info.get("deskGrade") or "").strip().upper(),
        "deskRole": str(info.get("deskRole") or "").strip().lower(),
        "createdAt": now,
        "expiresAt": now + _TTL_SEC,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    wrote = 0
    for path in _sessionCandidates(rootDir):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            wrote += 1
        except Exception:
            continue
    os.environ[_ENV_TOKEN] = token
    if wrote <= 0:
        # 최후: 기본 경로만이라도
        path = sessionPath(rootDir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return payload


def clearLoginSession(rootDir=None):
    for path in _sessionCandidates(rootDir):
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass
    os.environ.pop(_ENV_TOKEN, None)


def readLoginSession(rootDir=None):
    """세션 dict 또는 None (만료·손상 시 None). 후보 경로를 순회."""
    now = int(time.time())
    for path in _sessionCandidates(rootDir):
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            exp = int(data.get("expiresAt") or 0)
            if exp and now > exp:
                continue
            token = str(data.get("token") or "").strip()
            if not token:
                continue
            return data
        except Exception:
            continue
    return None


def hasValidSession(requireEnvMatch=True):
    """
    매매창 기동 허용 여부.
    requireEnvMatch=True 이면 런처가 넘긴 SATP_DESK_SESSION 과 파일 토큰이 같아야 함.
    """
    data = readLoginSession()
    if not data:
        return False
    fileTok = str(data.get("token") or "").strip()
    if not fileTok:
        return False
    if requireEnvMatch:
        envTok = str(os.environ.get(_ENV_TOKEN) or "").strip()
        if not envTok or envTok != fileTok:
            return False
    return True
