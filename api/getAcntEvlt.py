"""키움 계좌평가현황요청 API (kt00004)"""

import requests


def getAcntEvlt(
    hostUrl,
    token,
    qryTp="1",
    dmstStexTp="KRX",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    kt00004 계좌평가현황요청 호출 후 Response 반환.
    body: qry_tp(0전체/1상장폐지제외), dmst_stex_tp(KRX/NXT)
    """
    url = hostUrl + "/api/dostk/acnt"
    # 토큰타입은 보통 bearer — 앞에 Bearer 붙여 authorization 구성
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "kt00004",
    }
    data = {
        "qry_tp": str(qryTp),
        "dmst_stex_tp": dmstStexTp,
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchAcntEvlt(hostUrl, token, qryTp="1", dmstStexTp="KRX", tokenType="bearer"):
    """
    kt00004 호출 결과를 dict로 반환한다.
    성공: ok=True, body=응답본문
    실패: ok=False, errMsg=사유
    """
    try:
        res = getAcntEvlt(
            hostUrl, token, qryTp=qryTp, dmstStexTp=dmstStexTp, tokenType=tokenType
        )
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
        # return_code 누락 시 예수금/평가액 필드로 판단
        codeOk = "entr" in body or "aset_evlt_amt" in body

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '계좌평가 조회 실패'}",
            "body": body,
        }

    return {"ok": True, "body": body, "returnMsg": returnMsg}


def parsePadNum(raw):
    """
    명세의 좌측 0-padding·부호 포함 숫자 문자열을 int로 변환한다.
    예: '000000017534' → 17534, '-00000163958' → -163958
    파싱 실패 시 None.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "":
        return None
    # 부호만 남기고 선행 0 제거 — 전부 0이면 0
    sign = 1
    if text.startswith("-"):
        sign = -1
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    # 원 단위라 소수점은 버린다 (API가 1234.00 으로 줄 때)
    if "." in text:
        text = text.split(".", 1)[0]
    text = text.lstrip("0")
    if text == "":
        return 0
    try:
        return sign * int(text)
    except ValueError:
        return None


def fmtAmt(raw, empty="-"):
    """금액/수량을 천단위 콤마 문자열로. 파싱 불가 시 empty."""
    num = parsePadNum(raw)
    if num is None:
        text = str(raw).strip() if raw is not None else ""
        return text if text else empty
    return f"{num:,}"


def fmtPct(raw, empty="-"):
    """손익율 등 퍼센트 문자열 정리. 이미 % 가 있으면 중복하지 않는다."""
    if raw is None:
        return empty
    text = str(raw).strip()
    if not text:
        return empty
    return text[:-1] if text.endswith("%") else text


def parsePctNum(raw):
    """손익율 문자열을 float% 로. 예: '-1.3000', '+0.12%' → -1.3 / 0.12."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if not text or text in ("-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def heroHoldPnl(curPx, qty, purAmt, avgPrc=0, apiPnl=None, apiRt=None, apiCurPx=None):
    """
    영웅문 계좌평가와 같은 평가손익·수익률.
    평가손익 = 현재가×수량 − 매입금액(수수료 포함)
    수익률 = 평가손익 / 매입금액 × 100
    매입금액이 없으면 평단×수량으로 폴백.

    0B 현재가가 kt00004 cur_prc 와 같으면(1원 이내) API pl_amt/pl_rt 를
    그대로 써서 영웅문 표시와 반올림까지 맞춘다.
    """
    try:
        live = int(curPx or 0)
    except (TypeError, ValueError):
        live = 0
    try:
        q = int(qty or 0)
    except (TypeError, ValueError):
        q = 0
    try:
        pur = int(purAmt or 0)
    except (TypeError, ValueError):
        pur = 0
    try:
        avg = int(avgPrc or 0)
    except (TypeError, ValueError):
        avg = 0
    try:
        apiCur = int(apiCurPx or 0)
    except (TypeError, ValueError):
        apiCur = 0

    def _apiPair():
        pnl = apiPnl
        rt = apiRt
        try:
            if pnl is not None:
                pnl = int(pnl)
        except (TypeError, ValueError):
            pnl = None
        try:
            if rt is not None:
                rt = float(rt)
        except (TypeError, ValueError):
            rt = None
        return pnl, rt

    # 계좌평가 스냅샷과 동일 시세면 키움 수치 우선
    if live > 0 and apiCur > 0 and abs(live - apiCur) <= 1:
        apiPnlNum, apiRtNum = _apiPair()
        if apiPnlNum is not None or apiRtNum is not None:
            return apiPnlNum, apiRtNum

    if live > 0 and q > 0 and pur > 0:
        pnl = live * q - pur
        return pnl, pnl / float(pur) * 100.0
    if live > 0 and q > 0 and avg > 0:
        cost = avg * q
        pnl = live * q - cost
        return pnl, pnl / float(cost) * 100.0
    return _apiPair()


def fmtPnl(raw, empty="-"):
    """손익 금액. 부호+천단위."""
    num = parsePadNum(raw)
    if num is None:
        return empty
    return f"{num:+,}"
