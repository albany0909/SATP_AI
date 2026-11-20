"""키움 미체결요청 API (ka10075). 호출 호스트는 받은 값 그대로 쓴다."""

import requests


def getOpenOrd(hostUrl, token, contYn="N", nextKey="", tokenType="bearer"):
    """ka10075 미체결요청. all_stk_tp 0, trde_tp 0, stex_tp 1(KRX)."""
    url = hostUrl + "/api/dostk/acnt"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka10075",
    }
    data = {
        "all_stk_tp": "0",
        "trde_tp": "0",
        "stk_cd": "",
        "stex_tp": "1",
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchOpenOrd(hostUrl, token, tokenType="bearer"):
    """성공이면 ok=True, body=응답본문. 실패면 ok=False."""
    try:
        res = getOpenOrd(hostUrl, token, tokenType=tokenType)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}
    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}
    if res.status_code != 200:
        return {
            "ok": False,
            "errMsg": f"HTTP {res.status_code} {body.get('return_msg') or ''}",
            "body": body,
        }
    try:
        codeOk = int(body.get("return_code") or 0) == 0
    except (TypeError, ValueError):
        codeOk = "oso" in body
    if not codeOk and "oso" not in body:
        return {
            "ok": False,
            "errMsg": f"[{body.get('return_code')}] {body.get('return_msg') or '미체결 조회 실패'}",
            "body": body,
        }
    return {"ok": True, "body": body}
