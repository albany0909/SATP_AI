"""키움 미국주식 실시간 종목 조회 순위 API (usa01980)"""

import requests

# svc_type → 내부 구분코드 (국내 rank와 UX 통일)
# B286:30초, B281:1분, B282:10분, B283:1시간, B284:당일
SVC_30S = "B286"
SVC_1M = "B281"
SVC_10M = "B282"
SVC_1H = "B283"
SVC_DAY = "B284"

QRY_TP_1M = "1"
QRY_TP_10M = "2"
QRY_TP_1H = "3"
QRY_TP_DAY = "4"
QRY_TP_30S = "5"

QRY_TP_TO_SVC = {
    QRY_TP_1M: SVC_1M,
    QRY_TP_10M: SVC_10M,
    QRY_TP_1H: SVC_1H,
    QRY_TP_DAY: SVC_DAY,
    QRY_TP_30S: SVC_30S,
}

SVC_TO_QRY_TP = {v: k for k, v in QRY_TP_TO_SVC.items()}

QRY_TP_LABEL = {
    QRY_TP_1M: "1분",
    QRY_TP_10M: "10분",
    QRY_TP_1H: "1시간",
    QRY_TP_DAY: "당일누적",
    QRY_TP_30S: "30초",
}

QRY_TP_ALIAS = {
    "1": QRY_TP_1M,
    "1분": QRY_TP_1M,
    "1m": QRY_TP_1M,
    "b281": QRY_TP_1M,
    "2": QRY_TP_10M,
    "10분": QRY_TP_10M,
    "10m": QRY_TP_10M,
    "b282": QRY_TP_10M,
    "3": QRY_TP_1H,
    "1시간": QRY_TP_1H,
    "1h": QRY_TP_1H,
    "b283": QRY_TP_1H,
    "4": QRY_TP_DAY,
    "당일": QRY_TP_DAY,
    "당일누적": QRY_TP_DAY,
    "day": QRY_TP_DAY,
    "b284": QRY_TP_DAY,
    "5": QRY_TP_30S,
    "30초": QRY_TP_30S,
    "30s": QRY_TP_30S,
    "b286": QRY_TP_30S,
}

STEX_LABEL = {
    "ND": "NASDAQ",
    "NY": "NYSE",
    "NA": "AMEX",
}


def normUsQryTp(raw, default=QRY_TP_1M):
    """사용자 입력 → qry_tp(1~5). 실패 시 None."""
    if raw is None or str(raw).strip() == "":
        return default
    key = str(raw).strip().lower()
    if key in QRY_TP_ALIAS:
        return QRY_TP_ALIAS[key]
    rawKey = str(raw).strip()
    if rawKey in QRY_TP_ALIAS:
        return QRY_TP_ALIAS[rawKey]
    # 이미 svc_type인 경우
    up = rawKey.upper()
    if up in SVC_TO_QRY_TP:
        return SVC_TO_QRY_TP[up]
    return None


def normUsStkCd(raw):
    """미국 티커 정규화 — 대문자·공백제거."""
    text = str(raw or "").strip().upper()
    return text if text else ""


def getUsInqRank(
    hostUrl,
    token,
    svcType=SVC_1M,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """usa01980 1회 호출. body: svc_type"""
    url = hostUrl + "/api/us/rkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "usa01980",
    }
    data = {"svc_type": str(svcType).strip().upper()}
    return requests.post(url, headers=headers, json=data, timeout=30)


def fetchUsInqRank(hostUrl, token, qryTp=QRY_TP_1M, tokenType="bearer"):
    """
    usa01980 결과 dict.
    성공: ok, rows, qryTp, svcType, baseDate, baseTime
    """
    code = str(qryTp or QRY_TP_1M).strip()
    svc = QRY_TP_TO_SVC.get(code)
    if not svc:
        return {"ok": False, "errMsg": f"지원하지 않는 구분(qry_tp): {qryTp}"}

    try:
        res = getUsInqRank(hostUrl, token, svcType=svc, tokenType=tokenType)
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
        codeOk = "result_list" in body

    if not codeOk:
        return {
            "ok": False,
            "errMsg": f"[{returnCode}] {returnMsg or '미국순위 조회 실패'}",
            "body": body,
        }

    rows = body.get("result_list") or []
    if not isinstance(rows, list):
        rows = []

    return {
        "ok": True,
        "rows": rows,
        "rowCnt": len(rows),
        "qryTp": code,
        "svcType": svc,
        "qryLabel": QRY_TP_LABEL.get(code, code),
        "baseDate": str(body.get("base_date") or "").strip(),
        "baseTime": str(body.get("base_time") or "").strip(),
        "body": body,
        "returnMsg": returnMsg,
    }
