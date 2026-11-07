"""키움 테마그룹별요청 API (ka90001). 명세: tr_docs/kiwoom-rest-api-spec.json"""

from __future__ import annotations

import time

import requests

from api.getDayStkPrc import parseSignedDec, parseSignedInt

# 검색구분: 0=전체검색, 2=종목검색
QRY_TP_ALL = "0"
QRY_TP_STK = "2"

# 등락수익구분: 1=상위기간수익률, 2=하위기간수익률, 3=상위등락률, 4=하위등락률
FLU_PL_AMT_TP_TOP_PRFT = "1"
FLU_PL_AMT_TP_BOT_PRFT = "2"
FLU_PL_AMT_TP_TOP_FLU = "3"
FLU_PL_AMT_TP_BOT_FLU = "4"

# 거래소구분: 1=KRX, 2=NXT, 3=통합
STEX_KRX = "1"
STEX_NXT = "2"
STEX_UNI = "3"

_LIST_KEY = "thema_grp"
_MAX_CONT = 8


def getThemaGrp(
    hostUrl,
    token,
    qryTp=QRY_TP_ALL,
    stkCd="",
    dateTp="1",
    themaNm="",
    fluPlAmtTp=FLU_PL_AMT_TP_TOP_FLU,
    stexTp=STEX_UNI,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka90001 테마그룹별 1회 호출.
    body: qry_tp, stk_cd, date_tp, thema_nm, flu_pl_amt_tp, stex_tp
    """
    url = hostUrl + "/api/dostk/thme"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka90001",
    }
    data = {
        "qry_tp": str(qryTp or QRY_TP_ALL).strip(),
        "stk_cd": str(stkCd or "").strip(),
        "date_tp": str(dateTp or "1").strip(),
        "thema_nm": str(themaNm or "").strip(),
        "flu_pl_amt_tp": str(fluPlAmtTp or FLU_PL_AMT_TP_TOP_FLU).strip(),
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
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '테마그룹별 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToThemaRecord(row):
    """API row -> 정규화 dict. 테마코드 없으면 None."""
    if not isinstance(row, dict):
        return None
    themaCd = str(row.get("thema_grp_cd") or "").strip()
    if not themaCd:
        return None
    themaNm = str(row.get("thema_nm") or "").strip() or None
    stkNum = parseSignedInt(row.get("stk_num"))
    if stkNum is not None:
        stkNum = abs(int(stkNum))
    rising = parseSignedInt(row.get("rising_stk_num"))
    if rising is not None:
        rising = abs(int(rising))
    fall = parseSignedInt(row.get("fall_stk_num"))
    if fall is not None:
        fall = abs(int(fall))
    return {
        "themaCd": themaCd,
        "themaNm": themaNm,
        "stkNum": stkNum,
        "fluSig": str(row.get("flu_sig") or "").strip() or None,
        "fluRt": parseSignedDec(row.get("flu_rt")),
        "risingStkNum": rising,
        "fallStkNum": fall,
        "dtPrftRt": parseSignedDec(row.get("dt_prft_rt")),
        "mainStk": str(row.get("main_stk") or "").strip() or None,
        "raw": row,
    }


def fetchThemaGrp(
    hostUrl,
    token,
    qryTp=QRY_TP_ALL,
    stkCd="",
    dateTp="1",
    themaNm="",
    fluPlAmtTp=FLU_PL_AMT_TP_TOP_FLU,
    stexTp=STEX_UNI,
    tokenType="bearer",
    sleepSec=0.0,
):
    """
    ka90001 연속조회까지 읽어 테마 목록 전부 반환.
    성공: ok=True, rows, rowCnt, body
    """
    rows = []
    seen = set()
    contYn = "N"
    nextKey = ""
    lastBody = {}

    for page in range(_MAX_CONT):
        if page > 0 and sleepSec and sleepSec > 0:
            time.sleep(float(sleepSec))
        try:
            res = getThemaGrp(
                hostUrl,
                token,
                qryTp=qryTp,
                stkCd=stkCd,
                dateTp=dateTp,
                themaNm=themaNm,
                fluPlAmtTp=fluPlAmtTp,
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
        for item in chunk:
            rec = rowToThemaRecord(item)
            if rec is None:
                continue
            code = rec["themaCd"]
            if code in seen:
                continue
            seen.add(code)
            rows.append(rec)

        more = _hdr(res, "cont-yn").upper() == "Y"
        nextKey = _hdr(res, "next-key")
        if not more or not nextKey:
            break
        contYn = "Y"

    return {
        "ok": True,
        "rows": rows,
        "rowCnt": len(rows),
        "stexTp": str(stexTp),
        "fluPlAmtTp": str(fluPlAmtTp),
        "dateTp": str(dateTp),
        "body": lastBody,
    }


def fetchTopThemaByFluRt(
    hostUrl,
    token,
    topN=5,
    dateTp="1",
    stexTp=STEX_UNI,
    fluPlAmtTp=FLU_PL_AMT_TP_TOP_FLU,
    tokenType="bearer",
    isPaper=True,
    sortByDtPrft=False,
):
    """
    테마 등락률(또는 기간수익률) 내림차순 Top N.
    기본: flu_pl_amt_tp=3(상위등락률) 요청 후 fluRt desc 재정렬.
    sortByDtPrft=True 이면 dtPrftRt 우선.
    """
    waitSec = 1.05 if isPaper else 0.25
    fetched = fetchThemaGrp(
        hostUrl,
        token,
        qryTp=QRY_TP_ALL,
        dateTp=dateTp,
        fluPlAmtTp=fluPlAmtTp,
        stexTp=stexTp,
        tokenType=tokenType,
        sleepSec=waitSec,
    )
    if not fetched.get("ok"):
        return fetched

    rows = list(fetched.get("rows") or [])

    def _sortKey(r):
        flu = r.get("fluRt")
        prft = r.get("dtPrftRt")
        fluKey = float("-inf") if flu is None else float(flu)
        prftKey = float("-inf") if prft is None else float(prft)
        if sortByDtPrft:
            return (prftKey, fluKey)
        return (fluKey, prftKey)

    rows = sorted(rows, key=_sortKey, reverse=True)
    cap = max(1, int(topN or 5))
    top = rows[:cap]
    return {
        "ok": True,
        "rows": top,
        "rowCnt": len(top),
        "allCnt": len(fetched.get("rows") or []),
        "stexTp": str(stexTp),
        "fluPlAmtTp": str(fluPlAmtTp),
        "dateTp": str(dateTp),
        "body": fetched.get("body"),
    }
