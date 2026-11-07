"""
SATP_AI PC 설치 패키지 빌드.

흐름:
  1) SATP_AI_Setup.exe  — 필수 런타임 + 로그인 exe + 매매창(tradeApp) 설치
  2) 바탕화면 바로가기 → SATP_AI_Login.exe
  3) 로그인 → 업데이트 체크 → 매매창 실행

산출:
  dist/payload.zip
  dist/SATP_AI_Setup.exe

  python scripts/buildTradeSetup.py
  python scripts/buildTradeSetup.py --skip-runtime
  python scripts/buildTradeSetup.py --exe-only
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.deployPackUtil import resolveCompilePython, stageAppTree  # noqa: E402

DIST = ROOT / "dist"
FULL = DIST / "tradeFull"
PAYLOAD_DIR = DIST / "payloadRoot"
PAYLOAD = DIST / "payload.zip"
SETUP_EXE = DIST / "SATP_AI_Setup.exe"
LOGIN_DIR = DIST / "SATP_AI_Login"

_PY_VER = "3.12.8"
_PY_EMBED_URL = (
    f"https://www.python.org/ftp/python/{_PY_VER}/python-{_PY_VER}-embed-amd64.zip"
)
_GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {url}")
    urllib.request.urlretrieve(url, dest)


def _patchRuntimePth(runtimeDir: Path):
    """embed ._pth 에 site-packages·앱루트(..) 보장."""
    for pth in runtimeDir.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        text = text.replace("#import site", "import site")
        if "import site" not in text:
            text = text.rstrip() + "\nimport site\n"
        if "Lib\\site-packages" not in text and "Lib/site-packages" not in text:
            text = text.rstrip() + "\nLib\\site-packages\n.\n"
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if ".." not in lines:
            text = text.rstrip() + "\n..\n"
        pth.write_text(text, encoding="utf-8")


def _ensureRuntime(runtimeDir: Path, force=False):
    pyExe = runtimeDir / "python.exe"
    if pyExe.is_file() and not force:
        print(f"runtime reuse {runtimeDir}")
        _patchRuntimePth(runtimeDir)
        return pyExe

    if runtimeDir.exists():
        shutil.rmtree(runtimeDir, ignore_errors=True)
    runtimeDir.mkdir(parents=True, exist_ok=True)

    embedZip = DIST / f"python-{_PY_VER}-embed-amd64.zip"
    if not embedZip.is_file():
        _download(_PY_EMBED_URL, embedZip)
    with zipfile.ZipFile(embedZip, "r") as zf:
        zf.extractall(runtimeDir)

    _patchRuntimePth(runtimeDir)

    getPip = DIST / "get-pip.py"
    if not getPip.is_file():
        _download(_GET_PIP_URL, getPip)
    print("pip bootstrap ...")
    subprocess.check_call([str(pyExe), str(getPip), "--no-warn-script-location"])
    req = ROOT / "requirements.txt"
    print("pip install requirements ...")
    subprocess.check_call(
        [
            str(pyExe),
            "-m",
            "pip",
            "install",
            "-r",
            str(req),
            "--no-warn-script-location",
        ]
    )
    return pyExe


def _copyAppToTrade(destRoot: Path):
    """앱 소스 → tradeApp (슬림 + trading/strategies.impl .pyc)."""
    stage = DIST / "_setupAppStage"
    compilePy = resolveCompilePython(ROOT)
    # Setup 빌드 시 runtime 이 곧 만들어지므로, 캐시 runtime 우선
    rt = destRoot / "runtime" / "python.exe"
    if rt.is_file():
        compilePy = rt
    elif (FULL / "runtime" / "python.exe").is_file():
        compilePy = FULL / "runtime" / "python.exe"
    n = stageAppTree(ROOT, stage, compilePy=compilePy)
    if destRoot.exists():
        # runtime 보존 — dest 가 이미 runtime 포함일 수 있음
        for item in list(destRoot.iterdir()):
            if item.name == "runtime":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except Exception:
                    pass
    else:
        destRoot.mkdir(parents=True, exist_ok=True)
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(stage)
        target = destRoot / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    marker = destRoot / "config" / "data" / ".fullInstall"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1\n", encoding="utf-8")
    bat = destRoot / "SATP_AI.bat"
    bat.write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\"%~dp0runtime\\pythonw.exe\" \"%~dp0main.py\" %*\r\n"
        "if errorlevel 1 \"%~dp0runtime\\python.exe\" \"%~dp0main.py\" %*\r\n",
        encoding="utf-8",
    )
    print(f"tradeApp files staged={n}")
    return n


def _ensureLoginDist(force=False):
    """로그인 onedir 빌드. force=True 또는 exe 없으면 재빌드."""
    exe = LOGIN_DIR / "SATP_AI_Login.exe"
    if exe.is_file() and not force:
        print(f"login reuse {exe}")
        return LOGIN_DIR
    if LOGIN_DIR.exists() and force:
        shutil.rmtree(LOGIN_DIR, ignore_errors=True)
    print("building SATP_AI_Login ...")
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "buildLoginDist.py")],
        cwd=str(ROOT),
    )
    if not exe.is_file():
        raise RuntimeError("SATP_AI_Login.exe 빌드 실패")
    return LOGIN_DIR


def buildPayload(skipRuntime=False, forceLogin=True):
    """
    payload 구조 (설치 루트 = C:\\SATP_AI 등):
      SATP_AI_Login.exe + _internal + config + assets
      tradeApp/runtime + tradeApp 앱소스
      설치안내.txt
    forceLogin=True(기본): Login.exe 항상 최신 재빌드 (--full Setup용)
    """
    if PAYLOAD_DIR.exists():
        shutil.rmtree(PAYLOAD_DIR, ignore_errors=True)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 로그인 실행파일
    loginSrc = _ensureLoginDist(force=forceLogin)
    for item in loginSrc.iterdir():
        dest = PAYLOAD_DIR / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # 2) 매매창 tradeApp
    tradeApp = PAYLOAD_DIR / "tradeApp"
    tradeApp.mkdir(parents=True, exist_ok=True)
    runtimeDir = tradeApp / "runtime"
    needForce = not skipRuntime and not (runtimeDir / "python.exe").is_file()
    if (FULL / "runtime" / "python.exe").is_file() and not needForce:
        # 기존 tradeFull runtime 재사용
        if runtimeDir.exists():
            shutil.rmtree(runtimeDir, ignore_errors=True)
        shutil.copytree(FULL / "runtime", runtimeDir)
        print("runtime copied from tradeFull")
    else:
        _ensureRuntime(runtimeDir, force=needForce)
        # 캐시용 tradeFull 에도 복사
        FULL.mkdir(parents=True, exist_ok=True)
        cacheRt = FULL / "runtime"
        if cacheRt.exists():
            shutil.rmtree(cacheRt, ignore_errors=True)
        shutil.copytree(runtimeDir, cacheRt)

    # 재사용 runtime 에도 앱 루트(..) path 패치
    _patchRuntimePth(runtimeDir)

    _copyAppToTrade(tradeApp)

    # 안내
    (PAYLOAD_DIR / "설치안내.txt").write_text(
        "천둥이 설치 안내\n"
        "================\n\n"
        "1. 설치가 끝나면 바탕화면에 [천둥이 로그인] 바로가기가 생깁니다.\n"
        "2. 바로가기(또는 SATP_AI_Login.exe)를 실행해 로그인하세요.\n"
        "3. 로그인 후 최신 매매창을 확인하고 실행합니다.\n"
        "4. 비밀번호는 PC에 저장되지 않습니다.\n"
        "5. 제거: 설치 폴더의 SATP_AI_Uninstall.bat 을 실행하세요.\n",
        encoding="utf-8",
    )

    if PAYLOAD.is_file():
        PAYLOAD.unlink()
    print(f"zip {PAYLOAD} ...")
    with zipfile.ZipFile(PAYLOAD, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PAYLOAD_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(PAYLOAD_DIR).as_posix())
    print(f"payload {PAYLOAD.stat().st_size:,} bytes")
    return PAYLOAD


def buildSetupExe():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller", "-q"]
        )

    if not PAYLOAD.is_file():
        raise RuntimeError("payload.zip 없음 — buildPayload 먼저")

    work = DIST / "_setup_work"
    spec = DIST / "_setup_spec"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "SATP_AI_Setup",
        "--distpath",
        str(DIST),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--add-data",
        f"{PAYLOAD};.",
        "--add-data",
        f"{ROOT / 'assets'};assets",
        str(ROOT / "scripts" / "tradeSetupMain.py"),
    ]
    ico = ROOT / "assets" / "satpBolt.ico"
    if ico.is_file():
        oi = cmd.index("--onefile")
        cmd.insert(oi + 1, "--icon")
        cmd.insert(oi + 2, str(ico))
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    if not SETUP_EXE.is_file():
        raise RuntimeError("SATP_AI_Setup.exe 빌드 실패")
    print(f"OK {SETUP_EXE} ({SETUP_EXE.stat().st_size:,} bytes)")
    return SETUP_EXE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-runtime", action="store_true")
    ap.add_argument("--payload-only", action="store_true")
    ap.add_argument("--exe-only", action="store_true")
    ap.add_argument(
        "--reuse-login",
        action="store_true",
        help="기존 SATP_AI_Login.exe 재사용 (기본은 항상 재빌드)",
    )
    args = ap.parse_args()

    if args.exe_only:
        buildSetupExe()
        return 0
    buildPayload(skipRuntime=args.skip_runtime, forceLogin=not args.reuse_login)
    if args.payload_only:
        return 0
    buildSetupExe()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print(f"buildTradeSetup failed: {err}", file=sys.stderr)
        raise SystemExit(1)
