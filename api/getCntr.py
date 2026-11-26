"""키움 체결요청 (ka10076). 주문번호별 체결 수량."""

import requests


def getCntr(hostUrl, token, contYn="N", nextKey="", tokenType="bearer"):
    url = hostUrl + "/api/dostk/acnt"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka10076",
    }
    data = {"stk_cd": "", "qry_tp": "0", "sell_tp": "2", "ord_no": "", "stex_tp": "1"}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchCntr(hostUrl, token, tokenType="bearer"):
    try:
        res = getCntr(hostUrl, token, tokenType=tokenType)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}
    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}
    if res.status_code != 200:
        return {"ok": False, "errMsg": f"HTTP {res.status_code}", "body": body}
    return {"ok": True, "body": body}
