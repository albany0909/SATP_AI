"""minprc {종목코드} {틱범위} {시작일} {종료일} — ka10080 분봉 → AI_MIN_STK_PRC"""

from api.getMinStkPrc import TIC_SCOPE_1, TIC_SCOPE_LABEL, fetchMinStkPrcRange, normTicScope
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.minStkPrcStore import upsertMinStkPrcRows
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


def _usage():
    scopes = ", ".join(f"{k}={v}" for k, v in TIC_SCOPE_LABEL.items())
    return (
        "사용법: minprc {종목코드|종목명} {틱범위} {시작일} {종료일}\n"
        f"틱범위 — {scopes}\n"
        "예) minprc 005930 1 20250701 20250731\n"
        "예) minprc 삼성전자 5 2025-07-01 2025-07-31"
    )


def handleMinPrc(stkCd, ticScope, startDt, endDt):
    """
    종목·틱범위·기간 분봉(ka10080)을 조회해 AI_MIN_STK_PRC에 적재한다.
    """
    codeRaw = (stkCd or "").strip()
    scopeRaw = (ticScope or "").strip()
    startRaw = (startDt or "").strip()
    endRaw = (endDt or "").strip()

    if not codeRaw or not scopeRaw or not startRaw or not endRaw:
        return _usage()

    scope = normTicScope(scopeRaw, default=None)
    if scope is None:
        return f"지원하지 않는 틱범위입니다: {scopeRaw}\n{_usage()}"

    resolved = resolveStkRef(codeRaw)
    if not resolved.get("ok"):
        return resolved.get("errMsg")
    stkCdClean = resolved["stkCd"]
    stkLabel = formatStkLabel(stkCdClean, resolved.get("stkNm"))

    session = _pickTradeSession()
    if session is None:
        failMsg = "분봉 적재 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    ticLabel = TIC_SCOPE_LABEL.get(scope, scope)

    termLog(
        f"[minprc] ({modeLabel}) {stkLabel} {ticLabel} {startRaw}~{endRaw} 조회 시작"
    )

    fetched = fetchMinStkPrcRange(
        hostUrl,
        token,
        stkCdClean,
        startRaw,
        endRaw,
        ticScope=scope,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )

    if not fetched.get("ok"):
        failMsg = (
            f"[분봉실패] ({modeLabel}) {stkLabel}: {fetched.get('errMsg')}"
        )
        termLog(failMsg)
        return failMsg

    rows = fetched.get("rows") or []
    if not rows:
        msg = (
            f"[분봉] ({modeLabel}) {stkLabel} {ticLabel}\n"
            f"기간: {fetched.get('startDt')} ~ {fetched.get('endDt')}\n"
            f"조회 건수: 0 — 적재할 데이터 없음"
        )
        termLog(msg)
        return msg

    saved = upsertMinStkPrcRows(rows)
    if not saved.get("ok"):
        failMsg = (
            f"[분봉실패] ({modeLabel}) {stkLabel} DB: {saved.get('errMsg')}"
        )
        termLog(failMsg)
        return failMsg

    firstTm = rows[0]["CNTR_TM"]
    lastTm = rows[-1]["CNTR_TM"]
    okMsg = (
        f"[분봉] ({modeLabel}) 적재 완료\n"
        f"종목: {stkLabel}\n"
        f"틱범위: {ticLabel} ({scope})\n"
        f"요청기간: {fetched.get('startDt')} ~ {fetched.get('endDt')}\n"
        f"적재구간: {firstTm} ~ {lastTm}\n"
        f"건수: {saved.get('upsertCnt')}건 "
        f"(page={fetched.get('pageUsed')}, restart={fetched.get('restartCnt')})"
    )
    termLog(okMsg)
    return okMsg
