"""report / r — kt00004 계좌평가현황(자금·보유종목) 조회 명령"""

from api.getAcntEvlt import fetchAcntEvlt, fmtAmt, fmtPct
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog


def _pickQuerySession():
    """
    조회용 세션 — settings deskTradeMode 우선.
    반환: (mode, hostUrl, token, tokenType) 또는 None
    """
    try:
        from utils.tradeSession import getDeskTradeMode, getOrdSession

        mode = getDeskTradeMode()
        sess = getOrdSession(paperOnly=False)
        if sess is not None:
            hostUrl, token, tokenType = sess
            return (mode, hostUrl, token, tokenType)
    except Exception:
        pass
    # 폴백: 모의 → 실전
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


def formatReportMsg(body, modeLabel, acctNo=""):
    """
    kt00004 응답을 텔레그램용 자금현황+보유종목 텍스트로 만든다.
    (전체 필드를 나열하면 메시지가 과도하게 길어지므로 핵심만 표시)
    """
    from utils.acntPnlDayStore import formatPnlLine, upsertAcntPnlDay

    pnlSnap = {}
    try:
        mode = "paper" if "모의" in str(modeLabel) else "real"
        pnlSnap = upsertAcntPnlDay(body, mode=mode) or {}
    except Exception as err:
        termLog(f"[report] 계좌손익 적재 실패: {err}")
        pnlSnap = {}
    if pnlSnap.get("ok"):
        tdyLine = formatPnlLine("당일손익", pnlSnap.get("tdyPnl"), pnlSnap.get("tdyRt"))
        monLine = formatPnlLine("월간손익", pnlSnap.get("monPnl"), pnlSnap.get("monRt"))
        yearLine = formatPnlLine("년간손익", pnlSnap.get("yearPnl"), pnlSnap.get("yearRt"))
    else:
        tdyLine = (
            f"당일손익: {fmtAmt(body.get('tdy_lspft'))}원 "
            f"({fmtPct(body.get('tdy_lspft_rt'))}%)"
        )
        monLine = (
            f"월간손익: {fmtAmt(body.get('lspft2'))}원 "
            f"({fmtPct(body.get('lspft_ratio'))}%)"
        )
        yearLine = (
            f"년간손익: {fmtAmt(body.get('lspft'))}원 "
            f"({fmtPct(body.get('lspft_rt'))}%)"
        )

    acntNm = str(body.get("acnt_nm") or "-").strip()
    brchNm = str(body.get("brch_nm") or "-").strip()
    acctDisp = str(acctNo or "").strip() or "-"
    lines = [
        f"[{modeLabel}] 계좌현황",
        f"계좌번호: {acctDisp}",
        f"계좌: {acntNm} / {brchNm}",
        f"예수금: {fmtAmt(body.get('entr'))}원",
        f"D+2추정예수금: {fmtAmt(body.get('d2_entra'))}원",
        f"유가잔고평가액: {fmtAmt(body.get('tot_est_amt'))}원",
        f"예탁자산평가액: {fmtAmt(body.get('aset_evlt_amt'))}원",
        f"총매입금액: {fmtAmt(body.get('tot_pur_amt'))}원",
        f"추정예탁자산: {fmtAmt(body.get('prsm_dpst_aset_amt'))}원",
        tdyLine,
        monLine,
        yearLine,
        "",
        "[보유종목]",
    ]

    holdings = body.get("stk_acnt_evlt_prst") or []
    if not isinstance(holdings, list) or not holdings:
        lines.append("보유종목 없음")
        return "\n".join(lines)

    firstBuyMap = {}
    fmtBuyDt = None
    try:
        from utils.stratOrdLedgerStore import fmtYmdDash, openFirstBuyDtMap

        firstBuyMap = openFirstBuyDtMap() or {}
        fmtBuyDt = fmtYmdDash
    except Exception as err:
        termLog(f"[report] 최초매수일 조회 실패: {err}")

    for idx, item in enumerate(holdings, start=1):
        if not isinstance(item, dict):
            continue
        stkNm = str(item.get("stk_nm") or "-").strip()
        stkCd = normStkCd(item.get("stk_cd")) or "-"
        lines.append(f"{idx}. {stkNm} ({stkCd})")
        lines.append(
            f"   수량 {fmtAmt(item.get('rmnd_qty'))}주 / "
            f"평단 {fmtAmt(item.get('avg_prc'))} / "
            f"현재가 {fmtAmt(item.get('cur_prc'))}"
        )
        firstTxt = fmtBuyDt(firstBuyMap.get(stkCd)) if fmtBuyDt else ""
        firstTxt = firstTxt or "-"
        lines.append(
            f"   평가 {fmtAmt(item.get('evlt_amt'))}원 / "
            f"손익 {fmtAmt(item.get('pl_amt'))}원 "
            f"({fmtPct(item.get('pl_rt'))}%) / "
            f"최초매수 {firstTxt}"
        )

    return "\n".join(lines)


def handleReport():
    """
    kt00004로 자금현황·보유종목을 조회해 결과 메시지를 반환한다.
    """
    session = _pickQuerySession()
    if session is None:
        failMsg = "조회 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실전투자"

    # 계좌번호(ka00001) — 실패해도 평가현황은 계속
    acctLine = ""
    try:
        from api.getAcntNo import fetchAcntNo

        noRes = fetchAcntNo(hostUrl, token, tokenType=tokenType)
        if noRes.get("ok"):
            acctLine = str(noRes.get("acctNo") or "").strip()
        if mode == "paper":
            import time

            time.sleep(1.1)
    except Exception as err:
        termLog(f"[report] 계좌번호 조회 실패: {err}")

    # qry_tp=1: 상장폐지 제외 — 일반 보유 현황 조회에 적합
    result = fetchAcntEvlt(
        hostUrl, token, qryTp="1", dmstStexTp="KRX", tokenType=tokenType
    )
    if not result.get("ok"):
        failMsg = f"[계좌현황] 조회 실패: {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    msg = formatReportMsg(result["body"], modeLabel, acctNo=acctLine)
    termLog(msg)
    return msg
