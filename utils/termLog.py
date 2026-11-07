"""터미널 로그 유틸 — 좌측에 수행 시각을 붙여 출력한다."""

import sys
import time
from datetime import datetime
from io import TextIOWrapper


def _ensureUtf8Stdio():
    """
    한글 Windows 기본 stdout은 cp949인데 Cursor 터미널은 UTF-8이라
    한글 로그가 깨진다. 프로세스 표준출력을 UTF-8로 맞춘다.
    """
    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, TextIOWrapper):
            continue
        try:
            enc = (stream.encoding or "").lower().replace("-", "")
            if enc != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # 콘솔 코드페이지도 UTF-8(65001)로 — cmd/PowerShell 직접 실행 대비
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


_ensureUtf8Stdio()

# 매매창 오류 탭. termLog 재호출 금지(재귀).
_deskErrHook = None
_ERR_KEYS = (
    "실패",
    "오류",
    "에러",
    "Exception",
    "Traceback",
    "Error:",
    "FATAL",
    "처리되지 않은",
    "cannot be started from another thread",
    "cannot be stopped from another thread",
)

# 동일 DB 실패 메시지 폭주 억제
_rateLast = {}
_RATE_SEC = 120
_RATE_KEYS = (
    "테이블 생성",
    "local DB disabled",
    "Can't connect",
    "연결을 거부",
    "DB cooldown",
    "조회 실패",
    "시드 실패",
    "보강 실패",
)


def setDeskErrHook(fn):
    """매매창이 뜰 때 오류 한 줄을 받는 콜백. 닫히면 None."""
    global _deskErrHook
    _deskErrHook = fn


def clearDeskErrHook():
    global _deskErrHook
    _deskErrHook = None


def looksLikeErr(text):
    """실패·예외·Qt 스레드 경고만 오류로 본다."""
    raw = str(text or "")
    if not raw.strip():
        return False
    return any(k in raw for k in _ERR_KEYS)


def emitDeskErr(msg):
    """오류 탭으로만 보낸다. 콘솔 출력은 하지 않는다."""
    fn = _deskErrHook
    if fn is None:
        return
    try:
        fn(str(msg or ""))
    except Exception:
        pass


def _rateLimited(text: str) -> bool:
    """반복 DB/테이블 실패 로그면 True(출력 생략). client DB 비활성은 완전 무시."""
    if "local DB disabled" in text:
        return True
    if not any(k in text for k in _RATE_KEYS):
        return False
    now = time.monotonic()
    prev = _rateLast.get(text, 0.0)
    if now - prev < _RATE_SEC:
        return True
    _rateLast[text] = now
    return False


def termLog(msg):
    """
    메시지를 터미널에 출력한다.
    형식: [YYYY-MM-DD HH:MM:SS] 메시지
    여러 줄이면 각 줄 앞에 동일 시각을 붙인다.
    실패·예외 문구는 매매창 오류 탭에도 올린다.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = "" if msg is None else str(msg)
    if _rateLimited(text):
        return
    # 빈 줄도 시각만 남기지 않도록 strip 하지 않고 줄 단위로 처리
    lines = text.splitlines() or [""]
    for line in lines:
        print(f"[{stamp}] {line}", flush=True)
    if looksLikeErr(text):
        emitDeskErr(text)
    # 매매창 기동 중이면 로컬 일자별 파일에도 남김
    try:
        from utils.deskFileLog import appendDeskFileLog

        for line in lines:
            if line.strip():
                appendDeskFileLog(line, tag="TERM")
    except Exception:
        pass
