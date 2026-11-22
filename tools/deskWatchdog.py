# -*- coding: utf-8 -*-
"""Paper-only SATP desk watchdog. Does not modify settings.json."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTINGS = REPO / "config" / "data" / "settings.json"
PAUSE = REPO / "config" / "data" / "desk_watchdog.pause"
LOCK = REPO / "config" / "data" / "desk_watchdog.lock"
LOG_DIR = REPO / "config" / "data" / "logs"
VERSION = REPO / "config" / "data" / "appVersion.json"
TITLE_PREFIX = "천둥이 MAIN"
SECRET_RE = re.compile(
    r"(telegram|token|토큰|Bearer|appkey|secret|crtfc_key|DART_API_KEY|/bot[^/\s]+)",
    re.I,
)
LONG_RE = re.compile(r"[A-Za-z0-9_\-]{28,}")
ACCT_RE = re.compile(r"\b\d{8,12}\b")


def system_root():
    return os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"


def conhost_exe():
    return Path(system_root()) / "System32" / "conhost.exe"


def powershell_exe():
    return Path(system_root()) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def desk_python():
    """Desk launcher. pythonw becomes the sibling python.exe. SATP_DESK_PYTHON wins."""
    override = str(os.environ.get("SATP_DESK_PYTHON") or "").strip()
    if override:
        return Path(override)
    text = str(sys.executable)
    leaf = text.replace("/", "\\").rsplit("\\", 1)[-1]
    if leaf.lower() == "pythonw.exe":
        return Path(text[: -len(leaf)] + "python.exe")
    return Path(text)


def now_kst():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def filt(text):
    text = SECRET_RE.sub("[REDACTED]", text or "")
    text = LONG_RE.sub("[REDACTED]", text)
    return ACCT_RE.sub("[ACCT]", text)


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{now_kst()}] {filt(str(msg))}\n"
    path = LOG_DIR / f"desk_watchdog_{dt.datetime.now().strftime('%Y%m%d')}.log"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _pid_running(pid):
    try:
        # 0 signal works as an existence check on Windows via os.kill.
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def acquire_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+", encoding="utf-8")
    try:
        fh.seek(0)
        raw = (fh.read() or "").strip()
        if raw.isdigit():
            pid = int(raw)
            if pid != os.getpid() and _pid_running(pid):
                fh.close()
                return None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except Exception as err:
        try:
            fh.close()
        except Exception:
            pass
        log(f"lock error {type(err).__name__}")
        return None


def window_titles():
    windll = getattr(ctypes, "windll", None)
    factory = getattr(ctypes, "WINFUNCTYPE", None)
    if windll is None or factory is None:
        return []
    user32 = windll.user32
    found = []
    WNDENUMPROC = factory(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(pid.value), buf.value or ""))
        return True

    cb = WNDENUMPROC(_cb)
    user32.EnumWindows(cb, 0)
    return found


def python_cmds():
    rows = []
    ps = str(powershell_exe())
    cmd = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    import base64

    b64 = base64.b64encode(cmd.encode("utf-16le")).decode("ascii")
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
            capture_output=True,
            timeout=30,
        )
        raw = proc.stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return rows
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            rows.append(
                {
                    "pid": int(item.get("ProcessId") or 0),
                    "name": str(item.get("Name") or ""),
                    "cmd": str(item.get("CommandLine") or ""),
                }
            )
    except Exception as err:
        log(f"process query error {type(err).__name__}")
    return rows


def is_desk_cmd(cmd):
    text = (cmd or "").replace("/", "\\").lower()
    if "deskwatchdog.py" in text:
        return False
    if "desk_watchdog" in text:
        return False
    if "main.py" not in text:
        return False
    if "python" not in text:
        return False
    if "\\tests\\" in text or "unittest" in text:
        return False
    return True


def find_desk():
    titled = []
    for pid, title in window_titles():
        if title.startswith(TITLE_PREFIX):
            titled.append({"pid": pid, "title": title, "how": "title"})
    by_cmd = []
    cmds = python_cmds()
    for row in cmds:
        if is_desk_cmd(row.get("cmd")):
            by_cmd.append({"pid": row["pid"], "cmd": "main.py", "how": "cmd"})
    pids = []
    seen = set()
    for row in titled + by_cmd:
        pid = row["pid"]
        if pid and pid not in seen:
            seen.add(pid)
            pids.append(row)
    return pids, titled, cmds


def read_paper_guard():
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception as err:
        return False, f"settings read failed {type(err).__name__}"
    mode = str(data.get("deskTradeMode") or "").strip().lower()
    if mode != "paper":
        return False, f"deskTradeMode={mode or 'empty'}"
    flags = []
    mt = data.get("maTouchLive")
    if isinstance(mt, dict) and "paperOnly" in mt:
        flags.append(("maTouchLive", mt.get("paperOnly") is True))
    sl = data.get("stratLive")
    if isinstance(sl, dict):
        for name, row in sl.items():
            if isinstance(row, dict) and "paperOnly" in row:
                flags.append((f"stratLive.{name}", row.get("paperOnly") is True))
    if not flags:
        return False, "paperOnly missing"
    bad = [name for name, ok in flags if not ok]
    if bad:
        return False, "paperOnly=false:" + ",".join(bad)
    return True, "deskTradeMode=paper paperOnly=true"


def read_app_version():
    """v1.065 처럼 v{major}.{minor:03d}. 파일을 못 읽으면 빈 문자열."""
    try:
        data = json.loads(VERSION.read_text(encoding="utf-8"))
        major = int(data.get("major"))
        minor = int(data.get("minor"))
    except Exception:
        return ""
    return f"v{major}.{minor:03d}"


def watchdog_notice(pid, when=None):
    stamp = when or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ver = read_app_version()
    tail = f"PID {pid}"
    if ver:
        tail = f"{tail}, {ver}"
    text = f"워치독: 모의 데스크를 재기동했습니다. {stamp} KST ({tail})"
    return text, stamp


def start_desk():
    env = os.environ.copy()
    env["CURSOR_AGENT"] = "1"
    env["SATP_SKIP_LOGIN"] = "1"
    env.pop("SATP_TEST", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(REPO)
    py = desk_python()
    conhost = conhost_exe()
    if not conhost.is_file() or not py.is_file():
        raise FileNotFoundError("conhost or python missing")
    proc = subprocess.Popen(
        [str(conhost), "--", str(py), "main.py"],
        cwd=str(REPO),
        env=env,
        close_fds=False,
    )
    return proc.pid


def send_watchdog_telegram(pid):
    os.chdir(str(REPO))
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from telegram.telSend import sendTelegramMsg

    text, stamp = watchdog_notice(pid)
    sendTelegramMsg(text)
    return stamp


def main():
    os.chdir(str(REPO))
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    lock = acquire_lock()
    if lock is None:
        log("skip: another watchdog run is active")
        return 0
    try:
        desks, titled, _cmds = find_desk()
        if desks:
            bits = ", ".join(f"PID {row['pid']}" for row in desks)
            log(f"ok: desk already running ({bits}); no start")
            return 0
        if PAUSE.is_file():
            log(f"skip: pause flag present ({PAUSE})")
            return 0
        ok, reason = read_paper_guard()
        if not ok:
            log(f"skip: not paper-only ({reason}); will not start")
            return 0
        log(f"start: paper guard passed ({reason}), launching via conhost")
        conhost_pid = start_desk()
        time.sleep(8)
        desks, titled, _cmds = find_desk()
        desk_pid = desks[0]["pid"] if desks else None
        title = titled[0]["title"] if titled else ""
        log(
            f"started: conhost PID {conhost_pid} "
            f"desk PID {desk_pid or 'pending'} title={title or '-'}"
        )
        try:
            stamp = send_watchdog_telegram(desk_pid or conhost_pid)
            log(f"telegram: watchdog restart notice sent at {stamp} KST")
        except Exception as err:
            log(f"telegram: send failed {type(err).__name__}")
        return 0
    finally:
        try:
            lock.close()
        except Exception:
            pass
        try:
            if LOCK.is_file():
                LOCK.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as err:
        log(f"fatal {type(err).__name__}")
        raise SystemExit(1)
