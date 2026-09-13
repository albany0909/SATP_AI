"""키움 접근 토큰 발급 API (au10001)"""

from datetime import datetime

import requests

# 명세 notes_and_errors 의 주요 오류코드 → 사용자 안내
AU10001_ERR_MAP = {
    8001: "App Key와 Secret Key 검증에 실패했습니다",
    8002: "App Key와 Secret Key 검증에 실패했습니다",
    8006: "Access Token 생성에 실패했습니다",
    8009: "Access Token 발급에 실패했습니다",
    8011: "grant_type이 입력되지 않았습니다",
    8012: "grant_type 값이 올바르지 않습니다",
    8020: "appkey 또는 secretkey가 입력되지 않았습니다",
    8030: "투자구분(실전/모의)이 달라 App Key를 사용할 수 없습니다",
    8031: "투자구분(실전/모의)이 달라 Token을 사용할 수 없습니다",
}


def getAccToken(hostUrl, appKey, appSecret):
    """
    au10001 접근 토큰 발급 API 호출 후 Response 반환.
    client_credentials 방식으로 앱키/시크릿 인증한다.
    """
    url = hostUrl + "/oauth2/token"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        # 명세 request header — TR 식별
        "api-id": "au10001",
    }
    data = {
        "grant_type": "client_credentials",
        "appkey": appKey,
        "secretkey": appSecret,
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _fmtErrMsg(returnCode, returnMsg):
    """return_code를 명세 안내와 함께 읽기 쉬운 메시지로 만든다."""
    try:
        codeNum = int(returnCode)
    except (TypeError, ValueError):
        codeNum = None

    mapped = AU10001_ERR_MAP.get(codeNum) if codeNum is not None else None
    base = returnMsg or mapped or "토큰 발급 실패"
    if codeNum is not None and mapped and returnMsg and mapped not in str(returnMsg):
        return f"[{codeNum}] {returnMsg} ({mapped})"
    if codeNum is not None:
        return f"[{codeNum}] {base}"
    return str(base)


def parseExpiresDt(expiresDt):
    """
    명세 expires_dt(YYYYMMDDHHmmss) → datetime.
    파싱 실패 시 None.
    """
    if not expiresDt:
        return None
    raw = str(expiresDt).strip()
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def issueAccToken(hostUrl, appKey, appSecret):
    """
    au10001 호출 후 결과를 dict로 반환한다.
    성공: ok=True, token, expiresDt, tokenType, returnCode, returnMsg
    실패: ok=False, errMsg, returnCode, returnMsg
    """
    try:
        res = getAccToken(hostUrl, appKey, appSecret)
    except Exception as err:
        return {"ok": False, "errMsg": f"요청 실패: {err}"}

    # HTTP 레벨 실패도 안내
    if res.status_code != 200:
        try:
            body = res.json()
        except Exception:
            body = {}
        returnCode = body.get("return_code")
        returnMsg = body.get("return_msg") or res.text
        return {
            "ok": False,
            "errMsg": f"HTTP {res.status_code} {_fmtErrMsg(returnCode, returnMsg)}",
            "returnCode": returnCode,
            "returnMsg": returnMsg,
        }

    try:
        body = res.json()
    except Exception as err:
        return {"ok": False, "errMsg": f"응답 파싱 실패: {err}"}

    returnCode = body.get("return_code")
    returnMsg = body.get("return_msg", "")
    token = body.get("token", "") or ""
    expiresDt = body.get("expires_dt", "") or ""
    tokenType = body.get("token_type", "") or "bearer"

    # 명세: return_code 0 = 정상. 토큰도 반드시 있어야 함
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        # return_code 누락 시 토큰 유무로만 판단 (하위 호환)
        codeOk = bool(token)

    if not codeOk or not token:
        return {
            "ok": False,
            "errMsg": _fmtErrMsg(returnCode, returnMsg or str(body)),
            "returnCode": returnCode,
            "returnMsg": returnMsg,
        }

    return {
        "ok": True,
        "token": token,
        "expiresDt": expiresDt,
        "tokenType": tokenType,
        "returnCode": returnCode,
        "returnMsg": returnMsg,
    }
