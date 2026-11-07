"""프로젝트·설치루트 .env 로드 (시크릿은 설치루트 우선)."""

from __future__ import annotations

import os
from pathlib import Path

from utils.appPaths import projectRoot

_LOADED = False


def _secretEnvFromDir(dirPath: Path) -> Path:
    """
    시크릿 .env 위치.
    tradeApp 폴더면 부모(설치루트), 설치루트면 그곳.
    (localApiKeyStore.secretEnvPath 와 동일 규칙)
    """
    p = Path(dirPath)
    if p.name.lower() == "tradeapp":
        return p.parent / ".env"
    if (p / "tradeApp").is_dir() or (p / "tradeApp" / "main.py").is_file():
        return p / ".env"
    return p / ".env"


def resolveEnvFilePaths():
    """
    읽을 .env 후보 (앞→뒤, 뒤가 우선 override).
    - 개발: 프로젝트 루트 .env
    - 설치: C:\\SATP_AI\\.env (Login 옆 — tradeApp 부모)
    tradeApp\\.env 는 쓰지 않는다.
    """
    paths = []
    seen = set()

    def _add(p: Path):
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        paths.append(p)

    root = projectRoot()
    _add(_secretEnvFromDir(root))

    # 로그인 런처가 넘긴 경로 (tradeApp 또는 설치루트 모두 허용)
    ir = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if ir:
        _add(_secretEnvFromDir(Path(ir)))

    return paths


def ensureEnvLoaded(force=False):
    """
    .env 를 환경변수에 반영.
    설치루트 .env 가 있으면 그 값이 최종(override).
    force=True 이면 재로드 (런처가 키를 갱신한 뒤 등).

    중요: 파일 값이 빈 문자열이면, 이미 프로세스에 있는 비어 있지 않은
    환경변수(런처가 넘긴 앱키 등)를 지우지 않는다.
    """
    global _LOADED
    if _LOADED and not force:
        return
    _LOADED = True
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    cands = resolveEnvFilePaths()
    existing = [p for p in cands if p.is_file()]
    merged = {}
    for envPath in existing:
        try:
            chunk = dotenv_values(envPath) or {}
        except Exception:
            chunk = {}
        for name, val in chunk.items():
            if name is None:
                continue
            key = str(name).strip()
            if not key:
                continue
            text = "" if val is None else str(val).strip()
            # 빈 값으로 기존 키를 덮어쓰지 않음
            if text:
                merged[key] = text
            elif key not in merged:
                merged[key] = text
    for key, text in merged.items():
        if text:
            os.environ[key] = text
        elif not (os.environ.get(key) or "").strip():
            os.environ[key] = text
