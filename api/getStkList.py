"""키움 종목정보 리스트 API (ka10099)"""

import time

import requests

from utils.stkCdUtil import normStkCd

SRC_TR = "ka10099"
MAX_CONT_PAGES = 50

# 기본 적재 시장: 코스피·코스닥 (필요 시 ETF 등 확장)
DEFAULT_MRKT_TPS = ("0", "10")
MRKT_TP_LABEL = {
    "0": "코스피",
    "10": "코스닥",
    "30": "K-OTC",
    "50": "코넥스",
    "8": "ETF",
    "60": "ETN",
}


def getStkList(
    hostUrl,
    token,
    mrktTp,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """ka10099 종목정보 리스트 1회 호출."""
    url = hostUrl + "/api/dostk/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10099",
    }
    data = {"mrkt_tp": str(mrktTp).strip()}
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def _toInt(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    # 좌측 0-padding·부호 포함 숫자 대응
    try:
        return int(float(text))
    except ValueError:
        return None


def rowToStkInfo(row, mrktTp):
    """API list 요소 → AI_STK_INFO dict."""
    if not isinstance(row, dict):
        return None
    code = normStkCd(row.get("code") or row.get("stk_cd") or "")
    name = str(row.get("name") or row.get("stk_nm") or "").strip()
    if not code or not name:
        return None
    return {
        "STK_CD": code,
        "STK_NM": name,
        "MRKT_TP": str(mrktTp),
        "MARKET_CODE": str(row.get("marketCode") or "").strip() or None,
        "MARKET_NAME": str(row.get("marketName") or "").strip() or None,
        "LIST_CNT": _toInt(row.get("listCount")),
        "REG_DAY": str(row.get("regDay") or "").strip()[:8] or None,
        "LAST_PRICE": _toInt(row.get("lastPrice")),
        "STATE": str(row.get("state") or "").strip() or None,
        "UP_NAME": str(row.get("upName") or "").strip() or None,
        "NXT_ENABLE": str(row.get("nxtEnable") or "").strip()[:1] or None,
        "SRC_TR": SRC_TR,
    }


def fetchStkListMrkt(
    hostUrl,
    token,
    mrktTp,
    tokenType="bearer",
    isPaper=True,
):
    """
    한 시장(mrkt_tp) 종목 리스트 연속조회.
    반환: {ok, rows, rowCnt, pageUsed, errMsg?}
    """
    waitSec = 1.05 if isPaper else 0.25
    byCd = {}
    contYn = "N"
    nextKey = ""
    pageUsed = 0

    while pageUsed < MAX_CONT_PAGES:
        if pageUsed > 0:
            time.sleep(waitSec)
        try:
            res = getStkList(
                hostUrl,
                token,
                mrktTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}", "rows": [], "rowCnt": 0}

        pageUsed += 1
        try:
            body = res.json()
        except Exception as err:
            return {
                "ok": False,
                "errMsg": f"응답 파싱 실패: {err}",
                "rows": [],
                "rowCnt": 0,
            }

        if res.status_code != 200:
            return {
                "ok": False,
                "errMsg": (
                    f"HTTP {res.status_code} "
                    f"[{body.get('return_code')}] {body.get('return_msg') or res.text}"
                ),
                "rows": [],
                "rowCnt": 0,
            }

        returnCode = body.get("return_code")
        try:
            codeOk = int(returnCode) == 0
        except (TypeError, ValueError):
            codeOk = True
        if not codeOk and pageUsed == 1 and not body.get("list"):
            return {
                "ok": False,
                "errMsg": f"[{returnCode}] {body.get('return_msg') or '종목리스트 실패'}",
                "rows": [],
                "rowCnt": 0,
            }

        for item in body.get("list") or []:
            mapped = rowToStkInfo(item, mrktTp)
            if mapped:
                byCd[mapped["STK_CD"]] = mapped

        contYnHdr = _hdr(res, "cont-yn") or _hdr(res, "Cont-Yn")
        nextKeyHdr = _hdr(res, "next-key") or _hdr(res, "Next-Key")
        if contYnHdr.upper() != "Y" or not nextKeyHdr:
            break
        contYn = "Y"
        nextKey = nextKeyHdr

    rows = list(byCd.values())
    return {
        "ok": True,
        "rows": rows,
        "rowCnt": len(rows),
        "pageUsed": pageUsed,
        "mrktTp": str(mrktTp),
    }


def fetchStkListMarkets(
    hostUrl,
    token,
    mrktTps=None,
    tokenType="bearer",
    isPaper=True,
):
    """
    여러 시장 순차 조회·합치기.
    반환: {ok, rows, rowCnt, byMrkt:{tp:cnt}, errMsg?}
    """
    tps = list(mrktTps) if mrktTps else list(DEFAULT_MRKT_TPS)
    byCd = {}
    byMrkt = {}
    waitSec = 1.05 if isPaper else 0.25

    for i, tp in enumerate(tps):
        if i > 0:
            time.sleep(waitSec)
        got = fetchStkListMrkt(
            hostUrl, token, tp, tokenType=tokenType, isPaper=isPaper
        )
        if not got.get("ok"):
            return {
                "ok": False,
                "errMsg": f"시장 {tp} 실패: {got.get('errMsg')}",
                "rows": [],
                "rowCnt": 0,
                "byMrkt": byMrkt,
            }
        byMrkt[str(tp)] = got.get("rowCnt") or 0
        for row in got.get("rows") or []:
            byCd[row["STK_CD"]] = row

    rows = list(byCd.values())
    return {
        "ok": True,
        "rows": rows,
        "rowCnt": len(rows),
        "byMrkt": byMrkt,
    }
