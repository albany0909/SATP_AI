"""dayprc {종목코드} {시작일} {종료일} — ka10086 일별주가 조회 후 AI_DAY_STK_PRC 적재"""

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog


def _pickTradeSession():
    """모의 우선, 없으면 실투자."""
    if runtimeState.isPaperLoggedIn():
        return (
            "paper",
            paper_host_url,
            runtimeState.getToken("paper"),
            runtimeState.paperTokenType or "bearer",
        )
    if runtimeState.isRealLoggedIn():
        return (
            "real",
            real_host_url,
            runtimeState.getToken("real"),
            runtimeState.realTokenType or "bearer",
        )
    return None


def handleDayPrc(stkCd, startDt, endDt):
    """
    종목·기간 일별주가(ka10086)를 호출해 AI_DAY_STK_PRC에 적재한다.
    종목코드 또는 종목명 가능.
    """
    usage = (
        "사용법: dayprc {종목코드|종목명} {시작일} {종료일}\n"
        "예) dayprc 005930 20240101 20241231\n"
        "예) dayprc 삼성전자 2024-01-01 2024-12-31"
    )

    codeRaw = (stkCd or "").strip()
    startRaw = (startDt or "").strip()
    endRaw = (endDt or "").strip()

    if not codeRaw or not startRaw or not endRaw:
        return usage

    resolved = resolveStkRef(codeRaw)
    if not resolved.get("ok"):
        return resolved.get("errMsg")
    stkCdClean = resolved["stkCd"]
    stkLabel = formatStkLabel(stkCdClean, resolved.get("stkNm"))

    session = _pickTradeSession()
    if session is None:
        failMsg = "일별주가 적재 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"

    termLog(
        f"[dayprc] ({modeLabel}) {stkLabel} {startRaw}~{endRaw} 조회 시작"
    )

    fetched = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCdClean,
        startRaw,
        endRaw,
        indcTp=INDC_TP_QTY,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )

    if not fetched.get("ok"):
        failMsg = (
            f"[일별주가실패] ({modeLabel}) {stkLabel}: "
            f"{fetched.get('errMsg')}"
        )
        termLog(failMsg)
        return failMsg

    rows = fetched.get("rows") or []
    if not rows:
        msg = (
            f"[일별주가] ({modeLabel}) {stkLabel}\n"
            f"기간: {fetched.get('startDt')} ~ {fetched.get('endDt')}\n"
            f"조회 건수: 0 — 적재할 데이터 없음"
        )
        termLog(msg)
        return msg

    saved = upsertDayStkPrcRows(rows)
    if not saved.get("ok"):
        failMsg = (
            f"[일별주가실패] ({modeLabel}) {stkLabel} DB: "
            f"{saved.get('errMsg')}"
        )
        termLog(failMsg)
        return failMsg

    firstDt = rows[0]["TRADE_DT"]
    lastDt = rows[-1]["TRADE_DT"]
    pageUsed = fetched.get("pageUsed")
    restartCnt = fetched.get("restartCnt")
    extra = ""
    if pageUsed is not None:
        extra = f"\n조회: page={pageUsed}, restart={restartCnt}"
    # 요청 시작보다 적재 시작이 늦으면 안내 (상장일·API 한계 가능)
    note = ""
    reqStart = fetched.get("startDt") or ""
    if reqStart and firstDt > reqStart:
        note = (
            f"\n※ 요청시작({reqStart})보다 적재시작이 늦음 "
            f"— 상장일/제공구간 한계일 수 있음"
        )
    okMsg = (
        f"[일별주가] ({modeLabel}) 적재 완료\n"
        f"종목: {stkLabel}\n"
        f"요청기간: {fetched.get('startDt')} ~ {fetched.get('endDt')}\n"
        f"적재기간: {firstDt} ~ {lastDt}\n"
        f"건수: {saved.get('upsertCnt')}건 (표시구분=수량)"
        f"{extra}{note}"
    )
    termLog(okMsg)
    return okMsg
