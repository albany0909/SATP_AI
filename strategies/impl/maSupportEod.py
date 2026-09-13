"""
이평선지지 매매 (maSupportEod_v1 / MSE01)

- SMA5 상향 돌파 이후, 약음봉·십자/소형몸통 + 5일선 지지 + 거래량<최근양봉 매수
- 보유 중 장마감 종가 < SMA5 전량 매도
- 실전: maSupportEodLive 장중 창 (종가 근접)
"""

from strategies.indicators import calcSmaSeries

ST_IDLE = "IDLE"
ST_IN_POS = "IN_POSITION"


class MaSupportEodStrategy:
    """일봉 종가 기준 지지 매수 / SMA 이탈 청산."""

    def __init__(self, params=None):
        p = params or {}
        self.params = p
        self.smaLen = max(2, int(p.get("smaLen", 5)))
        self.weakBearMaxPct = float(p.get("weakBearMaxPct", 2.5))
        # 십자·소형 양봉도 지지 봉으로 인정 (시가 대비 몸통 %)
        self.dojiMaxPct = float(p.get("dojiMaxPct", 0.5))
        self.buyAmtWon = max(10_000.0, float(p.get("buyAmtWon", 2_000_000)))
        # 돌파 당일 봉에서는 매수하지 않음
        self.banSameDayBreakoutBuy = bool(p.get("banSameDayBreakoutBuy", True))
        # 당일 거래량 < 최근 N개 양봉 거래량(최솟값). 0 이면 거래량 필터 없음
        self.volCmpBullDays = max(0, int(p.get("volCmpBullDays", 3)))

        self.state = ST_IDLE
        self.pos = None  # {qty, avgPx, entryDt}
        self.brokeOut = False

    def prepare(self, bars):
        """전일까지 확정 SMA 를 각 봉에 붙인다."""
        if not bars:
            return bars
        closes = [float(b["close"]) for b in bars]
        series = calcSmaSeries(closes, self.smaLen)
        for i, b in enumerate(bars):
            # 당일 종가 미포함 → 직전 봉까지의 SMA
            srcI = i - 1
            b["sma"] = {
                self.smaLen: series[srcI] if srcI >= 0 else None,
            }
            # 엔진·리포트 호환
            b.setdefault("cntrTm", b.get("tradeDt"))
            b.setdefault("barTm", "153000")
            b.setdefault("tf", "1d")
        return bars

    def _resetPos(self):
        self.pos = None
        self.state = ST_IDLE

    def _bodyPct(self, o, c):
        if o is None or c is None or float(o) <= 0:
            return None
        return abs(float(c) - float(o)) / float(o) * 100.0

    def _isWeakBear(self, o, c):
        if o <= 0 or c <= 0 or c >= o:
            return False
        bodyPct = (o - c) / o * 100.0
        return 0 < bodyPct <= self.weakBearMaxPct

    def _isDojiOrSmallBody(self, o, c):
        """십자·소형몸통 (약양봉 포함). 장대양봉은 제외."""
        bodyPct = self._bodyPct(o, c)
        return bodyPct is not None and bodyPct <= self.dojiMaxPct

    def _isEntryBar(self, o, c):
        """매수 가능 봉: 약음봉 또는 십자/소형몸통."""
        return self._isWeakBear(o, c) or self._isDojiOrSmallBody(o, c)

    def _entryReason(self, o, c):
        if self._isWeakBear(o, c):
            return "sma5_support_weak_bear"
        if self._isDojiOrSmallBody(o, c):
            return "sma5_support_doji"
        return ""

    def _sma5Support(self, low, close, ma):
        return ma is not None and float(low) <= float(ma) <= float(close)

    def _isBullBar(self, bar):
        """양봉: 종가 > 시가."""
        try:
            o = float(bar.get("open") or 0)
            c = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            return False
        return o > 0 and c > o

    def _barQty(self, bar):
        try:
            return int(bar.get("qty") or 0)
        except (TypeError, ValueError):
            return 0

    def _recentBullQtys(self, bars, idx, n):
        """idx 직전 양봉 n개의 거래량 (최신→과거). 거래량 0 봉은 건너뛴다."""
        out = []
        if n <= 0 or not bars:
            return out
        for i in range(idx - 1, -1, -1):
            if not self._isBullBar(bars[i]):
                continue
            qty = self._barQty(bars[i])
            if qty <= 0:
                continue
            out.append(qty)
            if len(out) >= n:
                break
        return out

    def _volLtRecentBull(self, bars, idx):
        """
        당일 거래량이 최근 양봉 N개의 거래량보다 작아야 한다.
        기준은 N개 중 최솟값 — 모든 해당 양봉보다 작아야 통과.
        """
        n = int(self.volCmpBullDays or 0)
        if n <= 0:
            return True, None, None
        bulls = self._recentBullQtys(bars, idx, n)
        if len(bulls) < n:
            return False, "VOL_NO_BULL", f"최근 양봉 {len(bulls)}/{n}"
        todayQty = self._barQty(bars[idx])
        if todayQty <= 0:
            return False, "VOL_NONE", "당일 거래량 없음"
        refQty = min(bulls)
        if todayQty >= refQty:
            return (
                False,
                "VOL_GE_BULL",
                f"거래량 {todayQty:,} ≥ 양봉최소 {refQty:,}",
            )
        return True, None, None

    def explainNoBuy(self, bars, idx, brokeOut=None):
        """
        매수 미체결 사유. 복기 BUY_SKIP 용.
        brokeOut 을 넘기면 onBar 이후 플래그 변경과 무관하게 판정한다.
        반환: (reasonCode, note) 또는 (None, None) 이면 매수 가능.
        """
        if not bars or idx < 0 or idx >= len(bars):
            return "NO_OHLC", "당일 봉 없음"
        bar = bars[idx]
        o = float(bar.get("open") or 0)
        low = float(bar.get("low") or 0)
        close = float(bar.get("close") or 0)
        ma = (bar.get("sma") or {}).get(self.smaLen)
        if ma is None:
            return "NO_SMA", "SMA 없음"
        ma = float(ma)
        if self.pos is not None:
            return "IN_POS", "이미 보유"
        flag = self.brokeOut if brokeOut is None else bool(brokeOut)
        if not flag:
            return "NO_BREAKOUT", "SMA5 돌파 이력 없음"
        bodyPct = self._bodyPct(o, close)
        bodyTxt = f"{bodyPct:.2f}%" if bodyPct is not None else "-"
        if not self._isEntryBar(o, close):
            if close >= o:
                return "NOT_BEAR", f"양봉 몸통 {bodyTxt} (한도 십자 {self.dojiMaxPct:g}%)"
            return (
                "BEAR_TOO_FAT",
                f"음봉 몸통 {bodyTxt} > {self.weakBearMaxPct:g}%",
            )
        if not self._sma5Support(low, close, ma):
            return (
                "NO_SMA5_SUPPORT",
                f"지지 아님 low={low:.0f} sma={ma:.0f} c={close:.0f}",
            )
        volOk, volCode, volNote = self._volLtRecentBull(bars, idx)
        if not volOk:
            return volCode, volNote
        return None, None

    def onBar(self, bars, idx):
        if not bars or idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        o = float(bar["open"])
        low = float(bar["low"])
        close = float(bar["close"])
        tradeDt = bar.get("tradeDt")
        cntrTm = bar.get("cntrTm") or tradeDt
        smaMap = bar.get("sma") or {}
        ma = smaMap.get(self.smaLen)
        if ma is None:
            return None
        ma = float(ma)

        prev = bars[idx - 1] if idx > 0 else None
        prevClose = float(prev["close"]) if prev is not None else None
        events = []

        # 1) 보유 중 종가 < SMA → 전량 청산
        if self.pos is not None and close < ma:
            qty = int(self.pos["qty"])
            events.append(
                {
                    "type": "SELL",
                    "price": close,
                    "reason": "sma5_break_eod",
                    "cntrTm": cntrTm,
                    "qty": qty,
                }
            )
            self._resetPos()
            self.brokeOut = False
            # 청산 봉에서는 재매수하지 않음. 돌파 갱신만 아래서 처리

        # 2) 매수: 돌파 이력 + 지지봉 + SMA5 지지 + 거래량<최근양봉 (미보유)
        if self.pos is None and self.brokeOut:
            volOk, _, _ = self._volLtRecentBull(bars, idx)
            if (
                self._isEntryBar(o, close)
                and self._sma5Support(low, close, ma)
                and volOk
            ):
                qty = int(self.buyAmtWon // close) if close > 0 else 0
                if qty >= 1:
                    self.pos = {
                        "qty": qty,
                        "avgPx": close,
                        "entryDt": tradeDt,
                    }
                    self.state = ST_IN_POS
                    events.append(
                        {
                            "type": "BUY",
                            "price": close,
                            "reason": self._entryReason(o, close),
                            "cntrTm": cntrTm,
                            "qty": qty,
                            "buyAmtWon": self.buyAmtWon,
                        }
                    )

        # 3) 돌파/리셋 — 당일 종가 확정 후 반영 (다음날 매수부터 사용)
        if prevClose is not None and prevClose <= ma < close:
            self.brokeOut = True
        elif self.pos is None and close < ma:
            self.brokeOut = False

        return events or None
