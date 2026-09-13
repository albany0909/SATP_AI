"""
장대양봉 2050이평매매 (bullBodyMa2050_v1)

1) 유니버스(조건식 근사, 일봉):
   P: 1봉전 거래대금(백만) ≥ minAmtMn
   J: 1봉전 양봉(몸통)
   R: 1봉전 = 최근 20봉 중 최고 거래량
   S: 0봉전 종가 SMA1×SMA20 골든 (실무: 종가 상향 돌파 SMA20)
   T: 1봉전 종가 SMA1×SMA50 골든
   → P∧J∧R∧(S∨T)

2) 매수: 선정 후 일봉 SMA20 또는 SMA60 터치 확인
         → 5분봉에서 해당 이평 근처 첫 양봉 시 buyAmtWon 매수

3) 익절/매도: 매수 당일 exitEodTm(15:18) 이후 전량 매도
   (15분 이평 이탈 익절은 use15mTp=true 일 때만, 기본 OFF)
"""

from datetime import datetime

from strategies.indicators import calcSmaSeries

ST_IDLE = "IDLE"
ST_ARMED = "ARMED"  # 조건식 셋업 후 일 터치 대기
ST_WAIT_5M = "WAIT_5M"  # 일 터치 후 5분 모니터링
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


def _parseHm(s):
    text = str(s or "0").zfill(6)[-6:]
    return int(text[0:2]), int(text[2:4]), int(text[4:6])


def _bucketKey(tradeDt, barTm, bucketMin):
    """일자 + 버킷 시작 시각(HHmmss) 키."""
    h, m, s = _parseHm(barTm)
    total = h * 60 + m
    bucketStart = (total // bucketMin) * bucketMin
    bh, bm = divmod(bucketStart, 60)
    return f"{tradeDt}{bh:02d}{bm:02d}00"


def aggDayBars(minBars):
    """1분(또는 분봉) → 일봉 오름차순 [{dt,open,high,low,close,qty,amtMn}]."""
    byDt = {}
    order = []
    for b in minBars:
        dt = str(b.get("tradeDt") or "").strip()
        if len(dt) != 8:
            continue
        o = float(b["open"])
        h = float(b["high"])
        l = float(b["low"])
        c = float(b["close"])
        q = float(b.get("qty") or 0)
        # 거래대금(백만) 근사: 수량×종가 / 1e6
        amt = (q * c) / 1_000_000.0
        if dt not in byDt:
            byDt[dt] = {
                "dt": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "qty": q,
                "amtMn": amt,
            }
            order.append(dt)
        else:
            d = byDt[dt]
            d["high"] = max(d["high"], h)
            d["low"] = min(d["low"], l)
            d["close"] = c
            d["qty"] += q
            d["amtMn"] += amt
    order.sort()
    return [byDt[d] for d in order]


def resampleMinBars(minBars, bucketMin):
    """
    분봉 → N분봉 오름차순.
    반환 bars에 _endIdx = 원본 1분 마지막 인덱스 (봉 완료 시점용).
    """
    if bucketMin <= 1:
        out = []
        for i, b in enumerate(minBars):
            nb = dict(b)
            nb["_endIdx"] = i
            nb["_bucketMin"] = 1
            out.append(nb)
        return out

    buckets = []
    curKey = None
    cur = None
    for i, b in enumerate(minBars):
        dt = str(b.get("tradeDt") or "")
        key = _bucketKey(dt, _barHms(b), bucketMin)
        if key != curKey:
            if cur is not None:
                buckets.append(cur)
            curKey = key
            cur = {
                "tradeDt": dt,
                "barTm": key[8:] if len(key) >= 14 else _barHms(b),
                "cntrTm": b.get("cntrTm"),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "qty": float(b.get("qty") or 0),
                "_endIdx": i,
                "_bucketMin": bucketMin,
            }
        else:
            cur["high"] = max(cur["high"], float(b["high"]))
            cur["low"] = min(cur["low"], float(b["low"]))
            cur["close"] = float(b["close"])
            cur["qty"] += float(b.get("qty") or 0)
            cur["cntrTm"] = b.get("cntrTm")
            cur["_endIdx"] = i
            cur["barTm"] = key[8:] if len(key) >= 14 else _barHms(b)
    if cur is not None:
        buckets.append(cur)
    return buckets


def _crossUp(prevClose, prevSma, close, sma):
    """종가 기준 하방→상방 골든 (SMA1≈종가)."""
    if prevSma is None or sma is None:
        return False
    try:
        return float(prevClose) <= float(prevSma) and float(close) > float(sma)
    except (TypeError, ValueError):
        return False


def findSetupDts(days, minAmtMn, volLookback, smaFast, smaMid):
    """
    조건식 근사 셋업 일자 집합 (조건이 만족된 '0봉전' 일자).
    """
    setups = set()
    if not days or len(days) < max(volLookback, smaMid) + 2:
        return setups

    closes = [float(d["close"]) for d in days]
    sma20 = calcSmaSeries(closes, int(smaFast))
    sma50 = calcSmaSeries(closes, int(smaMid))

    for i in range(1, len(days)):
        prev = days[i - 1]
        cur = days[i]
        # P: 1봉전 거래대금
        if float(prev.get("amtMn") or 0) < float(minAmtMn):
            continue
        # J: 1봉전 양봉
        if float(prev["close"]) <= float(prev["open"]):
            continue
        # R: 1봉전 = 최근 volLookback 봉 최고 거래량 (prev 포함 구간)
        j0 = max(0, i - volLookback)
        window = days[j0:i]  # 직전 lookback 개 (prev 포함, 당일 미포함)
        if not window:
            continue
        maxQty = max(float(d["qty"]) for d in window)
        if float(prev["qty"]) < maxQty - 1e-9:
            continue
        # S: 0봉전 종가×SMA20 골든
        sOk = False
        if i >= 1 and sma20[i] is not None and sma20[i - 1] is not None:
            sOk = _crossUp(
                days[i - 1]["close"],
                sma20[i - 1],
                days[i]["close"],
                sma20[i],
            )
        # T: 1봉전 종가×SMA50 골든
        tOk = False
        if i >= 2 and sma50[i - 1] is not None and sma50[i - 2] is not None:
            tOk = _crossUp(
                days[i - 2]["close"],
                sma50[i - 2],
                days[i - 1]["close"],
                sma50[i - 1],
            )
        if not (sOk or tOk):
            continue
        setups.add(cur["dt"])
    return setups


class BullBodyMa2050Strategy:
    """장대양봉 후 20/60 단순이평 터치 → 5분 첫 양봉 매수 / 15:18 전량 청산."""

    def __init__(self, params=None):
        p = params or {}
        self.smaTouchA = int(p.get("smaTouchA", 20))  # 일 터치1
        self.smaTouchB = int(p.get("smaTouchB", 60))  # 일 터치2
        self.smaFast = int(p.get("smaFast", 20))  # 조건 S
        self.smaMid = int(p.get("smaMid", 50))  # 조건 T
        self.minAmtMn = float(p.get("minAmtMn", 10000))  # 백만 단위
        self.volLookback = int(p.get("volLookback", 20))
        self.entryBarMin = int(p.get("entryBarMin", 5))
        self.exitBarMin = int(p.get("exitBarMin", 15))
        self.buyAmtWon = float(p.get("buyAmtWon", 1_000_000))
        # 매수 당일 익절(전량): 기본 15:18
        self.exitEodTm = str(p.get("exitEodTm", "151800")).zfill(6)[-6:]
        self.maxEntriesPerDay = int(p.get("maxEntriesPerDay", 1))
        self.monitorDays = int(p.get("monitorDays", 10))
        self.universePrefiltered = bool(p.get("universePrefiltered", True))
        # 터치 우선: 20 먼저 확인, 없으면 60
        self.preferSmaA = bool(p.get("preferSmaA", True))
        # 15분 이평 하회 익절 — 명세 변경 후 기본 미사용
        self.use15mTp = bool(p.get("use15mTp", False))

        self.state = ST_IDLE
        self.pos = None
        self._entriesByDay = {}
        self.setupDts = set()
        self._armedFromDt = None
        self._touchMa = None  # 20 or 60
        self._touchLevel = None  # 고정 이평값 (터치 확정 시)
        self._touchDt = None
        # 미리 계산
        self._dayByDt = {}
        self._daySmaA = {}  # dt → sma
        self._daySmaB = {}
        self._r5 = []
        self._r15 = []
        self._r5EndToI = {}  # minIdx → r5 bar index (봉 완료된 그 봉)
        self._r15EndToI = {}
        self._minBars = []

    def prepare(self, bars):
        """1분봉에 일 SMA 붙이고 5/15분 리샘플 캐시."""
        self._minBars = bars
        days = aggDayBars(bars)
        closes = [float(d["close"]) for d in days]
        sa = calcSmaSeries(closes, self.smaTouchA)
        sb = calcSmaSeries(closes, self.smaTouchB)
        for i, d in enumerate(days):
            self._dayByDt[d["dt"]] = d
            self._daySmaA[d["dt"]] = sa[i]
            self._daySmaB[d["dt"]] = sb[i]

        self.setupDts = findSetupDts(
            days,
            minAmtMn=self.minAmtMn,
            volLookback=self.volLookback,
            smaFast=self.smaFast,
            smaMid=self.smaMid,
        )
        if self.setupDts:
            self._armedFromDt = min(self.setupDts)
            self.state = ST_ARMED
        elif self.universePrefiltered and days:
            # 관심/외부 유니버스: 전 구간 모니터링 가정 (조건 근사 0건 시)
            self._armedFromDt = days[0]["dt"]
            self.state = ST_ARMED

        # 각 1분에 전일 확정 이평 (당일 장중 레벨 = 직전 거래일 SMA)
        prevSmaA = prevSmaB = None
        dayList = sorted(self._dayByDt.keys())
        prevOf = {}
        for k in range(1, len(dayList)):
            prevOf[dayList[k]] = dayList[k - 1]
        for b in bars:
            dt = str(b.get("tradeDt") or "")
            pDt = prevOf.get(dt)
            if pDt:
                b["daySmaA"] = self._daySmaA.get(pDt)
                b["daySmaB"] = self._daySmaB.get(pDt)
            else:
                b["daySmaA"] = None
                b["daySmaB"] = None
            # 당일 종가형 SMA (일 터치 판별 보조, 장후 근사)
            b["daySmaAToday"] = self._daySmaA.get(dt)
            b["daySmaBToday"] = self._daySmaB.get(dt)

        self._r5 = resampleMinBars(bars, self.entryBarMin)
        self._r15 = resampleMinBars(bars, self.exitBarMin)
        self._r5EndToI = {int(x["_endIdx"]): i for i, x in enumerate(self._r5)}
        self._r15EndToI = {int(x["_endIdx"]): i for i, x in enumerate(self._r15)}
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
        dt = str(tradeDt or "")
        if not dt or not self._armedFromDt:
            return False
        if dt < self._armedFromDt:
            return False
        # 최근 셋업 ≤ 당일
        armed = None
        for s in sorted(self.setupDts):
            if s <= dt:
                armed = s
        if armed is None:
            if self.universePrefiltered:
                armed = self._armedFromDt
            else:
                return False
        if self.monitorDays > 0:
            try:
                d0 = datetime.strptime(armed, "%Y%m%d")
                d1 = datetime.strptime(dt, "%Y%m%d")
                if (d1 - d0).days > self.monitorDays:
                    return False
            except ValueError:
                pass
        return True

    def _checkDayTouch(self, tradeDt, bar):
        """
        일봉 기준 SMA20/60 터치.
        BT: 분봉 누적 high/low 대신 당일 완성일 봉 근사로
        현재 bar 까지의 당일 high/low 로도 가능 — 간단히 당일 day byDt 최종로 터치
        + 장중 low/high 와 전일 SMA 교차.
        """
        smaA = bar.get("daySmaA")
        smaB = bar.get("daySmaB")
        low = float(bar["low"])
        high = float(bar["high"])

        touchedA = smaA is not None and low <= float(smaA) <= high
        touchedB = smaB is not None and low <= float(smaB) <= high
        # 당일 일봉 전체(알고 있는 일 집계)로도 터치 허용
        day = self._dayByDt.get(str(tradeDt))
        if day:
            dLow, dHigh = float(day["low"]), float(day["high"])
            ta = self._daySmaA.get(str(tradeDt))  # 당일 종가포함 SMA — 터치 근사
            tb = self._daySmaB.get(str(tradeDt))
            # 전일 이평과 당일 레인지
            if smaA is not None and dLow <= float(smaA) <= dHigh:
                touchedA = True
            if smaB is not None and dLow <= float(smaB) <= dHigh:
                touchedB = True
            # 당일 SMA 라인 터치 (종가확정 후 일치용)
            if ta is not None and dLow <= float(ta) <= dHigh:
                touchedA = True
            if tb is not None and dLow <= float(tb) <= dHigh:
                touchedB = True

        if self.preferSmaA:
            if touchedA and smaA is not None:
                return self.smaTouchA, float(smaA)
            if touchedB and smaB is not None:
                return self.smaTouchB, float(smaB)
        else:
            if touchedB and smaB is not None:
                return self.smaTouchB, float(smaB)
            if touchedA and smaA is not None:
                return self.smaTouchA, float(smaA)
        return None, None

    def onBar(self, bars, idx):
        if idx < 0 or idx >= len(bars):
            return None
        bar = bars[idx]
        close = float(bar["close"])
        hms = _barHms(bar)
        tradeDt = bar.get("tradeDt")

        # 셋업 일 갱신
        if tradeDt and str(tradeDt) in self.setupDts:
            if self.state == ST_IDLE:
                self.state = ST_ARMED
            if self._armedFromDt is None or str(tradeDt) >= self._armedFromDt:
                self._armedFromDt = str(tradeDt)

        # --- 포지션 청산: 매수 당일 15:18 전량 (기본) ---
        if self.state == ST_IN_POS and self.pos is not None:
            # 옵션: 15분 음봉+이평 하회 익절 (use15mTp=true 만)
            if self.use15mTp:
                r15i = self._r15EndToI.get(idx)
                if r15i is not None:
                    b15 = self._r15[r15i]
                    ref = self.pos.get("touchLevel")
                    if (
                        ref is not None
                        and self._isBear(b15)
                        and float(b15["close"]) < float(ref)
                    ):
                        self.state = ST_IDLE
                        self.pos = None
                        self._touchMa = None
                        self._touchLevel = None
                        return {
                            "type": "SELL",
                            "price": float(b15["close"]),
                            "reason": "tp_15m_bear_below_day_ma",
                            "cntrTm": b15.get("cntrTm") or bar.get("cntrTm"),
                        }

            # 매수 당일 exitEodTm(기본 15:18) 이후 전량 매도
            buyDt = str(self.pos.get("tradeDt") or "")
            if str(tradeDt) == buyDt and _tmGe(hms, self.exitEodTm):
                self.state = ST_IDLE
                self.pos = None
                self._touchMa = None
                self._touchLevel = None
                return {
                    "type": "SELL",
                    "price": close,
                    "reason": "eod_1518_flat",
                    "cntrTm": bar.get("cntrTm"),
                }
            return None

        if not self._monitorOk(tradeDt):
            return None

        # --- 일 터치 확인 → 5분 모니터 ---
        if self.state in (ST_ARMED, ST_IDLE, ST_WAIT_5M):
            if self.state == ST_IDLE and self.universePrefiltered:
                self.state = ST_ARMED
            if self.state == ST_ARMED and self._touchLevel is None:
                maPer, lvl = self._checkDayTouch(tradeDt, bar)
                if maPer is not None and lvl is not None:
                    self._touchMa = maPer
                    self._touchLevel = lvl
                    self._touchDt = str(tradeDt)
                    self.state = ST_WAIT_5M

        # --- 5분 첫 양봉 매수 ---
        if self.state != ST_WAIT_5M or self._touchLevel is None:
            return None
        if not self._canEnterDay(tradeDt):
            return None
        # 청산 시각 이후 신규 진입 금지
        if _tmGe(hms, self.exitEodTm):
            return None

        r5i = self._r5EndToI.get(idx)
        if r5i is None:
            return None
        b5 = self._r5[r5i]
        if not self._isBull(b5):
            return None
        # 터치한 이평을 포함한 양봉 (저가~고가 이평 관통 또는 종가 이평 위)
        ref = float(self._touchLevel)
        lo, hi, o5, c5 = (
            float(b5["low"]),
            float(b5["high"]),
            float(b5["open"]),
            float(b5["close"]),
        )
        touchesLine = lo <= ref <= hi or (o5 <= ref <= c5) or (c5 >= ref and lo <= ref)
        if not touchesLine:
            return None

        px = c5
        qty = int(self.buyAmtWon // px) if px > 0 else 0
        if qty < 1:
            return None

        self.pos = {
            "entry": px,
            "entryTm": b5.get("cntrTm"),
            "entryIdx": idx,
            "tradeDt": tradeDt,
            "qty": qty,
            "touchMa": self._touchMa,
            "touchLevel": self._touchLevel,
            "setupDt": self._armedFromDt or tradeDt,
        }
        self.state = ST_IN_POS
        self._markEnter(tradeDt)
        return {
            "type": "BUY",
            "price": px,
            "reason": f"day_sma{self._touchMa}_touch_5m_bull",
            "cntrTm": b5.get("cntrTm"),
            "qty": qty,
            "buyAmtWon": self.buyAmtWon,
            "setupDt": self._armedFromDt or tradeDt,
            "catchDt": self._touchDt or self._armedFromDt or tradeDt,
            "touchMa": self._touchMa,
        }
