"""
매매창 로그 로컬 파일 적재 (일자별).

경로: tradeApp/config/data/logs/SATP_YYYYMMDD.log
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from utils.appPaths import configDataDir, projectRoot

_lock = threading.Lock()
_lastYmd = ""
_lastFp = None


def _tradeAppRoot() -> Path:
    """매매창 루트 (main.py 있는 폴더)."""
    ir = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if ir:
        p = Path(ir)
        if (p / "main.py").is_file() or p.name.lower() == "tradeapp":
            return p
        ta = p / "tradeApp"
        if (ta / "main.py").is_file():
            return ta
        return p
    root = projectRoot()
    if (root / "main.py").is_file():
        return root
    ta = root / "tradeApp"
    if (ta / "main.py").is_file():
        return ta
    return root


def deskLogsDir() -> Path:
    """로컬 로그 폴더. 없으면 생성."""
    path = _tradeAppRoot() / "config" / "data" / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        path = configDataDir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
    return path


def deskFileLogYmd(now=None) -> str:
    """파일명용 일자 — 달력 오늘(YYYYMMDD)."""
    return (now or datetime.now()).strftime("%Y%m%d")


def deskLogFilePath(ymd=None) -> Path:
    """해당 일자의 로그 파일 경로."""
    day = str(ymd or deskFileLogYmd())[:8] or datetime.now().strftime("%Y%m%d")
    return deskLogsDir() / f"SATP_{day}.log"


def appendDeskFileLog(line, tag=""):
    """
    매매창 로그 한 줄을 당일 파일에 append (재시작해도 같은 SATP_YYYYMMDD.log 이어쓰기).
    tag 예: TRADE / ERR / TERM / RUN
    """
    global _lastYmd, _lastFp
    text = str(line or "").rstrip("\n")
    if not text.strip():
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ymd = deskFileLogYmd()
    prefix = f"[{tag}] " if tag else ""
    out = f"[{stamp}] {prefix}{text}\n"
    try:
        with _lock:
            path = deskLogFilePath(ymd)
            # 일자 바뀌면 핸들 교체
            if _lastFp is not None and ymd != _lastYmd:
                try:
                    _lastFp.close()
                except Exception:
                    pass
                _lastFp = None
            if _lastFp is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                # "a" = 당일 재시작 시 기존 내용 뒤에 이어서 기록
                _lastFp = open(path, "a", encoding="utf-8", errors="replace")
                _lastYmd = ymd
            _lastFp.write(out)
            _lastFp.flush()
    except Exception:
        # 파일 실패해도 매매/UI는 계속
        pass


def markDeskFileLogRestart():
    """매매창 기동·재시작 구분선 (당일 파일에 append)."""
    appendDeskFileLog("======== 매매창 기동 ========", tag="RUN")
