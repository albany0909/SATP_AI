"""
다른 PC용 로그인 설치 패키지 빌드 (PyInstaller).

산출물:
  dist/SATP_AI_Login/SATP_AI_Login.exe
  dist/SATP_AI_Login_설치.zip   ← 이 파일을 다른 PC에 복사

  python scripts/buildLoginDist.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT_NAME = "SATP_AI_Login"
OUT_DIR = DIST / OUT_NAME
ZIP_PATH = DIST / f"{OUT_NAME}_Setup.zip"


def _ensurePyInstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("installing pyinstaller ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller", "-q"]
        )


def _writeReadme(dest: Path):
    text = """SATP_AI 로그인
================

정상 설치(SATP_AI_Setup.exe) 후에는 바탕화면 [SATP_AI 로그인] 바로가기를 사용하세요.

1) SATP_AI_Login.exe 실행
2) 회원 ID/비밀번호 · 키움 앱키 입력 후 로그인
3) 최신 매매창 확인 후 실행

비밀번호는 PC에 저장되지 않습니다.
매매창은 이 프로그램 옆 tradeApp 폴더에 있습니다.
"""
    (dest / "설치안내.txt").write_text(text, encoding="utf-8")


def _seedConfig(dest: Path):
    dataDir = dest / "config" / "data"
    dataDir.mkdir(parents=True, exist_ok=True)
    srcDefault = ROOT / "config" / "data" / "settings-default.json"
    if srcDefault.is_file():
        shutil.copy2(srcDefault, dataDir / "settings-default.json")
    settings = dataDir / "settings.json"
    if not settings.is_file():
        settings.write_text(
            '{\n  "stpServerUrl": "http://satp1.iptime.org:8080/STP/tran",\n'
            '  "deskRole": "client",\n  "deskTradeMode": "paper",\n'
            '  "deskDataViaServer": true\n}\n',
            encoding="utf-8",
        )
    verSrc = ROOT / "config" / "data" / "appVersion.json"
    if verSrc.is_file():
        shutil.copy2(verSrc, dataDir / "appVersion.json")
    # 번개 아이콘 (창·표시용)
    assetsDest = dest / "assets"
    assetsDest.mkdir(parents=True, exist_ok=True)
    for name in (
        "satpBolt.ico",
        "satpBolt.png",
        "satpLogin.ico",
        "satpLogin.png",
        "satpTrade.ico",
        "satpTrade.png",
    ):
        src = ROOT / "assets" / name
        if src.is_file():
            shutil.copy2(src, assetsDest / name)


def build():
    _ensurePyInstaller()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)

    # onedir — Qt DLL 안정
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        OUT_NAME,
        "--paths",
        str(ROOT),
        "--distpath",
        str(DIST),
        "--workpath",
        str(DIST / "_pyi_work"),
        "--specpath",
        str(DIST / "_pyi_spec"),
        # 로그인에 필요한 숨은 import
        "--hidden-import",
        "PySide6.QtCore",
        "--hidden-import",
        "PySide6.QtGui",
        "--hidden-import",
        "PySide6.QtWidgets",
        "--hidden-import",
        "PySide6.QtNetwork",
        "--hidden-import",
        "requests",
        "--hidden-import",
        "dotenv",
        "--collect-submodules",
        "ui",
        "--collect-submodules",
        "utils",
        "--collect-submodules",
        "config",
        # 기본 설정 JSON 번들
        "--add-data",
        f"{ROOT / 'config' / 'data' / 'settings-default.json'};config/data",
        "--add-data",
        f"{ROOT / 'config' / 'data' / 'appVersion.json'};config/data",
        "--add-data",
        f"{ROOT / 'assets'};assets",
        str(ROOT / "loginMain.py"),
    ]
    ico = ROOT / "assets" / "satpLogin.ico"
    if not ico.is_file():
        ico = ROOT / "assets" / "satpBolt.ico"
    if ico.is_file():
        wi = cmd.index("--windowed")
        cmd.insert(wi + 1, "--icon")
        cmd.insert(wi + 2, str(ico))
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    if not (OUT_DIR / f"{OUT_NAME}.exe").is_file():
        raise RuntimeError(f"빌드 실패 — {OUT_DIR} 에 exe 없음")

    _seedConfig(OUT_DIR)
    _writeReadme(OUT_DIR)

    if ZIP_PATH.is_file():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(OUT_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"{OUT_NAME}/{path.relative_to(OUT_DIR).as_posix()}")

    print(f"OK {OUT_DIR / (OUT_NAME + '.exe')}")
    print(f"OK {ZIP_PATH} ({ZIP_PATH.stat().st_size:,} bytes)")
    return ZIP_PATH


if __name__ == "__main__":
    try:
        build()
    except Exception as err:
        print(f"build failed: {err}", file=sys.stderr)
        raise SystemExit(1)
