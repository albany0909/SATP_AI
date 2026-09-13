"""키움 미국주식 일 차트 API (usa06012) — 기간 조회·파싱"""

import time
from datetime import datetime, timedelta

import requests

from api.getUsInqRank import normUsStkCd

MAX_CONT_PAGES = 400
MAX_RESTARTS = 80


def getUsDayChart(
    hostUrl,
    token,
    stexTp,
    stkCd,
    strtDt,
    updStkpcTp="1",
    exrtApplTp="0",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """usa06012 1회 호출."""
    url = hostUrl + "/api/us/chart"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "usa06012",
    }
    data = {
        "stex_tp": str(stexTp or "").strip().upper(),
        "stk_cd": normUsStkCd(stkCd),
        "strt_dt": str(strtDt).strip(),
        "upd_stkpc_tp": str(updStkpcTp),
        "exrt_appl_tp": str(exrtApplTp),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def parseSignedDec(raw):
    """부호 포함 소수. 실패 시 None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if text == "" or text in ("-", "--"):
        return None
    while text.startswith("--"):
        text = "-" + text[2:]
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parseAbsDec(raw):
    """절대값 소수(가격)."""
    num = parseSignedDec(raw)
    if num is None:
        return None
    return abs(float(num))


def parseAbsInt(raw):
    num = parseSignedDec(raw)
    if num is None:
        return None
    return abs(int(num))


def _normYmd(raw):
    text = str(raw or "").strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _ymdMinusOne(ymd):
    try:
        dt = datetime.strptime(ymd, "%Y%m%d") - timedelta(days=1)
        return dt.strftime("%Y%m%d")
    except ValueError:
        return None


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
        codeOk = "result_list" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '미국일봉 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(row, stkCd, stexTp):
    """API row → AI_US_DAY_STK_PRC dict."""
    if not isinstance(row, dict):
        return None
    tradeDt = _normYmd(row.get("dt"))
    if not tradeDt:
        return None

    return {
        "STK_CD": normUsStkCd(stkCd),
        "STEX_TP": str(stexTp or "").strip().upper() or None,
        "TRADE_DT": tradeDt,
        "OPEN_PRC": parseAbsDec(row.get("open_pric")),
        "HIGH_PRC": parseAbsDec(row.get("high_pric")),
        "LOW_PRC": parseAbsDec(row.get("low_pric")),
        "CLOSE_PRC": parseAbsDec(row.get("cur_prc")),
        "PRED_PRE": parseSignedDec(row.get("pred_pre")),
        "FLU_RT": parseSignedDec(row.get("flu_rt")),
        "TRDE_QTY": parseAbsInt(row.get("acc_trde_qty")),
        "TRDE_PRICA": parseAbsDec(row.get("acc_trde_prica")),
        "SRC_TR": "usa06012",
    }


def fetchUsDayChartRange(
    hostUrl,
    token,
    stexTp,
    stkCd,
    startDt,
    endDt,
    tokenType="bearer",
    isPaper=True,
):
    """
    usa06012 연속조회로 startDt~endDt 일봉 수집.
    strt_dt 기준으로 과거 방향. 끊기면 가장 오래된 날−1로 재시작.
    """
    startYmd = _normYmd(startDt)
    endYmd = _normYmd(endDt)
    if not startYmd or not endYmd:
        return {"ok": False, "errMsg": "시작일/종료일은 YYYYMMDD 형식이어야 합니다."}
    if startYmd > endYmd:
        return {"ok": False, "errMsg": "시작일이 종료일보다 클 수 없습니다."}

    code = normUsStkCd(stkCd)
    stex = str(stexTp or "").strip().upper()
    if not code:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}
    if stex not in ("ND", "NY", "NA"):
        return {"ok": False, "errMsg": f"거래소구분(stex_tp) 필요: ND/NY/NA (got={stex})"}

    waitSec = 1.05 if isPaper else 0.35
    byDt = {}
    contYn = "N"
    nextKey = ""
    # 시작일부터 요청 — 응답은 보통 최신→과거
    qryStrt = startYmd
    reachedPastStart = False
    pageUsed = 0
    restartCnt = 0

    while pageUsed < MAX_CONT_PAGES and restartCnt <= MAX_RESTARTS:
        if pageUsed > 0:
            time.sleep(waitSec)

        try:
            res = getUsDayChart(
                hostUrl,
                token,
                stex,
                code,
                qryStrt,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}"}

        pageUsed += 1
        ok, parsed = _parseBody(res)
        if not ok:
            return {
                "ok": False,
                "errMsg": parsed.get("errMsg"),
                "body": parsed.get("body"),
            }

        rows = parsed.get("result_list") or []
        pageOldest = None
        if isinstance(rows, list):
            for row in rows:
                rec = rowToRecord(row, code, stex)
                if rec is None:
                    continue
                dt = rec["TRADE_DT"]
                if pageOldest is None or dt < pageOldest:
                    pageOldest = dt
                if dt > endYmd:
                    continue
                if dt < startYmd:
                    reachedPastStart = True
                    continue
                byDt[dt] = rec

        if reachedPastStart:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKeyHdr = _hdr(res, "next-key", "")
        if contYnHdr == "Y" and nextKeyHdr:
            contYn = "Y"
            nextKey = nextKeyHdr
            continue

        oldestDt = pageOldest
        if oldestDt is None and byDt:
            oldestDt = min(byDt.keys())

        # 아직 시작일 미달이면 strt_dt를 더 과거로 (응답이 최신만 주는 경우 대비)
        # 실제로는 strt_dt 이후 데이터가 오므로, end 미달 시 연속이 끊기면 종료
        if oldestDt and oldestDt > startYmd and not byDt:
            prevDt = _ymdMinusOne(oldestDt)
            if not prevDt or prevDt < startYmd:
                break
            if prevDt >= qryStrt and restartCnt > 0:
                break
            qryStrt = startYmd
            contYn = "N"
            nextKey = ""
            restartCnt += 1
            continue
        break

    records = [byDt[k] for k in sorted(byDt.keys())]
    return {
        "ok": True,
        "rows": records,
        "rowCnt": len(records),
        "stkCd": code,
        "stexTp": stex,
        "startDt": startYmd,
        "endDt": endYmd,
        "pageUsed": pageUsed,
        "restartCnt": restartCnt,
    }
