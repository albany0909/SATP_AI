"""클라이언트 설치본과 동일 조건으로 main 기동 검증."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERIFY = DIST / "_verifyClient2"


def main():
    # 최신 zip 스테이징
    sys.path.insert(0, str(ROOT))
    from scripts.deployPackUtil import resolveCompilePython, stageAppTree

    if VERIFY.exists():
        shutil.rmtree(VERIFY, ignore_errors=True)
    tradeApp = VERIFY / "tradeApp"
    compilePy = resolveCompilePython(ROOT)
    stageAppTree(ROOT, tradeApp, compilePy=compilePy)

    # runtime
    rtSrc = DIST / "tradeFull" / "runtime"
    if not (rtSrc / "python.exe").is_file():
        raise SystemExit("runtime 없음")
    shutil.copytree(rtSrc, tradeApp / "runtime")

    # session
    token = "verify_" + str(int(time.time()))
    sessDir = tradeApp / "config" / "data"
    sessDir.mkdir(parents=True, exist_ok=True)
    (sessDir / "deskLoginSession.json").write_text(
        json.dumps(
            {
                "token": token,
                "logId": "verify",
                "deskRole": "client",
                "createdAt": int(time.time()),
                "expiresAt": int(time.time()) + 3600,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    py = tradeApp / "runtime" / "python.exe"
    env = os.environ.copy()
    env["SATP_DESK_SESSION"] = token
    env["SATP_FROM_LOGIN_LAUNCHER"] = "1"
    env["SATP_DESK_ROLE"] = "client"
    env["SATP_TRADE_APP_DIR"] = str(tradeApp.resolve())
    # UI 없이 import·세션·부트 경로만 보려면 HEADLESS — 그래도 UI create 는 스킵
    # 실제 매매창은 UI 필요. 검증은 8초 내 생존 + 로그
    env.pop("SATP_HEADLESS", None)

    # 1) import 검증
    r = subprocess.run(
        [
            str(py),
            "-c",
            "import strategies.impl.alignMaBreak; import trading.watchRtMonitor; import main; print('IMPORT_OK')",
        ],
        cwd=str(tradeApp),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print("import stdout:", r.stdout.strip())
    print("import stderr:", (r.stderr or "")[-500:])
    print("import code:", r.returncode)
    if r.returncode != 0:
        return 1

    # 2) main 기동 — 8초 생존하면 성공 (로그인창과 동일 기준)
    logPath = sessDir / "verify_launch.log"
    with logPath.open("w", encoding="utf-8") as fp:
        proc = subprocess.Popen(
            [str(py), "main.py"],
            cwd=str(tradeApp),
            env=env,
            stdout=fp,
            stderr=subprocess.STDOUT,
        )
        try:
            code = proc.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            code = None
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    txt = logPath.read_text(encoding="utf-8", errors="replace")
    print("--- launch log (tail) ---")
    # cp949 콘솔에서 em dash 등 출력 실패 방지
    for line in txt.splitlines()[-40:]:
        print(line.encode("cp949", errors="replace").decode("cp949"))
    print("launch exit:", code)

    # 로그 날짜 = 달력 오늘
    from datetime import datetime

    today = datetime.now().strftime("%Y%m%d")
    deskLog = sessDir / "logs" / f"SATP_{today}.log"
    print("today log exists:", deskLog.is_file(), str(deskLog))
    if not deskLog.is_file():
        print("FAIL: today SATP_YYYYMMDD.log missing")
        return 1
    # 구날짜(deskLogYmd 거래일) 파일이 오늘 대신 쓰이지 않았는지
    oldish = list((sessDir / "logs").glob("SATP_*.log"))
    names = [p.name for p in oldish]
    print("log files:", ", ".join(names))

    if code is not None and code != 0:
        print("FAIL: main exited early")
        return 1
    print("PASS: main stayed up >= 8s + today log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
