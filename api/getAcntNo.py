"""키움 계좌번호조회 API (ka00001)"""

import requests


def getAcntNo(hostUrl, token, contYn="N", nextKey="", tokenType="bearer"):
    """
    ka00001 계좌번호조회 호출 후 Response 반환.
    body 없음 — 토큰에 묶인 계좌번호만 응답.
    """
    url = hostUrl + "/api/dostk/acnt"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka00001",
    }
    return requests.post(url, headers=headers, json={}, timeout=30)


def fetchAcntNo(hostUrl, token, tokenType="bearer"):
    """
    ka00001 결과를 dict로 반환.
    성공: ok=True, acctNo=계좌번호
    실패: ok=False, errMsg=사유
    """
    try:
        res = getAcntNo(hostUrl, token, tokenType=tokenType)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}

    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}

    if res.status_code != 200:
        returnCode = body.get("return_code")
        returnMsg = body.get("return_msg") or res.text
        return {
            "ok": False,
            "errMsg": f"HTTP {res.status_code} [{returnCode}] {returnMsg}",
            "body": body,
        }

    returnCode = body.get("return_code")
    returnMsg = body.get("return_msg", "")
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        # return_code 누락이어도 acctNo 있으면 성공으로 본다
        codeOk = bool(str(body.get("acctNo") or "").strip())

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '계좌번호 조회 실패'}",
            "body": body,
        }

    acctNo = str(body.get("acctNo") or "").strip()
    if not acctNo:
        return {
            "ok": False,
            "errMsg": "응답에 계좌번호(acctNo)가 없습니다.",
            "body": body,
        }

    return {"ok": True, "acctNo": acctNo, "body": body, "returnMsg": returnMsg}
