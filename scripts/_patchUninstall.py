# -*- coding: utf-8 -*-
"""tradeSetupMain.writeUninstaller 교체."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "tradeSetupMain.py"

NEW_FN = r'''
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
            "Get-ChildItem -LiteralPath $d -Filter 'SATP_AI*.lnk' -EA SilentlyContinue | "
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


'''

def main():
    text = TARGET.read_text(encoding="utf-8")
    start = text.index("def writeUninstaller(dest: Path):")
    end = text.index("def _desktopDir() -> Path:")
    TARGET.write_text(text[:start] + NEW_FN.lstrip("\n") + text[end:], encoding="utf-8")
    print("patched", TARGET)


if __name__ == "__main__":
    main()
