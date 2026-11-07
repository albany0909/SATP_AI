"""MySQL 연결 헬퍼.

client(deskRole) 는 로컬 DB 를 쓰지 않는다 — 서버 조회·키움만.
연결 실패 시 쿨다운으로 TCP 대기·로그 폭주를 막는다.
"""

from __future__ import annotations

import time

import pymysql

from config import dbConfig

# 연결 실패 후 재시도 억제 (초)
_DOWN_COOLDOWN_SEC = 180
_CONNECT_TIMEOUT_SEC = 2

_downUntil = 0.0
_downReason = ""
_downLogged = False


class DbDisabledError(RuntimeError):
    """로컬 DB 미사용 (client 등). 로그·재시도 없이 즉시 실패."""


class DbDownError(RuntimeError):
    """최근 연결 실패로 쿨다운 중."""


def isLocalDbEnabled() -> bool:
    """
    로컬 MySQL 사용 여부.
    client 매매창은 STP 서버 + 키움만 — DB DDL/직결 금지.
    """
    try:
        from config.settingsStore import isDeskClient

        if isDeskClient():
            return False
    except Exception:
        pass
    return True


def isDbInCooldown() -> bool:
    return time.monotonic() < _downUntil


def tryOpenDbConn():
    """로컬 DB 사용 가능하면 커넥션, 아니면 None (예외·로그 없음)."""
    if not isLocalDbEnabled() or isDbInCooldown():
        return None
    try:
        return openDbConn()
    except (DbDisabledError, DbDownError):
        return None
    except Exception:
        return None


def openDbConn():
    """
    config.dbConfig 기준으로 새 커넥션을 연다.
    호출측에서 close / with 사용.
    client 는 DbDisabledError (로그 없음 — 호출측도 무시할 것).
    """
    global _downUntil, _downReason, _downLogged

    if not isLocalDbEnabled():
        raise DbDisabledError("client: local DB disabled")

    now = time.monotonic()
    if now < _downUntil:
        raise DbDownError(_downReason or "DB cooldown")

    try:
        conn = pymysql.connect(
            host=dbConfig.dbHost,
            port=int(dbConfig.dbPort),
            user=dbConfig.dbUser,
            password=dbConfig.dbPassword,
            database=dbConfig.dbName,
            charset=dbConfig.dbCharset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=_CONNECT_TIMEOUT_SEC,
        )
        _downUntil = 0.0
        _downReason = ""
        _downLogged = False
        return conn
    except DbDisabledError:
        raise
    except Exception as err:
        _downUntil = now + _DOWN_COOLDOWN_SEC
        _downReason = str(err)
        if not _downLogged:
            try:
                from utils.termLog import termLog

                termLog(
                    f"[db] 연결 실패 — {_DOWN_COOLDOWN_SEC}초간 재시도 생략: {err}"
                )
            except Exception:
                pass
            _downLogged = True
        raise
