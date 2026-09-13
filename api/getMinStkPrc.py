"""키움 주식분봉차트조회요청 API (ka10080) — 기간 조회·파싱"""

import time
from datetime import datetime, timedelta

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

# 연속조회 안전 상한 (장기간 1분봉은 페이지 많음)
MAX_CONT_PAGES = 500
MAX_RESTARTS = 80

# tic_scope: 1/3/5/10/15/30/45/60 분
TIC_SCOPE_1 = "1"
TIC_SCOPE_SET = {"1", "3", "5", "10", "15", "30", "45", "60"}
TIC_SCOPE_LABEL = {
    "1": "1분",
    "3": "3분",
    "5": "5분",
    "10": "10분",
    "15": "15분",
    "30": "30분",
    "45": "45분",
    "60": "60분",
}

SRC_TR = "ka10080"
UPD_STKPC_DEFAULT = "1"


def normTicScope(raw, default=TIC_SCOPE_1):
    """사용자 입력 → tic_scope. 실패 시 None."""
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower().replace("분", "")
    # 1m / 5min 등
    if text.endswith("min"):
        text = text[:-3]
    if text.endswith("m") and text[:-1].isdigit():
        text = text[:-1]
    if text in TIC_SCOPE_SET:
        return text
    return None


def getMinStkPrc(
    hostUrl,
    token,
    stkCd,
    ticScope=TIC_SCOPE_1,
    updStkpcTp=UPD_STKPC_DEFAULT,
    baseDt="",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10080 분봉차트 1회 호출.
    body: stk_cd, tic_scope, upd_stkpc_tp, base_dt(선택)
    """
    url = hostUrl + "/api/dostk/chart"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10080",
    }
    data = {
        "stk_cd": normStkCd(stkCd),
        "tic_scope": str(ticScope).strip(),
        "upd_stkpc_tp": str(updStkpcTp).strip(),
    }
    if baseDt:
        data["base_dt"] = str(baseDt).strip()
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


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


def parseSignedInt(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "" or text in ("-", "--"):
        return None
    while text.startswith("--"):
        text = "-" + text[2:]
    num = parsePadNum(text)
    if num is not None:
        return int(num)
    try:
        return int(float(text))
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
        codeOk = "stk_min_pole_chart_qry" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '분봉 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(row, stkCd, ticScope, updStkpcTp=UPD_STKPC_DEFAULT):
    """
    API row → AI_MIN_STK_PRC dict.
    cntr_tm=YYYYMMDDHHmmss 필수.
    """
    if not isinstance(row, dict):
        return None
    cntrTm = str(row.get("cntr_tm") or "").strip()
    if len(cntrTm) != 14 or not cntrTm.isdigit():
        return None

    tradeDt = cntrTm[:8]
    barTm = cntrTm[8:14]
    code = normStkCd(stkCd)
    if not code:
        return None

    predSig = str(row.get("pred_pre_sig") or "").strip()
    if len(predSig) > 1:
        predSig = predSig[:1]

    return {
        "STK_CD": code,
        "TIC_SCOPE": str(ticScope),
        "CNTR_TM": cntrTm,
        "TRADE_DT": tradeDt,
        "BAR_TM": barTm,
        "OPEN_PRC": _absOrNone(row.get("open_pric")),
        "HIGH_PRC": _absOrNone(row.get("high_pric")),
        "LOW_PRC": _absOrNone(row.get("low_pric")),
        "CLOSE_PRC": _absOrNone(row.get("cur_prc")),
        "TRDE_QTY": _absOrNone(row.get("trde_qty")),
        "ACC_TRDE_QTY": _absOrNone(row.get("acc_trde_qty")),
        "PRED_PRE": parseSignedInt(row.get("pred_pre")),
        "PRED_PRE_SIG": predSig or None,
        "UPD_STKPC_TP": str(updStkpcTp),
        "SRC_TR": SRC_TR,
    }


def fetchMinStkPrcRange(
    hostUrl,
    token,
    stkCd,
    startDt,
    endDt,
    ticScope=TIC_SCOPE_1,
    updStkpcTp=UPD_STKPC_DEFAULT,
    tokenType="bearer",
    isPaper=True,
):
    """
    ka10080 연속조회로 startDt~endDt(일자 포함) 분봉 수집.
    base_dt=endDt 기준 과거 방향. 연속조회 끊기면 하루 앞당겨 재시작.
    반환: ok, rows, rowCnt, ...
    """
    startYmd = _normYmd(startDt)
    endYmd = _normYmd(endDt)
    if not startYmd or not endYmd:
        return {"ok": False, "errMsg": "시작일/종료일은 YYYYMMDD 형식이어야 합니다."}
    if startYmd > endYmd:
        return {"ok": False, "errMsg": "시작일이 종료일보다 클 수 없습니다."}

    scope = normTicScope(ticScope, default=None)
    if scope is None:
        return {
            "ok": False,
            "errMsg": f"틱범위는 {', '.join(sorted(TIC_SCOPE_SET, key=int))} 중 하나여야 합니다.",
        }

    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}

    # 기간 필터: 시작일 00:00:00 ~ 종료일 23:59:59
    startKey = startYmd + "000000"
    endKey = endYmd + "235959"

    waitSec = 1.05 if isPaper else 0.25
    byTm = {}
    contYn = "N"
    nextKey = ""
    qryDt = endYmd
    reachedPastStart = False
    pageUsed = 0
    restartCnt = 0

    while pageUsed < MAX_CONT_PAGES and restartCnt <= MAX_RESTARTS:
        if pageUsed > 0:
            time.sleep(waitSec)

        try:
            res = getMinStkPrc(
                hostUrl,
                token,
                code,
                ticScope=scope,
                updStkpcTp=updStkpcTp,
                baseDt=qryDt,
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

        rows = parsed.get("stk_min_pole_chart_qry") or []
        pageOldest = None
        pageOldestDt = None
        if isinstance(rows, list):
            for row in rows:
                rec = rowToRecord(row, code, scope, updStkpcTp=updStkpcTp)
                if rec is None:
                    continue
                cntr = rec["CNTR_TM"]
                if pageOldest is None or cntr < pageOldest:
                    pageOldest = cntr
                    pageOldestDt = rec["TRADE_DT"]
                if cntr > endKey:
                    continue
                if cntr < startKey:
                    reachedPastStart = True
                    continue
                byTm[cntr] = rec

        if reachedPastStart:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKey = _hdr(res, "next-key", "")
        if contYnHdr == "Y" and nextKey:
            contYn = "Y"
            continue

        # 연속조회 종료 — 아직 시작일 미도달이면 base_dt 재시작
        if pageOldestDt and pageOldestDt > startYmd:
            prevDt = _ymdMinusOne(pageOldestDt)
            if not prevDt or prevDt < startYmd:
                break
            if prevDt >= qryDt:
                break
            qryDt = prevDt
            contYn = "N"
            nextKey = ""
            restartCnt += 1
            continue
        break

    records = [byTm[k] for k in sorted(byTm.keys())]
    return {
        "ok": True,
        "rows": records,
        "rowCnt": len(records),
        "stkCd": code,
        "ticScope": scope,
        "ticLabel": TIC_SCOPE_LABEL.get(scope, scope),
        "startDt": startYmd,
        "endDt": endYmd,
        "pageUsed": pageUsed,
        "restartCnt": restartCnt,
    }
