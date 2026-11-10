"""키움 주식 주봉(ka10082)·월봉(ka10083) 차트."""

import time

import requests

from api.getAcntEvlt import parsePadNum
from api.getDayChart import MAX_CONT_PAGES, _hdr, _parseBody
from utils.stkCdUtil import normStkCd

_SPEC = {
    "week": ("ka10082", "stk_stk_pole_chart_qry"),
    "month": ("ka10083", "stk_mth_pole_chart_qry"),
}


def getPoleChart(
    hostUrl,
    token,
    stkCd,
    baseDt,
    barTp,
    updStkpcTp="1",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """주봉 또는 월봉 1페이지. upd_stkpc_tp 기본 1(수정주가)."""
    apiId, _listKey = _SPEC[str(barTp)]
    url = hostUrl + "/api/dostk/chart"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": apiId,
    }
    data = {
        "stk_cd": normStkCd(stkCd),
        "base_dt": str(baseDt).strip(),
        "upd_stkpc_tp": str(updStkpcTp),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _barFromRow(row):
    if not isinstance(row, dict):
        return None
    dt = str(row.get("dt") or "").strip()[:8]
    close = parsePadNum(row.get("cur_prc"))
    if len(dt) != 8 or close is None:
        return None
    def _px(key, fallback=0):
        num = parsePadNum(row.get(key))
        if num is None:
            return abs(int(fallback))
        return abs(int(num))

    qty = parsePadNum(row.get("trde_qty"))
    return {
        "tradeDt": dt,
        "open": _px("open_pric", close),
        "high": _px("high_pric", close),
        "low": _px("low_pric", close),
        "close": abs(int(close)),
        "qty": abs(int(qty or 0)),
    }


def fetchPoleBars(
    hostUrl,
    token,
    stkCd,
    baseDt,
    barTp,
    updStkpcTp="1",
    tokenType="bearer",
    isPaper=True,
    minBars=60,
):
    """연속조회로 주봉 또는 월봉을 모은다. 최신→과거 응답을 오래된 순으로 돌린다."""
    apiId, listKey = _SPEC[str(barTp)]
    waitSec = 1.05 if isPaper else 0.25
    allBars = []
    seen = set()
    contYn = "N"
    nextKey = ""
    for pageIdx in range(MAX_CONT_PAGES):
        if pageIdx > 0:
            time.sleep(waitSec)
        try:
            res = getPoleChart(
                hostUrl,
                token,
                stkCd,
                baseDt,
                barTp,
                updStkpcTp=updStkpcTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}", "apiId": apiId, "bars": []}
        ok, parsed = _parseBody(res)
        if not ok:
            return {
                "ok": False,
                "errMsg": parsed.get("errMsg"),
                "apiId": apiId,
                "bars": [],
            }
        rows = parsed.get(listKey) or []
        if isinstance(rows, list):
            for row in rows:
                bar = _barFromRow(row)
                if not bar or bar["tradeDt"] in seen:
                    continue
                seen.add(bar["tradeDt"])
                allBars.append(bar)
        if len(allBars) >= int(minBars or 0):
            break
        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKey = _hdr(res, "next-key", "")
        if contYnHdr != "Y" or not nextKey:
            break
        contYn = "Y"
    allBars.sort(key=lambda item: item["tradeDt"])
    if not allBars:
        return {"ok": False, "errMsg": "봉 데이터가 비어 있습니다.", "apiId": apiId, "bars": []}
    return {"ok": True, "apiId": apiId, "bars": allBars, "barTp": barTp}
