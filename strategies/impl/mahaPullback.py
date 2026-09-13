"""마하세븐 눌림목 전략 — 분봉 바 순회 시그널"""

from strategies.indicators import calcEmaSeries, isDownFractal

# 상태
ST_IDLE = "IDLE"
ST_WAIT_PULLBACK = "WAIT_PULLBACK"
ST_WAIT_FRACTAL = "WAIT_FRACTAL"
ST_WAIT_BREAK = "WAIT_BREAK"
ST_IN_POS = "IN_POSITION"


class MahaPullbackStrategy:
    """
    정배열 → 20이평 눌림 → 다운프랙탈 → 20이평 재돌파 종가 진입.
    손절: 진입 시 stopMa / 또는 종가 < stopMa
    익절: entry + (entry-stopPrice)*rrMult
    """

    def __init__(self, params=None):
        p = params or {}
        self.maFast = int(p.get("maFast", 20))
        self.maMid = int(p.get("maMid", 50))
        self.maSlow = int(p.get("maSlow", 100))
        self.stopMa = int(p.get("stopMa", 5))
        self.fractalLeft = int(p.get("fractalLeft", 2))
        self.fractalRight = int(p.get("fractalRight", 2))
        self.rrMult = float(p.get("rrMult", 1.5))
        self.stopUse = str(p.get("stopUse", "close")).lower()

        self.state = ST_IDLE
        self.pendingFractal = False  # WAIT_BREAK 진입 전 프랙탈 확정 여부
        self.pos = None  # {entry, stopPrice, tpPrice, entryTm, entryIdx}

    def prepare(self, bars):
        """bars[{open,high,low,close,cntrTm,...}]에 ema 필드 부여."""
        closes = [float(b["close"]) for b in bars]
        emaF = calcEmaSeries(closes, self.maFast)
        emaM = calcEmaSeries(closes, self.maMid)
        emaS = calcEmaSeries(closes, self.maSlow)
        emaStop = calcEmaSeries(closes, self.stopMa)
        for i, b in enumerate(bars):
            b["emaFast"] = emaF[i]
            b["emaMid"] = emaM[i]
            b["emaSlow"] = emaS[i]
            b["emaStop"] = emaStop[i]
        # 프랙탈용 저가 배열 캐시 — onBar마다 O(n) 재생성 방지
        self._lows = [float(b["low"]) for b in bars]
        return bars

    def _trendOk(self, bar):
        a, b, c = bar.get("emaFast"), bar.get("emaMid"), bar.get("emaSlow")
        if a is None or b is None or c is None:
            return False
        return a > b > c

    def onBar(self, bars, idx):
        """
        idx 봉 처리. 반환: 이벤트 dict 또는 None
        event: {type: BUY|SELL, price, reason, ...}
        """
        if idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        events = []

        # --- 보유 중 청산 ---
        if self.state == ST_IN_POS and self.pos is not None:
            stopPrice = self.pos["stopPrice"]
            tpPrice = self.pos["tpPrice"]
            emaStop = bar.get("emaStop")
            hitStop = close <= stopPrice
            if self.stopUse == "low":
                hitStop = hitStop or low <= stopPrice
            # 5이평 이탈
            if emaStop is not None and close < emaStop:
                hitStop = True
            hitTp = close >= tpPrice or high >= tpPrice
            if hitStop and hitTp:
                # 같은 봉 — 보수적으로 손절 우선
                events.append(
                    {
                        "type": "SELL",
                        "price": close,
                        "reason": "stop_and_tp_same_bar",
                        "cntrTm": bar["cntrTm"],
                    }
                )
                self.state = ST_IDLE
                self.pos = None
                self.pendingFractal = False
                return events[0] if len(events) == 1 else events
            if hitStop:
                self.state = ST_IDLE
                self.pos = None
                self.pendingFractal = False
                return {
                    "type": "SELL",
                    "price": close,
                    "reason": "stop",
                    "cntrTm": bar["cntrTm"],
                }
            if hitTp:
                # 익절가는 목표가 도달 — 체결은 종가(fill 가정)
                fill = min(close, tpPrice) if close >= tpPrice else tpPrice
                # high가 tp 터치면 tpPrice로 체결 가정 가능하나 1단계는 종가
                fill = close
                self.state = ST_IDLE
                self.pos = None
                self.pendingFractal = False
                return {
                    "type": "SELL",
                    "price": fill,
                    "reason": "take_profit",
                    "cntrTm": bar["cntrTm"],
                }

        # --- 프랙탈 확정 시점: idx - fractalRight (lows는 prepare에서 캐시) ---
        confIdx = idx - self.fractalRight
        fractalNow = False
        if confIdx >= self.fractalLeft and getattr(self, "_lows", None) is not None:
            fractalNow = isDownFractal(
                self._lows, confIdx, left=self.fractalLeft, right=self.fractalRight
            )

        emaFast = bar.get("emaFast")
        prev = bars[idx - 1] if idx > 0 else None
        prevClose = float(prev["close"]) if prev else None
        prevEma = prev.get("emaFast") if prev else None

        if self.state == ST_IDLE:
            if self._trendOk(bar):
                self.state = ST_WAIT_PULLBACK
                self.pendingFractal = False

        if self.state == ST_WAIT_PULLBACK:
            if not self._trendOk(bar):
                self.state = ST_IDLE
            elif emaFast is not None and close < emaFast:
                self.state = ST_WAIT_FRACTAL

        if self.state == ST_WAIT_FRACTAL:
            if not self._trendOk(bar):
                self.state = ST_IDLE
                self.pendingFractal = False
            elif fractalNow:
                self.pendingFractal = True
                self.state = ST_WAIT_BREAK

        if self.state == ST_WAIT_BREAK:
            if not self._trendOk(bar):
                self.state = ST_IDLE
                self.pendingFractal = False
            elif emaFast is not None and prevClose is not None and prevEma is not None:
                # 종가가 20이평 상향 돌파
                crossUp = prevClose <= prevEma and close > emaFast
                if crossUp and self.pendingFractal:
                    stopRef = bar.get("emaStop")
                    if stopRef is None or stopRef >= close:
                        # 손절가 불가 — 스킵
                        return None
                    stopPrice = float(stopRef)
                    risk = close - stopPrice
                    if risk <= 0:
                        return None
                    tpPrice = close + risk * self.rrMult
                    self.pos = {
                        "entry": close,
                        "stopPrice": stopPrice,
                        "tpPrice": tpPrice,
                        "entryTm": bar["cntrTm"],
                        "entryIdx": idx,
                    }
                    self.state = ST_IN_POS
                    self.pendingFractal = False
                    return {
                        "type": "BUY",
                        "price": close,
                        "reason": "ma_break_after_fractal",
                        "cntrTm": bar["cntrTm"],
                        "stopPrice": stopPrice,
                        "tpPrice": tpPrice,
                        "catchDt": bar.get("tradeDt"),
                        "setupDt": bar.get("tradeDt"),
                    }

        return None
