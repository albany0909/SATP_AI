"""
톰캣 STP /tran 전문 송수신.
요청: { envelope: { header: { gramId, ... }, body: {...} } }
응답: envelope.body (또는 전체 envelope)
"""

from __future__ import annotations

from datetime import datetime

import requests

from utils.termLog import termLog


def _nowParts():
    now = datetime.now()
    return now.strftime("%Y%m%d"), now.strftime("%H%M%S")


def buildHeader(gramId, extra=None):
    tdt, hr = _nowParts()
    hdr = {
        "gramId": str(gramId or "").strip(),
        "rqstRspDvcd": "S",
        "TDT": tdt,
        "TRNS_HR": hr,
        "SYS_DV": "AI",
        "USER_ID": "satp_ai",
        "CSMNO": "10000000",
        "gblId": "",
    }
    if isinstance(extra, dict):
        hdr.update(extra)
    return hdr


def sendStpTran(gramId, body=None, serverUrl=None, timeoutSec=15):
    """
    STP 거래 1회.
    성공: {ok:True, body:dict, raw:dict}
    실패: {ok:False, errMsg:str, body:dict|None}
    """
    try:
        from config.settingsStore import getStpServerUrl

        url = (serverUrl or getStpServerUrl() or "").strip()
    except Exception:
        url = str(serverUrl or "").strip()
    if not url:
        return {"ok": False, "errMsg": "stpServerUrl 없음", "body": None}
    payload = {
        "envelope": {
            "header": buildHeader(gramId),
            "body": body if isinstance(body, dict) else {},
        }
    }
    try:
        res = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=max(3, int(timeoutSec or 15)),
        )
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}", "body": None}
    try:
        raw = res.json()
    except Exception as err:
        return {
            "ok": False,
            "errMsg": f"JSON 파싱 실패 HTTP {res.status_code}: {err}",
            "body": None,
        }
    if res.status_code != 200:
        return {
            "ok": False,
            "errMsg": f"HTTP {res.status_code}",
            "body": raw if isinstance(raw, dict) else None,
            "raw": raw,
        }
    env = raw.get("envelope") if isinstance(raw, dict) else None
    if not isinstance(env, dict):
        # 일부 응답은 body 만
        if isinstance(raw, dict) and ("ROWS" in raw or "SETTINGS_JSON" in raw or "MAP" in raw):
            return {"ok": True, "body": raw, "raw": raw}
        return {"ok": False, "errMsg": "envelope 없음", "body": None, "raw": raw}
    hdr = env.get("header") if isinstance(env.get("header"), dict) else {}
    bodyOut = env.get("body") if isinstance(env.get("body"), dict) else {}
    # 프레임워크 오류 코드가 있으면 실패
    rslt = str(hdr.get("rspRsltDvcd") or hdr.get("RSP_RSLT_DVCD") or "").strip()
    if rslt and rslt.upper() not in ("", "0", "S", "N", "SUCCESS"):
        msg = hdr.get("rspMsg") or hdr.get("RSP_MSG") or rslt
        return {"ok": False, "errMsg": str(msg), "body": bodyOut, "raw": raw}
    return {"ok": True, "body": bodyOut, "raw": raw, "header": hdr}


def termLogStpFail(opName, got):
    err = (got or {}).get("errMsg") or "unknown"
    termLog(f"[stpTran] {opName} 실패: {err}")
