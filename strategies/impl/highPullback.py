"""
신고가 눌림목매매1 (highPullback_v1)

- 유니버스(선별): 최초 52주 신고 + 장대 양봉 + 거래량 급증 (+ 수급은 파이프라인)
- 진입: 셋업 이후 15:15~ 20이평 터치 → 고정 금액 매수
- 청산: 15:18~ 60/20 음봉 하향 돌파, 1주 잔여 원칙
"""

from datetime import datetime

from strategies.indicators import calcEmaSeries

ST_IDLE = "IDLE"
ST_MONITOR = "MONITOR"
ST_IN_POS = "IN_POSITION"


def _barHms(bar):
    raw = bar.get("barTm")
    if raw is not None and str(raw).strip():
        text = str(raw).strip().replace(":", "")
        if text.isdigit():
            return text.zfill(6)[-6:]
    cntr = str(bar.get("cntrTm") or "").strip()
    if len(cntr) >= 6 and cntr[-6:].isdigit():
        return cntr[-6:]
    return "000000"


def _tmGe(barTm, thrTm):
    a = str(barTm or "0").zfill(6)[-6:]
    b = str(thrTm or "0").zfill(6)[-6:]
    return a >= b


def _aggDailyFromMin(bars):
    """
    분봉 → 일봉 리스트(오름차순).
    day: {dt, open, high, low, close, qty}
    """
    byDt = {}
    order = []
    for b in bars:
        dt = str(b.get("tradeDt") or "").strip()
        if len(dt) != 8:
            continue
        o = float(b["open"])
        h = float(b["high"])
        l = float(b["low"])
        c = float(b["close"])
        q = float(b.get("qty") or 0)
        if dt not in byDt:
            byDt[dt] = {
                "dt": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "qty": q,
            }
            order.append(dt)
        else:
            d = byDt[dt]
            d["high"] = max(d["high"], h)
            d["low"] = min(d["low"], l)
            d["close"] = c
            d["qty"] += q
    order.sort()
    return [byDt[d] for d in order]


def _findSetupDts(days, lookbackHigh, minFlu, volMaPeriod, volMult):
    """
    일봉에서 셋업 일자 집합.
    조건: 최초성 52주 고가 + 등락≥minFlu 양봉 + 거래량 급증.
    """
    setups = set()
    if not days:
        return setups
    n = len(days)
    for i in range(1, n):
        d = days[i]
        # 등락·양봉
        prevClose = float(days[i - 1]["close"])
        close = float(d["close"])
        openP = float(d["open"])
        if prevClose <= 0:
            continue
        flu = (close / prevClose - 1.0) * 100.0
        if flu < minFlu or close <= openP:
            continue
        # 거래량 평균
        vStart = max(0, i - volMaPeriod)
        prevVols = [float(days[j]["qty"]) for j in range(vStart, i)]
        if not prevVols:
            continue
        avgVol = sum(prevVols) / len(prevVols)
        if avgVol <= 0 or float(d["qty"]) < avgVol * volMult:
            continue
        # 52주(or 가용) 최초 신고 고가
        start = max(0, i - lookbackHigh)
        prevHighs = [float(days[j]["high"]) for j in range(start, i)]
        if not prevHighs:
            continue
        prevMax = max(prevHighs)
        high = float(d["high"])
        if high < prevMax:
            continue
        # 전일이 이미 신고 구간이면 최초 아님
        if i >= 2:
            startY = max(0, i - 1 - lookbackHigh)
            yPrev = [float(days[j]["high"]) for j in range(startY, i - 1)]
            if yPrev and float(days[i - 1]["high"]) >= max(yPrev):
                continue
        setups.add(d["dt"])
    return setups


class HighPullbackStrategy:
    """신고 후 15:15 20선 터치 진입 / 15:18 60·20 이탈 청산."""

    def __init__(self, params=None):
        p = params or {}
        self.maFast = int(p.get("maFast", 20))
        self.exitStopMa = int(p.get("exitStopMa", 60))
        self.buyAfterTm = str(p.get("buyAfterTm", "151500")).zfill(6)[-6:]
        self.exitAfterTm = str(p.get("exitAfterTm", "151800")).zfill(6)[-6:]
        self.buyAmtWon = float(p.get("buyAmtWon", 10_000_000))
        self.leaveQty = int(p.get("leaveQty", 1))
        self.requireCross = bool(p.get("requireCross", True))
        self.maxEntriesPerDay = int(p.get("maxEntriesPerDay", 1))
        self.useBullFilter = bool(p.get("useBullFilter", False))
        self.setupMinFluRt = float(p.get("setupMinFluRt", 10.0))
        self.lookbackHigh = int(p.get("lookbackHigh", 250))
        self.volMaPeriod = int(p.get("volMaPeriod", 20))
        self.volMult = float(p.get("volMult", 1.5))
        self.universePrefiltered = bool(p.get("universePrefiltered", True))
        self.monitorDays = int(p.get("monitorDays", 20))

        self.state = ST_IDLE
        self.pos = None
        self._entriesByDay = {}
        self.setupDts = set()
        # tradeDt → 셋업 이후 모니터링 가능 여부 캐시용
        self._armedFromDt = None  # 최초 셋업일 (문자열)

    def prepare(self, bars):
        closes = [float(b["close"]) for b in bars]
        emaF = calcEmaSeries(closes, self.maFast)
        emaStop = calcEmaSeries(closes, self.exitStopMa)
        for i, b in enumerate(bars):
            b["emaFast"] = emaF[i]
            b["emaStop"] = emaStop[i]

        days = _aggDailyFromMin(bars)
        # 가용 일수가 lookback 보다 작으면 가용분으로 근사
        lb = min(self.lookbackHigh, max(20, len(days) - 2))
        self.setupDts = _findSetupDts(
            days,
            lookbackHigh=lb,
            minFlu=self.setupMinFluRt,
            volMaPeriod=self.volMaPeriod,
            volMult=self.volMult,
        )
        if self.setupDts:
            self._armedFromDt = min(self.setupDts)
        elif self.universePrefiltered:
            # 외부에서 이미 신고 유니버스 종목으로 넣은 경우
            self._armedFromDt = days[0]["dt"] if days else None
            self.state = ST_MONITOR
        return bars

    def _isBull(self, bar):
        return float(bar["close"]) > float(bar["open"])

    def _isBear(self, bar):
        return float(bar["close"]) < float(bar["open"])

    def _canEnterDay(self, tradeDt):
        day = str(tradeDt or "")
        return int(self._entriesByDay.get(day, 0)) < self.maxEntriesPerDay

    def _markEnter(self, tradeDt):
        day = str(tradeDt or "")
        self._entriesByDay[day] = int(self._entriesByDay.get(day, 0)) + 1

    def _monitorOk(self, tradeDt):
        """셋업 당일 포함 이후, monitorDays 이내."""
        dt = str(tradeDt or "")
        if not dt or not self._armedFromDt:
            return self.universePrefiltered and bool(self._armedFromDt)
        if dt < self._armedFromDt:
            return False
        # 셋업 당일이 여러 개면 가장 최근 셋업 ≤ 당일
        armed = None
        for s in sorted(self.setupDts):
            if s <= dt:
                armed = s
        if armed is None:
            if self.universePrefiltered and self._armedFromDt:
                armed = self._armedFromDt
            else:
                return False
        if self.monitorDays and self.monitorDays > 0:
            try:
                d0 = datetime.strptime(armed, "%Y%m%d")
                d1 = datetime.strptime(dt, "%Y%m%d")
                if (d1 - d0).days > self.monitorDays:
                    return False
            except ValueError:
                pass
        return True

    def _crossDownMa(self, bars, idx, bar, emaKey):
        ema = bar.get(emaKey)
        if ema is None:
            return False
        close = float(bar["close"])
        if close >= float(ema):
            return False
        if not self.requireCross:
            return True
        if idx <= 0:
            return True
        prev = bars[idx - 1]
        prevEma = prev.get(emaKey)
        if prevEma is None:
            return True
        return float(prev["close"]) >= float(prevEma) and close < float(ema)

    def onBar(self, bars, idx):
        if idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        close = float(bar["close"])
        low = float(bar["low"])
        hms = _barHms(bar)
        tradeDt = bar.get("tradeDt")

        # 셋업 발생 시 MONITOR 로 전환
        if tradeDt and str(tradeDt) in self.setupDts:
            if self.state == ST_IDLE:
                self.state = ST_MONITOR
            if self._armedFromDt is None or str(tradeDt) < self._armedFromDt:
                self._armedFromDt = str(tradeDt)
            elif str(tradeDt) > (self._armedFromDt or ""):
                # 더 최근 셋업으로 갱신 가능
                self._armedFromDt = str(tradeDt)

        # --- 청산 ---
        if self.state == ST_IN_POS and self.pos is not None:
            if not _tmGe(hms, self.exitAfterTm):
                return None
            entry = float(self.pos["entry"])
            inProfit = close > entry
            stopHit = self._isBear(bar) and self._crossDownMa(
                bars, idx, bar, "emaStop"
            )
            tpHit = (
                inProfit
                and self._isBear(bar)
                and self._crossDownMa(bars, idx, bar, "emaFast")
            )
            if stopHit:
                self.state = ST_MONITOR if self._monitorOk(tradeDt) else ST_IDLE
                leave = self.leaveQty
                self.pos = None
                return {
                    "type": "SELL",
                    "price": close,
                    "reason": "stop_ma60_bear_break_leave1",
                    "cntrTm": bar["cntrTm"],
                    "leaveQty": leave,
                }
            if tpHit:
                self.state = ST_MONITOR if self._monitorOk(tradeDt) else ST_IDLE
                leave = self.leaveQty
                self.pos = None
                return {
                    "type": "SELL",
                    "price": close,
                    "reason": "tp_ma20_bear_break_leave1",
                    "cntrTm": bar["cntrTm"],
                    "leaveQty": leave,
                }
            return None

        # --- 진입: 모니터링 중 + 15:15 + 20 터치 ---
        if self.state not in (ST_MONITOR, ST_IDLE):
            return None
        # IDLE 이어도 prefiltered 면 모니터링 취급
        if self.state == ST_IDLE:
            if self.universePrefiltered and self._armedFromDt:
                self.state = ST_MONITOR
            else:
                return None

        if not self._monitorOk(tradeDt):
            return None
        if not _tmGe(hms, self.buyAfterTm):
            return None
        if not self._canEnterDay(tradeDt):
            return None
        if self.useBullFilter and not self._isBull(bar):
            return None

        emaFast = bar.get("emaFast")
        if emaFast is None:
            return None
        if low > float(emaFast):
            return None

        qty = int(self.buyAmtWon // close) if close > 0 else 0
        if qty < 1:
            return None

        self.pos = {
            "entry": close,
            "entryTm": bar["cntrTm"],
            "entryIdx": idx,
            "tradeDt": tradeDt,
            "qty": qty,
        }
        self.state = ST_IN_POS
        self._markEnter(tradeDt)
        return {
            "type": "BUY",
            "price": close,
            "reason": "high_setup_ma20_touch",
            "cntrTm": bar["cntrTm"],
            "qty": qty,
            "buyAmtWon": self.buyAmtWon,
            # 포착일 = 셋업(최초 신고 등) 일자 — 검증 리포트용
            "setupDt": self._armedFromDt or tradeDt,
            "catchDt": self._armedFromDt or tradeDt,
        }
