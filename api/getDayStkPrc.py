"""키움 일별주가요청 API (ka10086) — 기간 조회·파싱"""

import time
from datetime import datetime, timedelta

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

# 연속조회·재시작 상한 (장기간 일봉: 끊기면 qry_dt 앞당겨 재시작)
MAX_CONT_PAGES = 800
MAX_RESTARTS = 120
# indc_tp 기본: 수량
INDC_TP_QTY = "0"


def getDayStkPrc(
    hostUrl,
    token,
    stkCd,
    qryDt,
    indcTp=INDC_TP_QTY,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10086 일별주가 1회 호출.
    body: stk_cd, qry_dt(YYYYMMDD), indc_tp(0수량/1금액)
    """
    url = hostUrl + "/api/dostk/mrkcond"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10086",
    }
    data = {
        "stk_cd": normStkCd(stkCd),
        "qry_dt": str(qryDt).strip(),
        "indc_tp": str(indcTp),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def parseSignedInt(raw):
    """
    부호 포함 정수. API 예외 표기 '--714' 도 처리.
    파싱 실패 시 None.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "" or text in ("-", "--"):
        return None
    # 이중 부호 정리: --123 → -123
    while text.startswith("--"):
        text = "-" + text[2:]
    num = parsePadNum(text)
    if num is not None:
        return int(num)
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parseSignedDec(raw):
    """부호 포함 소수(등락률 등). 실패 시 None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
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


def _absOrNone(raw):
    num = parseSignedInt(raw)
    if num is None:
        return None
    return abs(int(num))


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
        codeOk = "daly_stkpc" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '일별주가 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(row, stkCd, indcTp):
    """
    API row → AI_DAY_STK_PRC 적재용 dict.
    OHLC는 절대값, 전일비·수급은 부호 유지.
    """
    if not isinstance(row, dict):
        return None
    tradeDt = str(row.get("date") or "").strip()
    if len(tradeDt) != 8 or not tradeDt.isdigit():
        return None

    return {
        "STK_CD": normStkCd(stkCd),
        "TRADE_DT": tradeDt,
        "INDC_TP": str(indcTp),
        "OPEN_PRC": _absOrNone(row.get("open_pric")),
        "HIGH_PRC": _absOrNone(row.get("high_pric")),
        "LOW_PRC": _absOrNone(row.get("low_pric")),
        "CLOSE_PRC": _absOrNone(row.get("close_pric")),
        "PRED_RT": parseSignedInt(row.get("pred_rt")),
        "FLU_RT": parseSignedDec(row.get("flu_rt")),
        "TRDE_QTY": _absOrNone(row.get("trde_qty")),
        "AMT_MN": parseSignedInt(row.get("amt_mn")),
        "CRD_RT": parseSignedDec(row.get("crd_rt")),
        "IND_VAL": parseSignedInt(row.get("ind")),
        "ORGN_VAL": parseSignedInt(row.get("orgn")),
        "FOR_QTY": parseSignedInt(row.get("for_qty")),
        "FRGN_VAL": parseSignedInt(row.get("frgn")),
        "PRM_VAL": parseSignedInt(row.get("prm")),
        "FOR_RT": parseSignedDec(row.get("for_rt")),
        "FOR_POSS": parseSignedDec(row.get("for_poss")),
        "FOR_WGHT": parseSignedDec(row.get("for_wght")),
        "FOR_NETPRPS": parseSignedInt(row.get("for_netprps")),
        "ORGN_NETPRPS": parseSignedInt(row.get("orgn_netprps")),
        "IND_NETPRPS": parseSignedInt(row.get("ind_netprps")),
        "CRD_REMN_RT": parseSignedDec(row.get("crd_remn_rt")),
    }


def _normYmd(raw):
    """YYYYMMDD 또는 YYYY-MM-DD → YYYYMMDD. 실패 시 None."""
    text = str(raw or "").strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _ymdMinusOne(ymd):
    """YYYYMMDD 하루 전. 실패 시 None."""
    try:
        dt = datetime.strptime(ymd, "%Y%m%d") - timedelta(days=1)
        return dt.strftime("%Y%m%d")
    except ValueError:
        return None


def fetchDayStkPrcRange(
    hostUrl,
    token,
    stkCd,
    startDt,
    endDt,
    indcTp=INDC_TP_QTY,
    tokenType="bearer",
    isPaper=True,
):
    """
    ka10086 연속조회로 startDt~endDt(포함) 일별주가 수집.
    qry_dt=endDt 기준 과거 방향. 연속조회가 끊기거나 페이지 한도에
    걸리면 수집된 가장 오래된 일자의 하루 전으로 qry_dt를 옮겨 재시작.
    반환: ok, rows, rowCnt, pageUsed, restartCnt, ...
    """
    startYmd = _normYmd(startDt)
    endYmd = _normYmd(endDt)
    if not startYmd or not endYmd:
        return {"ok": False, "errMsg": "시작일/종료일은 YYYYMMDD 형식이어야 합니다."}
    if startYmd > endYmd:
        return {"ok": False, "errMsg": "시작일이 종료일보다 클 수 없습니다."}

    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}

    waitSec = 1.05 if isPaper else 0.25
    byDt = {}
    contYn = "N"
    nextKey = ""
    # 조회 기준일은 종료일 — 응답은 보통 최신→과거
    qryDt = endYmd
    reachedPastStart = False
    pageUsed = 0
    restartCnt = 0

    while pageUsed < MAX_CONT_PAGES and restartCnt <= MAX_RESTARTS:
        if pageUsed > 0:
            time.sleep(waitSec)

        try:
            res = getDayStkPrc(
                hostUrl,
                token,
                code,
                qryDt,
                indcTp=indcTp,
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

        rows = parsed.get("daly_stkpc") or []
        pageOldest = None
        if isinstance(rows, list):
            for row in rows:
                rec = rowToRecord(row, code, indcTp)
                if rec is None:
                    continue
                dt = rec["TRADE_DT"]
                if pageOldest is None or dt < pageOldest:
                    pageOldest = dt
                # 기간 밖(미래 쪽)은 스킵, 과거로 더 가면 수집 중단 플래그
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

        # 연속조회 종료 — 아직 시작일 미도달이면 qry_dt 재시작
        # (페이지에 날짜가 없으면 전체 수집분 중 최오래된 날 사용)
        oldestDt = pageOldest
        if oldestDt is None and byDt:
            oldestDt = min(byDt.keys())

        if oldestDt and oldestDt > startYmd:
            prevDt = _ymdMinusOne(oldestDt)
            if not prevDt or prevDt < startYmd:
                break
            # 진행이 없으면 무한 재시작 방지
            if prevDt >= qryDt:
                break
            qryDt = prevDt
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
        "startDt": startYmd,
        "endDt": endYmd,
        "indcTp": str(indcTp),
        "pageUsed": pageUsed,
        "restartCnt": restartCnt,
    }
