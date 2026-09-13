"""키움 주식 취소주문 API (kt10003)"""

import requests

from utils.stkCdUtil import normStkCd


def cnclStk(
    hostUrl,
    token,
    dmstStexTp,
    origOrdNo,
    stkCd,
    cnclQty,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    kt10003 주식 취소주문 호출 후 Response 반환.
    body: dmst_stex_tp, orig_ord_no, stk_cd, cncl_qty
    orig_ord_no 는 매수/매도 응답 ord_no(7자리).
    cncl_qty '0' 이면 잔량 전부 취소.
    """
    url = hostUrl + "/api/dostk/ordr"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    # 키움 원주문번호는 7자리. 응답 값을 그대로 맞춘다.
    orig = str(origOrdNo or "").strip()
    if orig.isdigit():
        orig = orig.zfill(7)[-7:]

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "kt10003",
    }
    data = {
        "dmst_stex_tp": dmstStexTp,
        "orig_ord_no": orig,
        "stk_cd": normStkCd(stkCd),
        "cncl_qty": str(cnclQty) if cnclQty not in (None, "") else "0",
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def placeCnclOrd(
    hostUrl,
    token,
    dmstStexTp,
    origOrdNo,
    stkCd,
    cnclQty="0",
    tokenType="bearer",
):
    """
    kt10003 호출 결과를 dict로 반환한다.
    성공: ok=True, body=응답본문, ordNo
    실패: ok=False, errMsg=사유
    """
    try:
        res = cnclStk(
            hostUrl,
            token,
            dmstStexTp,
            origOrdNo,
            stkCd,
            cnclQty,
            tokenType=tokenType,
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
    ordNo = body.get("ord_no", "") or ""

    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        codeOk = bool(ordNo)

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '취소 주문 실패'}",
            "body": body,
        }

    return {
        "ok": True,
        "body": body,
        "ordNo": ordNo,
        "returnMsg": returnMsg,
    }
