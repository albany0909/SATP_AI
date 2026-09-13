"""키움 거래대금상위요청 API (ka10032). 명세: tr_docs/ka10032.json"""

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

# 시장구분
MRKT_ALL = "000"
MRKT_KOSPI = "001"
MRKT_KOSDAQ = "101"

# 관리종목포함 0:미포함, 1:포함
MANG_EXCL = "0"
MANG_INCL = "1"

# 거래소구분 1:KRX, 2:NXT, 3:통합
STEX_KRX = "1"
STEX_NXT = "2"
STEX_UNI = "3"

_LIST_KEY = "trde_prica_upper"
_MAX_CONT = 8


def getTrdePrcaUpper(
    hostUrl,
    token,
    mrktTp=MRKT_KOSPI,
    mangStkIncls=MANG_INCL,
    stexTp=STEX_UNI,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10032 1회 호출.
    body: mrkt_tp, mang_stk_incls, stex_tp
    """
    url = hostUrl + "/api/dostk/rkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10032",
    }
    data = {
        "mrkt_tp": str(mrktTp or MRKT_KOSPI).strip(),
        "mang_stk_incls": str(mangStkIncls or MANG_INCL).strip(),
        "stex_tp": str(stexTp or STEX_UNI).strip(),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def _parseBody(res):
    try:
        body = res.json()
    except Exception as err:
        return False, {"errMsg": f"응답 파싱 실패: {err}"}
    if res.status_code != 200:
        returnCode = body.get("return_code")
        returnMsg = body.get("return_msg") or res.text
        return False, {
            "errMsg": f"HTTP {res.status_code} [{returnCode}] {returnMsg}",
            "body": body,
        }
    returnCode = body.get("return_code")
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        codeOk = _LIST_KEY in body
    if not codeOk:
        return False, {
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '거래대금상위 조회 실패'}",
            "body": body,
        }
    return True, body


def _rowRank(row):
    num = parsePadNum((row or {}).get("now_rank"))
    if num is None:
        return None
    return abs(int(num))


def fetchTrdePrcaUpper(
    hostUrl,
    token,
    mrktTp=MRKT_KOSPI,
    mangStkIncls=MANG_INCL,
    stexTp=STEX_UNI,
    maxRank=30,
    tokenType="bearer",
):
    """
    ka10032 연속조회까지 읽어 now_rank <= maxRank 종목만 반환.
    성공: ok=True, rows, cds(set)
    """
    cap = max(1, int(maxRank or 30))
    rows = []
    cds = set()
    contYn = "N"
    nextKey = ""
    lastBody = {}
    for _page in range(_MAX_CONT):
        try:
            res = getTrdePrcaUpper(
                hostUrl,
                token,
                mrktTp=mrktTp,
                mangStkIncls=mangStkIncls,
                stexTp=stexTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}"}
        ok, parsed = _parseBody(res)
        if not ok:
            return {"ok": False, **parsed}
        lastBody = parsed
        chunk = parsed.get(_LIST_KEY) or []
        if not isinstance(chunk, list):
            chunk = []
        stop = False
        for item in chunk:
            if not isinstance(item, dict):
                continue
            rankNo = _rowRank(item)
            cd = normStkCd(item.get("stk_cd"))
            if not cd or rankNo is None or rankNo <= 0:
                continue
            if rankNo > cap:
                stop = True
                continue
            if cd in cds:
                continue
            cds.add(cd)
            rows.append(item)
        more = _hdr(res, "cont-yn").upper() == "Y"
        nextKey = _hdr(res, "next-key")
        if stop or not more or not nextKey:
            break
        contYn = "Y"
    return {
        "ok": True,
        "rows": rows,
        "cds": cds,
        "rowCnt": len(rows),
        "mrktTp": str(mrktTp),
        "body": lastBody,
    }
