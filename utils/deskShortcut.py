"""
바탕화면 바로가기 — OneDrive 등 실제 Desktop 폴더를 사용.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def desktopDir() -> Path:
    """사용자가 보는 실제 바탕화면 (OneDrive 리다이렉트 포함)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOP = 0 → 셸이 쓰는 현재 바탕화면
            hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
            if hr == 0 and buf.value:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            pass
        try:
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::GetFolderPath('Desktop')",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            line = (r.stdout or "").strip().splitlines()
            if line:
                p = Path(line[-1].strip())
                if p.is_dir():
                    return p
        except Exception:
            pass

    for cand in (
        Path.home() / "Desktop",
        Path(os.environ.get("USERPROFILE", "") or "") / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "OneDrive" / "바탕 화면",
    ):
        if cand.is_dir():
            return cand
    p = Path.home() / "Desktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _escPs(path) -> str:
    return str(path).replace("'", "''")


def createLoginShortcut(targetExe: Path, linkName="천둥이 로그인", iconPath=None):
    """
    실제 바탕화면에 .lnk 생성.
    반환: 생성된 lnk Path
    """
    targetExe = Path(targetExe).resolve()
    if not targetExe.is_file():
        raise FileNotFoundError(f"바로가기 대상 없음: {targetExe}")

    desk = desktopDir()
    desk.mkdir(parents=True, exist_ok=True)
    lnk = desk / f"{linkName}.lnk"

    icon = Path(iconPath) if iconPath else None
    if icon is None or not icon.is_file():
        for base in (
            targetExe.parent / "assets",
            Path(__file__).resolve().parents[1] / "assets",
        ):
            cand = base / "satpBolt.ico"
            if cand.is_file():
                icon = cand
                break
    if icon is None or not icon.is_file():
        icon = targetExe

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_escPs(lnk)}'); "
        f"$s.TargetPath = '{_escPs(targetExe)}'; "
        f"$s.WorkingDirectory = '{_escPs(targetExe.parent)}'; "
        f"$s.IconLocation = '{_escPs(icon)}'; "
        "$s.Description = '천둥이 로그인'; "
        "$s.Save()"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0 or not lnk.is_file():
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"바로가기 생성 실패: {err or lnk}")
    return lnk


def findLoginExe(installRoot: Path = None):
    """설치 루트 또는 실행 위치에서 SATP_AI_Login.exe 찾기."""
    cands = []
    if installRoot:
        root = Path(installRoot)
        # tradeApp 이면 부모가 설치 루트
        if root.name.lower() == "tradeapp":
            root = root.parent
        cands.extend(
            [
                root / "SATP_AI_Login.exe",
                root / "SATP_AI_Login" / "SATP_AI_Login.exe",
            ]
        )
    if getattr(sys, "frozen", False):
        cands.insert(0, Path(sys.executable).resolve())
    for c in cands:
        if c and c.is_file():
            return c
    return None


def ensureLoginShortcut(installRoot: Path = None, loginExe: Path = None):
    """로그인 exe 바로가기가 없으면(또는 갱신) 실제 바탕화면에 만든다."""
    exe = Path(loginExe) if loginExe else findLoginExe(installRoot)
    if not exe or not exe.is_file():
        return None
    return createLoginShortcut(exe)
