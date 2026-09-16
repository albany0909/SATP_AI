"""
매매창 전략 탭 문구 — 스펙·settings·실행기와 동기화.

실매매 로직이 읽는 params 와 같은 키·기본값을 사용한다.
"""

from __future__ import annotations

from strategies.specLoader import loadStratSpec
from utils.stkCdUtil import normStkCd


def fmtManWon(raw):
    """금액 → 만원 단위. 나머지가 있으면 원."""
    try:
        won = int(float(raw))
    except (TypeError, ValueError):
        return str(raw or "-")
    if won >= 10000 and won % 10000 == 0:
        return f"{won // 10000:,}만원"
    return f"{won:,}원"


def fmtHm(raw):
    """HHMMSS 또는 HHMM → HH:MM."""
    s = str(raw or "").strip()
    if len(s) >= 6:
        return f"{s[0:2]}:{s[2:4]}"
    if len(s) >= 4:
        return f"{s[0:2]}:{s[2:4]}"
    return s or "-"


def fmtHmWindow(startTm, endTm):
    """실행기와 동일: endTm 미포함 (15:20 → 15:19:59까지)."""
    return f"{fmtHm(startTm)}~{fmtHm(endTm)} (미만)"


def _maTouchReclaimLine(p):
    """매수 확인 한 줄. 3분봉 양봉 돌파 지정가 또는 틱 회복."""
    mode = str(p.get("confirmMode") or "")
    barMin = int(p.get("confirmBarMin") or 3)
    if mode == "bar3BreakMarket":
        return (
            f"터치 후 {barMin}분봉이 SMA20을 종가 돌파하고 양봉(종가>시가)이면 "
            f"그 봉 종가로 시장가 매수"
        )
    if mode in ("bar3BreakLimit", "bar3BreakBull", "bar3"):
        waitSec = int(p.get("limitWaitSec") or 600)
        waitMin = max(1, int(round(waitSec / 60.0)))
        return (
            f"터치 후 {barMin}분봉이 SMA20을 종가 돌파하고 양봉(종가>시가)이면 "
            f"SMA20 인근 호가 지정가 매수. {waitMin}분 미체결이면 취소 "
            f"(시장가 전환 없음)"
        )
    reclaimMin = float(p.get("reclaimMinPct") or 0)
    if reclaimMin > 0:
        return f"당일 현재가가 SMA20을 {reclaimMin:g}% 이상 뚫으면 매수"
    return "당일 현재가가 SMA20 위로 회복하면 매수"


def _maTouchEntryFilterLine(p):
    """시초금지·시가필터가 켜진 경우만 문구. 둘 다 꺼지면 빈 문자열."""
    banUntil = str(p.get("buyBanUntilTm") or "000000").zfill(6)[-6:]
    parts = []
    if banUntil > "090000":
        parts.append(f"{fmtHm(banUntil)} 미만 회복 매수 금지")
    if bool(p.get("banBuyIfOpenBelowSma")):
        parts.append("당일 시가≤SMA20 매수 제외")
    return " · ".join(parts)


def stratTitle(spec, fallback):
    try:
        from trading.deskLiveAlgs import stripAlgTradeSuffix

        return stripAlgTradeSuffix((spec or {}).get("name") or fallback)
    except Exception:
        return str((spec or {}).get("name") or fallback)


def _statusLabel(spec):
    stMap = {"test": "검증", "live": "실전", "off": "중지"}
    return stMap.get(str((spec or {}).get("status") or "").lower(), (spec or {}).get("status") or "-")


def _resolveOnOff(algCd, spec):
    onOff = "실행" if (spec or {}).get("enabled", True) else "중지"
    try:
        from config.settingsStore import loadSettings

        row = ((loadSettings() or {}).get("stratLive") or {}).get(algCd) or {}
        if isinstance(row, dict) and "enabled" in row:
            return "실행" if row.get("enabled") else "중지"
    except Exception:
        pass
    return onOff


def _algCounts(algCd, stratId):
    algCnt = 0
    openCnt = 0
    try:
        from utils.algStkStore import listAlgStk

        algCnt = len(listAlgStk(algCd, limit=2000) or [])
    except Exception:
        pass
    try:
        from utils.stratOrdLedgerStore import listOpenLedgerStkCds

        openCnt = len(
            [c for c in (listOpenLedgerStkCds(stratId) or []) if normStkCd(c)]
        )
    except Exception:
        pass
    return algCnt, openCnt


def _maSupportParams(spec):
    """maSupportEodLive._loadParams 와 동일 기본값."""
    p = dict((spec or {}).get("params") or {})
    p.setdefault("smaLen", 5)
    p.setdefault("weakBearMaxPct", 2.5)
    p.setdefault("dojiMaxPct", 0.5)
    p.setdefault("buyAmtWon", 2_000_000)
    p.setdefault("banSameDayBreakoutBuy", True)
    p.setdefault("volCmpBullDays", 3)
    p.setdefault("buyStartTm", "151800")
    p.setdefault("buyEndTm", "152000")
    p.setdefault("sellStartTm", "151800")
    p.setdefault("sellEndTm", "152000")
    p.setdefault("paperOnly", True)
    return p


def _intradayPollSec():
    try:
        from trading.maSupportEodLive import _INTRADAY_POLL_SEC

        return int(_INTRADAY_POLL_SEC or 12)
    except Exception:
        return 12


def _liveTrChunk():
    """폴링 1회에 조회할 종목 수 — 실행기 상수와 어긋나지 않게 읽어온다."""
    try:
        from trading.maSupportEodLive import _LIVE_TR_CHUNK

        return int(_LIVE_TR_CHUNK or 20)
    except Exception:
        return 20


def buildMaSupportDeskText(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = _maSupportParams(spec)
    smaLen = int(p.get("smaLen") or 5)
    buyWin = fmtHmWindow(p.get("buyStartTm"), p.get("buyEndTm"))
    sellWin = fmtHmWindow(p.get("sellStartTm"), p.get("sellEndTm"))
    poll = _intradayPollSec()
    paper = "모의만" if p.get("paperOnly", True) else "실전허용"
    algCnt, openCnt = _algCounts(algCd, stratId)
    univ = dict(spec.get("universe") or {})
    univNote = str(univ.get("note") or "").strip()

    lines = [
        stratTitle(spec, "이평선지지"),
        f"코드  {algCd}  v{spec.get('version') or 1}",
        f"상태  {_statusLabel(spec)} · {paper} · {_resolveOnOff(algCd, spec)}",
        f"관리종목 {algCnt} · 미청산 {openCnt}",
        "",
        "[대상선정]",
        "  장마감 후 16:00 ka10016 52주 신고가 (코스피+코스닥) 편입",
        "  종가>SMA20 유지, 종가≤SMA20 이면 관심 제외 (이평터치와 분리)",
    ]
    if univNote:
        lines.append(f"  {univNote}")
    lines.extend(
        [
            "",
            f"[매수 — 장중 {buyWin}]",
            f"  SMA{smaLen} 상향돌파 이력 후",
            f"  약음봉(≤{p.get('weakBearMaxPct', 2.5)}%) 또는 십자(≤{p.get('dojiMaxPct', 0.5)}%) + {smaLen}일선 지지 → 시장가",
            f"  당일 거래량 < 최근 {int(p.get('volCmpBullDays') or 3)}개 양봉 거래량(최솟값)",
            f"  금액  {fmtManWon(p.get('buyAmtWon'))}",
            "  돌파 당일 봉은 매수 안 함" if p.get("banSameDayBreakoutBuy", True) else "",
            "",
            f"[청산 — 장중 {sellWin}]",
            f"  현재가 < SMA{smaLen} → 전량 시장가 매도",
            "",
            "[실행]",
            f"  {paper} · 구간 내 약 {poll}초 간격 재확인",
            f"  매수 {buyWin}",
            f"  매도(SMA{smaLen} 이탈) {sellWin}",
            f"  장중 당일봉 ka10001 (폴링당 {_liveTrChunk()}종씩 나눠 조회) · 0B 구독분은 현재가 보정",
        ]
    )
    return "\n".join([x for x in lines if x is not None and x != ""])


def buildMaTouchDeskText(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = dict(spec.get("params") or {})
    live = {}
    try:
        from config.settingsStore import getMaTouchLive

        live = getMaTouchLive() or {}
    except Exception:
        live = {}
    if live.get("buyAmtByStep"):
        p["buyAmtByStep"] = live.get("buyAmtByStep")
    # v32부터 1차는 spec tp1Pct 고정. MFE 통계 오버레이는 halfTpFromStat 이 켜진 경우만
    specFromStat = bool((spec.get("params") or {}).get("halfTpFromStat", False))
    if specFromStat and live.get("halfTpFromStat") and live.get("halfTpPct") is not None:
        p["halfTpPct"] = float(live.get("halfTpPct"))
        p["halfTpFromStat"] = True
    else:
        p["halfTpFromStat"] = False
    onOff = "실행" if live.get("enabled", spec.get("enabled", True)) else "중지"
    paper = "모의만" if live.get("paperOnly", p.get("paperOnly", True)) else "실전허용"
    amts = p.get("buyAmtByStep") or {}
    cutoff = str(p.get("buyCutoffTm") or "140000")
    exitTm = str(p.get("exitAfterTm") or "151800")
    cutoffTxt = fmtHm(cutoff)
    exitTxt = fmtHm(exitTm)
    steps = p.get("maSteps") or [20]
    stopMa = int(p.get("stopMa") or 20)
    hardPct = float(p.get("hardStopPct") or 2)
    tp1Pct = float(p.get("tp1Pct") or 1.4)
    tp1Ratio = float(p.get("tp1QtyRatio") or 0.3)
    tp2Pct = float(p.get("tp2Pct") or 2)
    tp2Ratio = float(p.get("tp2QtyRatio") or 0.3)
    tp3Pct = float(p.get("tp3Pct") or 4)
    tp3Ratio = float(p.get("tp3QtyRatio") or 0.5)
    tp4Pct = float(p.get("tp4Pct") or 7)
    trailMin = float(p.get("afterHalfTrailMinPct") or 0)
    trailDd = float(p.get("afterHalfTrailTpPct") or 0)
    afterHalfStop = float(p["afterHalfStopPct"]) if p.get("afterHalfStopPct") is not None else 0.0
    afterHalfStopLab = f"-{afterHalfStop:g}%" if afterHalfStop else "0%"
    halfAddOn = bool(p.get("halfAddNextDay", False))
    halfAddMin = float(p.get("halfAddMinPnlPct") or 5)
    halfAddRatio = float(p.get("halfAddQtyRatio") or 0.5)
    nextHalfTm = fmtHm(p.get("nextClosedHalfTm") or "151500")
    nextHalfMin = float(p.get("nextClosedHalfMinPct") or 0)
    nextHalfMax = float(p.get("nextClosedHalfMaxPct") or 3)
    rsiTpMin = float(p.get("rsiTpMin") or 0)
    rsiTpPeriod = int(p.get("rsiTpPeriod") or 14)
    entryFilt = _maTouchEntryFilterLine(p)
    algCnt, openCnt = _algCounts(algCd, stratId)
    univ = dict(spec.get("universe") or {})
    rankMax = int(univ.get("rankMax") or 20)
    univMinQty = int(univ.get("minQtyPerStep") or 1)
    trdeRank = int(univ.get("maxTrdePrcaRank") or 30)
    univNote = str(univ.get("note") or "").strip()

    lines = [
        stratTitle(spec, "이평선터치"),
        f"코드  {algCd}  v{spec.get('version') or 1}",
        f"상태  {_statusLabel(spec)} · {paper} · {onOff}",
        f"관리종목 {algCnt} · 미청산 {openCnt}",
        "",
        "[대상선정]",
        "  전 거래일 16:00 조회순위(ka00198) 당일누적",
        f"  순위 상위 {rankMax}종 ∪ 거래대금(ka10032) 상위 {trdeRank}종",
        "  중복 제거 · 코스피/코스닥만",
        f"  매차 최소금액으로 {univMinQty}주 이상 (고가주 제외)",
        "  매일 전량 교체 · 원장 미청산은 유지",
        "  장중 신규 편입 없음",
        "  기존관심: 이틀 연속 음봉+종가≤SMA20 만 제외 (하루 이탈 유예)",
    ]
    if univNote:
        lines.append(f"  {univNote}")
    lines.append("")
    lines.append("[매수 — 장중 실시간]")
    ordLab = ("1차", "2차", "3차")
    for i, step in enumerate(steps[:3]):
        stepN = int(step)
        amt = amts.get(stepN) or amts.get(str(stepN))
        lab = ordLab[i] if i < len(ordLab) else f"{i + 1}차"
        lines.append(f"  {lab} SMA{stepN}  {fmtManWon(amt)}")
    buyLines = [
        "  위에서 내려와 해당 이평 터치 시 매차 1회 (청산 전까지)",
        "  터치 즉시 매수 없음 → " + _maTouchReclaimLine(p),
    ]
    if entryFilt:
        buyLines.append(f"  {entryFilt}")
    buyLines.extend(
        [
            f"  다음날 휴장일만 {cutoffTxt} 이후 신규매수 금지 (평일은 장 마감까지)",
            "  전량 청산 후 당일 재매수 금지",
            "  이평은 전일 확정 종가 (장중 고정)",
            "",
            "[익절]",
        ]
    )
    if rsiTpMin > 0:
        buyLines.append(
            f"  수익 중 RSI{rsiTpPeriod} ≥ {rsiTpMin:g} → 즉시 전량"
        )
    buyLines.append(
        f"  1차 평단 +{tp1Pct:g}% → 보유 {tp1Ratio*100:.0f}%"
    )
    buyLines.append(
        f"  2차 평단 +{tp2Pct:g}% → 남은 보유 {tp2Ratio*100:.0f}%"
    )
    if tp3Pct > 0:
        buyLines.append(
            f"  3차 2차 후 잔량 +{tp3Pct:g}% → 보유 {tp3Ratio*100:.0f}%"
        )
    if tp4Pct > 0:
        buyLines.append(
            f"  4차 3차 후 잔량 +{tp4Pct:g}% → 전량"
        )
    if trailMin > 0 and trailDd > 0:
        buyLines.append(
            f"  잔량 고점 +{trailMin:g}% 이후 고점 대비 -{trailDd:g}%p → 전량 "
            f"(3차 후·4차와 먼저 충족)"
        )
    if halfAddOn:
        buyLines.append(
            f"  2차 다음날 잔량 +{halfAddMin:g}% 이상 → 잔량의 {halfAddRatio*100:.0f}% 추가"
        )
    buyLines.extend(
        [
            f"  매일 {nextHalfTm}부터 평단 {nextHalfMin:g}% 이상 {nextHalfMax:g}% 이하 → 전량 (손실·{nextHalfMax:g}% 초과는 보유)",
            "",
            "[손절]",
            f"  {exitTxt} 이후 당일 매수 · 현재가≤평단 → 전량 (미수익 오버나잇 금지)",
            f"  {exitTxt} 이후 전일 이전 진입 · 현재가 < SMA{stopMa} → 전량",
            f"  하드스탑 계좌평단 -{hardPct:g}% (1·2차 전) · 1·2차 후 잔량 평단 {afterHalfStopLab}",
            f"  약세조기  ≥{int(p.get('weakHoldMin') or 90)}분·고점<{float(p.get('weakPeakMaxPct') or 1):g}%·평가≤{float(p.get('weakExitMaxPnlPct') or -0.5):g}%·1차(현 tp1Pct)미달 → 전량",
            "",
            "[실행]",
            f"  {paper} · 장중 0B 실시간 (watchRtMonitor)",
            f"  익절·손절 참고 시각 {exitTxt} 이후",
        ]
    )
    lines.extend(buyLines)
    return "\n".join(lines)


def _joinRule(parts):
    """ruleRows v 필드용 — 빈 항목 제거 후 공백 연결."""
    return " ".join(str(x).strip() for x in parts if str(x or "").strip())


def buildMaSupportRuleRows(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = _maSupportParams(spec)
    smaLen = int(p.get("smaLen") or 5)
    buyWin = fmtHmWindow(p.get("buyStartTm"), p.get("buyEndTm"))
    sellWin = fmtHmWindow(p.get("sellStartTm"), p.get("sellEndTm"))
    poll = _intradayPollSec()
    paper = "모의만" if p.get("paperOnly", True) else "실전허용"
    univ = dict(spec.get("universe") or {})
    univNote = str(univ.get("note") or "").strip()
    univTxt = _joinRule(
        [
            "장마감 후 16:00 ka10016 52주 신고가(코스피+코스닥) 편입",
            "종가>SMA20 유지 · 종가≤SMA20 제외 (이평터치와 분리)",
            univNote,
        ]
    )
    buyTxt = _joinRule(
        [
            f"장중 {buyWin}",
            f"SMA{smaLen} 상향돌파 이력 후",
            f"약음봉(≤{p.get('weakBearMaxPct', 2.5)}%) 또는 십자(≤{p.get('dojiMaxPct', 0.5)}%) + {smaLen}일선 지지 → 시장가",
            f"당일 거래량 < 최근 {int(p.get('volCmpBullDays') or 3)}개 양봉 거래량(최솟값)",
            f"금액 {fmtManWon(p.get('buyAmtWon'))}",
            "돌파 당일 봉은 매수 안 함" if p.get("banSameDayBreakoutBuy", True) else "",
        ]
    )
    sellTxt = _joinRule(
        [
            f"장중 {sellWin}",
            f"현재가 < SMA{smaLen} → 전량 시장가 매도",
        ]
    )
    execTxt = _joinRule(
        [
            paper,
            f"구간 내 약 {poll}초 간격 재확인",
            f"매수 {buyWin}",
            f"매도(SMA{smaLen} 이탈) {sellWin}",
            f"장중 당일봉 ka10001 (폴링당 {_liveTrChunk()}종씩 나눠 조회) · 0B 구독분은 현재가 보정",
        ]
    )
    return [
        {"k": "대상", "v": univTxt},
        {"k": "매수", "v": buyTxt},
        {"k": "청산", "v": sellTxt},
        {"k": "실행", "v": execTxt},
    ]


def buildMaTouchRuleRows(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = dict(spec.get("params") or {})
    live = {}
    try:
        from config.settingsStore import getMaTouchLive

        live = getMaTouchLive() or {}
    except Exception:
        live = {}
    if live.get("buyAmtByStep"):
        p["buyAmtByStep"] = live.get("buyAmtByStep")
    specFromStat = bool((spec.get("params") or {}).get("halfTpFromStat", False))
    if specFromStat and live.get("halfTpFromStat") and live.get("halfTpPct") is not None:
        p["halfTpPct"] = float(live.get("halfTpPct"))
        p["halfTpFromStat"] = True
    else:
        p["halfTpFromStat"] = False
    paper = "모의만" if live.get("paperOnly", p.get("paperOnly", True)) else "실전허용"
    cutoff = str(p.get("buyCutoffTm") or "140000")
    exitTm = str(p.get("exitAfterTm") or "151800")
    cutoffTxt = fmtHm(cutoff)
    exitTxt = fmtHm(exitTm)
    steps = p.get("maSteps") or [20]
    stopMa = int(p.get("stopMa") or 20)
    hardPct = float(p.get("hardStopPct") or 2)
    tp1Pct = float(p.get("tp1Pct") or 1.4)
    tp1Ratio = float(p.get("tp1QtyRatio") or 0.3)
    tp2Pct = float(p.get("tp2Pct") or 2)
    tp2Ratio = float(p.get("tp2QtyRatio") or 0.3)
    tp3Pct = float(p.get("tp3Pct") or 4)
    tp3Ratio = float(p.get("tp3QtyRatio") or 0.5)
    tp4Pct = float(p.get("tp4Pct") or 7)
    trailMin = float(p.get("afterHalfTrailMinPct") or 0)
    trailDd = float(p.get("afterHalfTrailTpPct") or 0)
    afterHalfStop = float(p["afterHalfStopPct"]) if p.get("afterHalfStopPct") is not None else 0.0
    afterHalfStopLab = f"-{afterHalfStop:g}%" if afterHalfStop else "0%"
    halfAddOn = bool(p.get("halfAddNextDay", False))
    halfAddMin = float(p.get("halfAddMinPnlPct") or 5)
    nextHalfTm = fmtHm(p.get("nextClosedHalfTm") or "151500")
    nextHalfMin = float(p.get("nextClosedHalfMinPct") or 0)
    nextHalfMax = float(p.get("nextClosedHalfMaxPct") or 3)
    rsiTpMin = float(p.get("rsiTpMin") or 0)
    rsiTpPeriod = int(p.get("rsiTpPeriod") or 14)
    entryFilt = _maTouchEntryFilterLine(p)
    univ = dict(spec.get("universe") or {})
    rankMax = int(univ.get("rankMax") or 20)
    univMinQty = int(univ.get("minQtyPerStep") or 1)
    trdeRank = int(univ.get("maxTrdePrcaRank") or 30)
    univNote = str(univ.get("note") or "").strip()
    stepTxt = ", ".join(f"SMA{int(s)}" for s in steps[:3])
    univTxt = _joinRule(
        [
            "전 거래일 16:00 조회순위(ka00198) 당일누적",
            f"순위 상위 {rankMax}종 ∪ 거래대금(ka10032) 상위 {trdeRank}종",
            "중복 제거 · 코스피/코스닥만",
            f"매차 최소금액으로 {univMinQty}주 이상",
            "매일 전량 교체 · 원장 미청산 유지 · 장중 신규 편입 없음",
            "기존관심: 이틀 연속 음봉+종가≤SMA20 만 제외",
            univNote,
        ]
    )
    buyTxt = _joinRule(
        [
            f"{stepTxt} 터치 후 {_maTouchReclaimLine(p)} (터치 즉시 매수 없음)",
            entryFilt,
            f"다음날 휴장일만 {cutoffTxt} 이후 신규매수 금지",
            "전량 청산 후 당일 재매수 금지",
            "이평은 전일 확정 종가 (장중 고정)",
        ]
    )
    halfParts = []
    if rsiTpMin > 0:
        halfParts.append(
            f"수익 중 RSI{rsiTpPeriod} ≥ {rsiTpMin:g} → 즉시 전량"
        )
    halfParts.append(f"1차 평단 +{tp1Pct:g}% → 보유 {tp1Ratio*100:.0f}%")
    halfParts.append(f"2차 평단 +{tp2Pct:g}% → 남은 보유 {tp2Ratio*100:.0f}%")
    if tp3Pct > 0:
        halfParts.append(
            f"3차 2차 후 잔량 +{tp3Pct:g}% → 보유 {tp3Ratio*100:.0f}%"
        )
    if tp4Pct > 0:
        halfParts.append(f"4차 3차 후 잔량 +{tp4Pct:g}% → 전량")
    if trailMin > 0 and trailDd > 0:
        halfParts.append(
            f"잔량 고점 +{trailMin:g}% 이후 고점 대비 -{trailDd:g}%p → 전량 "
            f"(3차 후·4차와 먼저 충족)"
        )
    if halfAddOn:
        halfParts.append(
            f"2차 다음날 잔량 +{halfAddMin:g}% 이상 → 잔량의 50% 추가"
        )
    halfParts.extend(
        [
            f"매일 {nextHalfTm}부터 평단 {nextHalfMin:g}% 이상 {nextHalfMax:g}% 이하 → 전량 (손실·{nextHalfMax:g}% 초과는 보유)",
        ]
    )
    halfTxt = _joinRule(halfParts)
    stopTxt = _joinRule(
        [
            f"{exitTxt} 이후 당일 매수·현재가≤평단 → 전량 (미수익 오버나잇 금지)",
            f"{exitTxt} 이후 전일 이전 진입 · 현재가 < SMA{stopMa} → 전량",
            f"하드스탑 계좌평단 -{hardPct:g}% (1·2차 전) · 1·2차 후 잔량 평단 {afterHalfStopLab}",
            f"약세조기 ≥{int(p.get('weakHoldMin') or 90)}분·고점<{float(p.get('weakPeakMaxPct') or 1):g}%·평가≤{float(p.get('weakExitMaxPnlPct') or -0.5):g}%·1차(현 tp1Pct)미달 → 전량",
        ]
    )
    execTxt = _joinRule(
        [
            paper,
            "장중 0B 실시간 (watchRtMonitor)",
            f"익절·손절 참고 시각 {exitTxt} 이후",
        ]
    )
    return [
        {"k": "대상", "v": univTxt},
        {"k": "매수", "v": buyTxt},
        {"k": "익절", "v": halfTxt},
        {"k": "손절", "v": stopTxt},
        {"k": "실행", "v": execTxt},
    ]


def buildGenericRuleRows(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = dict(spec.get("params") or {})
    rules = spec.get("rules") or {}
    uni = spec.get("universe") or {}
    rows = []
    if uni.get("note"):
        rows.append({"k": "대상", "v": str(uni.get("note"))})
    labels = {
        "universe": "대상",
        "entry": "매수",
        "halfTp": "익절",
        "afterHalf": "2차 후",
        "stop": "손절",
        "takeProfit": "익절",
        "trend": "추세",
        "pullback": "눌림",
        "breakout": "돌파",
        "exit": "청산",
        "reset": "리셋",
    }
    for key, lab in labels.items():
        val = rules.get(key)
        if val:
            rows.append({"k": lab, "v": str(val)})
    notes = []
    if p.get("maSteps"):
        notes.append("이평 " + str(p.get("maSteps")))
    if p.get("buyCutoffTm") or p.get("exitAfterTm"):
        notes.append(
            f"매수마감 {p.get('buyCutoffTm') or '-'} / "
            f"청산허용 {p.get('exitAfterTm') or '-'}"
        )
    if p.get("hardStopPct") is not None:
        notes.append(f"하드스탑 -{float(p.get('hardStopPct')):g}%")
    if notes:
        rows.append({"k": "참고", "v": " · ".join(notes)})
    specNotes = str(spec.get("notes") or "").strip()
    if specNotes:
        rows.append({"k": "메모", "v": specNotes})
    return rows


def buildStratRuleRows(algCd, stratId):
    """웹 알고리즘 설정·규칙 테이블용 [{k,v}, ...]."""
    sid = str(stratId or "")
    if sid.startswith("maSupportEod"):
        return buildMaSupportRuleRows(algCd, stratId)
    if sid.startswith("maTouchScale") or str(algCd) == "MTS01":
        return buildMaTouchRuleRows(algCd, stratId)
    return buildGenericRuleRows(algCd, stratId)


def buildStratValidateNote(stratId):
    """정적 검증 notes — ruleRows 요약 한 줄."""
    try:
        from strategies.specLoader import resolveAlgRef

        got = resolveAlgRef(stratId)
        alg = got.get("algCd") if got.get("ok") else ""
    except Exception:
        alg = ""
    rows = buildStratRuleRows(alg, stratId)
    if not rows:
        return ""
    return " / ".join(f"{r.get('k')}: {r.get('v')}" for r in rows[:5])


def buildStratRunnerNote(algCd, stratId):
    """운영 배너 한 줄 (웹·하단 칩 보조)."""
    sid = str(stratId or "")
    if sid.startswith("maSupportEod"):
        spec = loadStratSpec(stratId) or {}
        p = _maSupportParams(spec)
        buyWin = fmtHmWindow(p.get("buyStartTm"), p.get("buyEndTm"))
        paper = "모의" if p.get("paperOnly", True) else "실전"
        return f"{buyWin} 장중 · {paper} 이평지지"
    if sid.startswith("maTouchScale") or str(algCd) == "MTS01":
        live = {}
        try:
            from config.settingsStore import getMaTouchLive

            live = getMaTouchLive() or {}
        except Exception:
            live = {}
        paper = "모의" if live.get("paperOnly", True) else "실전"
        return f"0B 실시간 · {paper} 이평터치"
    return ""


def buildStratDeskText(algCd, stratId):
    """알고리즘·스펙에 맞는 전략 탭 본문."""
    sid = str(stratId or "")
    if sid.startswith("maSupportEod"):
        return buildMaSupportDeskText(algCd, stratId)
    if sid.startswith("maTouchScale") or str(algCd) == "MTS01":
        return buildMaTouchDeskText(algCd, stratId)
    return buildGenericDeskText(algCd, stratId)


def buildGenericDeskText(algCd, stratId):
    spec = loadStratSpec(stratId) or {}
    p = dict(spec.get("params") or {})
    lines = [
        stratTitle(spec, algCd),
        f"코드  {algCd}  v{spec.get('version') or 1}",
        f"상태  {_statusLabel(spec)} · {'실행' if spec.get('enabled', True) else '중지'}",
        f"파일  {stratId or '-'}",
        "",
        "[파라미터]",
    ]
    if p:
        for k in sorted(p.keys()):
            lines.append(f"  {k}  {p[k]}")
    else:
        lines.append("  (없음)")
    notes = spec.get("notes") or ""
    if notes:
        lines.extend(["", "[메모]", f"  {notes}"])
    return "\n".join(lines)


def maTouchDeskHtml(text):
    """매매창 전략탭 — 1차 수익률 (통계치) 만 빨간색."""
    import html
    import re

    esc = html.escape(str(text or "")).replace("\n", "<br>")
    esc = re.sub(
        r"1차 수익률\s+평단 \+[0-9.]+% \(통계치\)",
        lambda m: f'<span style="color:#c62828;font-weight:700">{m.group(0)}</span>',
        esc,
    )
    return (
        '<div style="font-family:Consolas,\'Malgun Gothic\',sans-serif;'
        'white-space:pre-wrap;line-height:1.45;">'
        f"{esc}</div>"
    )
