"""키움 전업종지수요청 API (ka20003). 명세: tr_docs/kiwoom-rest-api-spec.json"""

from __future__ import annotations

import time

import requests

from api.getAcntEvlt import parsePadNum
from api.getDayStkPrc import parseSignedDec, parseSignedInt

# 요청 업종코드(시장 루트) — 해당 시장 하위 전업종 반환
INDS_CD_KOSPI = "001"
INDS_CD_KOSDAQ = "101"

_LIST_KEY = "all_inds_idex"
_MAX_CONT = 8

# 드릴다운 시 제외할 종합/규모/지수 코드
AGG_INDS_CDS = frozenset(
    {
        "001",  # 종합(KOSPI)
        "002",  # 대형주
        "003",  # 중형주
        "004",  # 소형주
        "101",  # 종합(KOSDAQ)
        "201",  # KOSPI200
        "301",
        "302",  # KOSTAR
        "701",  # KRX100
    }
)

# ka20002 mrkt_tp 매핑
MRKT_TP_KOSPI = "0"
MRKT_TP_KOSDAQ = "1"
MRKT_TP_KOSPI200 = "2"


def getAllIndsIdex(
    hostUrl,
    token,
    indsCd=INDS_CD_KOSPI,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka20003 전업종지수 1회 호출.
    body: inds_cd (001=KOSPI종합, 101=KOSDAQ종합)
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
        "api-id": "ka20003",
    }
    data = {"inds_cd": str(indsCd or INDS_CD_KOSPI).strip()}
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
            "errMsg": f"[{returnCode}] {body.get('return_msg') or '전업종지수 조회 실패'}",
            "body": body,
        }
    return True, body


def _parseAmt(raw):
    """거래대금(백만원) 등 -> float. 실패 시 None."""
    num = parsePadNum(raw)
    if num is None:
        dec = parseSignedDec(raw)
        if dec is None:
            return None
        return abs(float(dec))
    return float(abs(int(num)))


def rowToIndsRecord(row, mrktTp=None, srcRoot=None):
    """API row -> 정규화 dict. stk_cd=업종코드, stk_nm=업종명."""
    if not isinstance(row, dict):
        return None
    indsCd = str(row.get("stk_cd") or "").strip()
    if not indsCd:
        return None
    indsNm = str(row.get("stk_nm") or "").strip() or None
    fluRt = parseSignedDec(row.get("flu_rt"))
    curPrc = parseSignedDec(row.get("cur_prc"))
    if curPrc is not None:
        curPrc = abs(curPrc)
    trdeQty = parseSignedInt(row.get("trde_qty"))
    if trdeQty is not None:
        trdeQty = abs(int(trdeQty))
    trdePrica = _parseAmt(row.get("trde_prica"))
    return {
        "indsCd": indsCd,
        "indsNm": indsNm,
        "curPrc": curPrc,
        "fluRt": fluRt,
        "predPre": parseSignedDec(row.get("pred_pre")),
        "preSig": str(row.get("pre_sig") or "").strip() or None,
        "trdeQty": trdeQty,
        "trdePrica": trdePrica,
        "wght": parseSignedDec(row.get("wght")),
        "mrktTp": mrktTp,
        "srcRoot": srcRoot,
        "raw": row,
    }


def fetchAllIndsIdex(
    hostUrl,
    token,
    indsCd=INDS_CD_KOSPI,
    tokenType="bearer",
    sleepSec=0.0,
):
    """
    ka20003 연속조회까지 읽어 전업종 목록 반환.
    성공: ok=True, rows, rowCnt, indsCd, mrktTp, body
    """
    rootCd = str(indsCd or INDS_CD_KOSPI).strip()
    mrktTp = MRKT_TP_KOSDAQ if rootCd == INDS_CD_KOSDAQ else MRKT_TP_KOSPI

    rows = []
    seen = set()
    contYn = "N"
    nextKey = ""
    lastBody = {}

    for page in range(_MAX_CONT):
        if page > 0 and sleepSec and sleepSec > 0:
            time.sleep(float(sleepSec))
        try:
            res = getAllIndsIdex(
                hostUrl,
                token,
                indsCd=rootCd,
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
            rec = rowToIndsRecord(item, mrktTp=mrktTp, srcRoot=rootCd)
            if rec is None:
                continue
            code = rec["indsCd"]
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
        "indsCd": rootCd,
        "mrktTp": mrktTp,
        "body": lastBody,
    }


def fetchTopIndsByFluRt(
    hostUrl,
    token,
    roots=None,
    topN=5,
    excludeAgg=True,
    tokenType="bearer",
    isPaper=True,
    sortByTrdePrica=False,
):
    """
    KOSPI/KOSDAQ 전업종을 모아 등락률(또는 거래대금) 상위 Top N.
    roots 기본: (001, 101)
    sortByTrdePrica=True 이면 거래대금 우선, 아니면 등락률 desc -> 거래대금 desc.
    """
    rootList = list(roots) if roots else [INDS_CD_KOSPI, INDS_CD_KOSDAQ]
    waitSec = 1.05 if isPaper else 0.25
    merged = []
    errors = []

    for i, rootCd in enumerate(rootList):
        if i > 0:
            time.sleep(waitSec)
        fetched = fetchAllIndsIdex(
            hostUrl,
            token,
            indsCd=rootCd,
            tokenType=tokenType,
            sleepSec=waitSec,
        )
        if not fetched.get("ok"):
            errors.append(f"{rootCd}: {fetched.get('errMsg')}")
            continue
        merged.extend(fetched.get("rows") or [])

    if not merged and errors:
        return {"ok": False, "errMsg": "; ".join(errors), "rows": []}

    rows = merged
    if excludeAgg:
        rows = [r for r in rows if r.get("indsCd") not in AGG_INDS_CDS]

    def _sortKey(r):
        flu = r.get("fluRt")
        amt = r.get("trdePrica")
        fluKey = float("-inf") if flu is None else float(flu)
        amtKey = float("-inf") if amt is None else float(amt)
        if sortByTrdePrica:
            return (amtKey, fluKey)
        return (fluKey, amtKey)

    rows = sorted(rows, key=_sortKey, reverse=True)
    cap = max(1, int(topN or 5))
    top = rows[:cap]
    return {
        "ok": True,
        "rows": top,
        "rowCnt": len(top),
        "allCnt": len(merged),
        "filteredCnt": len(rows),
        "errors": errors,
    }
