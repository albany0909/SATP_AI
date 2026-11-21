"""테스트가 키움·운영 DB·라이브 로그·config/data 에 닿으면 실패한다."""

from __future__ import annotations

import os

os.environ["SATP_TEST"] = "1"

_INSTALLED = False


class LiveGuardError(AssertionError):
    """운영 자원에 닿은 테스트."""


def _blocked(what):
    raise LiveGuardError(what)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _patch_http()
    _patch_ws()
    _patch_db()
    _patch_logs()
    _patch_state()


def _patch_http():
    try:
        import requests
    except Exception:
        return
    real = requests.sessions.Session.request

    def _request(self, method, url, *args, **kwargs):
        host = str(url or "")
        if "kiwoom.com" in host or "mockapi" in host:
            _blocked(f"키움 HTTP 금지 {host}")
        return real(self, method, url, *args, **kwargs)

    requests.sessions.Session.request = _request


def _patch_ws():
    try:
        import websocket
    except Exception:
        return

    def _no(*_a, **_k):
        _blocked("키움 WS 금지")

    websocket.create_connection = _no
    if hasattr(websocket, "WebSocket"):
        websocket.WebSocket.connect = _no


def _patch_db():
    try:
        import utils.dbConn as db
    except Exception:
        return

    def _open(*_a, **_k):
        _blocked("운영 DB 연결 금지")

    db.openDbConn = _open


def _patch_logs():
    try:
        import utils.deskFileLog as logs
    except Exception:
        return

    def _append(*_a, **_k):
        _blocked("라이브 로그 쓰기 금지")

    logs.appendDeskFileLog = _append

    import utils.termLog as term

    def _print_only(msg):
        text = "" if msg is None else str(msg)
        print(text, flush=True)

    term.termLog = _print_only


def _patch_state():
    try:
        import utils.pbk01Store as store
    except Exception:
        return
    real = store.appendRow

    def _append(path, row):
        text = str(path).replace("\\", "/")
        if "/config/data/" in text:
            _blocked(f"운영 상태 쓰기 금지 {text}")
        return real(path, row)

    store.appendRow = _append


install()
