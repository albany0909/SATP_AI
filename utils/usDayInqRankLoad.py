"""usa01980 → AI_US_DAY_INQ_RANK 적재"""

from datetime import datetime

from api.getUsDayChart import parseAbsDec, parseSignedDec
from api.getUsInqRank import (
    QRY_TP_DAY,
    QRY_TP_LABEL,
    fetchUsInqRank,
    normUsStkCd,
)
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.usDayInqRankStore import upsertUsDayInqRankRows
from utils.usIndGrp import enrichUsRankRecordsWithInd
from utils.termLog import termLog

SRC_TR = "usa01980"


def _pickQuerySession():
    # 웹 프로세스(수동 배치)에서도 쓰이도록 .env 앱키 발급까지 위임
    return acquireKiwoomSession()


def _parseRankNo(raw):
    if raw is None:
        return None
    text = str(raw).strip().lstrip("0") or "0"
    try:
        return abs(int(text))
    except (TypeError, ValueError):
        return None


def _parseRankChg(signRaw, chgRaw):
    """sign(+/-/공백) + chg_val → (rankChg int, sign str)."""
    sign = str(signRaw or "").strip()
    chgText = str(chgRaw or "").strip()
    chgNum = 0
    try:
        chgNum = abs(int(chgText.lstrip("0") or "0"))
    except (TypeError, ValueError):
        chgNum = 0

    if not sign or sign in ("N", "'") or chgNum == 0:
        return 0, "N"
    if sign == "-":
        return -chgNum, "-"
    if sign == "+":
        return chgNum, "+"
    return chgNum, "N"


def rowToRecord(row, qryTp, fallbackDt, fallbackTm):
    """API row → AI_US_DAY_INQ_RANK dict."""
    if not isinstance(row, dict):
        return None

    rankNo = _parseRankNo(row.get("rank"))
    if rankNo is None:
        return None

    stkCd = normUsStkCd(row.get("stk_cd") or "")
    if not stkCd:
        return None

    stex = str(row.get("stex_tp") or "").strip().upper() or None
    rankChg, rankSign = _parseRankChg(row.get("sign"), row.get("chg_val"))

    baseSign = str(row.get("sign_for_gjga") or "").strip()[:1] or None
    prevSign = str(row.get("sign_for_prev") or "").strip()[:1] or None

    return {
        "TRADE_DT": fallbackDt,
        "QRY_TP": str(qryTp),
        "RANK_NO": rankNo,
        "STK_CD": stkCd,
        "STK_NM": str(row.get("stk_nm") or "").strip() or None,
        "STEX_TP": stex,
        "IND_GRP": None,
        "RANK_CHG": rankChg,
        "RANK_CHG_SIGN": rankSign,
        "CUR_PRC": parseAbsDec(row.get("curr_pric")),
        "BASE_COMP_SIGN": baseSign,
        "BASE_COMP_CHGR": parseSignedDec(row.get("diff_rate_for_gjga")),
        "PREV_BASE_SIGN": prevSign,
        "PREV_BASE_CHGR": parseSignedDec(row.get("diff_rate_for_prev")),
        "BASE_TM": fallbackTm,
        "SRC_TR": SRC_TR,
    }


def loadUsDayInqRank(qryTp=QRY_TP_DAY):
    """
    usa01980 조회 후 AI_US_DAY_INQ_RANK UPSERT.
    기본: 당일누적(4 / B284).
    """
    session = _pickQuerySession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "미국순위 적재 전 login paper 또는 login real이 필요합니다.",
            "upsertCnt": 0,
        }

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    qryLabel = QRY_TP_LABEL.get(str(qryTp), str(qryTp))

    termLog(f"[usInqRankLoad] ({modeLabel}) {qryLabel} 조회·적재 시작")
    fetched = fetchUsInqRank(hostUrl, token, qryTp=qryTp, tokenType=tokenType)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "errMsg": fetched.get("errMsg"),
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    baseDate = fetched.get("baseDate") or ""
    if len(baseDate) != 8 or not baseDate.isdigit():
        baseDate = datetime.now().strftime("%Y%m%d")
    baseTime = fetched.get("baseTime") or ""
    if len(baseTime) != 6 or not baseTime.isdigit():
        baseTime = None

    records = []
    for row in fetched.get("rows") or []:
        rec = rowToRecord(row, qryTp, baseDate, baseTime)
        if rec is not None:
            records.append(rec)

    if not records:
        return {
            "ok": False,
            "errMsg": "적재할 미국순위 행이 없습니다.",
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    indFilled = enrichUsRankRecordsWithInd(records)
    saved = upsertUsDayInqRankRows(records)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg"),
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    # 일봉 적재용: [{stkCd, stexTp}, ...]
    stkItems = []
    seen = set()
    for r in records:
        cd = r.get("STK_CD")
        if not cd or cd in seen:
            continue
        seen.add(cd)
        stkItems.append({"stkCd": cd, "stexTp": r.get("STEX_TP") or ""})

    return {
        "ok": True,
        "upsertCnt": saved.get("upsertCnt") or 0,
        "tradeDt": baseDate,
        "baseTm": baseTime,
        "qryTp": str(qryTp),
        "qryLabel": qryLabel,
        "modeLabel": modeLabel,
        "rowCnt": len(records),
        "indFilled": indFilled,
        "stkItems": stkItems,
        "stkCds": [x["stkCd"] for x in stkItems],
    }
