"""
앱 경로 — 소스 실행 vs PyInstaller(frozen) 공통.

frozen: 쓰기 가능 루트 = exe 옆 폴더
        읽기 리소스 = sys._MEIPASS
소스: 둘 다 프로젝트 루트
"""

from __future__ import annotations

import sys
from pathlib import Path


def isFrozen():
    return bool(getattr(sys, "frozen", False))


def projectRoot():
    """설정·.env·tradeApp 등 쓰기 기준 루트."""
    if isFrozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resourceRoot():
    """번들된 읽기 전용 리소스 루트."""
    if isFrozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return projectRoot()


def configDataDir():
    """config/data (쓰기). 없으면 생성."""
    path = projectRoot() / "config" / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
