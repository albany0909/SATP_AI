"""
정배열 이평선 돌파매매1 (alignMaBreak_v1)

- 정배열(20>50>100) + 15:15 이후 20이평 터치·양봉 돌파 매수(기본 1,000만원)
- 15:18 이후 음봉으로 60이평 하락 돌파 → 손절(1주 잔여 원칙)
- 15:18 이후 수익·음봉으로 20이평 하락 돌파 → 익절(1주 잔여 원칙)
"""

from strategies.indicators import calcEmaSeries

ST_IDLE = "IDLE"
ST_IN_POS = "IN_POSITION"


def _barHms(bar):
    """
    봉 시각 HHmmss 문자열.
    BAR_TM / CNTR_TM 하위 6자리 사용.
    """
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
    """barTm >= thrTm (문자열 HHmmss 비교)."""
    a = str(barTm or "0").zfill(6)[-6:]
    b = str(thrTm or "0").zfill(6)[-6:]
    return a >= b


class AlignMaBreakStrategy:
    """15:15 이후 정배열·20선 돌파 진입 / 15:18 이후 60·20 이탈 청산."""

    def __init__(self, params=None):
        p = params or {}
        self.maFast = int(p.get("maFast", 20))
        self.maMid = int(p.get("maMid", 50))
        self.maSlow = int(p.get("maSlow", 100))
        self.exitStopMa = int(p.get("exitStopMa", 60))
        self.buyAfterTm = str(p.get("buyAfterTm", "151500")).zfill(6)[-6:]
        self.exitAfterTm = str(p.get("exitAfterTm", "151800")).zfill(6)[-6:]
        self.buyAmtWon = float(p.get("buyAmtWon", 10_000_000))
        self.leaveQty = int(p.get("leaveQty", 1))
        self.requireCross = bool(p.get("requireCross", True))
        self.maxEntriesPerDay = int(p.get("maxEntriesPerDay", 1))
        self.useBullFilter = bool(p.get("useBullFilter", True))

        self.state = ST_IDLE
        self.pos = None  # {entry, entryTm, entryIdx, tradeDt, qty}
        self._entriesByDay = {}

    def prepare(self, bars):
        """OHLC 봉에 EMA 필드 부여."""
        closes = [float(b["close"]) for b in bars]
        emaF = calcEmaSeries(closes, self.maFast)
        emaM = calcEmaSeries(closes, self.maMid)
        emaS = calcEmaSeries(closes, self.maSlow)
        emaStop = calcEmaSeries(closes, self.exitStopMa)
        for i, b in enumerate(bars):
            b["emaFast"] = emaF[i]
            b["emaMid"] = emaM[i]
            b["emaSlow"] = emaS[i]
            b["emaStop"] = emaStop[i]
        return bars

    def _trendOk(self, bar):
        a, b, c = bar.get("emaFast"), bar.get("emaMid"), bar.get("emaSlow")
        if a is None or b is None or c is None:
            return False
        return a > b > c

    def _isBull(self, bar):
        return float(bar["close"]) > float(bar["open"])

    def _isBear(self, bar):
        return float(bar["close"]) < float(bar["open"])

    def _canEnterDay(self, tradeDt):
        day = str(tradeDt or "")
        used = int(self._entriesByDay.get(day, 0))
        return used < self.maxEntriesPerDay

    def _markEnter(self, tradeDt):
        day = str(tradeDt or "")
        self._entriesByDay[day] = int(self._entriesByDay.get(day, 0)) + 1

    def _crossUpFast(self, bars, idx, bar):
        """종가 > 20이평이면 돌파 (터치 조건은 onBar 에서 별도 검사)."""
        ema = bar.get("emaFast")
        if ema is None:
            return False
        return float(bar["close"]) > float(ema)

    def _crossDownMa(self, bars, idx, bar, emaKey):
        """종가가 해당 이평 하향 돌파."""
        ema = bar.get(emaKey)
        if ema is None:
            return False
        close = float(bar["close"])
        if close >= float(ema):
            return False
        if not self.requireCross:
            return True
        if idx <= 0:
            return close < float(ema)
        prev = bars[idx - 1]
        prevEma = prev.get(emaKey)
        prevClose = float(prev["close"])
        if prevEma is None:
            return close < float(ema)
        return prevClose >= float(prevEma) and close < float(ema)

    def onBar(self, bars, idx):
        """
        idx 봉 처리. BUY/SELL 이벤트 또는 None.
        leaveQty 는 reason·메타에 기록. BT 엔진은 전량 SELL 수익 계산.
        """
        if idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        close = float(bar["close"])
        low = float(bar["low"])
        openP = float(bar["open"])
        hms = _barHms(bar)
        tradeDt = bar.get("tradeDt")

        # --- 보유 중 청산 (15:18 이후) ---
        if self.state == ST_IN_POS and self.pos is not None:
            if not _tmGe(hms, self.exitAfterTm):
                return None
            entry = float(self.pos["entry"])
            inProfit = close > entry

            # 손절: 음봉 + 60이평 하락 돌파
            stopHit = (
                self._isBear(bar)
                and self._crossDownMa(bars, idx, bar, "emaStop")
            )
            # 익절: 수익 + 음봉 + 20이평 하락 돌파
            tpHit = (
                inProfit
                and self._isBear(bar)
                and self._crossDownMa(bars, idx, bar, "emaFast")
            )

            if stopHit:
                # 동일 봉 익절 동시 → 손절 우선
                self.state = ST_IDLE
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
                self.state = ST_IDLE
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

        # --- 진입 (15:15 이후) ---
        if self.state != ST_IDLE:
            return None
        if not _tmGe(hms, self.buyAfterTm):
            return None
        if not self._canEnterDay(tradeDt):
            return None
        if not self._trendOk(bar):
            return None
        if self.useBullFilter and not self._isBull(bar):
            return None

        emaFast = bar.get("emaFast")
        if emaFast is None:
            return None
        # 20이평 터치: 저가가 이평 이하로 접촉
        touched = low <= float(emaFast)
        if not touched:
            return None
        if not self._crossUpFast(bars, idx, bar):
            return None

        # 매수 수량 (정보용, 최소 1주)
        qty = int(self.buyAmtWon // close) if close > 0 else 0
        if qty < 1:
            return None

        self.pos = {
            "entry": close,
            "entryTm": bar["cntrTm"],
            "entryIdx": idx,
            "tradeDt": tradeDt,
            "qty": qty,
            "openP": openP,
        }
        self.state = ST_IN_POS
        self._markEnter(tradeDt)
        return {
            "type": "BUY",
            "price": close,
            "reason": "ma20_touch_bull_break_align",
            "cntrTm": bar["cntrTm"],
            "qty": qty,
            "buyAmtWon": self.buyAmtWon,
            # 포착일 미구분 시 매수일 사용
            "catchDt": tradeDt,
            "setupDt": tradeDt,
        }
