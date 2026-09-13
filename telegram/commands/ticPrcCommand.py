"""ticprc {종목} {틱범위} [시작일] [종료일] — ka10079 → AI_TIC_STK_PRC"""

from api.getTicStkPrc import TIC_SCOPE_LABEL, fetchTicStkPrc, normTicScopeTick
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog
from utils.ticStkPrcStore import upsertTicStkPrcRows


def _pickTradeSession():
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
        "사용법: ticprc {종목코드|종목명} {틱범위} [시작일] [종료일]\n"
        f"틱범위 — {scopes} (분봉 minprc 와 다름)\n"
        "예) ticprc 005930 1\n"
        "예) ticprc 삼성전자 1 20250731 20250731\n"
        "※ ka10079는 base_dt 없음 — 최신부터 연속조회 후 일자 필터"
    )


def handleTicPrc(stkCd, ticScope, startDt="", endDt=""):
    """틱차트 조회 후 AI_TIC_STK_PRC 적재."""
    codeRaw = (stkCd or "").strip()
    scopeRaw = (ticScope or "").strip()
    startRaw = (startDt or "").strip()
    endRaw = (endDt or "").strip()

    if not codeRaw or not scopeRaw:
        return _usage()

    # 시작일만 있고 종료일 없으면 시작일=종료일
    if startRaw and not endRaw:
        endRaw = startRaw

    scope = normTicScopeTick(scopeRaw, default=None)
    if scope is None:
        return f"지원하지 않는 틱범위입니다: {scopeRaw}\n{_usage()}"

    resolved = resolveStkRef(codeRaw)
    if not resolved.get("ok"):
        return resolved.get("errMsg")
    stkCdClean = resolved["stkCd"]
    stkLabel = formatStkLabel(stkCdClean, resolved.get("stkNm"))

    session = _pickTradeSession()
    if session is None:
        failMsg = "틱 적재 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    ticLabel = TIC_SCOPE_LABEL.get(scope, scope)

    termLog(
        f"[ticprc] ({modeLabel}) {stkLabel} {ticLabel} "
        f"{startRaw or '-'}~{endRaw or '-'} 조회 시작"
    )

    fetched = fetchTicStkPrc(
        hostUrl,
        token,
        stkCdClean,
        ticScope=scope,
        startDt=startRaw or None,
        endDt=endRaw or None,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )

    if not fetched.get("ok"):
        failMsg = f"[틱실패] ({modeLabel}) {stkLabel}: {fetched.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    rows = fetched.get("rows") or []
    if not rows:
        msg = (
            f"[틱] ({modeLabel}) {stkLabel} {ticLabel}\n"
            f"조회 건수: 0 — 적재할 데이터 없음"
        )
        termLog(msg)
        return msg

    saved = upsertTicStkPrcRows(rows)
    if not saved.get("ok"):
        failMsg = f"[틱실패] ({modeLabel}) {stkLabel} DB: {saved.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    firstTm = rows[0]["CNTR_TM"]
    lastTm = rows[-1]["CNTR_TM"]
    okMsg = (
        f"[틱] ({modeLabel}) 적재 완료\n"
        f"종목: {stkLabel}\n"
        f"틱범위: {ticLabel} ({scope})\n"
        f"적재구간: {firstTm} ~ {lastTm}\n"
        f"건수: {saved.get('upsertCnt')}건 (page={fetched.get('pageUsed')})"
    )
    termLog(okMsg)
    return okMsg
