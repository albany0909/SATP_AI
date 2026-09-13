"""키움 실시간종목조회순위 API (ka00198)"""

import requests

# qry_tp: 1:1분, 2:10분, 3:1시간, 4:당일누적, 5:30초
QRY_TP_1M = "1"
QRY_TP_10M = "2"
QRY_TP_1H = "3"
QRY_TP_DAY = "4"
QRY_TP_30S = "5"

QRY_TP_LABEL = {
    QRY_TP_1M: "1분",
    QRY_TP_10M: "10분",
    QRY_TP_1H: "1시간",
    QRY_TP_DAY: "당일누적",
    QRY_TP_30S: "30초",
}

# 별칭 → qry_tp (텔레그램 인자용)
QRY_TP_ALIAS = {
    "1": QRY_TP_1M,
    "1분": QRY_TP_1M,
    "1m": QRY_TP_1M,
    "2": QRY_TP_10M,
    "10분": QRY_TP_10M,
    "10m": QRY_TP_10M,
    "3": QRY_TP_1H,
    "1시간": QRY_TP_1H,
    "1h": QRY_TP_1H,
    "4": QRY_TP_DAY,
    "당일": QRY_TP_DAY,
    "당일누적": QRY_TP_DAY,
    "day": QRY_TP_DAY,
    "5": QRY_TP_30S,
    "30초": QRY_TP_30S,
    "30s": QRY_TP_30S,
}


def normQryTp(raw, default=QRY_TP_1M):
    """사용자 입력 → qry_tp 코드. 실패 시 None."""
    if raw is None or str(raw).strip() == "":
        return default
    key = str(raw).strip().lower()
    # 한글 별칭은 lower 영향 없음 — 원문/소문자 둘 다 조회
    if key in QRY_TP_ALIAS:
        return QRY_TP_ALIAS[key]
    rawKey = str(raw).strip()
    if rawKey in QRY_TP_ALIAS:
        return QRY_TP_ALIAS[rawKey]
    return None


def getInqRank(
    hostUrl,
    token,
    qryTp=QRY_TP_1M,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka00198 실시간종목조회순위 1회 호출.
    body: qry_tp (1/2/3/4/5)
    """
    url = hostUrl + "/api/dostk/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka00198",
    }
    data = {"qry_tp": str(qryTp).strip()}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchInqRank(hostUrl, token, qryTp=QRY_TP_1M, tokenType="bearer"):
    """
    ka00198 호출 결과를 dict로 반환.
    성공: ok=True, rows=[item...], qryTp, body
    실패: ok=False, errMsg
    """
    code = str(qryTp or QRY_TP_1M).strip()
    if code not in QRY_TP_LABEL:
        return {"ok": False, "errMsg": f"지원하지 않는 구분(qry_tp): {qryTp}"}

    try:
        res = getInqRank(hostUrl, token, qryTp=code, tokenType=tokenType)
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
        codeOk = "item_inq_rank" in body

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '실시간조회순위 조회 실패'}",
            "body": body,
        }

    rows = body.get("item_inq_rank") or []
    if not isinstance(rows, list):
        rows = []

    return {
        "ok": True,
        "rows": rows,
        "rowCnt": len(rows),
        "qryTp": code,
        "qryLabel": QRY_TP_LABEL.get(code, code),
        "body": body,
        "returnMsg": returnMsg,
    }
