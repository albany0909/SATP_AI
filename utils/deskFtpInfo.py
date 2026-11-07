"""
FTP 접속정보 조회 — 구버전과 동일하게 서버 공통코드 FTP_INFO.

TB_CMN_CODE (CMN_CDNM='FTP_INFO') 는 서버(STP)만 조회한다.
클라이언트는 DB 직접 접속이 불가하므로 로컬 DB 폴백 없음.

조회: STP SATPLITEIQ008 (CMN_CDNM=FTP_INFO)
  → FTP_PATH, USER, PWD, SETUP_FILE, KEY, PROCESS_NM
"""

from __future__ import annotations

from utils.termLog import termLog

_CMN_CDNM = "FTP_INFO"


def _mapFromPairs(rows):
    """[{CMN_CD, CMN_CD_DC}, ...] → ftp dict."""
    out = {
        "ftpPath": "",
        "ftpUser": "",
        "ftpPwd": "",
        "setupFile": "",
        "key": "",
        "processNm": "",
    }
    if not rows:
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        cd = str(row.get("CMN_CD") or "").strip().upper()
        dc = str(row.get("CMN_CD_DC") or "").strip()
        if cd == "FTP_PATH":
            out["ftpPath"] = dc
        elif cd == "USER":
            out["ftpUser"] = dc
        elif cd == "PWD":
            out["ftpPwd"] = dc
        elif cd == "SETUP_FILE":
            out["setupFile"] = dc
        elif cd == "KEY":
            out["key"] = dc
        elif cd == "PROCESS_NM":
            out["processNm"] = dc
    return out


def _fromStp():
    """SATPLITEIQ008 공통코드조회."""
    try:
        from utils.stpTranClient import sendStpTran

        got = sendStpTran(
            "SATPLITEIQ008",
            {"CMN_CDNM": _CMN_CDNM},
            timeoutSec=15,
        )
    except Exception as err:
        termLog(f"[ftpInfo] STP 조회 실패: {err}")
        return None
    if not got.get("ok"):
        termLog(f"[ftpInfo] STP 실패: {got.get('errMsg')}")
        return None
    body = got.get("body") if isinstance(got.get("body"), dict) else {}
    common = body.get("COMMON_LIST")
    rows = []
    if isinstance(common, list):
        rows = common
    elif isinstance(common, dict):
        for v in common.values():
            if isinstance(v, list):
                rows = v
                break
            if isinstance(v, dict) and ("CMN_CD" in v or "cmn_cd" in v):
                rows.append(v)
    if not rows:
        if body.get("FTP_PATH") or body.get("USER"):
            return {
                "ftpPath": str(body.get("FTP_PATH") or "").strip(),
                "ftpUser": str(body.get("USER") or "").strip(),
                "ftpPwd": str(body.get("PWD") or "").strip(),
                "setupFile": str(body.get("SETUP_FILE") or "").strip(),
                "key": str(body.get("KEY") or "").strip(),
                "processNm": str(body.get("PROCESS_NM") or "").strip(),
                "source": "stp",
            }
        return None
    mapped = _mapFromPairs(rows)
    mapped["source"] = "stp"
    return mapped if mapped.get("ftpPath") else None


def fetchFtpInfo():
    """
    FTP 정보 dict (STP 서버만).
    {ftpPath, ftpUser, ftpPwd, setupFile, key, processNm, source}
    실패 시 None. 클라이언트 DB 직접 조회 없음.
    """
    got = _fromStp()
    if got and got.get("ftpPath"):
        termLog(f"[ftpInfo] OK source=stp path={got.get('ftpPath')}")
        return got
    termLog("[ftpInfo] FTP_INFO 없음 (STP SATPLITEIQ008)")
    return None


def mergeFtpIntoLoginBody(body: dict):
    """
    로그인 body 에 FTP 필드가 비어 있으면 서버 FTP_INFO 로 채움.
    반환: body (동일 객체 갱신).
    """
    data = body if isinstance(body, dict) else {}
    need = not str(data.get("FTP_PATH") or "").strip()
    if not need:
        return data
    info = fetchFtpInfo()
    if not info:
        return data
    data.setdefault("FTP_PATH", info.get("ftpPath") or "")
    data.setdefault("USER", info.get("ftpUser") or "")
    data.setdefault("PWD", info.get("ftpPwd") or "")
    data.setdefault("SETUP_FILE", info.get("setupFile") or "")
    data.setdefault("KEY", info.get("key") or "")
    data.setdefault("PROCESS_NM", info.get("processNm") or "")
    return data
