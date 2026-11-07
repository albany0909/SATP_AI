"""
키움 앱키를 로컬 .env 에만 반영 (AI_DESK_CFG / settings 동기 금지).
시크릿은 설치루트(또는 개발 프로젝트 루트) .env 한곳만 — tradeApp/.env 금지.
"""

from __future__ import annotations

import os
from pathlib import Path

from utils.appPaths import projectRoot

_KEY_MAP = {
    "realAppKey": "KIWOOM_REAL_APP_KEY",
    "realAppSecret": "KIWOOM_REAL_APP_SECRET",
    "paperAppKey": "KIWOOM_PAPER_APP_KEY",
    "paperAppSecret": "KIWOOM_PAPER_APP_SECRET",
}


def secretEnvPath() -> Path:
    """
    앱키를 읽고 쓸 단일 .env 경로.
    Setup 설치루트(C:\\SATP_AI\\.env). tradeApp 하위에 두지 않는다.
    """
    ir = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if ir:
        p = Path(ir)
        if p.name.lower() == "tradeapp":
            return p.parent / ".env"
        if (p / "tradeApp").is_dir():
            return p / ".env"
        return p / ".env"
    root = projectRoot()
    if root.name.lower() == "tradeapp":
        return root.parent / ".env"
    return root / ".env"


def _envPaths():
    """저장 대상 — 설치/프로젝트 루트만 (tradeApp/.env 제외)."""
    return [secretEnvPath()]


def _readEnvLines(path: Path):
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def loadApiKeysFromEnv():
    """현재 프로세스/.env 기준 앱키 스냅샷."""
    from config.loadEnv import ensureEnvLoaded

    ensureEnvLoaded()
    return {
        "realAppKey": (os.environ.get("KIWOOM_REAL_APP_KEY") or "").strip(),
        "realAppSecret": (os.environ.get("KIWOOM_REAL_APP_SECRET") or "").strip(),
        "paperAppKey": (os.environ.get("KIWOOM_PAPER_APP_KEY") or "").strip(),
        "paperAppSecret": (os.environ.get("KIWOOM_PAPER_APP_SECRET") or "").strip(),
    }


def saveApiKeysToEnv(keys: dict):
    """
    .env 의 키움 앱키 4종만 갱신. 다른 키는 유지.
    설치루트(또는 개발 루트) .env 한 파일만 갱신.
    """
    from config import loadEnv as loadEnvMod

    updates = {}
    for localKey, envName in _KEY_MAP.items():
        val = str((keys or {}).get(localKey) or "").strip()
        updates[envName] = val

    for envPath in _envPaths():
        try:
            envPath.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        lines = _readEnvLines(envPath)
        seen = set()
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                out.append(line)
                continue
            name = line.split("=", 1)[0].strip()
            if name in updates:
                out.append(f"{name}={updates[name]}")
                seen.add(name)
            else:
                out.append(line)
        for name, val in updates.items():
            if name not in seen:
                out.append(f"{name}={val}")
        try:
            envPath.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        except Exception:
            pass

    for name, val in updates.items():
        os.environ[name] = val

    try:
        import config.config as cfg

        cfg.real_app_key = updates["KIWOOM_REAL_APP_KEY"]
        cfg.real_app_secret = updates["KIWOOM_REAL_APP_SECRET"]
        cfg.paper_app_key = updates["KIWOOM_PAPER_APP_KEY"]
        cfg.paper_app_secret = updates["KIWOOM_PAPER_APP_SECRET"]
    except Exception:
        pass

    _ = loadEnvMod
    return True
