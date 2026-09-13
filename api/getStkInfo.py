"""키움 주식기본정보요청 API (ka10001)"""

import requests

from utils.stkCdUtil import normStkCd


def getStkInfo(hostUrl, token, stkCd, contYn="N", nextKey="", tokenType="bearer"):
    """
    ka10001 주식기본정보요청 호출 후 Response 반환.
    body: stk_cd (거래소별 종목코드)
    """
    url = hostUrl + "/api/dostk/stkinfo"
    # 토큰타입은 보통 bearer — 앞에 Bearer 붙여 authorization 구성
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka10001",
    }
    # 요청 직전 종목코드 정규화 (선행 A 제거)
    data = {"stk_cd": normStkCd(stkCd)}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchStkInfo(hostUrl, token, stkCd, tokenType="bearer"):
    """
    ka10001 호출 결과를 dict로 반환한다.
    성공: ok=True, body=응답본문
    실패: ok=False, errMsg=사유
    """
    try:
        res = getStkInfo(hostUrl, token, stkCd, tokenType=tokenType)
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
        # return_code 없으면 종목명/코드 유무로 판단
        codeOk = bool(body.get("stk_nm") or body.get("stk_cd"))

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '종목 정보 조회 실패'}",
            "body": body,
        }

    return {"ok": True, "body": body, "returnMsg": returnMsg}
