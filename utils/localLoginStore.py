"""
로그인 ID·앱키 로컬 기억 (비밀번호는 저장하지 않음).
경로: config/data/loginLocal.json — gitignore
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.appPaths import configDataDir

_PATH = configDataDir() / "loginLocal.json"

# 절대 디스크에 남기지 않을 키
_STRIP_KEYS = ("loginPswd", "password", "passwd", "pw", "LOGIN_PSWD")


def loadLoginLocal():
    """
    {remember:bool, logId:str,
     realAppKey, realAppSecret, paperAppKey, paperAppSecret}
    비밀번호 필드는 있어도 무시·제거한다.
    """
    try:
        if not _PATH.is_file():
            return {}
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        for key in _STRIP_KEYS:
            data.pop(key, None)
        return data
    except Exception:
        return {}


def saveLoginLocal(payload: dict):
    """로컬만 저장. 비밀번호 키는 제거 후 기록."""
    data = dict(payload or {})
    for key in _STRIP_KEYS:
        data.pop(key, None)
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clearLoginLocal():
    try:
        if _PATH.is_file():
            _PATH.unlink()
    except Exception:
        pass
