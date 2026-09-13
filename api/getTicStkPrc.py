"""키움 주식틱차트조회요청 API (ka10079) — 조회·파싱"""

import time
from collections import defaultdict
from datetime import datetime

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

MAX_CONT_PAGES = 200

# tic_scope: 1/3/5/10/30 틱 (분봉과 다름)
TIC_SCOPE_1 = "1"
TIC_SCOPE_SET = {"1", "3", "5", "10", "30"}
TIC_SCOPE_LABEL = {
    "1": "1틱",
    "3": "3틱",
    "5": "5틱",
    "10": "10틱",
    "30": "30틱",
}

SRC_TR = "ka10079"
UPD_STKPC_DEFAULT = "1"


def normTicScopeTick(raw, default=TIC_SCOPE_1):
    """사용자 입력 → 틱범위. 실패 시 None."""
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower().replace("틱", "").replace("tic", "")
    if text in TIC_SCOPE_SET:
        return text
    return None


def getTicStkPrc(
    hostUrl,
    token,
    stkCd,
    ticScope=TIC_SCOPE_1,
    updStkpcTp=UPD_STKPC_DEFAULT,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10079 틱차트 1회 호출.
    body: stk_cd, tic_scope, upd_stkpc_tp (base_dt 없음)
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
        "api-id": "ka10079",
    }
    data = {
        "stk_cd": normStkCd(stkCd),
        "tic_scope": str(ticScope).strip(),
        "upd_stkpc_tp": str(updStkpcTp).strip(),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def _normYmd(raw):
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


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
        codeOk = "stk_tic_chart_qry" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '틱차트 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(
    row,
    stkCd,
    ticScope,
    ticSeq,
    updStkpcTp=UPD_STKPC_DEFAULT,
    lastTicCnt=None,
):
    """API row → AI_TIC_STK_PRC dict. cntr_tm 필수."""
    if not isinstance(row, dict):
        return None
    cntrTm = str(row.get("cntr_tm") or "").strip()
    if len(cntrTm) != 14 or not cntrTm.isdigit():
        return None

    code = normStkCd(stkCd)
    if not code:
        return None

    predSig = str(row.get("pred_pre_sig") or "").strip()
    if len(predSig) > 1:
        predSig = predSig[:1]

    lastCnt = None
    if lastTicCnt is not None and str(lastTicCnt).strip() != "":
        lastCnt = str(lastTicCnt).strip()[:20]

    return {
        "STK_CD": code,
        "TIC_SCOPE": str(ticScope),
        "CNTR_TM": cntrTm,
        "TIC_SEQ": int(ticSeq),
        "TRADE_DT": cntrTm[:8],
        "BAR_TM": cntrTm[8:14],
        "OPEN_PRC": _absOrNone(row.get("open_pric")),
        "HIGH_PRC": _absOrNone(row.get("high_pric")),
        "LOW_PRC": _absOrNone(row.get("low_pric")),
        "CLOSE_PRC": _absOrNone(row.get("cur_prc")),
        "TRDE_QTY": _absOrNone(row.get("trde_qty")),
        "PRED_PRE": parseSignedInt(row.get("pred_pre")),
        "PRED_PRE_SIG": predSig or None,
        "UPD_STKPC_TP": str(updStkpcTp),
        "LAST_TIC_CNT": lastCnt,
        "SRC_TR": SRC_TR,
    }


def fetchTicStkPrc(
    hostUrl,
    token,
    stkCd,
    ticScope=TIC_SCOPE_1,
    startDt=None,
    endDt=None,
    updStkpcTp=UPD_STKPC_DEFAULT,
    tokenType="bearer",
    isPaper=True,
    maxPages=MAX_CONT_PAGES,
):
    """
    ka10079 연속조회로 틱 수집.
    startDt/endDt(YYYYMMDD)가 있으면 cntr_tm 일자로 필터. 없으면 API가 주는 만큼.
    동일 초 복수 틱 → TIC_SEQ 부여.
    """
    scope = normTicScopeTick(ticScope, default=None)
    if scope is None:
        return {
            "ok": False,
            "errMsg": f"틱범위는 {', '.join(sorted(TIC_SCOPE_SET, key=int))} 중 하나여야 합니다.",
        }

    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드가 비어 있습니다."}

    startYmd = _normYmd(startDt) if startDt else None
    endYmd = _normYmd(endDt) if endDt else None
    if startDt and not startYmd:
        return {"ok": False, "errMsg": "시작일 YYYYMMDD 형식 오류"}
    if endDt and not endYmd:
        return {"ok": False, "errMsg": "종료일 YYYYMMDD 형식 오류"}
    if startYmd and endYmd and startYmd > endYmd:
        return {"ok": False, "errMsg": "시작일이 종료일보다 클 수 없습니다."}

    startKey = (startYmd + "000000") if startYmd else None
    endKey = (endYmd + "235959") if endYmd else None

    waitSec = 1.05 if isPaper else 0.25
    # (cntr_tm, seq) → record — 페이지 간 동일초 순번 이어가기
    seqByTm = defaultdict(int)
    records = []
    seenKeys = set()
    contYn = "N"
    nextKey = ""
    reachedPastStart = False
    lastTicCnt = ""

    for pageIdx in range(maxPages):
        if pageIdx > 0:
            time.sleep(waitSec)

        try:
            res = getTicStkPrc(
                hostUrl,
                token,
                code,
                ticScope=scope,
                updStkpcTp=updStkpcTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}"}

        ok, parsed = _parseBody(res)
        if not ok:
            return {
                "ok": False,
                "errMsg": parsed.get("errMsg"),
                "body": parsed.get("body"),
            }

        lastTicCnt = str(parsed.get("last_tic_cnt") or "").strip()
        rows = parsed.get("stk_tic_chart_qry") or []
        if isinstance(rows, list):
            for row in rows:
                cntr = str(row.get("cntr_tm") or "").strip()
                if len(cntr) != 14 or not cntr.isdigit():
                    continue
                if endKey and cntr > endKey:
                    continue
                if startKey and cntr < startKey:
                    reachedPastStart = True
                    continue

                ticSeq = seqByTm[cntr]
                seqByTm[cntr] = ticSeq + 1
                rec = rowToRecord(
                    row,
                    code,
                    scope,
                    ticSeq,
                    updStkpcTp=updStkpcTp,
                    lastTicCnt=lastTicCnt,
                )
                if rec is None:
                    continue
                key = (rec["CNTR_TM"], rec["TIC_SEQ"])
                if key in seenKeys:
                    continue
                seenKeys.add(key)
                records.append(rec)

        if reachedPastStart:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKey = _hdr(res, "next-key", "")
        if contYnHdr != "Y" or not nextKey:
            break
        contYn = "Y"

    records.sort(key=lambda r: (r["CNTR_TM"], r["TIC_SEQ"]))
    return {
        "ok": True,
        "rows": records,
        "rowCnt": len(records),
        "stkCd": code,
        "ticScope": scope,
        "ticLabel": TIC_SCOPE_LABEL.get(scope, scope),
        "startDt": startYmd,
        "endDt": endYmd,
        "lastTicCnt": lastTicCnt,
        "pageUsed": pageIdx + 1,
    }
