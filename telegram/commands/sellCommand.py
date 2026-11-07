"""sell {종목코드|종목명} {수량} [지정가] — kt10001 주식 매도주문 명령"""

from api.sellStk import placeSellOrd
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog

TRDE_TP_LIMIT = "0"
TRDE_TP_MARKET = "3"


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


def _isPosInt(text):
    if text is None or str(text).strip() == "":
        return False
    raw = str(text).strip().replace(",", "")
    if not raw.isdigit():
        return False
    return int(raw) > 0


def handleSell(stkCd, ordQty, limitPrice=None):
    usage = (
        "사용법:\n"
        "sell {종목코드|종목명} {수량} — 시장가 매도\n"
        "sell {종목코드|종목명} {수량} {지정가} — 지정가 매도\n"
        "예) sell 005930 1 / sell 삼성전자 1"
    )

    codeRaw = (stkCd or "").strip()
    qtyRaw = (ordQty or "").strip() if ordQty is not None else ""
    priceRaw = (limitPrice or "").strip() if limitPrice is not None else ""

    if not codeRaw or not qtyRaw:
        return usage

    if not _isPosInt(qtyRaw):
        return f"수량은 1 이상의 정수여야 합니다.\n\n{usage}"

    isLimit = bool(priceRaw)
    if isLimit and not _isPosInt(priceRaw):
        return f"지정가는 1 이상의 정수(원)여야 합니다.\n\n{usage}"

    resolved = resolveStkRef(codeRaw)
    if not resolved.get("ok"):
        return resolved.get("errMsg") or usage

    stkCdClean = resolved["stkCd"]
    dmstStexTp = resolved.get("dmstStexTp") or "KRX"
    stkLabel = formatStkLabel(stkCdClean, resolved.get("stkNm"))

    session = _pickTradeSession()
    if session is None:
        failMsg = "매도 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"

    if isLimit:
        trdeTp = TRDE_TP_LIMIT
        ordUv = priceRaw
        ordTypeLabel = "지정가"
    else:
        trdeTp = TRDE_TP_MARKET
        ordUv = ""
        ordTypeLabel = "시장가"

    try:
        from trading.realAcntMgr import acntOrderBlockReason

        if acntOrderBlockReason(stkCdClean):
            failMsg = f"실계좌 미등록 보유 — 매도 생략 {stkCdClean}"
            termLog(failMsg)
            return failMsg
    except Exception:
        pass

    result = placeSellOrd(
        hostUrl,
        token,
        dmstStexTp,
        stkCdClean,
        qtyRaw,
        trdeTp,
        ordUv=ordUv,
        tokenType=tokenType,
    )

    if not result.get("ok"):
        failMsg = (
            f"[매도실패] ({modeLabel}) {stkLabel} "
            f"{qtyRaw}주 {ordTypeLabel}: {result.get('errMsg')}"
        )
        termLog(failMsg)
        return failMsg

    ordNo = result.get("ordNo") or "-"
    priceText = f"{ordUv}원" if isLimit else "시장가"
    okMsg = (
        f"[매도주문] ({modeLabel}) 접수 완료\n"
        f"종목: {stkLabel} / 거래소: {dmstStexTp}\n"
        f"구분: {ordTypeLabel} / 수량: {qtyRaw}주 / 단가: {priceText}\n"
        f"주문번호: {ordNo}"
    )
    termLog(okMsg)
    return okMsg
