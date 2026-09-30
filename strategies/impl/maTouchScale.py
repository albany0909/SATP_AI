"""
이평선터치 매매 (maTouchScale_v1)

- 전일 확정 일봉 SMA20 터치 후, **당일** 3분봉이 SMA20을 종가 돌파하고
  그 봉이 양봉(종가>시가)으로 끝나면 SMA20 **인근 호가 지정가** 매수.
  **10분** 안에 안 체결되면 취소만 (시장가 전환 없음).
  confirmMode=bar3BreakMarket 이면 돌파 봉 종가 시장가
- 터치 즉시 매수하지 않음. 확인 대기 중 날짜가 바뀌면 소멸. 14:00 컷은 다음날 휴장일만
- confirmMode=reclaim 이면 예전처럼 당일 현재가>SMA20 틱/봉 회복 매수
- 1차: 평단 +1.5% 도달 시 보유비중의 30% 익절 / 2차: +2% 도달 시 보유 30% 익절
- 3차: 2차 후 잔량 +4% 이상이면 보유 50% / 4차: +7% 이상이면 잔량 전량
- 수익 중 RSI14 >= rsiTpMin(기본 70) 이면 잔량 전량 (부분익절보다 앞)
- 하드 -hardStopPct(기본 1.5%)는 **1·2차 전**만. **1차 또는 2차 이후** 잔량은 평단 -afterHalfStopPct(기본 0%, 본전) 이하면 전량
- 시초 매수금지·회복 +N%·시가≤SMA20 제외·다음날 추가는 스펙이 켜진 경우만

- 당일 매수: 15:18 이후 평단 이하(미수익)면 전량. 오버나잇 시초 갭을 막기 위함
- 매일 15:15부터 평단 0% 이상 3% 이하면 전량 (손실은 제외, 3% 초과는 보유). 14:00 매수컷만 다음날 휴장일
- 익일 보유: 15:18 이후 SMA20 손절
- 전량 청산 후 당일 재매수 금지(banRebuySameDay)
- 1분봉 BT: 14:00(다음날 휴장일만)·15:18 청산을 봉 시각으로 적용. 1분봉을 3분봉으로 합침
"""

from datetime import datetime

from strategies.indicators import calcRsiSeries, calcSmaSeries
from utils.krxTick import floorToKrxTick


def inNextClosedHalfBand(px, avgPx, minPct=0.0, maxPct=4.0):
    """평단 대비 수익률이 [minPct, maxPct] 이면 True. 매일 15:15 약수익 전량(손실 제외)."""
    try:
        avg = float(avgPx or 0)
        cur = float(px or 0)
    except (TypeError, ValueError):
        return False
    if avg <= 0 or cur <= 0:
        return False
    pnlPct = (cur - avg) / avg * 100.0
    return float(minPct) <= pnlPct <= float(maxPct)


def scaleOutQty(qtyNow, ratio):
    """
    보유비중의 ratio 만큼 판다. 최소 1주, 전량은 남기지 않는다.
    1주만 있으면 0 (부분익절 생략).
    """
    try:
        q = int(qtyNow or 0)
        r = float(ratio or 0)
    except (TypeError, ValueError):
        return 0
    if q < 2 or r <= 0:
        return 0
    sell = int(q * r)
    if sell < 1:
        sell = 1
    if sell >= q:
        sell = q - 1
    return sell


def buyCutoffApplies(tradeDt, params=None):
    """
    14:00 매수금지 적용 여부.
    기본은 다음날(달력 +1일) 장이 안 열리는 날만 True (금·연휴 전날).
    """
    p = params or {}
    if not bool(p.get("buyCutoffIfNextClosed", True)):
        return True
    try:
        from config.krHoliday import isNextDayClosed

        return bool(isNextDayClosed(tradeDt))
    except Exception:
        # 휴일표를 못 읽으면 금요일만 컷 (주말은 장이 없음)
        try:
            day = datetime.strptime(str(tradeDt).replace("-", "")[:8], "%Y%m%d")
            return day.weekday() == 4
        except ValueError:
            return False

ST_IDLE = "IDLE"
ST_IN_POS = "IN_POSITION"

_DEFAULT_STEPS = [20]
_DEFAULT_AMT_BY_STEP = {20: 1_000_000}
# 정규장만 시뮬 (1분봉에 시간외가 섞여 있을 수 있음)
_SESS_OPEN_TM = "090000"
_SESS_CLOSE_TM = "153000"
_SEQ_AMT_KEYS = ("buyAmt5", "buyAmt10", "buyAmt15")
_LEGACY_STEP_MAP = {5: 7, 10: 13, 15: 20}


def normalizeFilledSteps(filled, maSteps=None):
    """구 매차 5/10/15·7/13 를 현재 maSteps 로 맞춰 재매수를 막는다."""
    steps = [int(s) for s in (maSteps or _DEFAULT_STEPS)]
    out = []
    hadAny = False
    for s in filled or []:
        try:
            n = int(s)
        except (TypeError, ValueError):
            continue
        hadAny = True
        n = _LEGACY_STEP_MAP.get(n, n)
        if n in steps and n not in out:
            out.append(n)
    # SMA20 단일 매차로 전환된 뒤에도 구 매차 잔량이 있으면 20을 채운 것으로 본다
    if hadAny and not out and steps == [20]:
        return [20]
    return out


def bar3BucketTm(hms):
    """HHMMSS → 3분봉 시작시각. 09:04:01 → 090300."""
    text = str(hms or "000000").replace(":", "").zfill(6)[-6:]
    try:
        hour = int(text[0:2])
        minute = int(text[2:4])
    except ValueError:
        return "000000"
    startMin = (minute // 3) * 3
    return f"{hour:02d}{startMin:02d}00"


def bar3EndTm(hms):
    """3분봉 종료시각(다음 버킷 시작). 090300 → 090600."""
    start = bar3BucketTm(hms)
    hour = int(start[0:2])
    minute = int(start[2:4])
    tot = hour * 60 + minute + 3
    return f"{tot // 60:02d}{tot % 60:02d}00"


def isBar3CloseMin(hms):
    """1분봉 시각이 3분봉의 마지막 분인지 (02, 05, 08, ...)."""
    text = str(hms or "000000").replace(":", "").zfill(6)[-6:]
    try:
        minute = int(text[2:4])
    except ValueError:
        return False
    return (minute % 3) == 2


def confirmModeIsBar3(params):
    """터치 후 3분봉 양봉 돌파 확인 모드인지 (시장가·지정가 공통)."""
    mode = str((params or {}).get("confirmMode") or "").strip()
    return mode in ("bar3BreakMarket", "bar3BreakLimit", "bar3BreakBull", "bar3")


def confirmModeIsBar3Limit(params):
    """3분봉 돌파 후 SMA20 인근 호가 지정가인지."""
    return str((params or {}).get("confirmMode") or "").strip() == "bar3BreakLimit"


def addBarTmMin(hms, addMin):
    """HHMMSS 에 분을 더한다. 세션 넘침은 그대로 둔다."""
    text = str(hms or "000000").replace(":", "").zfill(6)[-6:]
    try:
        hour = int(text[0:2])
        minute = int(text[2:4])
        addN = int(addMin)
    except (TypeError, ValueError):
        return text
    tot = hour * 60 + minute + addN
    if tot < 0:
        tot = 0
    return f"{tot // 60:02d}{tot % 60:02d}{text[4:6]}"


def limitWaitMin(params):
    """지정가 미체결 대기(분). 기본 10분."""
    try:
        sec = float((params or {}).get("limitWaitSec") or 600)
    except (TypeError, ValueError):
        sec = 600.0
    return max(1, int(round(sec / 60.0)))


def bar3ClosePx(closedBar):
    """3분봉 종가. 없으면 None."""
    if not closedBar:
        return None
    raw = closedBar.get("close")
    if raw is None:
        raw = closedBar.get("c")
    try:
        px = float(raw)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def applyBar3Confirm(pend, closedBar, maPx):
    """
    터치 후 3분봉 1개로 확인. pend 는 호환용으로 받아 두지만 phase 는 쓰지 않는다.
    종가 > SMA20 이고 양봉(종가>시가)이면 buy, 아니면 wait.
    반환: buy | wait
    """
    if not isinstance(pend, dict) or maPx is None:
        return "wait"
    try:
        ma = float(maPx)
    except (TypeError, ValueError):
        return "wait"
    if ma <= 0 or not closedBar:
        return "wait"
    try:
        o = float(
            closedBar["open"]
            if closedBar.get("open") is not None
            else closedBar.get("o") or 0
        )
        c = float(
            closedBar["close"]
            if closedBar.get("close") is not None
            else closedBar.get("c") or 0
        )
    except (TypeError, ValueError, KeyError):
        return "wait"
    # 돌파 + 양봉이 같은 3분봉에서 끝나야 주문을 낸다
    if c > o and c > ma:
        return "buy"
    return "wait"


def resolveBar3LimitFill(closedBar, maPx, limitPx):
    """
    지정가 대기 중 한 봉의 체결 모형.
    저가 ≤ 지정가 → 지정가 체결. 아니면 wait (10분 뒤 취소는 호출측).
    반환: ("limit"|"wait", fillPx|None)
    """
    if not closedBar:
        return "wait", None
    try:
        lim = float(limitPx)
        low = float(
            closedBar["low"]
            if closedBar.get("low") is not None
            else closedBar.get("l") or 0
        )
    except (TypeError, ValueError, KeyError):
        return "wait", None
    if lim <= 0:
        return "wait", None
    if low <= lim:
        return "limit", lim
    return "wait", None


def parseMaSteps(params):
    """매차 이평 목록. 없으면 SMA20만."""
    raw = (params or {}).get("maSteps") or list(_DEFAULT_STEPS)
    steps = []
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n >= 2:
            steps.append(n)
    return steps or list(_DEFAULT_STEPS)


def parseBuyAmtByStep(params):
    """매차별 금액. buyAmtByStep 또는 1/2/3차(buyAmt5/10/15) 또는 buyAmt7 등."""
    steps = parseMaSteps(params)
    out = {s: 1_000_000.0 for s in steps}
    raw = (params or {}).get("buyAmtByStep") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = max(10_000.0, float(v))
            except (TypeError, ValueError):
                continue
    # 웹 설정은 이평 숫자와 무관하게 1/2/3차를 buyAmt5/10/15 로 둔다
    for i, step in enumerate(steps):
        if i >= len(_SEQ_AMT_KEYS):
            break
        if params is None or params.get(_SEQ_AMT_KEYS[i]) is None:
            continue
        try:
            out[step] = max(10_000.0, float(params[_SEQ_AMT_KEYS[i]]))
        except (TypeError, ValueError):
            pass
    for step in steps:
        key = f"buyAmt{int(step)}"
        if params is None or params.get(key) is None:
            continue
        try:
            out[step] = max(10_000.0, float(params[key]))
        except (TypeError, ValueError):
            pass
    return out


def buyAmtForStep(params, step):
    """해당 이평 매차 금액(원)."""
    amts = parseBuyAmtByStep(params)
    try:
        n = int(step)
    except (TypeError, ValueError):
        n = 0
    if n in amts:
        return float(amts[n])
    return float((params or {}).get("buyAmtWon") or 1_000_000)


def stampMinBarsWithDaySma(minBars, dayBars, periods):
    """
    1분봉에 전일 확정 일봉 SMA 와 전일 종가를 붙인다.
    장중 이평은 고정(실시간과 동일).
    """
    if not minBars:
        return minBars
    closes = [float(b["close"]) for b in (dayBars or [])]
    smaByP = {}
    for p in periods or []:
        smaByP[int(p)] = calcSmaSeries(closes, int(p))
    smaByDt = {}
    prevCloseByDt = {}
    for i, d in enumerate(dayBars or []):
        dt = d.get("tradeDt")
        smap = {}
        srcI = i - 1
        for p, series in smaByP.items():
            smap[p] = series[srcI] if srcI >= 0 else None
        smaByDt[dt] = smap
        if i > 0:
            prevCloseByDt[dt] = float(dayBars[i - 1]["close"])
    rsiPriorByDt = {}
    rsiRun = []
    for d in dayBars or []:
        dt = str(d.get("tradeDt") or "")[:8]
        rsiPriorByDt[dt] = list(rsiRun)
        try:
            rsiRun.append(float(d["close"]))
        except (TypeError, ValueError):
            pass
    for b in minBars:
        dt = b.get("tradeDt")
        b["tf"] = "1m"
        b["sma"] = smaByDt.get(dt) or {}
        b["prevDayClose"] = prevCloseByDt.get(dt)
        bdt = str(dt or "")[:8]
        prior = rsiPriorByDt.get(bdt)
        if prior is None:
            prior = [x for x in rsiRun]
        b["rsiPriorCloses"] = prior
    return minBars


class MaTouchScaleStrategy:
    """
    1분봉 시뮬(기본) — 일봉 SMA + 봉 시각으로 14:00/15:18 적용.
    매수는 터치 직후가 아니라 3분봉 양봉 돌파 후 SMA20 인근 지정가(또는 reclaim).
    1분봉이 없으면 일봉 근사(종가 회복). 실시간은 trading/maTouchLive.
    """

    def __init__(self, params=None):
        p = params or {}
        self.maSteps = parseMaSteps(p)
        self.params = p
        self.buyAmtByStep = parseBuyAmtByStep(p)
        self.exitAfterTm = str(p.get("exitAfterTm", "151800")).zfill(6)[-6:]
        self.buyCutoffTm = str(p.get("buyCutoffTm", "140000")).zfill(6)[-6:]
        self.buyCutoffIfNextClosed = bool(p.get("buyCutoffIfNextClosed", True))
        self.stopMa = int(p.get("stopMa", 20))
        self.takeMa = int(p.get("takeMa", 10))
        # 1·2차 부분익절. halfTpPct 는 구버전 호환(미사용)
        self.tp1Pct = float(p.get("tp1Pct", 1.4) or 0)
        self.tp1QtyRatio = float(p.get("tp1QtyRatio", 0.5) or 0)
        self.tp2Pct = float(p.get("tp2Pct", 2) or 0)
        self.tp2QtyRatio = float(p.get("tp2QtyRatio", 0.5) or 0)
        self.halfTpPct = float(p.get("halfTpPct", 3))
        self.hardStopPct = float(p.get("hardStopPct", 1.5))
        # 1차 또는 2차 이후 잔량: 평단 대비 이 % 이하이면 전량 (기본 0 = 본전)
        self.afterHalfStopPct = float(p.get("afterHalfStopPct", 0) or 0)
        self.breakevenAfterTp1 = bool(p.get("breakevenAfterTp1", True))
        self.tp1UseLimit = bool(p.get("tp1UseLimit", True))
        self.tp1LimitSec = float(p.get("tp1LimitSec") or 0)
        self.tp1PreArmPct = float(p.get("tp1PreArmPct", 1.0) or 1.0)
        self.tp1CancelPct = float(p.get("tp1CancelPct", -1.0) or -1.0)
        # 이 시각 미만은 회복 매수 없음. 000000 이면 시초 금지 없음
        self.buyBanUntilTm = str(p.get("buyBanUntilTm", "000000")).zfill(6)[-6:]
        self.reclaimMinPct = float(p.get("reclaimMinPct", 0))
        self.confirmMode = str(p.get("confirmMode") or "bar3BreakLimit").strip()
        self.confirmBarMin = int(p.get("confirmBarMin") or 3)
        self.banBuyIfOpenBelowSma = bool(p.get("banBuyIfOpenBelowSma", False))
        # 절반 익절 종목만 다음날, 수익 중일 때 잔량 비율 추가 (v17 기본 끔)
        self.halfAddNextDay = bool(p.get("halfAddNextDay", False))
        self.halfAddMinPnlPct = float(p.get("halfAddMinPnlPct", 5))
        self.halfAddQtyRatio = float(p.get("halfAddQtyRatio", 0.5))
        # 전량 청산 후 당일 재매수 금지
        self.banRebuySameDay = bool(p.get("banRebuySameDay", True))
        self._openDt = ""
        self._dayOpenBelowSma = False
        # 매일 15:15부터 0%이상 3%이하 전량. 손실·3% 초과는 보유.
        self.nextClosedHalfTm = str(p.get("nextClosedHalfTm", "151500")).zfill(6)[-6:]
        self.nextClosedHalfMinPct = float(p.get("nextClosedHalfMinPct", 0))
        self.nextClosedHalfMaxPct = float(p.get("nextClosedHalfMaxPct", 4))
        # 당일 매수가 15:18에 미수익이면 전량. 시초 갭을 넘기지 않는다
        self.eodFlatIfNoProfit = bool(p.get("eodFlatIfNoProfit", True))
        # 수익 중 일봉 RSI 가 이 값 이상이면 전량. 0 이면 끔
        self.rsiTpPeriod = max(2, int(p.get("rsiTpPeriod", 14) or 14))
        self.rsiTpMin = float(p.get("rsiTpMin", 70) or 0)
        # 2차 후 잔량: 3차 +tp3Pct 보유 50%, 4차 +tp4Pct 전량
        self.tp3Pct = float(p.get("tp3Pct", 4) or 0)
        self.tp3QtyRatio = float(p.get("tp3QtyRatio", 0.5) or 0)
        self.tp4Pct = float(p.get("tp4Pct", 7) or 0)
        # 잔량 트레일: 고점>=Min 이후 고점-Dd%p (0=끔). tp4와 병행·먼저
        self.afterHalfTrailMinPct = float(p.get("afterHalfTrailMinPct", 0) or 0)
        self.afterHalfTrailTpPct = float(p.get("afterHalfTrailTpPct", 0) or 0)
        self.weakMfeExitEnabled = bool(p.get("weakMfeExitEnabled", True))
        self.weakHoldMin = float(p.get("weakHoldMin", 90) or 90)
        self.weakPeakMaxPct = float(p.get("weakPeakMaxPct", 1.0) or 1.0)
        self.weakExitMaxPnlPct = float(p.get("weakExitMaxPnlPct", -0.5) or -0.5)

        self.state = ST_IDLE
        # {qty, avgPx, filled, halfTaken, halfDt, halfAddDone, firstPx, firstDt}
        self.pos = None
        # 당일: 터치는 됐으나 3분봉 확인(또는 reclaim)을 기다리는 매차
        # step → {maPx, phase, brokeBucket}
        self.pendingConfirm = {}
        self.pendingDt = ""
        # 당일 3분봉 집계. {bucket, open, high, low, close}
        self._bar3Cur = None
        self._bar3Dt = ""
        # 양봉 돌파 후 지정가 대기. step → {maPx, limitPx, qty, fromBucket}
        self._limitWait = {}
        # 전량 청산한 날짜 — 같으면 재매수 안 함
        self.buyBanDt = ""

    def _scaleStopOn(self):
        """1차 또는 2차(구 절반) 이후면 잔량 본전 손절을 쓴다."""
        if not self.pos:
            return False
        if self.pos.get("tp2Taken") or self.pos.get("halfTaken"):
            return True
        # v60: BE only after tp1Taken (BT fills TP1 same bar at trigger+)
        if self.breakevenAfterTp1 and self.pos.get("tp1Taken"):
            return True
        return False

    def _needPeriods(self):
        return set(self.maSteps + [self.stopMa, self.takeMa])

    def prepare(self, bars):
        """
        각 봉에 '전일까지' SMA를 붙인다 (당일 종가를 넣지 않음).
        1분봉은 stampMinBarsWithDaySma 가 이미 붙였으면 그대로 둔다.
        """
        if bars and str(bars[0].get("tf") or "") == "1m":
            return bars
        closes = [float(b["close"]) for b in bars]
        smaByP = {}
        for p in self._needPeriods():
            smaByP[p] = calcSmaSeries(closes, p)

        for i, b in enumerate(bars):
            smap = {}
            srcI = i - 1
            for p, series in smaByP.items():
                smap[p] = series[srcI] if srcI >= 0 else None
            b["sma"] = smap
        rsiRun = []
        for b in bars:
            b["rsiPriorCloses"] = list(rsiRun)
            try:
                rsiRun.append(float(b["close"]))
            except (TypeError, ValueError):
                pass
        return bars

    def _resetPos(self, banBuyDt=None):
        # 전량 청산 시 당일 재매수 금지 날짜를 남긴다
        if banBuyDt and self.banRebuySameDay:
            self.buyBanDt = str(banBuyDt)
        self.pos = None
        self.state = ST_IDLE
        self.pendingConfirm = {}
        self._limitWait = {}

    def _addLot(self, px, step, qty=None):
        """한 매차 수량. qty 가 있으면 지정가에서 잡은 수량을 그대로 쓴다."""
        if qty is None:
            amt = buyAmtForStep(self.params, step)
            qty = int(amt // px) if px > 0 else 0
        else:
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                qty = 0
        if qty < 1:
            return 0
        if self.pos is None:
            self.pos = {
                "qty": 0,
                "avgPx": 0.0,
                "filled": [],
                "halfTaken": False,
                "tp1Taken": False,
                "tp2Taken": False,
                "tp3Taken": False,
                "halfDt": None,
                "halfAddDone": False,
                "brokeAboveTakeMa": False,
                "firstPx": px,
                "firstDt": None,
                "entryTm": None,
            }
            self.state = ST_IN_POS
        prevQty = int(self.pos["qty"])
        prevAvg = float(self.pos["avgPx"] or 0)
        newQty = prevQty + qty
        self.pos["avgPx"] = (prevAvg * prevQty + px * qty) / float(newQty)
        self.pos["qty"] = newQty
        self.pos["filled"].append(int(step))
        from utils.maTouchMfe import initMaxPnl, noteMaxPnl

        if prevQty <= 0:
            initMaxPnl(self.pos, px)
        else:
            noteMaxPnl(self.pos, px)
        return qty

    def _cutQty(self, sellQty):
        left = int(self.pos["qty"]) - int(sellQty)
        if left <= 0:
            self._resetPos()
            return
        self.pos["qty"] = left

    def _addExactQty(self, px, qty):
        """절반익절 다음날 재진입 — 잔량×비율 수량으로 평단을 갱신한다."""
        qty = int(qty or 0)
        if qty < 1 or px is None or float(px) <= 0 or self.pos is None:
            return 0
        fillPx = float(px)
        prevQty = int(self.pos["qty"])
        prevAvg = float(self.pos["avgPx"] or 0)
        newQty = prevQty + qty
        self.pos["avgPx"] = (prevAvg * prevQty + fillPx * qty) / float(newQty)
        self.pos["qty"] = newQty
        from utils.maTouchMfe import noteMaxPnl

        noteMaxPnl(self.pos, fillPx)
        return qty

    def _barTm(self, bar):
        return str(bar.get("barTm") or "000000").zfill(6)[-6:]

    def _rsiNow(self, bar, px):
        """전일까지 일봉 + 현재 종가(틱) Wilder RSI. 표본 모자라면 None."""
        hist = []
        for x in bar.get("rsiPriorCloses") or []:
            try:
                v = float(x)
            except (TypeError, ValueError):
                continue
            if v > 0:
                hist.append(v)
        try:
            cur = float(px or 0)
        except (TypeError, ValueError):
            cur = 0.0
        if cur > 0:
            hist.append(cur)
        p = self.rsiTpPeriod
        if len(hist) < p + 1:
            return None
        series = calcRsiSeries(hist, p)
        if not series:
            return None
        return series[-1]

    def _mfeSnap(self):
        """청산 이벤트에 붙일 보유기간 고점."""
        pos = self.pos or {}
        return {
            "maxPnlPct": pos.get("maxPnlPct"),
            "maxPx": pos.get("maxPx"),
        }

    def _buyStep(self, step, fillPx, tradeDt, cntrTm, reason, ordQty=None):
        """한 매차 매수. 수량 0이면 None. ordQty 는 지정가에서 잡은 수량."""
        qty = self._addLot(fillPx, step, qty=ordQty)
        if qty < 1:
            return None
        if self.pos.get("firstDt") is None:
            self.pos["firstDt"] = tradeDt
        if not self.pos.get("entryTm"):
            self.pos["entryTm"] = str(cntrTm or "").zfill(6)[-6:]
        self.pendingConfirm.pop(int(step), None)
        return {
            "type": "BUY" if len(self.pos["filled"]) == 1 else "ADD",
            "price": fillPx,
            "reason": reason,
            "cntrTm": cntrTm,
            "qty": qty,
            "buyAmtWon": buyAmtForStep(self.params, step),
            "step": step,
            "catchDt": tradeDt,
            "setupDt": tradeDt,
        }

    def _tryReclaimBuy(self, closePx, highPx, tradeDt, cntrTm, events, allowBuy, skipSteps=None):
        """
        당일 터치 대기 매차: 현재 봉이 이평 위로 회복하면 매수.
        skipSteps 는 방금 터치한 매차 — 터치 봉 고가는 터치 전 구간이라 종가 회복만 인정한다.
        """
        if not allowBuy or closePx is None:
            return
        skip = {int(s) for s in (skipSteps or [])}
        filled = (self.pos or {}).get("filled") or []
        for step in list(self.pendingConfirm.keys()):
            if step in filled:
                self.pendingConfirm.pop(int(step), None)
                continue
            pend = self.pendingConfirm.get(step) or {}
            maPx = float(pend.get("maPx") or 0)
            if maPx <= 0:
                continue
            # 이평을 reclaimMinPct 이상 뚫어야 가짜 회복으로 안 산다
            needPx = maPx * (1.0 + self.reclaimMinPct / 100.0)
            closeOk = float(closePx) > needPx
            highOk = highPx is not None and float(highPx) > needPx
            if step in skip:
                # 터치한 그 봉: 종가가 이평 위로 마감했을 때만
                if not closeOk:
                    continue
                fillPx = float(closePx)
            else:
                if not (closeOk or highOk):
                    continue
                fillPx = float(closePx) if closeOk else needPx
            ev = self._buyStep(step, fillPx, tradeDt, cntrTm, f"ma{step}_reclaim")
            if ev:
                events.append(ev)
                filled = (self.pos or {}).get("filled") or []

    def _feedBar3Min(self, bar):
        """1분봉을 3분봉으로 합친다. 3분 마지막 분이면 완성 봉을 반환."""
        tradeDt = bar.get("tradeDt")
        if self._bar3Dt != tradeDt:
            self._bar3Dt = tradeDt
            self._bar3Cur = None
        barTm = self._barTm(bar)
        bkt = bar3BucketTm(barTm)
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        rec = self._bar3Cur
        if rec is None or rec.get("bucket") != bkt:
            self._bar3Cur = {
                "bucket": bkt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
            rec = self._bar3Cur
        else:
            rec["high"] = max(float(rec["high"]), h)
            rec["low"] = min(float(rec["low"]), l)
            rec["close"] = c
        if isBar3CloseMin(barTm):
            return {
                "bucket": rec["bucket"],
                "open": rec["open"],
                "high": rec["high"],
                "low": rec["low"],
                "close": rec["close"],
            }
        return None

    def _resolveBar3LimitOnMin(self, bar, tradeDt, cntrTm, events):
        """지정가 대기: 이후 1분봉 저가가 호가면 체결. 10분이 지나면 취소만."""
        if not bar or not self._limitWait:
            return
        barTm = self._barTm(bar)
        for step in list(self._limitWait.keys()):
            pend = self._limitWait.get(step) or {}
            fromTm = str(pend.get("fromTm") or "")
            expireTm = str(pend.get("expireTm") or "")
            # 주문을 낸 그 1분봉에서는 아직 대기 시작 전
            if fromTm and barTm <= fromTm:
                continue
            limitPx = float(pend.get("limitPx") or 0)
            ordQty = int(pend.get("qty") or 0)
            kind, fillPx = resolveBar3LimitFill(bar, pend.get("maPx"), limitPx)
            if kind == "limit" and fillPx and fillPx > 0:
                self._limitWait.pop(int(step), None)
                ev = self._buyStep(
                    step,
                    float(fillPx),
                    tradeDt,
                    cntrTm,
                    f"ma{step}_bar3_limit",
                    ordQty=ordQty if ordQty > 0 else None,
                )
                if ev:
                    events.append(ev)
                continue
            if expireTm and barTm >= expireTm:
                # 실전과 동일: 미체결은 취소만 (시장가 전환 없음)
                self._limitWait.pop(int(step), None)

    def _tryBar3Buy(self, closed3, tradeDt, cntrTm, events, allowBuy):
        """완성된 3분봉이 양봉 돌파면 SMA20 인근 호가 지정가(또는 시장가 모드)."""
        if not allowBuy or not closed3:
            return
        filled = (self.pos or {}).get("filled") or []
        useLimit = confirmModeIsBar3Limit(self.params)
        closePx = bar3ClosePx(closed3)
        waitMin = limitWaitMin(self.params)
        for step in list(self.pendingConfirm.keys()):
            if step in filled or int(step) in self._limitWait:
                self.pendingConfirm.pop(int(step), None)
                continue
            pend = self.pendingConfirm.get(step)
            if not isinstance(pend, dict):
                continue
            maPx = float(pend.get("maPx") or 0)
            if maPx <= 0:
                continue
            act = applyBar3Confirm(pend, closed3, maPx)
            if act != "buy":
                continue
            if useLimit:
                # v63: 실전과 같이 지정가 매수는 호가 내림
                limitPx = float(floorToKrxTick(maPx))
                if limitPx <= 0:
                    continue
                qty = int(buyAmtForStep(self.params, step) // limitPx)
                if qty < 1:
                    self.pendingConfirm.pop(int(step), None)
                    continue
                # 실전: 지정가 후 10분 대기. BT는 이후 1분봉 저가로 체결, 만료 시 취소
                self._limitWait[int(step)] = {
                    "maPx": maPx,
                    "limitPx": limitPx,
                    "qty": qty,
                    "fromBucket": str(closed3.get("bucket") or ""),
                    "fromTm": str(cntrTm or "").zfill(6)[-6:],
                    "expireTm": addBarTmMin(cntrTm, waitMin),
                }
                self.pendingConfirm.pop(int(step), None)
                continue
            if not closePx:
                continue
            ev = self._buyStep(
                step,
                closePx,
                tradeDt,
                cntrTm,
                f"ma{step}_bar3_market",
            )
            if ev:
                events.append(ev)

    def onBar(self, bars, idx):
        """
        1분봉: 터치 시각·14:00·15:18 을 봉 시각으로 적용.
        매수 확인은 3분봉 양봉 돌파 후 SMA20 인근 지정가(reclaim 모드·일봉은 종가 회복).
        일봉: 저가 터치 후 종가가 이평 위 (분봉 없을 때 근사).
        """
        if not bars or idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        isMin = str(bar.get("tf") or "") == "1m"
        if not isMin and idx <= 0:
            return None

        barTm = self._barTm(bar)
        if isMin and (barTm < _SESS_OPEN_TM or barTm > _SESS_CLOSE_TM):
            return None

        low = float(bar["low"])
        close = float(bar["close"])
        prev = bars[idx - 1] if idx > 0 else None
        if prev is not None and prev.get("tradeDt") == bar.get("tradeDt"):
            prevClose = float(prev["close"])
        elif bar.get("prevDayClose") is not None:
            prevClose = float(bar.get("prevDayClose"))
        elif prev is not None:
            prevClose = float(prev["close"])
        else:
            return None

        sma = bar.get("sma") or {}
        events = []
        tradeDt = bar.get("tradeDt")
        cntrTm = bar.get("cntrTm") or tradeDt
        # 당일 시가 ≤ SMA20 이면 그날 신규매수 제외 (갭다운·시초 약세)
        if self._openDt != tradeDt:
            self._openDt = tradeDt
            openPx = float(bar.get("open") or close)
            sma20 = sma.get(20)
            if sma20 is None and self.maSteps:
                sma20 = sma.get(int(self.maSteps[0]))
            self._dayOpenBelowSma = bool(
                self.banBuyIfOpenBelowSma
                and sma20 is not None
                and openPx <= float(sma20)
            )
        # 14:00 매수컷만 다음날 휴장일. 15:15 0~3% 전량은 매일.
        eveClosed = buyCutoffApplies(tradeDt, self.params)
        cutOn = isMin and eveClosed
        allowBuy = (not isMin) or (not cutOn) or (barTm < self.buyCutoffTm)
        # buyBanUntilTm 미만은 터치 대기만 하고 회복 매수는 막는다 (000000이면 없음)
        openTmBan = bool(isMin and barTm < self.buyBanUntilTm)
        allowReclaim = bool(
            allowBuy and (not openTmBan) and (not self._dayOpenBelowSma)
        )
        allowEodExit = (not isMin) or (barTm >= self.exitAfterTm)
        allowWeakFlat = (not isMin) or (barTm >= self.nextClosedHalfTm)

        # 당일만 확인 대기. 날짜가 바뀌면 전날 터치는 버린다.
        if self.pendingDt != tradeDt:
            self.pendingConfirm = {}
            self._limitWait = {}
            self.pendingDt = tradeDt
            self._bar3Cur = None
            self._bar3Dt = tradeDt

        closed3 = None
        if isMin and confirmModeIsBar3(self.params):
            closed3 = self._feedBar3Min(bar)

        # 전량 청산 당일이면 신규 매수·터치 대기 금지
        rebuyBanned = bool(
            self.banRebuySameDay and self.buyBanDt and self.buyBanDt == tradeDt
        )
        filled = (self.pos or {}).get("filled") or []
        touchedThisBar = set()
        allowNew = bool(
            allowBuy and (not rebuyBanned) and (not self._dayOpenBelowSma)
        )
        if allowNew:
            for step in self.maSteps:
                if step in filled or int(step) in self._limitWait:
                    self.pendingConfirm.pop(int(step), None)
                    continue
                ma = sma.get(step)
                if ma is None:
                    continue
                ma = float(ma)
                # 위→아래로 닿은 뒤에만 확인 대기를 건다. 즉시 매수하지 않는다.
                touched = prevClose > ma and low <= ma
                if not touched:
                    continue
                self.pendingConfirm[int(step)] = {"maPx": ma, "phase": "touch"}
                touchedThisBar.add(int(step))
        else:
            self.pendingConfirm = {}

        if confirmModeIsBar3(self.params) and isMin:
            # 지정가 대기는 매수컷 이후에도 1분봉마다 체결/취소를 본다
            if confirmModeIsBar3Limit(self.params):
                self._resolveBar3LimitOnMin(bar, tradeDt, cntrTm, events)
            if closed3:
                self._tryBar3Buy(
                    closed3, tradeDt, cntrTm, events, allowNew and allowReclaim
                )
        elif allowNew:
            # reclaim 모드 또는 1분봉 없는 일봉 근사
            high = float(bar.get("high") or close)
            self._tryReclaimBuy(
                close, high, tradeDt, cntrTm, events, allowReclaim, touchedThisBar
            )

        if self.pos is None:
            return events or None

        avgPx = float(self.pos["avgPx"] or 0)
        qtyNow = int(self.pos["qty"] or 0)
        enteredToday = self.pos.get("firstDt") == tradeDt
        takeLine = sma.get(self.takeMa)
        stopLine = sma.get(self.stopMa)
        high = float(bar.get("high") or close)

        # 매수 후 고점 수익률(1차 익절 통계용). 손절 판단 전에 고가를 남긴다.
        from utils.maTouchMfe import noteMaxPnl

        noteMaxPnl(self.pos, high, self._barTm(bar))

        # 매수 후 장중 고가가 SMA(takeMa) 이상이면 돌파 이력 확정
        if takeLine is not None and high >= float(takeLine):
            self.pos["brokeAboveTakeMa"] = True

        # 하드스탑: 1·2차 전에는 -hardStopPct, 1차(또는 2차) 이후 잔량은 평단 -afterHalfStopPct
        if avgPx > 0 and qtyNow > 0:
            scaleStop = self._scaleStopOn()
            stopPct = self.afterHalfStopPct if scaleStop else self.hardStopPct
            hardPx = avgPx * (1.0 - stopPct / 100.0)
            if low <= hardPx:
                rsn = (
                    f"after_half_stop_{stopPct:g}pct"
                    if scaleStop
                    else f"hard_stop_{stopPct:g}pct"
                )
                events.append(
                    {
                        "type": "SELL",
                        "price": hardPx,
                        "reason": rsn,
                        "cntrTm": cntrTm,
                        "qty": qtyNow,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
                return events

        # 약세조기청산: N분·현행 1차(tp1Pct) 미도달·고점·평가
        if (
            self.weakMfeExitEnabled
            and avgPx > 0
            and qtyNow > 0
            and (not self.pos.get("tp1Taken"))
            and (not self.pos.get("halfTaken"))
        ):
            try:
                mx = float(self.pos.get("maxPnlPct") or 0)
            except (TypeError, ValueError):
                mx = 0.0
            tp1_hit = self.tp1Pct > 0 and mx + 1e-9 >= float(self.tp1Pct)
            et = str(self.pos.get("entryTm") or "").zfill(6)[-6:]
            try:
                hold = (
                    int(cntrTm[0:2]) * 60
                    + int(cntrTm[2:4])
                    + int(cntrTm[4:6]) / 60.0
                    - (int(et[0:2]) * 60 + int(et[2:4]) + int(et[4:6]) / 60.0)
                ) if et and et != "000000" else None
            except (TypeError, ValueError):
                hold = None
            pnlPct = (close / avgPx - 1.0) * 100.0 if avgPx > 0 else 0.0
            if (
                (not tp1_hit)
                and hold is not None
                and hold + 1e-9 >= self.weakHoldMin
                and mx + 1e-9 < self.weakPeakMaxPct
                and pnlPct <= self.weakExitMaxPnlPct + 1e-9
            ):
                events.append(
                    {
                        "type": "SELL",
                        "price": close,
                        "reason": (
                            f"weak_mfe_{self.weakHoldMin:g}m_"
                            f"peak<{self.weakPeakMaxPct:g}_"
                            f"no_tp1_{self.tp1Pct:g}"
                        ),
                        "cntrTm": cntrTm,
                        "qty": qtyNow,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
                return events

        # 매일 15:15~ : 평단 0% 이상 3% 이하면 잔량 포함 전량. 손실·3% 초과는 보유.
        # 부분익절보다 먼저 봐서, 구간 안이면 잘라 두지 않고 정리한다.
        if (
            allowWeakFlat
            and qtyNow >= 1
            and avgPx > 0
            and inNextClosedHalfBand(
                close,
                avgPx,
                self.nextClosedHalfMinPct,
                self.nextClosedHalfMaxPct,
            )
        ):
            events.append(
                {
                    "type": "SELL",
                    "price": close,
                    "reason": (f"flat_1515_{self.nextClosedHalfMinPct:g}_{self.nextClosedHalfMaxPct:g}"),
                    "cntrTm": cntrTm,
                    "qty": qtyNow,
                    **self._mfeSnap(),
                }
            )
            self._resetPos(banBuyDt=tradeDt)
            return events

        # 수익 중 RSI 과매수 → 부분익절보다 먼저 전량
        if (
            self.rsiTpMin > 0
            and avgPx > 0
            and qtyNow > 0
            and close > avgPx
        ):
            rsiVal = self._rsiNow(bar, close)
            if rsiVal is not None and rsiVal >= self.rsiTpMin:
                events.append(
                    {
                        "type": "SELL",
                        "price": close,
                        "reason": f"rsi_tp_{self.rsiTpMin:g}",
                        "cntrTm": cntrTm,
                        "qty": qtyNow,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
                return events

        # 1차 +tp1Pct 보유 30%, 2차 +tp2Pct 보유 30%. 같은 봉에서 둘 다 가능.
        # 1주는 비율 불가 → 해당 % 도달 시 전량.
        tp2Now = False
        if avgPx > 0 and qtyNow == 1:
            pnlPct = (close - avgPx) / avgPx * 100.0
            sold1 = False
            reason = ""
            if (not self.pos.get("tp1Taken")) and self.tp1Pct > 0 and pnlPct >= self.tp1Pct:
                self.pos["tp1Taken"] = True
                sold1 = True
                reason = f"tp1_{self.tp1Pct:g}pct"
            elif (
                self.pos.get("tp1Taken")
                and (not self.pos.get("tp2Taken"))
                and self.tp2Pct > 0
                and pnlPct >= self.tp2Pct
            ):
                self.pos["tp2Taken"] = True
                self.pos["halfTaken"] = True
                self.pos["halfDt"] = tradeDt
                tp2Now = True
                sold1 = True
                reason = f"tp2_{self.tp2Pct:g}pct"
            elif (
                (self.pos.get("tp2Taken") or self.pos.get("halfTaken"))
                and (not self.pos.get("tp3Taken"))
                and self.tp3Pct > 0
                and pnlPct >= self.tp3Pct
            ):
                self.pos["tp3Taken"] = True
                sold1 = True
                reason = f"tp3_{self.tp3Pct:g}pct"
            elif self.pos.get("tp1Taken"):
                maxPnl = float(self.pos.get("maxPnlPct") or 0)
                trailMin = float(self.afterHalfTrailMinPct or 0)
                trailDd = float(self.afterHalfTrailTpPct or 0)
                if (
                    trailMin > 0
                    and trailDd > 0
                    and maxPnl + 1e-9 >= trailMin
                    and pnlPct <= maxPnl - trailDd + 1e-9
                ):
                    sold1 = True
                    reason = f"trail_peak{maxPnl:g}_dd{trailDd:g}"
                elif self.tp4Pct > 0 and pnlPct >= self.tp4Pct:
                    sold1 = True
                    reason = f"tp4_{self.tp4Pct:g}pct"
            if sold1:
                events.append(
                    {
                        "type": "SELL",
                        "price": close,
                        "reason": reason,
                        "cntrTm": cntrTm,
                        "qty": qtyNow,
                        **self._mfeSnap(),
                    }
                )
                self._resetPos(banBuyDt=tradeDt)
                return events
        elif avgPx > 0 and qtyNow >= 2:
            pnlPct = (close - avgPx) / avgPx * 100.0
            if (not self.pos.get("tp1Taken")) and self.tp1Pct > 0 and pnlPct >= self.tp1Pct:
                sellQty = scaleOutQty(qtyNow, self.tp1QtyRatio)
                if sellQty >= 1:
                    self.pos["tp1Taken"] = True
                    self._cutQty(sellQty)
                    events.append(
                        {
                            "type": "SELL_HALF",
                            "price": close,
                            "reason": f"tp1_{self.tp1Pct:g}pct",
                            "cntrTm": cntrTm,
                            "qty": sellQty,
                        }
                    )
                    if self.pos is None:
                        self._resetPos(banBuyDt=tradeDt)
                        return events
                    qtyNow = int(self.pos["qty"] or 0)
                    avgPx = float(self.pos["avgPx"] or 0)
                    pnlPct = (close - avgPx) / avgPx * 100.0 if avgPx > 0 else 0.0
            if (
                self.pos is not None
                and self.pos.get("tp1Taken")
                and (not self.pos.get("tp2Taken"))
                and self.tp2Pct > 0
                and qtyNow >= 2
                and avgPx > 0
                and pnlPct >= self.tp2Pct
            ):
                sellQty = scaleOutQty(qtyNow, self.tp2QtyRatio)
                if sellQty >= 1:
                    self.pos["tp2Taken"] = True
                    self.pos["halfTaken"] = True
                    self.pos["halfDt"] = tradeDt
                    tp2Now = True
                    self._cutQty(sellQty)
                    events.append(
                        {
                            "type": "SELL_HALF",
                            "price": close,
                            "reason": f"tp2_{self.tp2Pct:g}pct",
                            "cntrTm": cntrTm,
                            "qty": sellQty,
                        }
                    )
                    if self.pos is None:
                        self._resetPos(banBuyDt=tradeDt)
                        return events

        # 3차: 2차 후 잔량 +tp3Pct 이면 보유 50%. 2차를 친 그 봉은 다음 봉부터.
        if (
            (not tp2Now)
            and self.pos is not None
            and (self.pos.get("tp2Taken") or self.pos.get("halfTaken"))
            and (not self.pos.get("tp3Taken"))
            and self.tp3Pct > 0
        ):
            qtyNow = int(self.pos["qty"] or 0)
            avgPx = float(self.pos["avgPx"] or 0)
            pnlPct = (close - avgPx) / avgPx * 100.0 if avgPx > 0 else 0.0
            if qtyNow >= 2 and avgPx > 0 and pnlPct >= self.tp3Pct:
                sellQty = scaleOutQty(qtyNow, self.tp3QtyRatio)
                if sellQty >= 1:
                    self.pos["tp3Taken"] = True
                    self._cutQty(sellQty)
                    events.append(
                        {
                            "type": "SELL_HALF",
                            "price": close,
                            "reason": f"tp3_{self.tp3Pct:g}pct",
                            "cntrTm": cntrTm,
                            "qty": sellQty,
                        }
                    )
                    if self.pos is None:
                        self._resetPos(banBuyDt=tradeDt)
                        return events

        # 잔량(3차 후 또는 1주라 3차 불가): 트레일 vs 4차 중 먼저
        if self.pos is not None and (
            self.pos.get("tp2Taken")
            or self.pos.get("halfTaken")
            or (
                self.pos.get("tp1Taken")
                and int(self.pos.get("qty") or 0) < 2
            )
        ):
            qtyNow = int(self.pos["qty"] or 0)
            avgPx = float(self.pos["avgPx"] or 0)
            pnlPct = (close - avgPx) / avgPx * 100.0 if avgPx > 0 else 0.0
            canResid = bool(self.pos.get("tp3Taken")) or float(self.tp3Pct or 0) <= 0 or qtyNow < 2
            if canResid and qtyNow >= 1 and avgPx > 0:
                maxPnl = float(self.pos.get("maxPnlPct") or 0)
                trailMin = float(self.afterHalfTrailMinPct or 0)
                trailDd = float(self.afterHalfTrailTpPct or 0)
                if (
                    trailMin > 0
                    and trailDd > 0
                    and maxPnl + 1e-9 >= trailMin
                    and pnlPct <= maxPnl - trailDd + 1e-9
                ):
                    events.append(
                        {
                            "type": "SELL",
                            "price": close,
                            "reason": f"trail_peak{maxPnl:g}_dd{trailDd:g}",
                            "cntrTm": cntrTm,
                            "qty": qtyNow,
                            **self._mfeSnap(),
                        }
                    )
                    self._resetPos(banBuyDt=tradeDt)
                    return events
                if self.tp4Pct > 0 and pnlPct >= self.tp4Pct:
                    events.append(
                        {
                            "type": "SELL",
                            "price": close,
                            "reason": f"tp4_{self.tp4Pct:g}pct",
                            "cntrTm": cntrTm,
                            "qty": qtyNow,
                            **self._mfeSnap(),
                        }
                    )
                    self._resetPos(banBuyDt=tradeDt)
                    return events

        # 절반 익절한 종목만 다음날, +halfAddMinPnlPct 이상이면 잔량×비율 추가.
        # 손절은 절반 후 잔량 기준(평단 -afterHalfStopPct)을 그대로 쓴다.
        if (
            self.halfAddNextDay
            and self.pos is not None
            and self.pos.get("halfTaken")
            and (not self.pos.get("halfAddDone"))
            and str(self.pos.get("halfDt") or "")
            and str(tradeDt) > str(self.pos.get("halfDt"))
            and (not openTmBan)
            and avgPx > 0
            and close >= avgPx * (1.0 + self.halfAddMinPnlPct / 100.0)
        ):
            remain = int(self.pos.get("qty") or 0)
            addQty = int(remain * self.halfAddQtyRatio)
            if addQty < 1 and remain >= 2:
                addQty = 1
            got = self._addExactQty(close, addQty) if addQty >= 1 else 0
            if got >= 1:
                self.pos["halfAddDone"] = True
                events.append(
                    {
                        "type": "ADD",
                        "price": close,
                        "reason": "half_add_next_day",
                        "cntrTm": cntrTm,
                        "qty": got,
                        "catchDt": tradeDt,
                        "setupDt": tradeDt,
                    }
                )

        if self.pos is None:
            return events or None
        avgPx = float(self.pos["avgPx"] or 0)
        qtyNow = int(self.pos["qty"] or 0)

        # 15:18 이후:
        # 당일 매수 + 미수익 → 전량 (오버나잇 갭 방지)
        # 전일 이전 진입 + SMA20 아래 → 손절
        # (수익 중 SMA10/SMA20 이탈 익절은 사용하지 않음)
        if not allowEodExit:
            return events or None
        if (
            self.eodFlatIfNoProfit
            and enteredToday
            and avgPx > 0
            and close <= avgPx
        ):
            events.append(
                {
                    "type": "SELL",
                    "price": close,
                    "reason": "eod_flat_no_profit",
                    "cntrTm": cntrTm,
                    "qty": qtyNow,
                    **self._mfeSnap(),
                }
            )
            self._resetPos(banBuyDt=tradeDt)
            return events
        if stopLine is not None and close < float(stopLine) and (not enteredToday):
            events.append(
                {
                    "type": "SELL",
                    "price": close,
                    "reason": "stop_ma20_eod",
                    "cntrTm": cntrTm,
                    "qty": qtyNow,
                    **self._mfeSnap(),
                }
            )
            self._resetPos(banBuyDt=tradeDt)
            return events

        return events or None
