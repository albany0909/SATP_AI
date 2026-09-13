"""sma {종목코드} — ka10081 일봉 기반 이동평균(5~240) 조회"""

from datetime import datetime

from api.getDayChart import MIN_BARS_FOR_SMA240, fetchDayBars
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.smaUtil import SMA_PERIODS, barsToClosePrices, calcSmaSet, formatSmaVal
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog


def _pickQuerySession():
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


def handleSma(stkCd):
    """
    수정주가 일봉을 모아 SMA 5/10/20/60/120/240을 계산해 반환한다.
    종목코드 또는 종목명 입력 가능.
    """
    raw = (stkCd or "").strip()
    if not raw:
        return "사용법: sma {종목코드|종목명} 예) sma 005930 / sma 삼성전자"

    resolved = resolveStkRef(raw)
    if not resolved.get("ok"):
        return resolved.get("errMsg")
    code = resolved["stkCd"]
    stkLabel = formatStkLabel(code, resolved.get("stkNm"))

    session = _pickQuerySession()
    if session is None:
        failMsg = "이동평균 조회 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    baseDt = datetime.now().strftime("%Y%m%d")

    termLog(f"[SMA] ({modeLabel}) {stkLabel} 일봉 수집 중… (수정주가, 최소 {MIN_BARS_FOR_SMA240}봉)")
    result = fetchDayBars(
        hostUrl,
        token,
        code,
        baseDt,
        minBars=MIN_BARS_FOR_SMA240,
        updStkpcTp="1",
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    if not result.get("ok"):
        failMsg = f"[SMA] 조회 실패 ({stkLabel}): {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    bars = result.get("bars") or []
    closes = barsToClosePrices(bars)
    smaMap = calcSmaSet(closes, SMA_PERIODS)
    latestPrc = closes[0] if closes else None
    latestDt = bars[0].get("dt") if bars else "-"

    lines = [
        f"[이동평균] ({modeLabel}) {stkLabel}",
        f"기준일: {latestDt} / 종가: {formatSmaVal(latestPrc)}",
        f"수집봉: {len(closes)}일 (수정주가)",
        "",
    ]
    for n in SMA_PERIODS:
        val = smaMap.get(n)
        note = "" if val is not None else " (데이터부족)"
        lines.append(f"SMA{n}: {formatSmaVal(val)}{note}")

    if len(closes) < MIN_BARS_FOR_SMA240:
        lines.append("")
        lines.append(
            f"※ SMA240에는 {MIN_BARS_FOR_SMA240}봉 필요 "
            f"(현재 {len(closes)}봉)"
        )

    msg = "\n".join(lines)
    termLog(msg)
    return msg
