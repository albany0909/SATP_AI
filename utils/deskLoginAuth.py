"""
회원 로그인 (STP SATPLITEIQ003).

배포 PC(로그인 런처)는 항상 deskRole=client.
main 모드는 Cursor/개발 기동(SATP_SKIP_LOGIN) 전용 — DESK_GRADE 로 main 부여하지 않음.
"""

from __future__ import annotations

from utils.termLog import termLog


def _stpLogin(logId, loginPswd):
    from utils.stpTranClient import sendStpTran

    return sendStpTran(
        "SATPLITEIQ003",
        {"LOG_ID": str(logId or "").strip(), "LOGIN_PSWD": str(loginPswd or "").strip()},
        timeoutSec=20,
    )


def _gradeFromDb(logId):
    """STP 응답에 DESK_GRADE 없을 때 DB 직접 조회 (표시용)."""
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DESK_GRADE, CSMNO, CUST_NM FROM tb_cusm_cust WHERE LOG_ID=%s LIMIT 1",
                    (str(logId or "").strip(),),
                )
                row = cur.fetchone() or {}
                if not isinstance(row, dict):
                    return None
                return row
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskLogin] DESK_GRADE DB 조회 실패: {err}")
        return None


def loginMember(logId, loginPswd):
    """
    성공: {
      ok:True, logId, csmno, custNm, deskGrade:'M'|'C', deskRole:'client',
      sessToken, ftp…
    }
    실패: {ok:False, errMsg:str}
    """
    lid = str(logId or "").strip()
    pw = str(loginPswd or "").strip()
    if not lid or not pw:
        return {"ok": False, "errMsg": "ID/비밀번호를 입력하세요"}

    got = _stpLogin(lid, pw)
    body = got.get("body") if isinstance(got.get("body"), dict) else {}
    if not got.get("ok"):
        dbRow = _authDb(lid, pw)
        if not dbRow:
            return {
                "ok": False,
                "errMsg": got.get("errMsg") or "로그인 실패 (서버 연결/인증)",
            }
        body = dict(dbRow)
        body["LOGIN_YN"] = "Y"

    try:
        from utils.deskFtpInfo import mergeFtpIntoLoginBody

        body = mergeFtpIntoLoginBody(body)
    except Exception as err:
        termLog(f"[deskLogin] FTP_INFO 보강 실패: {err}")

    loginYn = str(body.get("LOGIN_YN") or "").strip().upper()
    if loginYn and loginYn != "Y":
        return {"ok": False, "errMsg": "ID 또는 비밀번호가 올바르지 않습니다"}

    csmno = str(body.get("CSMNO") or "").strip()
    if not csmno and loginYn != "Y":
        return {"ok": False, "errMsg": "ID 또는 비밀번호가 올바르지 않습니다"}

    grade = str(body.get("DESK_GRADE") or "").strip().upper()
    if grade not in ("M", "C"):
        dbInfo = _gradeFromDb(lid) or {}
        grade = str(dbInfo.get("DESK_GRADE") or "C").strip().upper()
        if not csmno:
            csmno = str(dbInfo.get("CSMNO") or "").strip()
        if not body.get("CUST_NM"):
            body["CUST_NM"] = dbInfo.get("CUST_NM") or ""

    if grade not in ("M", "C"):
        grade = "C"

    # 로그인 런처 경로 = 항상 client (마스터 계정도 main 아님)
    deskRole = "client"
    try:
        from config.settingsStore import setDeskRole

        setDeskRole(deskRole)
    except Exception as err:
        termLog(f"[deskLogin] deskRole 저장 실패: {err}")

    from utils.deskLoginSess import claimDeskLogin, newSessToken

    sessTok = newSessToken()
    claim = claimDeskLogin(lid, sessTok, force=True)
    if not claim.get("ok"):
        return {
            "ok": False,
            "errMsg": claim.get("errMsg") or "다른 곳에서 이미 로그인 중입니다",
        }

    return {
        "ok": True,
        "logId": lid,
        "csmno": csmno,
        "custNm": str(body.get("CUST_NM") or "").strip(),
        "deskGrade": grade,
        "deskRole": deskRole,
        "sessToken": claim.get("sessToken") or sessTok,
        "sessKicked": bool(claim.get("kicked")),
        "ftpPath": str(body.get("FTP_PATH") or "").strip(),
        "ftpUser": str(body.get("USER") or "").strip(),
        "ftpPwd": str(body.get("PWD") or "").strip(),
        "setupFile": str(body.get("SETUP_FILE") or "").strip(),
        "processNm": str(body.get("PROCESS_NM") or "").strip(),
        "key": str(body.get("KEY") or "").strip(),
    }


def _authDb(logId, loginPswd):
    """로컬 DB 폴백 인증."""
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT CSMNO, LOG_ID, CUST_NM, DESK_GRADE
                    FROM tb_cusm_cust
                    WHERE LOG_ID=%s AND LOGIN_PSWD=%s
                    LIMIT 1
                    """,
                    (logId, loginPswd),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()
    except Exception as err:
        termLog(f"[deskLogin] DB 인증 실패: {err}")
        return None
