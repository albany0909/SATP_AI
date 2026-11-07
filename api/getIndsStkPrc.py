"""키움 업종별주가요청 API (ka20002). 명세: tr_docs/kiwoom-rest-api-spec.json"""

from __future__ import annotations

import time

import requests

from api.getDayStkPrc import parseSignedDec, parseSignedInt
from utils.stkCdUtil import normStkCd

# 시장구분: 0=코스피, 1=코스닥, 2=코스피200
MRKT_TP_KOSPI = "0"
MRKT_TP_KOSDAQ = "1"
MRKT_TP_KOSPI200 = "2"

# 거래소구분: 1=KRX, 2=NXT, 3=통합
STEX_KRX = "1"
STEX_NXT = "2"
STEX_UNI = "3"

_LIST_KEY = "inds_stkpc"
_MAX_CONT = 8


def getIndsStkPrc(
    hostUrl,
    token,
    mrktTp=MRKT_TP_KOSPI,
    indsCd="001",
    stexTp=STEX_UNI,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka20002 업종별주가 1회 호출.
    body: mrkt_tp, inds_cd, stex_tp
    """
    url = hostUrl + "/api/dostk/sect"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka20002",
    }
    data = {
        "mrkt_tp": str(mrktTp or MRKT_TP_KOSPI).strip(),
        "inds_cd": str(indsCd or "001").strip(),
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
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '업종별주가 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToStkRecord(row):
    """API row -> 정규화 dict. 실패 시 None."""
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
    nowQty = parseSignedInt(row.get("now_trde_qty"))
    if nowQty is not None:
        nowQty = abs(int(nowQty))
    # 명세에 거래대금 필드 없음 — 있으면 보조 정렬용
    trdePrica = parseSignedDec(row.get("trde_prica"))
    if trdePrica is not None:
        trdePrica = abs(float(trdePrica))
    return {
        "stkCd": stkCd,
        "stkNm": stkNm,
        "curPrc": curPrc,
        "fluRt": fluRt,
        "predPre": parseSignedInt(row.get("pred_pre")),
        "predPreSig": str(row.get("pred_pre_sig") or "").strip() or None,
        "nowTrdeQty": nowQty,
        "trdePrica": trdePrica,
        "raw": row,
    }


def fetchIndsStkPrc(
    hostUrl,
    token,
    mrktTp=MRKT_TP_KOSPI,
    indsCd="001",
    stexTp=STEX_UNI,
    tokenType="bearer",
    sleepSec=0.0,
):
    """
    ka20002 연속조회까지 읽어 업종 내 종목 목록 반환.
    성공: ok=True, rows, rowCnt, indsCd, mrktTp, body
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
            res = getIndsStkPrc(
                hostUrl,
                token,
                mrktTp=mrktTp,
                indsCd=indsCd,
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
        "indsCd": str(indsCd),
        "mrktTp": str(mrktTp),
        "stexTp": str(stexTp),
        "body": lastBody,
    }


def fetchTopStkInInds(
    hostUrl,
    token,
    indsCd,
    mrktTp=MRKT_TP_KOSPI,
    stexTp=STEX_UNI,
    topN=5,
    tokenType="bearer",
    isPaper=True,
    sortByTrdePrica=False,
):
    """
    업종 내 종목을 등락률 desc (동률이면 거래대금/거래량) Top N.
    ka20002 응답에 trde_prica 가 없으면 now_trde_qty 를 2차 키로 사용.
    """
    waitSec = 1.05 if isPaper else 0.25
    fetched = fetchIndsStkPrc(
        hostUrl,
        token,
        mrktTp=mrktTp,
        indsCd=indsCd,
        stexTp=stexTp,
        tokenType=tokenType,
        sleepSec=waitSec,
    )
    if not fetched.get("ok"):
        return fetched

    rows = list(fetched.get("rows") or [])

    def _sortKey(r):
        flu = r.get("fluRt")
        amt = r.get("trdePrica")
        qty = r.get("nowTrdeQty")
        fluKey = float("-inf") if flu is None else float(flu)
        amtKey = float("-inf") if amt is None else float(amt)
        qtyKey = float("-inf") if qty is None else float(qty)
        if sortByTrdePrica:
            return (amtKey, qtyKey, fluKey)
        return (fluKey, amtKey, qtyKey)

    rows = sorted(rows, key=_sortKey, reverse=True)
    cap = max(1, int(topN or 5))
    top = rows[:cap]
    return {
        "ok": True,
        "rows": top,
        "rowCnt": len(top),
        "allCnt": len(fetched.get("rows") or []),
        "indsCd": str(indsCd),
        "mrktTp": str(mrktTp),
        "body": fetched.get("body"),
    }
