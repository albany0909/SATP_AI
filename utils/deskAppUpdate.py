"""
매매창 앱 업데이트·실행 (구 SATP_UPDATE FTP 방식).

- 최초 설치: SATP_AI_Setup.exe (런타임+필수패키지+앱)
- 단순 업데이트: SATP_AI.zip (앱 소스만, runtime 유지)

로그인 런처만 이 모듈로 패키지를 받고 매매창을 실행한다.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin

from utils.appPaths import isFrozen, projectRoot
from utils.appVersion import (
    formatAppVersion,
    getAppVersionParts,
    isNewer,
    parseVersionLabel,
    saveAppVersion,
)
from utils.termLog import termLog

_ROOT = projectRoot()
_DEFAULT_ZIP = "SATP_AI.zip"
_SETUP_EXE = "SATP_AI_Setup.exe"
_REMOTE_VER = "appVersion.json"
_TRADE_SUBDIR = "tradeApp"


def _ftpJoin(base, name):
    base = str(base or "").strip()
    if not base.endswith("/"):
        base += "/"
    return urljoin(base, str(name or "").lstrip("/"))


def getInstallRoot():
    """
    매매창 설치 루트.
    - SATP_TRADE_APP_DIR 우선
    - 배포(로그인만): 실행파일 옆 tradeApp/
    - 개발(소스): 프로젝트 루트 (tradeApp 없으면)
    """
    env = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if env:
        return Path(env).resolve()

    if isFrozen():
        return (projectRoot() / _TRADE_SUBDIR).resolve()

    tradeDir = (projectRoot() / _TRADE_SUBDIR).resolve()
    if tradeDir.is_dir() and (
        (tradeDir / "main.py").is_file()
        or (tradeDir / "runtime" / "python.exe").is_file()
        or (tradeDir / "SATP_AI.exe").is_file()
        or list(tradeDir.glob("SATP_AI*.exe"))
    ):
        return tradeDir

    return projectRoot().resolve()


def localVersionParts(installRoot=None):
    """설치 루트의 appVersion.json 기준 (major, minor). 없으면 (0,0)."""
    root = Path(installRoot or getInstallRoot())
    verPath = root / "config" / "data" / "appVersion.json"
    try:
        data = json.loads(verPath.read_text(encoding="utf-8"))
        return int(data.get("major") or 0), int(data.get("minor") or 0)
    except Exception:
        pass
    if root.resolve() == projectRoot().resolve():
        return getAppVersionParts()
    return 0, 0


def isFullInstall(installRoot=None):
    """최초 설치(런타임 포함) 완료 여부."""
    root = Path(installRoot or getInstallRoot())
    marker = root / "config" / "data" / ".fullInstall"
    runtimePy = root / "runtime" / "python.exe"
    return marker.is_file() and runtimePy.is_file() and (root / "main.py").is_file()


def tradeAppExists(installRoot=None):
    """매매창 실행 가능 여부."""
    root = Path(installRoot or getInstallRoot())
    if isFullInstall(root):
        return True
    if (root / "main.py").is_file():
        return True
    if (root / "SATP_AI.exe").is_file():
        return True
    if list(root.glob("SATP_AI*.exe")):
        return True
    if (root / "SATP_AI" / "SATP_AI.exe").is_file():
        return True
    return False


def _pythonForMain():
    if not isFrozen():
        return [sys.executable]
    for cand in (("py", "-3"), ("python",), ("python3",)):
        try:
            r = subprocess.run(
                list(cand) + ["-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if r.returncode == 0 and (r.stdout or "").strip():
                return list(cand)
        except Exception:
            continue
    return None


def resolveTradeLaunch(installRoot=None):
    """매매창 실행 명령. 반환: (argv, cwd) 또는 None
    콘솔 창 없이 띄우기 위해 pythonw.exe 우선.
    """
    root = Path(installRoot or getInstallRoot())
    mainPy = root / "main.py"
    runtimeDir = root / "runtime"
    # pythonw = GUI 전용(검은 콘솔 없음). 없으면 python.exe
    for name in ("pythonw.exe", "python.exe"):
        runtimePy = runtimeDir / name
        if runtimePy.is_file() and mainPy.is_file():
            return [str(runtimePy), str(mainPy)], root

    for exe in [
        root / "SATP_AI.exe",
        root / "SATP_AI" / "SATP_AI.exe",
        *sorted(root.glob("SATP_AI*.exe")),
    ]:
        if exe.is_file() and exe.name.lower() != _SETUP_EXE.lower():
            return [str(exe)], exe.parent

    if mainPy.is_file():
        py = _pythonForMain()
        if not py:
            termLog("[deskUpdate] main.py 있으나 Python 런타임 없음 — 최초 Setup 필요")
            return None
        return [*py, str(mainPy)], root
    return None


def fetchRemoteVersion(ftpPath, ftpUser, ftpPwd):
    import ftplib
    from urllib.parse import urlparse

    url = _ftpJoin(ftpPath, _REMOTE_VER)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 21
    path = parsed.path or f"/{_REMOTE_VER}"
    if not host:
        return None
    try:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=20)
        ftp.login(ftpUser or "anonymous", ftpPwd or "")
        buf = io.BytesIO()
        ftp.retrbinary(f"RETR {path.lstrip('/')}", buf.write)
        ftp.quit()
        data = json.loads(buf.getvalue().decode("utf-8"))
        if isinstance(data, dict):
            return int(data.get("major") or 0), int(data.get("minor") or 0)
        return parseVersionLabel(data if isinstance(data, str) else "")
    except Exception as err:
        termLog(f"[deskUpdate] 원격버전 조회 실패: {err}")
        return None


def downloadPackage(ftpPath, ftpUser, ftpPwd, setupFile, destPath):
    import ftplib
    from urllib.parse import urlparse

    name = str(setupFile or _DEFAULT_ZIP).strip() or _DEFAULT_ZIP
    url = _ftpJoin(ftpPath, name)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 21
    path = parsed.path or f"/{name}"
    if not host:
        raise RuntimeError("FTP 호스트 없음")
    dest = Path(destPath)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=600)
    ftp.login(ftpUser or "anonymous", ftpPwd or "")
    with dest.open("wb") as fp:
        ftp.retrbinary(f"RETR {path.lstrip('/')}", fp.write)
    ftp.quit()
    return dest


def applyZipUpdate(zipPath, rootDir=None, skipRuntime=True):
    """zip 을 설치 루트에 해제 (업데이트 시 runtime 보호)."""
    root = Path(rootDir or getInstallRoot())
    root.mkdir(parents=True, exist_ok=True)
    skipNames = {
        ".env",
        "loginLocal.json",
        "deskLoginSession.json",
        "deskApiKeys.json",
        ".fullInstall",
    }
    with zipfile.ZipFile(zipPath, "r") as zf:
        names = [i.filename.replace("\\", "/") for i in zf.infolist()]
        stripPrefix = ""
        tops = {n.split("/")[0] for n in names if n and not n.endswith("/")}
        if len(tops) == 1:
            only = next(iter(tops))
            if only.lower() in ("satp_ai", "tradeapp", "app"):
                stripPrefix = only + "/"

        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if stripPrefix and name.startswith(stripPrefix):
                name = name[len(stripPrefix) :]
            if not name or name.endswith("/"):
                if name:
                    (root / name).mkdir(parents=True, exist_ok=True)
                continue
            if skipRuntime and name.startswith("runtime/"):
                continue
            base = Path(name).name
            if base in skipNames or name.endswith(".env"):
                continue
            target = (root / name).resolve()
            if not str(target).startswith(str(root.resolve())):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
    return True


def _writeLocalVersion(installRoot, remMaj, remMin):
    """
    버전 파일을 매매창·로그인 루트 양쪽에 기록.
    로그인창은 exe 옆 config/data 를 읽고, zip 업데이트는 tradeApp 만
    풀면 로그인 표시 버전이 안 올라가므로 부모(설치루트)에도 쓴다.
    """
    roots = []
    seen = set()

    def _add(p):
        try:
            rp = Path(p).resolve()
        except Exception:
            rp = Path(p)
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(rp)

    root = Path(installRoot)
    _add(root)
    if root.name.lower() == "tradeapp":
        _add(root.parent)
    try:
        _add(projectRoot())
    except Exception:
        pass

    payload = (
        json.dumps({"major": int(remMaj), "minor": int(remMin)}, ensure_ascii=False, indent=2)
        + "\n"
    )
    for r in roots:
        try:
            verFile = r / "config" / "data" / "appVersion.json"
            verFile.parent.mkdir(parents=True, exist_ok=True)
            verFile.write_text(payload, encoding="utf-8")
        except Exception as err:
            termLog(f"[deskUpdate] 버전기록 실패 {r}: {err}")
    try:
        if Path(installRoot).resolve() == projectRoot().resolve():
            saveAppVersion(remMaj, remMin)
    except Exception:
        pass


def _runSetupExe(setupExePath, installRoot, progressCb=None):
    """
    풀설치: 로그인 exe 부모 폴더(설치 루트)에 Setup 해제.
    installRoot 가 .../tradeApp 이면 그 부모가 dest.
    """
    def _prog(msg):
        if callable(progressCb):
            try:
                progressCb(str(msg))
            except Exception:
                pass
        termLog(f"[deskUpdate] {msg}")

    tradeRoot = Path(installRoot)
    # Setup 은 C:\SATP_AI 처럼 설치 루트에 Login+tradeApp 을 펼친다
    if tradeRoot.name.lower() == "tradeapp":
        setupDest = tradeRoot.parent
    else:
        setupDest = tradeRoot
    setupDest.mkdir(parents=True, exist_ok=True)
    _prog(f"최초 설치 실행 중… → {setupDest}")
    # 바로가기는 실제 Desktop(OneDrive 포함)에 생성 — Setup 내부 deskShortcut 사용
    proc = subprocess.run(
        [
            str(setupExePath),
            "--dest",
            str(setupDest),
            "--silent",
        ],
        cwd=str(setupDest),
        timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Setup.exe 실패 (code={proc.returncode})")
    # 매매창 기준 확인
    checkRoot = setupDest / "tradeApp" if (setupDest / "tradeApp").is_dir() else setupDest
    if not isFullInstall(checkRoot):
        raise RuntimeError("Setup 후에도 런타임 설치 확인 실패")
    return True


def checkAndUpdate(loginInfo, progressCb=None, forceIfMissing=True):
    """
    - 최초(풀설치 없음): SATP_AI_Setup.exe
    - 버전 상승: SATP_AI.zip (소스만, runtime 유지)
    """
    def _prog(msg):
        if callable(progressCb):
            try:
                progressCb(str(msg))
            except Exception:
                pass
        termLog(f"[deskUpdate] {msg}")

    installRoot = getInstallRoot()
    localMaj, localMin = localVersionParts(installRoot)
    localVer = (
        formatAppVersion(localMaj, localMin) if (localMaj or localMin) else "(미설치)"
    )
    fullOk = isFullInstall(installRoot)
    hasLocal = tradeAppExists(installRoot)
    info = loginInfo or {}
    ftpPath = info.get("ftpPath") or ""
    ftpUser = info.get("ftpUser") or ""
    ftpPwd = info.get("ftpPwd") or ""

    if not ftpPath:
        return {
            "ok": hasLocal,
            "updated": False,
            "localVer": localVer,
            "remoteVer": "",
            "msg": "FTP 정보 없음"
            + (" — 로컬 매매창으로 실행" if hasLocal else " — 설치 불가"),
            "canLaunch": hasLocal,
            "installRoot": str(installRoot),
            "needRestart": False,
        }

    _prog("버전정보 확인 중…")
    remote = fetchRemoteVersion(ftpPath, ftpUser, ftpPwd)
    remMaj = remMin = 0
    remoteVer = ""
    needFull = (not fullOk) and forceIfMissing
    needUpdate = False

    if remote:
        remMaj, remMin = remote
        remoteVer = formatAppVersion(remMaj, remMin)
        if fullOk and isNewer(remMaj, remMin, localMaj, localMin):
            needUpdate = True
            _prog(f"앱 업데이트 {localVer} → {remoteVer}")
        elif needFull:
            _prog(f"최초 설치 — Setup.exe ({remoteVer})")
        elif fullOk:
            return {
                "ok": True,
                "updated": False,
                "localVer": localVer,
                "remoteVer": remoteVer,
                "msg": f"최신버전입니다 ({localVer})",
                "canLaunch": True,
                "installRoot": str(installRoot),
                "needRestart": False,
            }
    else:
        if fullOk:
            return {
                "ok": True,
                "updated": False,
                "localVer": localVer,
                "remoteVer": "",
                "msg": f"원격버전 확인 불가 — 로컬 {localVer} 로 실행",
                "canLaunch": True,
                "installRoot": str(installRoot),
                "needRestart": False,
            }
        if not forceIfMissing:
            return {
                "ok": False,
                "updated": False,
                "localVer": localVer,
                "remoteVer": "",
                "msg": "매매창 없음 · 원격버전 확인 실패",
                "canLaunch": False,
                "installRoot": str(installRoot),
                "needRestart": False,
            }
        needFull = True
        _prog("원격버전 확인 불가 — 최초 Setup 강제 시도")

    if not needFull and not needUpdate:
        return {
            "ok": hasLocal,
            "updated": False,
            "localVer": localVer,
            "remoteVer": remoteVer,
            "msg": f"최신버전입니다 ({localVer})",
            "canLaunch": hasLocal,
            "installRoot": str(installRoot),
            "needRestart": False,
        }

    dataDir = projectRoot() / "config" / "data"
    dataDir.mkdir(parents=True, exist_ok=True)

    try:
        if needFull:
            _prog(f"다운로드 중 ({_SETUP_EXE})…")
            setupPath = dataDir / "_setup_download.exe"
            downloadPackage(ftpPath, ftpUser, ftpPwd, _SETUP_EXE, setupPath)
            _runSetupExe(setupPath, installRoot, progressCb=progressCb)
            try:
                setupPath.unlink()
            except Exception:
                pass
            if remote:
                _writeLocalVersion(installRoot, remMaj, remMin)
            hasLocal = tradeAppExists(installRoot)
            verTxt = remoteVer or formatAppVersion(*localVersionParts(installRoot))
            return {
                "ok": hasLocal,
                "updated": True,
                "localVer": localVer,
                "remoteVer": verTxt,
                "msg": f"최초 설치 완료 → {verTxt}",
                "canLaunch": hasLocal,
                "installRoot": str(installRoot),
                "needRestart": False,
            }

        _prog(f"다운로드 중 ({_DEFAULT_ZIP})…")
        zipPath = dataDir / "_update_download.zip"
        downloadPackage(ftpPath, ftpUser, ftpPwd, _DEFAULT_ZIP, zipPath)
        _prog("앱 소스 반영 중…")
        applyZipUpdate(zipPath, installRoot, skipRuntime=True)
        try:
            zipPath.unlink()
        except Exception:
            pass
        if remote:
            _writeLocalVersion(installRoot, remMaj, remMin)
        hasLocal = tradeAppExists(installRoot)
        return {
            "ok": hasLocal,
            "updated": True,
            "localVer": localVer,
            "remoteVer": remoteVer,
            "msg": f"업데이트 완료 {localVer} → {remoteVer}",
            "canLaunch": hasLocal,
            "installRoot": str(installRoot),
            "needRestart": False,
        }
    except Exception as err:
        termLog(f"[deskUpdate] 실패: {err}")
        hasLocal = tradeAppExists(installRoot)
        return {
            "ok": False,
            "updated": False,
            "localVer": localVer,
            "remoteVer": remoteVer,
            "msg": f"업데이트 실패: {err}",
            "canLaunch": hasLocal,
            "installRoot": str(installRoot),
            "needRestart": False,
        }


def _writeDeskApiKeysFile(root: Path, keys: dict):
    """
    매매창(tradeApp) config/data/deskApiKeys.json 에 앱키 기록.
    설치루트 .env 를 못 찾는 경우의 최종 폴백. zip 업데이트 시 유지.
    """
    destRoot = Path(root)
    # root 가 설치루트면 tradeApp 하위, 이미 tradeApp 이면 그대로
    if (destRoot / "tradeApp" / "main.py").is_file():
        destRoot = destRoot / "tradeApp"
    path = destRoot / "config" / "data" / "deskApiKeys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "realAppKey": str((keys or {}).get("realAppKey") or "").strip(),
        "realAppSecret": str((keys or {}).get("realAppSecret") or "").strip(),
        "paperAppKey": str((keys or {}).get("paperAppKey") or "").strip(),
        "paperAppSecret": str((keys or {}).get("paperAppSecret") or "").strip(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    termLog(f"[deskUpdate] deskApiKeys 기록 {path}")


def _syncTradeAppDeskSettings(root: Path, loginInfo=None):
    """매매창 settings 에 deskRole·서버조회 모드 반영 (client=로컬DB 없음)."""
    import json

    sp = Path(root) / "config" / "data" / "settings.json"
    try:
        sp.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if sp.is_file():
            try:
                data = json.loads(sp.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        info = loginInfo or {}
        # 로그인 런처로 뜬 매매창은 항상 client (마스터 계정 무관)
        role = "client"
        data["deskRole"] = role
        data["deskDataViaServer"] = True
        _ = info  # loginInfo 는 세션·FTP 용으로만 사용
        try:
            from config.settingsStore import getDeskTradeMode

            data["deskTradeMode"] = getDeskTradeMode()
        except Exception:
            pass
        sp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as err:
        termLog(f"[deskUpdate] tradeApp settings 동기화 경고: {err}")


def launchTradeApp(loginInfo=None, installRoot=None, apiKeys=None):
    """매매창 프로세스 기동. 세션을 설치 루트에 기록하고 env 로 전달.
    반환: {ok, msg, pid, proc, exitCode, logPath}
    apiKeys: 로그인창 메모리 앱키(우선). 없으면 .env/환경변수.
    """
    from utils.deskLoginSession import sessionEnvName, writeLoginSession
    from utils.localApiKeyStore import saveApiKeysToEnv, loadApiKeysFromEnv

    root = Path(installRoot or getInstallRoot())
    # 이후 secretEnvPath / 자식 프로세스가 설치루트를 보게
    os.environ["SATP_TRADE_APP_DIR"] = str(root.resolve())
    # 로그인창에서 넘긴 앱키를 최우선으로 .env·환경변수·tradeApp 로컬파일에 반영
    try:
        merged = dict(loadApiKeysFromEnv() or {})
        if isinstance(apiKeys, dict):
            for k in (
                "realAppKey",
                "realAppSecret",
                "paperAppKey",
                "paperAppSecret",
            ):
                val = str(apiKeys.get(k) or "").strip()
                if val:
                    merged[k] = val
        # loginInfo 에 키가 섞여 온 경우도 수용
        if isinstance(loginInfo, dict):
            for k in (
                "realAppKey",
                "realAppSecret",
                "paperAppKey",
                "paperAppSecret",
            ):
                val = str(loginInfo.get(k) or "").strip()
                if val:
                    merged[k] = val
        saveApiKeysToEnv(merged)
        # tradeApp 이 확실히 읽는 로컬 파일 (설치루트 .env 누락 대비)
        _writeDeskApiKeysFile(root, merged)
        from utils.localApiKeyStore import secretEnvPath as _secretEnvPath

        termLog(
            "[deskUpdate] 앱키 반영 "
            f"real={bool(merged.get('realAppKey') and merged.get('realAppSecret'))} "
            f"paper={bool(merged.get('paperAppKey') and merged.get('paperAppSecret'))} "
            f"secretEnv={_secretEnvPath()}"
        )
    except Exception as err:
        termLog(f"[deskUpdate] 앱키 동기화 경고: {err}")
    # 예전 tradeApp/.env → 설치루트로 이전 후 삭제
    try:
        candidates = []
        ta = root / "tradeApp" / ".env"
        if ta.is_file():
            candidates.append(ta)
        if root.name.lower() == "tradeapp" and (root / ".env").is_file():
            candidates.append(root / ".env")
        from utils.localApiKeyStore import secretEnvPath

        destEnv = secretEnvPath()
        # launch 시 SATP_TRADE_APP_DIR=설치루트 이므로 secretEnvPath 는 설치루트
        if not destEnv.is_file():
            for src in candidates:
                try:
                    destEnv.parent.mkdir(parents=True, exist_ok=True)
                    destEnv.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                    termLog(f"[deskUpdate] .env 이전: {src} → {destEnv}")
                    break
                except Exception:
                    pass
        for src in candidates:
            if src.resolve() == destEnv.resolve():
                continue
            try:
                src.unlink()
                termLog(f"[deskUpdate] legacy 제거: {src}")
            except Exception:
                pass
    except Exception as err:
        termLog(f"[deskUpdate] legacy .env 정리 경고: {err}")

    # client 설정(서버 조회)을 tradeApp settings 에 먼저 반영
    _syncTradeAppDeskSettings(root, loginInfo)

    # 기존 설치: embed ._pth 에 tradeApp 루트(..) 없으면 import 실패 → 보정
    try:
        rtDir = root / "runtime"
        for pth in rtDir.glob("python*._pth"):
            text = pth.read_text(encoding="utf-8")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if ".." not in lines:
                pth.write_text(text.rstrip() + "\n..\n", encoding="utf-8")
                termLog(f"[deskUpdate] runtime path 보정: {pth.name} +..")
    except Exception as err:
        termLog(f"[deskUpdate] runtime path 보정 경고: {err}")

    sess = writeLoginSession(loginInfo or {}, rootDir=root)
    launch = resolveTradeLaunch(root)
    if not launch:
        return {
            "ok": False,
            "msg": "매매창 실행파일을 찾을 수 없습니다.\n"
            f"경로: {root}\nSetup 재설치 또는 업데이트를 확인하세요.",
            "pid": None,
            "proc": None,
            "exitCode": None,
            "logPath": None,
        }

    argv, cwd = launch
    # 매매창 일자 로그 경로 (팝업 안내용)
    try:
        # deskFileLog 는 configDataDir(=cwd/config/data) 기준
        prevCwd = os.getcwd()
        try:
            os.chdir(str(cwd))
        except Exception:
            prevCwd = None
        from utils.deskFileLog import appendDeskFileLog, deskLogFilePath, markDeskFileLogRestart

        deskLogPath = deskLogFilePath()
        markDeskFileLogRestart()
        appendDeskFileLog(
            f"launch argv={' '.join(map(str, argv))} cwd={cwd}",
            tag="RUN",
        )
        if prevCwd is not None:
            try:
                os.chdir(prevCwd)
            except Exception:
                pass
    except Exception:
        deskLogPath = Path(cwd) / "config" / "data" / "logs" / (
            f"SATP_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.log"
        )
        try:
            deskLogPath.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    logDir = Path(cwd) / "config" / "data"
    try:
        logDir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logDir = Path(cwd)
    capturePath = logDir / "tradeLaunch.log"

    env = os.environ.copy()
    env[sessionEnvName()] = str(sess.get("token") or "")
    env["SATP_FROM_LOGIN_LAUNCHER"] = "1"
    # 설치 루트(또는 tradeApp) — secretEnvPath/loadEnv 가 부모 .env 를 찾게
    # cwd(tradeApp)만 넘기면 예전엔 tradeApp/.env 를 잘못 찾을 수 있었음
    env["SATP_TRADE_APP_DIR"] = str(Path(root).resolve())
    env["SATP_DESK_ROLE"] = "client"
    env.pop("SATP_SKIP_LOGIN", None)
    # 로그인창 선택 모드를 자식에 명시 (settings 파일 어긋남 방지)
    try:
        from config.settingsStore import getDeskTradeMode

        env["SATP_DESK_TRADE_MODE"] = str(getDeskTradeMode() or "paper")
    except Exception:
        pass
    # 런처·apiKeys 로 확정된 앱키를 자식에 명시 전달
    try:
        keys = loadApiKeysFromEnv()
        _envMap = {
            "realAppKey": "KIWOOM_REAL_APP_KEY",
            "realAppSecret": "KIWOOM_REAL_APP_SECRET",
            "paperAppKey": "KIWOOM_PAPER_APP_KEY",
            "paperAppSecret": "KIWOOM_PAPER_APP_SECRET",
        }
        for localKey, envName in _envMap.items():
            val = str(keys.get(localKey) or "").strip()
            if val:
                env[envName] = val
        termLog(
            "[deskUpdate] 자식 env 앱키 "
            f"real={bool(env.get('KIWOOM_REAL_APP_KEY') and env.get('KIWOOM_REAL_APP_SECRET'))} "
            f"paper={bool(env.get('KIWOOM_PAPER_APP_KEY') and env.get('KIWOOM_PAPER_APP_SECRET'))}"
        )
    except Exception as err:
        termLog(f"[deskUpdate] 자식 env 앱키 전달 경고: {err}")

    try:
        logFp = open(capturePath, "w", encoding="utf-8", errors="replace")
        logFp.write(f"argv={argv}\ncwd={cwd}\nroot={root}\ntoken_set=1\n")
        logFp.flush()
        popenKw = {
            "cwd": str(cwd),
            "env": env,
            "close_fds": False,
            "stdout": logFp,
            "stderr": subprocess.STDOUT,
        }
        if sys.platform == "win32":
            try:
                exeName = Path(str(argv[0])).name.lower()
                if exeName == "python.exe":
                    popenKw["creationflags"] = subprocess.CREATE_NO_WINDOW
            except Exception:
                pass
        proc = subprocess.Popen(argv, **popenKw)
        termLog(f"[deskUpdate] 매매창 기동 pid={proc.pid} · {' '.join(map(str, argv))}")

        try:
            code = proc.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            code = None

        if code is not None:
            try:
                logFp.write(f"\nexitCode={code}\n")
                logFp.flush()
                logFp.close()
            except Exception:
                pass
            captureTxt = ""
            try:
                captureTxt = capturePath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            try:
                prevCwd = os.getcwd()
                os.chdir(str(cwd))
                from utils.deskFileLog import appendDeskFileLog

                appendDeskFileLog(f"매매창 즉시 종료 code={code}", tag="ERR")
                for ln in (captureTxt or "").splitlines()[-80:]:
                    appendDeskFileLog(ln, tag="LAUNCH")
                os.chdir(prevCwd)
            except Exception:
                pass
            msg = (
                f"매매창이 바로 종료되었습니다 (code={code}).\n"
                f"경로: {cwd}\n"
                f"세션/앱키/.env 또는 런타임을 확인하세요.\n"
                f"오류로그: {deskLogPath}"
            )
            termLog(f"[deskUpdate] 매매창 즉시 종료 code={code} log={deskLogPath}")
            return {
                "ok": False,
                "msg": msg,
                "pid": proc.pid,
                "proc": None,
                "exitCode": code,
                "logPath": str(deskLogPath),
            }

        return {
            "ok": True,
            "msg": f"매매창 실행 (pid={proc.pid})",
            "pid": proc.pid,
            "proc": proc,
            "exitCode": None,
            "logPath": str(deskLogPath),
        }
    except Exception as err:
        termLog(f"[deskUpdate] 매매창 기동 실패: {err}")
        try:
            prevCwd = os.getcwd()
            os.chdir(str(cwd))
            from utils.deskFileLog import appendDeskFileLog

            appendDeskFileLog(f"매매창 기동 실패: {err}", tag="ERR")
            os.chdir(prevCwd)
        except Exception:
            pass
        return {
            "ok": False,
            "msg": f"매매창 실행 실패: {err}\n오류로그: {deskLogPath}",
            "pid": None,
            "proc": None,
            "exitCode": None,
            "logPath": str(deskLogPath),
        }
