"""
매매창 FTP 배포 스크립트.

기본(단순 업데이트 배포):
  minor +1 → SATP_AI.zip(슬림+핵심.pyc) + appVersion.json 업로드

최초 설치 패키지(--full):
  SATP_AI_Setup.exe (런타임+필수패키지+앱) 도 함께 빌드·업로드

  - .env 미포함 (설치루트에만 보관)
  - docs/examples/research/financeAgent/web/tr_docs/sql/scripts 제외
  - trading · strategies/impl 은 .pyc 만 포함 (Python 3.12)
"""

from __future__ import annotations

import argparse
import ftplib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.deployPackUtil import (  # noqa: E402
    iterStagedFiles,
    resolveCompilePython,
    stageAppTree,
)
from utils.appVersion import (  # noqa: E402
    bumpMajor,
    bumpMinor,
    formatAppVersion,
    getAppVersionParts,
)
from utils.deskFtpInfo import fetchFtpInfo  # noqa: E402

_DIST = ROOT / "dist"
_DEFAULT_ZIP_NAME = "SATP_AI.zip"
_SETUP_EXE_NAME = "SATP_AI_Setup.exe"
_VER_NAME = "appVersion.json"
_STAGE = _DIST / "_zipStage"


def _loadEnv():
    from config.loadEnv import ensureEnvLoaded

    ensureEnvLoaded()


def _ftpConfigFromInfo(info: dict, zipName: str = ""):
    path = str((info or {}).get("ftpPath") or "").strip()
    user = str((info or {}).get("ftpUser") or "").strip()
    pwd = str((info or {}).get("ftpPwd") or "").strip()
    setupDb = str((info or {}).get("setupFile") or "").strip()
    setupFile = (zipName or "").strip() or _DEFAULT_ZIP_NAME
    if not zipName and setupDb.lower().endswith(".zip"):
        setupFile = setupDb

    host = ""
    port = 21
    remoteDir = ""
    if path:
        parsed = urlparse(path)
        host = parsed.hostname or ""
        port = parsed.port or 21
        if parsed.path and parsed.path not in ("", "/"):
            remoteDir = parsed.path.strip("/")
        if parsed.username and not user:
            user = parsed.username
        if parsed.password and not pwd:
            pwd = parsed.password

    return {
        "host": host,
        "port": port,
        "user": user,
        "pwd": pwd,
        "remoteDir": remoteDir,
        "setupFile": setupFile,
        "ftpPath": path,
        "source": (info or {}).get("source") or "",
    }


def buildZip(zipPath: Path) -> int:
    """슬림 스테이징 + 핵심 .pyc 난독화 후 zip (업데이트용)."""
    zipPath.parent.mkdir(parents=True, exist_ok=True)
    if zipPath.is_file():
        zipPath.unlink()
    compilePy = resolveCompilePython(ROOT)
    nStage = stageAppTree(ROOT, _STAGE, compilePy=compilePy)
    print(f"stage files ~{nStage} → {_STAGE}")
    count = 0
    with zipfile.ZipFile(zipPath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, rel in iterStagedFiles(_STAGE):
            zf.write(path, arcname=rel)
            count += 1
    return count


def _writeDistVersion():
    maj, mn = getAppVersionParts()
    payload = {"major": maj, "minor": mn}
    out = _DIST / _VER_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out, formatAppVersion(maj, mn)


def _buildSetupExe():
    print("building SATP_AI_Setup.exe (full install) ...")
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "buildTradeSetup.py")],
        cwd=str(ROOT),
    )
    exe = _DIST / _SETUP_EXE_NAME
    if not exe.is_file():
        raise RuntimeError("SATP_AI_Setup.exe 빌드 실패")
    return exe


def uploadFtp(cfg: dict, files: list):
    host = cfg["host"]
    if not host:
        raise RuntimeError("FTP 호스트 없음 — FTP_INFO(FTP_PATH) 확인")
    ftp = ftplib.FTP()
    ftp.connect(host, int(cfg["port"] or 21), timeout=120)
    ftp.login(cfg["user"] or "anonymous", cfg["pwd"] or "")
    remoteDir = (cfg.get("remoteDir") or "").strip("/")
    if remoteDir:
        for part in remoteDir.split("/"):
            if not part:
                continue
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)

    for local, remoteName in files:
        local = Path(local)
        with local.open("rb") as fp:
            ftp.storbinary(f"STOR {remoteName}", fp)
        print(f"  uploaded {remoteName} ({local.stat().st_size:,} bytes)")
    ftp.quit()


def main():
    ap = argparse.ArgumentParser(description="SATP_AI 매매창 배포")
    ap.add_argument("--major", action="store_true", help="메이저 상향 (관리자)")
    ap.add_argument("--no-bump", action="store_true", help="버전 유지")
    ap.add_argument("--dry-run", action="store_true", help="업로드 안 함")
    ap.add_argument(
        "--full",
        action="store_true",
        help="최초설치용 SATP_AI_Setup.exe 도 빌드·업로드",
    )
    ap.add_argument("--zip-name", default="", help="업데이트 zip 명")
    args = ap.parse_args()

    _loadEnv()

    print("FTP_INFO from server ...")
    info = fetchFtpInfo()
    if not info:
        raise RuntimeError("FTP_INFO 조회 실패 (STP SATPLITEIQ008)")
    cfg = _ftpConfigFromInfo(info, zipName=args.zip_name.strip())
    print(
        f"  source={cfg.get('source')} path={cfg.get('ftpPath')} "
        f"user={cfg.get('user')}"
    )

    before = formatAppVersion()
    if args.no_bump:
        print(f"version keep {before}")
    elif args.major:
        print(f"version major {before} -> {bumpMajor()}")
    else:
        print(f"version minor {before} -> {bumpMinor()}")

    zipPath = _DIST / (cfg["setupFile"] or _DEFAULT_ZIP_NAME)
    print(f"building update zip {zipPath} ...")
    n = buildZip(zipPath)
    verPath, verLabel = _writeDistVersion()
    print(f"zip ok | {n} files | {zipPath.stat().st_size:,} bytes | {verLabel}")

    setupExe = None
    if args.full:
        setupExe = _buildSetupExe()

    uploadFiles = [
        (verPath, _VER_NAME),
        (zipPath, cfg["setupFile"] or _DEFAULT_ZIP_NAME),
    ]
    if setupExe and setupExe.is_file():
        uploadFiles.append((setupExe, _SETUP_EXE_NAME))

    if args.dry_run:
        print("dry-run - FTP upload skipped")
        for p, name in uploadFiles:
            print(f"  {name}: {p}")
        return 0

    print(f"FTP {cfg['host']}:{cfg['port']}/{cfg.get('remoteDir') or ''} ...")
    uploadFtp(cfg, uploadFiles)
    print(f"deploy done | {verLabel}")
    if setupExe:
        print(f"  full setup: {_SETUP_EXE_NAME}")
    print(f"  update zip: {cfg['setupFile'] or _DEFAULT_ZIP_NAME}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("aborted")
        raise SystemExit(130)
    except Exception as err:
        print(f"deploy failed: {err}", file=sys.stderr)
        raise SystemExit(1)
