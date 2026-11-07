"""
계정 단위 로그인 세션 (STP AI_DESK_LOGIN_SESS).

- claim: 로그인 성공 시 토큰 등록 (기존 세션 무효화 = 중복 차단)
- check: 주기 확인 — 토큰 불일치면 다른 곳 로그인
- release: 로그아웃·종료 시 해제
"""

from __future__ import annotations

import secrets
import socket
from utils.termLog import termLog


def _hostNm():
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _stp(gramId, body, timeoutSec=15):
    from utils.stpTranClient import sendStpTran

    return sendStpTran(gramId, body or {}, timeoutSec=timeoutSec)


def newSessToken():
    return secrets.token_hex(16)


def claimDeskLogin(logId, sessToken=None, force=True, hostNm=None):
    """
    서버에 세션 점유.
    성공: {ok:True, sessToken, kicked:bool}
    실패: {ok:False, errMsg, duplicate:bool}
    """
    lid = str(logId or "").strip()
    tok = str(sessToken or newSessToken()).strip()
    if not lid or not tok:
        return {"ok": False, "errMsg": "세션 정보가 없습니다"}
    got = _stp(
        "SATPAIWR050",
        {
            "LOG_ID": lid,
            "SESS_TOKEN": tok,
            "HOST_NM": str(hostNm if hostNm is not None else _hostNm())[:120],
            "FORCE": "Y" if force else "N",
        },
    )
    body = got.get("body") if isinstance(got.get("body"), dict) else {}
    if not got.get("ok"):
        # STP 실패 시 로컬 DB 폴백 (main)
        db = _claimDb(lid, tok, force=force, hostNm=hostNm)
        if db.get("ok"):
            return db
        return {
            "ok": False,
            "errMsg": got.get("errMsg") or "로그인 세션 등록 실패 (서버)",
            "duplicate": False,
        }
    if str(body.get("CLAIMED") or "").upper() != "Y":
        dup = str(body.get("DUPLICATE") or "").upper() == "Y"
        return {
            "ok": False,
            "errMsg": str(body.get("MSG") or "다른 곳에서 이미 로그인 중입니다"),
            "duplicate": dup,
            "hostNm": str(body.get("HOST_NM") or ""),
        }
    return {
        "ok": True,
        "sessToken": tok,
        "kicked": str(body.get("PREV_KICKED") or "").upper() == "Y",
    }


def checkDeskLogin(logId, sessToken):
    """유효하면 {ok:True, valid:True}. 다른 곳 로그인이면 valid:False."""
    lid = str(logId or "").strip()
    tok = str(sessToken or "").strip()
    if not lid or not tok:
        return {"ok": False, "valid": False, "errMsg": "세션 없음"}
    got = _stp("SATPAIIQ050", {"LOG_ID": lid, "SESS_TOKEN": tok})
    body = got.get("body") if isinstance(got.get("body"), dict) else {}
    if not got.get("ok"):
        db = _checkDb(lid, tok)
        if db.get("ok"):
            return db
        # 서버 일시 장애 시 끊지 않음 (오탐 종료 방지)
        termLog(f"[deskSess] check 서버실패 — 유지: {got.get('errMsg')}")
        return {"ok": True, "valid": True, "softFail": True}
    valid = str(body.get("VALID") or "").upper() == "Y"
    return {
        "ok": True,
        "valid": valid,
        "errMsg": str(body.get("MSG") or ""),
        "hostNm": str(body.get("HOST_NM") or ""),
    }


def releaseDeskLogin(logId, sessToken):
    lid = str(logId or "").strip()
    tok = str(sessToken or "").strip()
    if not lid or not tok:
        return {"ok": True}
    got = _stp("SATPAIWR051", {"LOG_ID": lid, "SESS_TOKEN": tok})
    if not got.get("ok"):
        _releaseDb(lid, tok)
    return {"ok": True}


def _claimDb(logId, tok, force=True, hostNm=None):
    try:
        from utils.dbConn import isLocalDbEnabled, openDbConn

        if not isLocalDbEnabled():
            return {"ok": False, "errMsg": "로컬 DB 없음"}
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS AI_DESK_LOGIN_SESS (
                      LOG_ID VARCHAR(64) NOT NULL,
                      SESS_TOKEN VARCHAR(64) NOT NULL,
                      HOST_NM VARCHAR(128) NULL,
                      LOGIN_AT DATETIME NOT NULL,
                      HEARTBEAT_AT DATETIME NOT NULL,
                      EXPIRES_AT DATETIME NOT NULL,
                      PRIMARY KEY (LOG_ID)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cur.execute(
                    "SELECT SESS_TOKEN, HEARTBEAT_AT, HOST_NM FROM AI_DESK_LOGIN_SESS WHERE LOG_ID=%s",
                    (logId,),
                )
                row = cur.fetchone() or {}
                kicked = False
                if row and str(row.get("SESS_TOKEN") or "") not in ("", tok):
                    if not force:
                        return {
                            "ok": False,
                            "duplicate": True,
                            "errMsg": "다른 곳에서 이미 로그인 중입니다",
                            "hostNm": str(row.get("HOST_NM") or ""),
                        }
                    kicked = True
                host = str(hostNm if hostNm is not None else _hostNm())[:120]
                cur.execute(
                    """
                    INSERT INTO AI_DESK_LOGIN_SESS
                      (LOG_ID, SESS_TOKEN, HOST_NM, LOGIN_AT, HEARTBEAT_AT, EXPIRES_AT)
                    VALUES (%s, %s, %s, NOW(), NOW(), DATE_ADD(NOW(), INTERVAL 16 HOUR))
                    ON DUPLICATE KEY UPDATE
                      SESS_TOKEN=VALUES(SESS_TOKEN),
                      HOST_NM=VALUES(HOST_NM),
                      LOGIN_AT=VALUES(LOGIN_AT),
                      HEARTBEAT_AT=VALUES(HEARTBEAT_AT),
                      EXPIRES_AT=VALUES(EXPIRES_AT)
                    """,
                    (logId, tok, host),
                )
            conn.commit()
            return {"ok": True, "sessToken": tok, "kicked": kicked}
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskSess] claim DB 실패: {err}")
        return {"ok": False, "errMsg": str(err)}


def _checkDb(logId, tok):
    try:
        from utils.dbConn import isLocalDbEnabled, openDbConn

        if not isLocalDbEnabled():
            return {"ok": False, "valid": False}
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT SESS_TOKEN, HOST_NM, EXPIRES_AT
                    FROM AI_DESK_LOGIN_SESS WHERE LOG_ID=%s
                    """,
                    (logId,),
                )
                row = cur.fetchone() or {}
                if not row:
                    return {"ok": True, "valid": False, "errMsg": "세션 없음"}
                if str(row.get("SESS_TOKEN") or "") != tok:
                    return {
                        "ok": True,
                        "valid": False,
                        "errMsg": "다른 곳에서 로그인됨",
                        "hostNm": str(row.get("HOST_NM") or ""),
                    }
                cur.execute(
                    """
                    UPDATE AI_DESK_LOGIN_SESS
                    SET HEARTBEAT_AT=NOW()
                    WHERE LOG_ID=%s AND SESS_TOKEN=%s
                    """,
                    (logId, tok),
                )
            conn.commit()
            return {"ok": True, "valid": True}
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskSess] check DB 실패: {err}")
        return {"ok": False, "valid": False, "errMsg": str(err)}


def _releaseDb(logId, tok):
    try:
        from utils.dbConn import isLocalDbEnabled, openDbConn

        if not isLocalDbEnabled():
            return
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM AI_DESK_LOGIN_SESS WHERE LOG_ID=%s AND SESS_TOKEN=%s",
                    (logId, tok),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskSess] release DB 실패: {err}")
