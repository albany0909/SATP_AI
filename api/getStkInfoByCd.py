"""키움 종목정보 조회 API (ka10100) — 종목코드 → 상세"""

import requests

from utils.stkCdUtil import normStkCd


def getStkInfoByCd(hostUrl, token, stkCd, contYn="N", nextKey="", tokenType="bearer"):
    """ka10100 종목정보 조회 1회 호출."""
    url = hostUrl + "/api/dostk/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10100",
    }
    data = {"stk_cd": normStkCd(stkCd)}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchStkInfoByCd(hostUrl, token, stkCd, tokenType="bearer"):
    """
    ka10100 결과 dict.
    성공: ok, body(code/name 등)
    """
    try:
        res = getStkInfoByCd(hostUrl, token, stkCd, tokenType=tokenType)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}

    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}

    if res.status_code != 200:
        return {
            "ok": False,
            "errMsg": (
                f"HTTP {res.status_code} "
                f"[{body.get('return_code')}] {body.get('return_msg') or res.text}"
            ),
            "body": body,
        }

    returnCode = body.get("return_code")
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        codeOk = bool(body.get("code") or body.get("name"))

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '종목정보 조회 실패'}",
            "body": body,
        }

    return {"ok": True, "body": body, "returnMsg": body.get("return_msg", "")}
