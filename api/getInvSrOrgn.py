"""키움 종목별투자자기관별요청 API (ka10059) — 조회·파싱"""

import time
from datetime import datetime

import requests

from api.getAcntEvlt import parsePadNum
from api.getDayStkPrc import parseSignedInt
from utils.stkCdUtil import normStkCd

SRC_TR = "ka10059"
# 명세: 1금액 2수량 / 0순매수 1매수 2매도 / 1단주 1000천주
AMT_QTY_AMT = "1"
AMT_QTY_QTY = "2"
TRDE_NET = "0"
UNIT_SHARE = "1"
UNIT_THOU = "1000"

MAX_CONT_PAGES = 40


def getInvSrOrgn(
    hostUrl,
    token,
    stkCd,
    dt,
    amtQtyTp=AMT_QTY_QTY,
    trdeTp=TRDE_NET,
    unitTp=UNIT_SHARE,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10059 1회 호출.
    body: dt, stk_cd, amt_qty_tp, trde_tp, unit_tp
    """
    url = hostUrl + "/api/dostk/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": SRC_TR,
    }
    data = {
        "dt": str(dt).strip(),
        "stk_cd": normStkCd(stkCd),
        "amt_qty_tp": str(amtQtyTp),
        "trde_tp": str(trdeTp),
        "unit_tp": str(unitTp),
    }
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


def parseFluRtImplied2(raw):
    """
    ka10059 등락율.
    명세: 우측 2자리 소수점 — 예 '+698' → 6.98.
    소수점이 있으면 그대로 파싱.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "" or text in ("-", "--"):
        return None
    while text.startswith("--"):
        text = "-" + text[2:]
    sign = 1
    if text.startswith("-"):
        sign = -1
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if text == "":
        return None
    try:
        if "." in text:
            return sign * float(text)
        return sign * (int(text) / 100.0)
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
        codeOk = "stk_invsr_orgn" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '종목별투자자 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(row, stkCd, amtQtyTp, trdeTp, unitTp):
    """API row → AI_DAY_INV_SR 적재 dict. 수급·전일비는 부호 유지."""
    if not isinstance(row, dict):
        return None
    tradeDt = _normYmd(row.get("dt"))
    if not tradeDt:
        return None

    return {
        "STK_CD": normStkCd(stkCd),
        "TRADE_DT": tradeDt,
        "AMT_QTY_TP": str(amtQtyTp),
        "TRDE_TP": str(trdeTp),
        "UNIT_TP": str(unitTp),
        "CUR_PRC": _absOrNone(row.get("cur_prc")),
        "PRE_SIG": str(row.get("pre_sig") or "").strip()[:1] or None,
        "PRED_PRE": parseSignedInt(row.get("pred_pre")),
        "FLU_RT": parseFluRtImplied2(row.get("flu_rt")),
        "ACC_TRDE_QTY": parsePadNum(row.get("acc_trde_qty")),
        "ACC_TRDE_PRICA": parsePadNum(row.get("acc_trde_prica")),
        "IND_INVSR": parseSignedInt(row.get("ind_invsr")),
        "FRGNR_INVSR": parseSignedInt(row.get("frgnr_invsr")),
        "ORGN": parseSignedInt(row.get("orgn")),
        "FNNC_INVT": parseSignedInt(row.get("fnnc_invt")),
        "INSRNC": parseSignedInt(row.get("insrnc")),
        "INVTRT": parseSignedInt(row.get("invtrt")),
        "ETC_FNNC": parseSignedInt(row.get("etc_fnnc")),
        "BANK": parseSignedInt(row.get("bank")),
        "PENFND_ETC": parseSignedInt(row.get("penfnd_etc")),
        "SAMO_FUND": parseSignedInt(row.get("samo_fund")),
        "NATN": parseSignedInt(row.get("natn")),
        "ETC_CORP": parseSignedInt(row.get("etc_corp")),
        "NATFOR": parseSignedInt(row.get("natfor")),
        "SRC_TR": SRC_TR,
    }


def fetchInvSrOrgn(
    hostUrl,
    token,
    stkCd,
    dt,
    amtQtyTp=AMT_QTY_QTY,
    trdeTp=TRDE_NET,
    unitTp=UNIT_SHARE,
    tokenType="bearer",
    isPaper=True,
    minDt=None,
):
    """
    ka10059 연속조회. dt 기준 과거 방향 리스트를 UPSERT용 row로 반환.
    minDt 있으면 그 일자 미만은 버림.
    """
    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}
    qryDt = _normYmd(dt)
    if not qryDt:
        return {"ok": False, "errMsg": "일자는 YYYYMMDD 형식이어야 합니다."}
    minYmd = _normYmd(minDt) if minDt else None

    waitSec = 1.05 if isPaper else 0.25
    byDt = {}
    contYn = "N"
    nextKey = ""
    pageUsed = 0
    reachedPast = False

    while pageUsed < MAX_CONT_PAGES:
        if pageUsed > 0:
            time.sleep(waitSec)

        try:
            res = getInvSrOrgn(
                hostUrl,
                token,
                code,
                qryDt,
                amtQtyTp=amtQtyTp,
                trdeTp=trdeTp,
                unitTp=unitTp,
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

        rows = parsed.get("stk_invsr_orgn") or []
        if isinstance(rows, list):
            for row in rows:
                rec = rowToRecord(row, code, amtQtyTp, trdeTp, unitTp)
                if rec is None:
                    continue
                tradeDt = rec["TRADE_DT"]
                if minYmd and tradeDt < minYmd:
                    reachedPast = True
                    continue
                byDt[tradeDt] = rec

        if reachedPast:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKeyHdr = _hdr(res, "next-key", "")
        if contYnHdr == "Y" and nextKeyHdr:
            contYn = "Y"
            nextKey = nextKeyHdr
            continue
        break

    records = [byDt[k] for k in sorted(byDt.keys(), reverse=True)]
    return {
        "ok": True,
        "rows": records,
        "rowCnt": len(records),
        "stkCd": code,
        "dt": qryDt,
        "amtQtyTp": str(amtQtyTp),
        "trdeTp": str(trdeTp),
        "unitTp": str(unitTp),
        "pageUsed": pageUsed,
        "srcTr": SRC_TR,
    }
