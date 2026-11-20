"""키움 주식호가요청 (ka10004). 매도 1호가만 꺼낸다."""

import requests

from utils.stkCdUtil import normStkCd


def getHoga(hostUrl, token, stkCd, contYn="N", nextKey="", tokenType="bearer"):
    url = hostUrl + "/api/dostk/mrkcond"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka10004",
    }
    data = {"stk_cd": normStkCd(stkCd)}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchHoga(hostUrl, token, stkCd, tokenType="bearer"):
    try:
        res = getHoga(hostUrl, token, stkCd, tokenType=tokenType)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}
    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}
    if res.status_code != 200:
        return {"ok": False, "errMsg": f"HTTP {res.status_code}", "body": body}
    return {"ok": True, "body": body}
