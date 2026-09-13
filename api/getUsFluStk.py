"""키움 미국주식 가격급등락 API (usa20930)"""

from __future__ import annotations

import time

import requests

from api.getUsDayChart import parseAbsDec, parseSignedDec
from api.getUsInqRank import normUsStkCd

# flu_tp
FLU_UP = "1"
FLU_DN = "2"
FLU_LABEL = {FLU_UP: "급등", FLU_DN: "급락"}

SRC_TR = "usa20930"


def getUsFluStk(
    hostUrl,
    token,
    body=None,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """usa20930 1회 호출."""
    url = hostUrl + "/api/us/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "usa20930",
    }
    return requests.post(url, headers=headers, json=body or {}, timeout=30)


def defaultFluBody(fluTp=FLU_UP, tmTp="2", tm="1"):
    """
    기본 조회 조건.
    - flu_tp: 1급등 / 2급락
    - tm_tp=2(일전), tm=1(1일전) — 직전 세션 근사
    """
    return {
        "stex_tp": "0",
        "stk_tp": "1",
        "inds_cd": "000",
        "stk_cnd": "0",
        "flu_tp": str(fluTp),
        "tm_tp": str(tmTp),
        "tm": str(tm),
        "pric_cnd": "0",
        "trde_qty_tp": "0",
        "trde_prica_cnd": "0",
    }


def fetchUsFluStkList(
    hostUrl,
    token,
    fluTp=FLU_UP,
    tmTp="2",
    tm="1",
    tokenType="bearer",
    maxPages=3,
    sleepSec=1.05,
):
    """
    급등 또는 급락 목록 (연속조회 포함).
    반환: {ok, items, returnCode, returnMsg, errMsg?}
    """
    body = defaultFluBody(fluTp=fluTp, tmTp=tmTp, tm=tm)
    items = []
    contYn = "N"
    nextKey = ""
    lastCode = None
    lastMsg = ""

    for _ in range(max(1, int(maxPages))):
        try:
            res = getUsFluStk(
                hostUrl,
                token,
                body=body,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {
                "ok": False,
                "items": items,
                "errMsg": str(err),
                "fluTp": fluTp,
            }

        try:
            data = res.json() if res is not None else {}
        except Exception:
            data = {}

        lastCode = data.get("return_code")
        lastMsg = data.get("return_msg") or ""
        try:
            codeNum = int(lastCode) if lastCode is not None else -1
        except (TypeError, ValueError):
            codeNum = -1

        if codeNum != 0:
            return {
                "ok": False,
                "items": items,
                "errMsg": lastMsg or f"return_code={lastCode}",
                "returnCode": lastCode,
                "returnMsg": lastMsg,
                "fluTp": fluTp,
            }

        rows = data.get("result_list") or []
        if isinstance(rows, list):
            items.extend(rows)

        contYn = (res.headers.get("cont-yn") or res.headers.get("Cont-Yn") or "N").strip()
        nextKey = (res.headers.get("next-key") or res.headers.get("Next-Key") or "").strip()
        if contYn.upper() != "Y" or not nextKey:
            break
        time.sleep(max(0.3, float(sleepSec)))

    return {
        "ok": True,
        "items": items,
        "returnCode": lastCode,
        "returnMsg": lastMsg,
        "fluTp": fluTp,
    }


def rowToFluRecord(row, tradeDt, fluTp, rankNo):
    """API row → DB record."""
    if not isinstance(row, dict):
        return None
    stkCd = normUsStkCd(row.get("stk_cd") or "")
    if not stkCd:
        return None
    stex = str(row.get("stex_tp") or "").strip().upper() or None
    return {
        "TRADE_DT": str(tradeDt),
        "FLU_TP": str(fluTp),
        "RANK_NO": int(rankNo),
        "STK_CD": stkCd,
        "STK_NM": str(row.get("stk_nm") or "").strip() or None,
        "STK_ENM": str(row.get("stk_enm") or "").strip() or None,
        "STEX_TP": stex,
        "IND_GRP": None,
        "FLU_RT": parseSignedDec(row.get("flu_rt")),
        "CUR_PRC": parseAbsDec(row.get("cur_prc")),
        "PRED_PRE": parseSignedDec(row.get("pred_pre")),
        "BASE_PRIC": parseAbsDec(row.get("base_pric")),
        "ACC_TRDE_QTY": parseAbsDec(row.get("acc_trde_qty")),
        "SDNIN_RT": parseSignedDec(row.get("sdnin_rt")),
        "PRED_PRE_SIG": str(row.get("pred_pre_sig") or "").strip()[:1] or None,
        "SRC_TR": SRC_TR,
    }
