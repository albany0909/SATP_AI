"""키움 주식 매도주문 API (kt10001)"""

import requests

from utils.krxTick import limitOrdPx
from utils.stkCdUtil import normStkCd


def _tickOrdUv(ordUv, stkCd=""):
    """
    지정가 단가를 유효 KRX 호가로 맞춘다 (매도: 올림).
    빈 값(시장가 등)·0 이하·숫자 아님은 그대로 둔다.
    """
    if ordUv in (None, ""):
        return ""
    try:
        raw = float(str(ordUv).replace(",", ""))
    except (TypeError, ValueError):
        return str(ordUv)
    if raw <= 0:
        return str(ordUv)
    px = limitOrdPx(raw, "SELL")
    if px != raw:
        try:
            from utils.termLog import termLog

            termLog(f"[주문호가] 매도 {normStkCd(stkCd)} 단가 {raw:,.2f} → {px:,} (호가단위)")
        except Exception:
            pass
    return str(px)


def sellStk(
    hostUrl,
    token,
    dmstStexTp,
    stkCd,
    ordQty,
    trdeTp,
    ordUv="",
    condUv="",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    kt10001 주식 매도주문 호출 후 Response 반환.
    body: dmst_stex_tp, stk_cd, ord_qty, ord_uv, trde_tp, cond_uv
    """
    url = hostUrl + "/api/dostk/ordr"
    # 토큰타입은 보통 bearer — 앞에 Bearer 붙여 authorization 구성
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "kt10001",
    }
    data = {
        "dmst_stex_tp": dmstStexTp,
        "stk_cd": normStkCd(stkCd),
        "ord_qty": str(ordQty),
        "ord_uv": _tickOrdUv(ordUv, stkCd),
        "trde_tp": str(trdeTp),
        "cond_uv": str(condUv) if condUv not in (None, "") else "",
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def placeSellOrd(
    hostUrl,
    token,
    dmstStexTp,
    stkCd,
    ordQty,
    trdeTp,
    ordUv="",
    condUv="",
    tokenType="bearer",
):
    """
    kt10001 호출 결과를 dict로 반환한다.
    성공: ok=True, body=응답본문, ordNo
    실패: ok=False, errMsg=사유
    """
    try:
        res = sellStk(
            hostUrl,
            token,
            dmstStexTp,
            stkCd,
            ordQty,
            trdeTp,
            ordUv=ordUv,
            condUv=condUv,
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

    # 명세: return_code 0 = 정상. 주문번호도 확인
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        # return_code 누락 시 주문번호 유무로 판단 (하위 호환)
        codeOk = bool(ordNo)

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '매도 주문 실패'}",
            "body": body,
        }

    return {
        "ok": True,
        "body": body,
        "ordNo": ordNo,
        "returnMsg": returnMsg,
    }
