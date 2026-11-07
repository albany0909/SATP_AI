"""
SATP_AI PC 설치 프로그램 (SATP_AI_Setup.exe).

더블클릭:
  - 설치 경로 표시 · 프로그레스바
  - 로그인 exe + 런타임 + 매매창(tradeApp) 설치
  - 바탕화면 [SATP_AI 로그인] 바로가기 생성

로그인 런처 자동설치:
  SATP_AI_Setup.exe --dest <설치루트> --silent
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def _payloadZip():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        for cand in (base / "payload.zip", base / "dist" / "payload.zip"):
            if cand.is_file():
                return cand
        return base / "payload.zip"
    return Path(__file__).resolve().parents[1] / "dist" / "payload.zip"


def _assetPath(*names):
    bases = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bases.append(Path(sys._MEIPASS) / "assets")
        bases.append(Path(sys.executable).resolve().parent / "assets")
    bases.append(Path(__file__).resolve().parents[1] / "assets")
    for base in bases:
        for name in names:
            p = base / name
            if p.is_file():
                return p
    return None


def _defaultDest():
    return Path("C:/SATP_AI")


def _findLoginExe(installRoot: Path):
    root = Path(installRoot)
    for cand in (
        root / "SATP_AI_Login.exe",
        root / "SATP_AI_Login" / "SATP_AI_Login.exe",
    ):
        if cand.is_file():
            return cand
    return None


def writeUninstaller(dest: Path):
    """
    설치 폴더에 제거 스크립트 생성.
      SATP_AI_Uninstall.bat
      SATP_AI_Uninstall_Ask.vbs   (영문 ASCII)
      SATP_AI_Uninstall_Do.cmd    (경로 고정, TEMP 복사 실행)

    bat 안에서 echo 로 VBS/CMD 를 만들면 )/> 이스케이프가 깨지므로
    보조 파일은 Python 이 직접 기록한다.
    """
    dest = Path(dest).resolve()
    rootStr = str(dest)

    vbs = dest / "SATP_AI_Uninstall_Ask.vbs"
    vbs.write_text(
        "Set sh = CreateObject(\"WScript.Shell\")\r\n"
        f'p = "{rootStr.replace(chr(34), chr(34) + chr(34))}"\r\n'
        'msg = "Remove SATP_AI?" & vbCrLf & vbCrLf & p & vbCrLf & vbCrLf '
        '& "Login, tradeApp, runtime, shortcuts will be deleted."\r\n'
        'r = sh.Popup(msg, 0, "SATP_AI Uninstall", 4 + 48)\r\n'
        "If r <> 6 Then WScript.Quit 1\r\n"
        "WScript.Quit 0\r\n",
        encoding="ascii",
        errors="replace",
    )

    doCmd = dest / "SATP_AI_Uninstall_Do.cmd"
    doLines = [
        "@echo off",
        "setlocal EnableExtensions",
        f'set "TARGET={rootStr}"',
        'set "EMPTY=%TEMP%\\SATP_AI_empty_%RANDOM%"',
        "echo Waiting for file locks...",
        "ping -n 4 127.0.0.1 >nul",
        "taskkill /F /IM SATP_AI_Login.exe >nul 2>&1",
        "taskkill /F /IM SATP_AI.exe >nul 2>&1",
        'attrib -R -S -H "%TARGET%\\*.*" /S /D >nul 2>&1',
        'echo Deleting "%TARGET%" ...',
        'mkdir "%EMPTY%" >nul 2>&1',
        'robocopy "%EMPTY%" "%TARGET%" /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NC /NS >nul 2>&1',
        'rd /s /q "%EMPTY%" >nul 2>&1',
        'rd /s /q "%TARGET%" >nul 2>&1',
        'if exist "%TARGET%" ping -n 3 127.0.0.1 >nul',
        'if exist "%TARGET%" rd /s /q "%TARGET%" >nul 2>&1',
        'if exist "%TARGET%" (',
        "  echo [FAIL] Could not delete:",
        '  echo   %TARGET%',
        "  echo Close Login/Trade windows and retry.",
        "  pause",
        ") else (",
        "  echo [OK] Uninstall complete.",
        "  ping -n 3 127.0.0.1 >nul",
        ")",
        'del "%~f0" >nul 2>&1',
    ]
    doCmd.write_text("\r\n".join(doLines) + "\r\n", encoding="ascii", errors="replace")

    batLines = [
        "@echo off",
        "setlocal EnableExtensions",
        'cd /d "%~dp0"',
        "echo.",
        "echo SATP_AI Uninstall",
        "echo Path: %CD%",
        "echo.",
        "",
        'if not exist "%~dp0SATP_AI_Uninstall_Ask.vbs" (',
        "  echo Missing Ask.vbs - reinstall SATP_AI Setup.",
        "  pause",
        "  exit /b 1",
        ")",
        'if not exist "%~dp0SATP_AI_Uninstall_Do.cmd" (',
        "  echo Missing Do.cmd - reinstall SATP_AI Setup.",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        'cscript //nologo "%~dp0SATP_AI_Uninstall_Ask.vbs"',
        "if errorlevel 1 (",
        "  echo Cancelled.",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        "echo Stopping processes...",
        "taskkill /F /IM SATP_AI_Login.exe >nul 2>&1",
        "taskkill /F /IM SATP_AI.exe >nul 2>&1",
        "",
        "echo Removing shortcuts...",
        (
            "powershell -NoProfile -ExecutionPolicy Bypass -Command "
            "\"$paths=@([Environment]::GetFolderPath('Desktop'),"
            "[Environment]::GetFolderPath('CommonDesktopDirectory'),"
            "(Join-Path $env:USERPROFILE 'Desktop')); "
            "foreach($d in $paths){ if($d -and (Test-Path -LiteralPath $d)){ "
            "Get-ChildItem -LiteralPath $d -Filter '*.lnk' -EA SilentlyContinue | "
            "Where-Object { $_.Name -like 'SATP_AI*.lnk' -or $_.Name -like '천둥이*.lnk' } | "
            "Remove-Item -Force -EA SilentlyContinue } }\""
        ),
        "",
        'set "TMPDEL=%TEMP%\\SATP_AI_Uninstall_Do_%RANDOM%.cmd"',
        'copy /Y "%~dp0SATP_AI_Uninstall_Do.cmd" "%TMPDEL%" >nul',
        'if not exist "%TMPDEL%" (',
        "  echo Failed to copy delete script to TEMP.",
        "  pause",
        "  exit /b 1",
        ")",
        "echo Starting delete...",
        'start "SATP_AI Uninstall" cmd /c "%TMPDEL%"',
        "ping -n 3 127.0.0.1 >nul",
        "endlocal",
        "exit /b 0",
    ]
    bat = dest / "SATP_AI_Uninstall.bat"
    bat.write_text("\r\n".join(batLines) + "\r\n", encoding="ascii", errors="replace")

    ps1 = dest / "SATP_AI_Uninstall.ps1"
    ps1.write_text(
        "# Run SATP_AI_Uninstall.bat\r\n"
        f'Start-Process -FilePath "{bat}" -WorkingDirectory "{dest}"\r\n',
        encoding="ascii",
    )
    return bat


def _desktopDir() -> Path:
    """사용자가 보는 실제 바탕화면 (OneDrive 리다이렉트 포함)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
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
            lines = (r.stdout or "").strip().splitlines()
            if lines:
                p = Path(lines[-1].strip())
                if p.is_dir():
                    return p
        except Exception:
            pass
    for cand in (
        Path.home() / "Desktop",
        Path(os.environ.get("USERPROFILE", "") or "") / "Desktop",
    ):
        if cand.is_dir():
            return cand
    p = Path.home() / "Desktop"
    p.mkdir(parents=True, exist_ok=True)
    return p


def createDesktopShortcut(targetExe: Path, linkName="천둥이 로그인"):
    """실제 바탕화면(OneDrive 포함)에 .lnk 생성."""
    targetExe = Path(targetExe).resolve()
    if not targetExe.is_file():
        raise FileNotFoundError(f"바로가기 대상 없음: {targetExe}")

    desktop = _desktopDir()
    desktop.mkdir(parents=True, exist_ok=True)
    lnk = desktop / f"{linkName}.lnk"

    icon = _assetPath("satpBolt.ico") or targetExe

    def _esc(p):
        return str(p).replace("'", "''")

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_esc(lnk)}'); "
        f"$s.TargetPath = '{_esc(targetExe)}'; "
        f"$s.WorkingDirectory = '{_esc(targetExe.parent)}'; "
        f"$s.IconLocation = '{_esc(icon)}'; "
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


def installTo(dest: Path, silent=False, progressCb=None, makeShortcut=True):
    """payload.zip → dest (설치 루트)."""

    def _prog(cur, total, msg):
        if callable(progressCb):
            try:
                progressCb(int(cur), int(total), str(msg))
            except Exception:
                pass
        if not silent:
            print(f"[{cur}/{total}] {msg}")

    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    zpath = _payloadZip()
    if not zpath.is_file():
        raise FileNotFoundError(
            f"설치 패키지(payload.zip)가 없습니다.\n경로: {zpath}"
        )

    with zipfile.ZipFile(zpath, "r") as zf:
        members = [
            i for i in zf.infolist() if i.filename and not i.filename.endswith("/")
        ]
        total = max(1, len(members) + (1 if makeShortcut else 0))
        _prog(0, total, "설치 준비…")
        done = 0
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                if name:
                    (dest / name).mkdir(parents=True, exist_ok=True)
                continue
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest)):
                continue
            if Path(name).name in (".env", "loginLocal.json", "deskLoginSession.json"):
                if target.is_file():
                    done += 1
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            done += 1
            if done == 1 or done == total or done % 40 == 0:
                _prog(done, total, name)

    # tradeApp 풀설치 마커 보강
    marker = dest / "tradeApp" / "config" / "data" / ".fullInstall"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1\n", encoding="utf-8")

    _prog(max(0, total - 2), total, "제거 프로그램 생성…")
    writeUninstaller(dest)

    lnkPath = None
    if makeShortcut:
        loginExe = _findLoginExe(dest)
        if not loginExe:
            raise RuntimeError(
                "설치 후 SATP_AI_Login.exe 를 찾지 못했습니다. 패키지를 확인하세요."
            )
        _prog(total - 1, total, "바탕화면 바로가기 생성…")
        lnkPath = createDesktopShortcut(loginExe)
        _prog(total, total, f"바로가기: {lnkPath}")
    else:
        _prog(total, total, "설치 완료")

    if not silent:
        print(f"install ok -> {dest}")
    return {"dest": dest, "shortcut": lnkPath, "loginExe": _findLoginExe(dest)}


def _applyWinIcon(app, widget=None):
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "SATP_AI.Setup"
            )
    except Exception:
        pass
    ico = _assetPath("satpBolt.ico", "satpBolt.png")
    if not ico:
        return
    try:
        from PySide6.QtGui import QIcon

        icon = QIcon(str(ico))
        if app is not None:
            app.setWindowIcon(icon)
        if widget is not None:
            widget.setWindowIcon(icon)
    except Exception:
        pass


def _logoLabel(parent=None, heightPx=90):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel

    lbl = QLabel(parent)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    png = _assetPath("satpBolt.png", "satpBolt.ico")
    if png:
        pm = QPixmap(str(png))
        if not pm.isNull():
            lbl.setPixmap(
                pm.scaledToHeight(
                    int(heightPx), Qt.TransformationMode.SmoothTransformation
                )
            )
            lbl.setMinimumHeight(int(heightPx))
            return lbl
    lbl.setText("⚡")
    lbl.setStyleSheet("font-size: 64px; color: #FFD600;")
    lbl.setMinimumHeight(int(heightPx))
    return lbl


def _runGui():
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    class _InstallWorker(QThread):
        progress = Signal(int, int, str)
        finishedOk = Signal(object)
        failed = Signal(str)

        def __init__(self, dest, makeShortcut):
            super().__init__()
            self._dest = dest
            self._makeShortcut = makeShortcut

        def run(self):
            try:
                got = installTo(
                    Path(self._dest),
                    silent=True,
                    makeShortcut=self._makeShortcut,
                    progressCb=lambda c, t, m: self.progress.emit(c, t, m),
                )
                self.finishedOk.emit(got)
            except Exception as err:
                self.failed.emit(str(err))

    class SetupWin(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("천둥이 설치")
            self.setFixedSize(310, 320)
            self._worker = None
            self._done = False
            self._build()

        def _build(self):
            lay = QVBoxLayout(self)
            lay.setContentsMargins(18, 12, 18, 10)
            lay.setSpacing(6)
            lay.addWidget(_logoLabel(self, 64))

            title = QLabel("천둥이 설치")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("font-size: 14px; font-weight: 700; color:#1B5E20;")
            lay.addWidget(title)

            lay.addWidget(QLabel("설치 위치"))
            row = QHBoxLayout()
            self.pathEdit = QLineEdit(str(_defaultDest()))
            self.browseBtn = QPushButton("찾아보기…")
            row.addWidget(self.pathEdit, 1)
            row.addWidget(self.browseBtn)
            lay.addLayout(row)

            self.shortcutChk = QCheckBox("바탕화면 바로가기 (천둥이 로그인)")
            self.shortcutChk.setChecked(True)
            lay.addWidget(self.shortcutChk)

            self.prog = QProgressBar()
            self.prog.setRange(0, 100)
            self.prog.setValue(0)
            lay.addWidget(self.prog)

            self.msgLbl = QLabel("「설치 시작」을 누르면 진행됩니다.")
            self.msgLbl.setWordWrap(False)
            self.msgLbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.msgLbl.setStyleSheet("color:#1565c0; font-size: 11px;")
            lay.addWidget(self.msgLbl)

            self.okBtn = QPushButton("설치 시작")
            self.okBtn.setMinimumHeight(34)
            self.okBtn.setStyleSheet(
                "QPushButton { background:#6495ED; color:white; font-weight:700;"
                " border:none; border-radius:4px; }"
                "QPushButton:hover { background:#4A7FD4; }"
                "QPushButton:disabled { background:#A0B4D8; }"
            )
            lay.addWidget(self.okBtn)

            self.closeBtn = QPushButton("닫기")
            self.closeBtn.setFlat(True)
            lay.addWidget(self.closeBtn)

            self._loginExe = None
            self.browseBtn.clicked.connect(self._browse)
            self.okBtn.clicked.connect(self._onOkBtn)
            self.closeBtn.clicked.connect(self.close)

        def _browse(self):
            cur = self.pathEdit.text().strip() or str(_defaultDest())
            path = QFileDialog.getExistingDirectory(self, "설치 폴더 선택", cur)
            if path:
                self.pathEdit.setText(path)

        def _onOkBtn(self):
            # 설치 완료 후 → 로그인 실행
            if self._done:
                self._launchLogin()
                return
            self._start()

        def _start(self):
            dest = self.pathEdit.text().strip()
            if not dest:
                QMessageBox.warning(self, "설치", "설치 위치를 입력하세요.")
                return
            self.okBtn.setEnabled(False)
            self.browseBtn.setEnabled(False)
            self.pathEdit.setEnabled(False)
            self.shortcutChk.setEnabled(False)
            self.prog.setValue(0)
            self.msgLbl.setText("설치 중…")
            self._worker = _InstallWorker(dest, self.shortcutChk.isChecked())
            self._worker.progress.connect(self._onProg)
            self._worker.finishedOk.connect(self._onOk)
            self._worker.failed.connect(self._onFail)
            self._worker.start()

        def _onProg(self, cur, total, msg):
            pct = int(cur * 100 / max(1, total))
            self.prog.setValue(min(100, pct))
            short = msg if len(msg) < 40 else ("…" + msg[-36:])
            self.msgLbl.setText(f"{pct}% · {short}")

        def _onOk(self, got):
            self.prog.setValue(100)
            dest = Path((got or {}).get("dest") or self.pathEdit.text())
            self._loginExe = (got or {}).get("loginExe")
            sc = (got or {}).get("shortcut")
            info = f"설치 완료\n\n위치: {dest}\n"
            if sc:
                info += f"바로가기: {sc}\n"
            info += f"\n제거: {dest}\\SATP_AI_Uninstall.bat"
            self.msgLbl.setText("설치 완료")
            QMessageBox.information(self, "설치 완료", info)
            self._done = True
            self.okBtn.setText("실행")
            self.okBtn.setEnabled(True)
            self.okBtn.setStyleSheet(
                "QPushButton { background:#228B22; color:white; font-weight:700;"
                " border:none; border-radius:4px; }"
                "QPushButton:hover { background:#1B6E1B; }"
            )
            self.browseBtn.setEnabled(True)
            self.pathEdit.setEnabled(True)
            self.shortcutChk.setEnabled(True)

        def _onFail(self, err):
            QMessageBox.critical(self, "설치 실패", err)
            self._done = False
            self.okBtn.setText("설치 시작")
            self.okBtn.setEnabled(True)
            self.browseBtn.setEnabled(True)
            self.pathEdit.setEnabled(True)
            self.shortcutChk.setEnabled(True)
            self.msgLbl.setText("다시 시도할 수 있습니다.")

        def _launchLogin(self):
            exe = self._loginExe
            if not exe or not Path(exe).is_file():
                QMessageBox.warning(self, "실행", "로그인 실행파일을 찾을 수 없습니다.")
                return
            subprocess.Popen([str(exe)], cwd=str(Path(exe).parent))
            self.close()

    app = QApplication.instance() or QApplication(sys.argv)
    _applyWinIcon(app)
    win = SetupWin()
    _applyWinIcon(app, win)
    win.show()
    win.raise_()
    win.activateWindow()
    return app.exec()


def main():
    ap = argparse.ArgumentParser(description="SATP_AI 설치")
    ap.add_argument("--dest", default="", help="설치 루트 (예: C:\\SATP_AI)")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--no-shortcut", action="store_true")
    args, _unknown = ap.parse_known_args()

    dest = str(args.dest or "").strip()
    if not dest and not args.silent:
        try:
            return _runGui()
        except Exception as err:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                app = QApplication.instance() or QApplication(sys.argv)
                QMessageBox.critical(None, "설치 실패", str(err))
            except Exception:
                print(f"GUI failed: {err}", file=sys.stderr)
            return 1

    if not dest:
        dest = str(_defaultDest())
    try:
        installTo(
            Path(dest),
            silent=args.silent,
            makeShortcut=not args.no_shortcut,
        )
        return 0
    except Exception as err:
        if not args.silent:
            print(f"install failed: {err}", file=sys.stderr)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "설치 실패", str(err))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
