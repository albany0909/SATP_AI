"""ka00198 실시간조회순위 → AI_DAY_INQ_RANK 적재"""

from datetime import datetime

from api.getAcntEvlt import parsePadNum
from api.getInqRank import QRY_TP_DAY, QRY_TP_LABEL, fetchInqRank
from utils.dayInqRankStore import upsertDayInqRankRows
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.stkCdUtil import normStkCd
from utils.stkIndGrp import enrichRankRecordsWithInd
from utils.termLog import termLog

SRC_TR = "ka00198"


def _pickQuerySession():
    """모의 우선. 로그인 토큰 → 프로세스 캐시 → .env 앱키 발급 (웹 수동 배치 대응)."""
    return acquireKiwoomSession()


def _parseSignedInt(raw):
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


def _parseDec(raw):
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


def rowToRecord(row, qryTp, fallbackDt):
    """API row → AI_DAY_INQ_RANK dict. 파싱 실패 시 None."""
    if not isinstance(row, dict):
        return None

    rankRaw = row.get("bigd_rank")
    rankNo = _parseSignedInt(rankRaw)
    if rankNo is None:
        return None
    rankNo = abs(int(rankNo))

    stkCd = normStkCd(row.get("stk_cd") or "")
    if not stkCd:
        return None

    tradeDt = str(row.get("dt") or "").strip()
    if len(tradeDt) != 8 or not tradeDt.isdigit():
        tradeDt = fallbackDt

    baseTm = str(row.get("tm") or "").strip()
    if len(baseTm) != 6 or not baseTm.isdigit():
        baseTm = None

    prcNum = parsePadNum(row.get("past_curr_prc"))
    curPrc = abs(int(prcNum)) if prcNum is not None else None

    rankChg = _parseSignedInt(row.get("rank_chg"))
    # 변동없음(0/빈값)은 0으로 통일
    if rankChg is None:
        rankChg = 0

    sign = str(row.get("rank_chg_sign") or "").strip() or "N"
    if len(sign) > 4:
        sign = sign[:4]

    return {
        "TRADE_DT": tradeDt,
        "QRY_TP": str(qryTp),
        "RANK_NO": rankNo,
        "STK_CD": stkCd,
        "STK_NM": str(row.get("stk_nm") or "").strip() or None,
        "RANK_CHG": rankChg,
        "RANK_CHG_SIGN": sign,
        "CUR_PRC": curPrc,
        "BASE_COMP_SIGN": (str(row.get("base_comp_sign") or "").strip()[:1] or None),
        "BASE_COMP_CHGR": _parseDec(row.get("base_comp_chgr")),
        "PREV_BASE_SIGN": (str(row.get("prev_base_sign") or "").strip()[:1] or None),
        "PREV_BASE_CHGR": _parseDec(row.get("prev_base_chgr")),
        "BASE_TM": baseTm,
        "SRC_TR": SRC_TR,
        "IND_GRP": None,
        "UP_NAME": None,
    }


def loadDayInqRank(qryTp=QRY_TP_DAY):
    """
    ka00198 조회 후 AI_DAY_INQ_RANK UPSERT.
    기본 구분: 당일누적(4) — 장마감 스냅샷용.
    반환: {ok, upsertCnt, tradeDt, qryTp, qryLabel, modeLabel, errMsg?}
    """
    session = _pickQuerySession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "순위 적재 전 login paper 또는 login real이 필요합니다.",
            "upsertCnt": 0,
        }

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    qryLabel = QRY_TP_LABEL.get(str(qryTp), str(qryTp))
    fallbackDt = datetime.now().strftime("%Y%m%d")

    termLog(f"[inqRankLoad] ({modeLabel}) {qryLabel} 조회·적재 시작")
    fetched = fetchInqRank(hostUrl, token, qryTp=qryTp, tokenType=tokenType)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "errMsg": fetched.get("errMsg"),
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    apiRows = fetched.get("rows") or []
    records = []
    for row in apiRows:
        rec = rowToRecord(row, qryTp, fallbackDt)
        if rec is not None:
            records.append(rec)

    if not records:
        return {
            "ok": False,
            "errMsg": "적재할 순위 행이 없습니다.",
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    # 테마/업종 보강 — 맵+마스터+누락분 ka10100
    enrichRankRecordsWithInd(
        records,
        hostUrl=hostUrl,
        token=token,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    indFilled = sum(1 for r in records if r.get("IND_GRP"))
    themeLike = sum(
        1
        for r in records
        if r.get("IND_GRP")
        and r.get("IND_GRP") not in ("기타",)
        and r.get("IND_GRP") != (r.get("UP_NAME") or "")
    )

    saved = upsertDayInqRankRows(records)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg"),
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "qryTp": str(qryTp),
            "qryLabel": qryLabel,
        }

    tradeDt = records[0]["TRADE_DT"]
    stkCds = []
    seen = set()
    for r in records:
        cd = r.get("STK_CD")
        if cd and cd not in seen:
            seen.add(cd)
            stkCds.append(cd)

    return {
        "ok": True,
        "upsertCnt": saved.get("upsertCnt") or 0,
        "tradeDt": tradeDt,
        "qryTp": str(qryTp),
        "qryLabel": qryLabel,
        "modeLabel": modeLabel,
        "rowCnt": len(records),
        "indFilled": indFilled,
        "themeFilled": themeLike,
        "stkCds": stkCds,
    }


def ensureInqRankReady(tradeDt=None, qryTp=QRY_TP_DAY):
    """
    관심/검증용 순위 기준일을 확보한다.
    DB에 휴일·직전영업일 적재가 있으면 그걸 쓰고,
    둘 다 없으면 ka00198 실시간 순위를 조회·적재한다.
    """
    from utils.dayInqRankStore import resolveInqRankTradeDt

    resolved = resolveInqRankTradeDt(tradeDt, qryTp=qryTp)
    if resolved.get("ok") and resolved.get("tradeDt"):
        return resolved

    req = resolved.get("reqDt") or tradeDt
    termLog(
        f"[inqRankLoad] 휴일·직전영업일 순위 없음 — 실시간 조회 (요청 {req})"
    )
    loaded = loadDayInqRank(qryTp=qryTp)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "tradeDt": "",
            "reqDt": req,
            "fallback": False,
            "source": "live",
            "needLive": True,
            "errMsg": loaded.get("errMsg") or "실시간 순위 조회 실패",
        }
    used = str(loaded.get("tradeDt") or "")
    return {
        "ok": True,
        "tradeDt": used,
        "reqDt": req,
        "fallback": True,
        "source": "live",
        "needLive": False,
        "fetched": True,
        "stkCds": loaded.get("stkCds") or [],
        "errMsg": "",
    }
