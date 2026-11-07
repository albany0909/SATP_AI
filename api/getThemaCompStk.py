"""키움 테마구성종목요청 API (ka90002). 명세: tr_docs/kiwoom-rest-api-spec.json"""

from __future__ import annotations

import time

import requests

from api.getDayStkPrc import parseSignedDec, parseSignedInt
from utils.stkCdUtil import normStkCd

# 거래소구분: 1=KRX, 2=NXT, 3=통합
STEX_KRX = "1"
STEX_NXT = "2"
STEX_UNI = "3"

_LIST_KEY = "thema_comp_stk"
_MAX_CONT = 8


def getThemaCompStk(
    hostUrl,
    token,
    themaGrpCd,
    dateTp="1",
    stexTp=STEX_UNI,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka90002 테마구성종목 1회 호출.
    body: date_tp, thema_grp_cd, stex_tp
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
        "api-id": "ka90002",
    }
    data = {
        "date_tp": str(dateTp or "1").strip(),
        "thema_grp_cd": str(themaGrpCd or "").strip(),
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
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '테마구성종목 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToStkRecord(row):
    """API row -> 정규화 dict. 종목코드 없으면 None."""
    if not isinstance(row, dict):
        return None
    rawCd = str(row.get("stk_cd") or "").strip()
    if "_" in rawCd:
        rawCd = rawCd.split("_")[0]
    stkCd = normStkCd(rawCd)
    if not stkCd:
        return None
    stkNm = str(row.get("stk_nm") or "").strip() or None
    fluRt = parseSignedDec(row.get("flu_rt"))
    curPrc = parseSignedInt(row.get("cur_prc"))
    if curPrc is not None:
        curPrc = abs(int(curPrc))
    accQty = parseSignedInt(row.get("acc_trde_qty"))
    if accQty is not None:
        accQty = abs(int(accQty))
    selBid = parseSignedInt(row.get("sel_bid"))
    if selBid is not None:
        selBid = abs(int(selBid))
    buyBid = parseSignedInt(row.get("buy_bid"))
    if buyBid is not None:
        buyBid = abs(int(buyBid))
    selReq = parseSignedInt(row.get("sel_req"))
    if selReq is not None:
        selReq = abs(int(selReq))
    buyReq = parseSignedInt(row.get("buy_req"))
    if buyReq is not None:
        buyReq = abs(int(buyReq))
    return {
        "stkCd": stkCd,
        "stkNm": stkNm,
        "curPrc": curPrc,
        "fluSig": str(row.get("flu_sig") or "").strip() or None,
        "predPre": parseSignedInt(row.get("pred_pre")),
        "fluRt": fluRt,
        "accTrdeQty": accQty,
        "selBid": selBid,
        "selReq": selReq,
        "buyBid": buyBid,
        "buyReq": buyReq,
        "dtPrftRtN": parseSignedDec(row.get("dt_prft_rt_n")),
        "raw": row,
    }


def fetchThemaCompStk(
    hostUrl,
    token,
    themaGrpCd,
    dateTp="1",
    stexTp=STEX_UNI,
    tokenType="bearer",
    sleepSec=0.0,
):
    """
    ka90002 연속조회까지 읽어 테마 구성종목 전부 반환.
    성공: ok=True, rows, rowCnt, themaGrpCd, fluRt(테마), dtPrftRt(테마), body
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
            res = getThemaCompStk(
                hostUrl,
                token,
                themaGrpCd=themaGrpCd,
                dateTp=dateTp,
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
            rec = rowToStkRecord(item)
            if rec is None:
                continue
            cd = rec["stkCd"]
            if cd in seen:
                continue
            seen.add(cd)
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
        "themaGrpCd": str(themaGrpCd),
        "stexTp": str(stexTp),
        "dateTp": str(dateTp),
        "fluRt": parseSignedDec(lastBody.get("flu_rt")),
        "dtPrftRt": parseSignedDec(lastBody.get("dt_prft_rt")),
        "body": lastBody,
    }


def fetchTopStkInThema(
    hostUrl,
    token,
    themaGrpCd,
    dateTp="1",
    stexTp=STEX_UNI,
    topN=5,
    tokenType="bearer",
    isPaper=True,
    sortByDtPrft=False,
):
    """
    테마 내 종목 등락률 desc (동률이면 기간수익률/누적거래량) Top N.
    """
    waitSec = 1.05 if isPaper else 0.25
    fetched = fetchThemaCompStk(
        hostUrl,
        token,
        themaGrpCd=themaGrpCd,
        dateTp=dateTp,
        stexTp=stexTp,
        tokenType=tokenType,
        sleepSec=waitSec,
    )
    if not fetched.get("ok"):
        return fetched

    rows = list(fetched.get("rows") or [])

    def _sortKey(r):
        flu = r.get("fluRt")
        prft = r.get("dtPrftRtN")
        qty = r.get("accTrdeQty")
        fluKey = float("-inf") if flu is None else float(flu)
        prftKey = float("-inf") if prft is None else float(prft)
        qtyKey = float("-inf") if qty is None else float(qty)
        if sortByDtPrft:
            return (prftKey, fluKey, qtyKey)
        return (fluKey, prftKey, qtyKey)

    rows = sorted(rows, key=_sortKey, reverse=True)
    cap = max(1, int(topN or 5))
    top = rows[:cap]
    return {
        "ok": True,
        "rows": top,
        "rowCnt": len(top),
        "allCnt": len(fetched.get("rows") or []),
        "themaGrpCd": str(themaGrpCd),
        "stexTp": str(stexTp),
        "fluRt": fetched.get("fluRt"),
        "dtPrftRt": fetched.get("dtPrftRt"),
        "body": fetched.get("body"),
    }
