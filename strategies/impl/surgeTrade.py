"""급등주매매(SRG01 / surgeTrade_v1) 순수 판단.

주문·네트워크·DB·텔레그램을 호출하지 않는다.
수익률은 현재 보유수량의 실제 체결가 가중평균(수수료 제외)이다.
최초 체결가만 따로 보관하며 임계값에 쓰지 않는다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

ALG_CD = "SRG01"
ALG_VER = 37
STRAT_ID = "surgeTrade_v1"
ENGLISH_NAME = "Surge Trade"
NAME_KR = "급등주매매"

BUDGET_KRW = 1_000_000
FEE_RATE = 0.0015
ADD1_PCT = 5.0
ADD2_PCT = 10.0
FIRST_STOP_PCT = -5.0
FULL_STOP_PCT = -10.0
FIRST_SURGE_PCT = 10.0
QUOTE_MAX_AGE_SEC = 20
DECISION_SNAP_MAX_AGE_SEC = 120
# 퍼센트 포인트. 5.0000001 과 5 를 같은 경계로 보지 않을 만큼만 부동소수 오차를 흡수한다.
PCT_EPS = 1e-6

PHASE_FLAT = "FLAT"
PHASE_PENDING = "PENDING_ENTRY"
PHASE_OPEN = "OPEN"
PHASE_STOP1 = "STOP1"
PHASE_FALLBACK = "PENDING_FALLBACK"
PHASE_CLOSED = "CLOSED"


def _ymd(dt):
    return dt.strftime("%Y%m%d")


def _hms(dt):
    return dt.strftime("%H%M%S")


def _asDt(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip().replace("T", " ").replace("-", "").replace(":", "")
    text = text.replace(" ", "")
    if len(text) >= 14 and text[:14].isdigit():
        return datetime.strptime(text[:14], "%Y%m%d%H%M%S")
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d")
    raise ValueError(f"시각 형식 아님: {value}")


def retPct(px, avg):
    """가중평균 체결가 대비 수익률(%). avg 가 없으면 None."""
    try:
        price = float(px)
        basis = float(avg)
    except (TypeError, ValueError):
        return None
    if price <= 0 or basis <= 0:
        return None
    return (price / basis - 1.0) * 100.0


def _cmp(ret, threshold):
    """ret ? threshold. 같음 0, 큼 1, 작음 -1. None 이면 None."""
    if ret is None:
        return None
    if abs(float(ret) - float(threshold)) <= PCT_EPS:
        return 0
    return 1 if float(ret) > float(threshold) else -1


def affordableQty(budgetKrw, px, feeRate=FEE_RATE):
    """수수료 포함 예산으로 살 수 있는 정수 수량. 1주 미만이면 0."""
    try:
        budget = float(budgetKrw)
        price = float(px)
        fee = float(feeRate)
    except (TypeError, ValueError):
        return 0
    if budget <= 0 or price <= 0 or fee < 0:
        return 0
    unit = price * (1.0 + fee)
    if unit <= 0:
        return 0
    return int(budget // unit)


def cashForQty(qty, px, feeRate=FEE_RATE):
    try:
        return float(qty) * float(px) * (1.0 + float(feeRate))
    except (TypeError, ValueError):
        return None


def orderStyle(hms):
    """
    정규장 주문 형태.
    15:20 이전 연속매매는 시장가(kt10000 trde_tp=3).
    15:20~15:30 장마감 동시호가는 명세에 전용 코드가 없어 보통 지정가(0)만 쓴다.
    81(장후 시간외)은 쓰지 않는다. 정규장 밖이면 None.
    """
    text = str(hms or "").zfill(6)[-6:]
    if text < "090000" or text >= "153000":
        return None
    if text >= "152000":
        return {
            "trdeTp": "0",
            "style": "LIMIT_CLOSING_AUCTION",
            "note": (
                "kt10000 에 장마감 동시호가 전용 trde_tp 가 없다. "
                "15:20~15:30 은 보통 지정가 0 만 사용. 시장가 3·시간외 81 금지."
            ),
        }
    return {
        "trdeTp": "3",
        "style": "MARKET_CONTINUOUS",
        "note": "연속매매 시장가 3. 15:20 이전.",
    }


def quoteFresh(quote, now, maxAgeSec=QUOTE_MAX_AGE_SEC):
    if not isinstance(quote, dict):
        return False, "NO_QUOTE"
    try:
        px = float(quote.get("px"))
    except (TypeError, ValueError):
        return False, "NO_PRICE"
    if px <= 0:
        return False, "NO_PRICE"
    if quote.get("stale") is True:
        return False, "STALE_QUOTE"
    asof = quote.get("asof")
    if asof is None:
        return False, "QUOTE_ASOF_MISSING"
    try:
        seen = _asDt(asof)
        cur = _asDt(now)
    except ValueError:
        return False, "QUOTE_ASOF_BAD"
    if seen > cur:
        return False, "QUOTE_FUTURE"
    age = (cur - seen).total_seconds()
    if age > float(maxAgeSec):
        return False, "STALE_QUOTE"
    return True, ""


def decisionSnapOk(candidate, now, maxAgeSec=DECISION_SNAP_MAX_AGE_SEC):
    """15:18 주문은 15:18 이후 관측 스냅만. 16:00 스냅·미래 시각은 거부."""
    if not isinstance(candidate, dict):
        return False, "NO_CANDIDATE"
    slot = str(candidate.get("snapSlot") or "")
    if slot in ("1600", "16:00", "FINAL"):
        return False, "FUTURE_OR_FINAL_SNAP"
    observed = candidate.get("snapObservedAt")
    if not observed:
        return False, "SNAP_MISSING"
    try:
        seen = _asDt(observed)
        cur = _asDt(now)
    except ValueError:
        return False, "SNAP_TIME_BAD"
    if seen > cur:
        return False, "SNAP_FUTURE"
    if _ymd(seen) != _ymd(cur) or _hms(seen) < "151800":
        return False, "SNAP_BEFORE_1518"
    if (cur - seen).total_seconds() > float(maxAgeSec):
        return False, "SNAP_STALE"
    basis = str(candidate.get("dailyBasis") or "")
    if basis != "ka00198_base_comp_chgr":
        return False, "DAILY_BASIS_UNVERIFIED"
    return True, ""


def _blankPos(stkCd):
    return {
        "stkCd": str(stkCd),
        "cycle": 1,
        "phase": PHASE_FLAT,
        "qty": 0,
        "avgPx": 0.0,
        "firstPx": 0.0,
        "costPxQty": 0.0,
        "entryYmd": "",
        "entryKind": "",
        "stage": 0,
        "addsOn": {},
        "fallbackYmd": "",
        "needPosCross": False,
        "eventId": "",
        "eventFirstYmd": "",
        "blockRebuy": "",
        "pending": None,
        "rejected": [],
        "spent": 0.0,
        "frozen": "",
    }


class SurgeBook:
    """종목별 사이클. decide 는 주문을 만들지, 넣지는 않는다."""

    def __init__(self, calendar=None):
        self.pos = {}
        self._lock = threading.Lock()
        self._calendar = calendar

    def _isTrading(self, ymd):
        if self._calendar is not None:
            return bool(self._calendar.isTrading(ymd))
        from config.krHoliday import isTradingDay

        return bool(isTradingDay(ymd))

    def _nextTrading(self, ymd):
        if self._calendar is not None:
            return str(self._calendar.nextTrading(ymd) or "")
        from config.krHoliday import nextTradingDay

        return str(nextTradingDay(ymd) or "")

    def position(self, stkCd):
        cd = str(stkCd)
        if cd not in self.pos:
            self.pos[cd] = _blankPos(cd)
        return self.pos[cd]

    def snapshot(self):
        return {"positions": {k: _copyPos(v) for k, v in self.pos.items()}}

    def restore(self, data):
        self.pos = {}
        raw = (data or {}).get("positions") or {}
        for cd, row in raw.items():
            base = _blankPos(cd)
            if isinstance(row, dict):
                base.update(row)
                base["stkCd"] = str(cd)
                base["addsOn"] = dict(row.get("addsOn") or {})
                base["rejected"] = list(row.get("rejected") or [])
            self.pos[str(cd)] = base

    def tryBegin(self):
        return self._lock.acquire(blocking=False)

    def end(self):
        if self._lock.locked():
            try:
                self._lock.release()
            except RuntimeError:
                pass

    def decide(self, now, quotes, candidates, cash, ownOk=None, entryOk=None):
        """
        한 사이클 판단. 종목당 주문은 최대 1건.
        quotes: {stkCd: quote}
        candidates: 최초·폴백 후보 목록
        cash: 사용 가능 현금(원). 레버리지 없음.
        ownOk: {stkCd: True} 일 때만 신규·재매수. 없으면 매수 거부.
        entryOk: None 이면 기존과 같다. dict 이면 True 가 아닌 종목의 매수는 막는다.
                 매도는 막지 않는다.
        """
        if not self.tryBegin():
            return {"ok": False, "reason": "DUPLICATE_CYCLE", "actions": []}
        try:
            return self._decide(
                now, quotes or {}, candidates or [], cash, ownOk or {}, entryOk
            )
        finally:
            self.end()

    def _entryOpen(self, stkCd, entryOk):
        if entryOk is None:
            return True
        return entryOk.get(str(stkCd)) is True

    def _decide(self, now, quotes, candidates, cash, ownOk, entryOk=None):
        cur = _asDt(now)
        ymd = _ymd(cur)
        hms = _hms(cur)
        if not self._isTrading(ymd):
            return {"ok": True, "reason": "NOT_TRADING_DAY", "actions": [], "cash": cash}
        actions = []
        left = float(cash or 0)
        seen = set()
        for cd, pos in list(self.pos.items()):
            if int(pos.get("qty") or 0) > 0 or pos.get("phase") not in (PHASE_FLAT, PHASE_CLOSED):
                seen.add(cd)
                act, left = self._decideHeld(
                    pos, cur, quotes.get(cd), left, ownOk.get(cd), entryOk
                )
                if act:
                    actions.append(act)
        for row in candidates:
            cd = str((row or {}).get("stkCd") or "")
            if not cd or cd in seen:
                continue
            pos = self.position(cd)
            if int(pos.get("qty") or 0) > 0 or pos.get("phase") not in (PHASE_FLAT, PHASE_CLOSED, ""):
                continue
            act, left = self._decideFlat(
                pos, row, cur, quotes.get(cd), left, ownOk.get(cd), entryOk
            )
            if act:
                actions.append(act)
        return {"ok": True, "actions": actions, "cash": left, "ymd": ymd, "hms": hms}

    def _hold(self, pos, reason):
        return {
            "stkCd": pos["stkCd"],
            "side": "NONE",
            "qty": 0,
            "reason": reason,
            "algCd": ALG_CD,
            "algVer": ALG_VER,
            "exemptLossApproval": True,
        }

    def _intentId(self, pos, reason):
        if reason in ("INITIAL", "FALLBACK"):
            return (
                f"{pos['stkCd']}:{reason}:{pos.get('eventId') or ''}:"
                f"{pos.get('eventFirstYmd') or pos.get('fallbackYmd') or ''}"
            )
        if reason == "REENTRY":
            return f"{pos['stkCd']}:REENTRY:{pos.get('cycle')}:{pos.get('eventId') or ''}"
        return (
            f"{pos['stkCd']}:{reason}:{pos.get('entryYmd') or ''}:"
            f"{pos.get('cycle')}:{pos.get('stage')}"
        )

    def _order(self, pos, side, qty, reason, style, limitPx, cashLeft):
        intentId = self._intentId(pos, reason)
        if side == "BUY" and intentId in (pos.get("rejected") or []):
            return self._hold(pos, "REJECTED_NO_RETRY"), cashLeft
        pending = {
            "intentId": intentId,
            "side": side,
            "qty": int(qty),
            "filled": 0,
            "reason": reason,
            "trdeTp": style.get("trdeTp"),
            "orderStyle": style.get("style"),
            "limitPx": limitPx,
        }
        pos["pending"] = pending
        if pos["phase"] == PHASE_FLAT and reason == "INITIAL":
            pos["phase"] = PHASE_PENDING
        return {
            "stkCd": pos["stkCd"],
            "side": side,
            "qty": int(qty),
            "reason": reason,
            "trdeTp": style.get("trdeTp"),
            "orderStyle": style.get("style"),
            "orderNote": style.get("note"),
            "limitPx": limitPx,
            "intentId": intentId,
            "algCd": ALG_CD,
            "algVer": ALG_VER,
            "exemptLossApproval": True,
            "avgPx": pos.get("avgPx") or 0,
            "cycle": pos.get("cycle"),
            "stage": pos.get("stage"),
        }, cashLeft

    def _gateQuote(self, pos, quote, now, needBroker):
        if pos.get("frozen"):
            return None, self._hold(pos, pos["frozen"])
        if pos.get("pending"):
            return None, self._hold(pos, "WAIT_RECONCILE")
        ok, why = quoteFresh(quote, now)
        if not ok:
            if int(pos.get("qty") or 0) > 0 or pos.get("phase") in (
                PHASE_PENDING,
                PHASE_FALLBACK,
                PHASE_OPEN,
                PHASE_STOP1,
            ):
                return None, self._hold(pos, why)
            return None, self._hold(pos, why)
        if needBroker:
            broker = quote.get("brokerQty")
            if broker is None:
                return None, self._hold(pos, "BROKER_QTY_UNKNOWN")
            try:
                brokerQty = int(broker)
            except (TypeError, ValueError):
                return None, self._hold(pos, "BROKER_QTY_UNKNOWN")
            if brokerQty < int(pos.get("qty") or 0):
                pos["frozen"] = "RECONCILE_MISMATCH"
                return None, self._hold(pos, "RECONCILE_MISMATCH")
        return quote, None

    def _decideHeld(self, pos, now, quote, cash, owned, entryOk=None):
        ymd = _ymd(now)
        hms = _hms(now)
        if pos.get("phase") == PHASE_PENDING and int(pos.get("qty") or 0) == 0:
            return self._decidePendingEntry(pos, now, quote, cash, owned, entryOk)
        if pos.get("phase") == PHASE_FALLBACK and int(pos.get("qty") or 0) == 0:
            return self._decideFallback(pos, now, quote, cash, owned, entryOk)
        quote, held = self._gateQuote(pos, quote, now, needBroker=True)
        if held:
            return held, cash
        if int(pos.get("qty") or 0) <= 0:
            return None, cash
        exitAct = self._exit(pos, quote, now, cash)
        if exitAct is not None:
            return exitAct, cash
        return self._buyMore(pos, quote, now, cash, owned, ymd, hms, entryOk)

    def _exit(self, pos, quote, now, cash):
        hms = _hms(now)
        style = orderStyle(hms)
        px = float(quote["px"])
        ret = retPct(px, pos.get("avgPx"))
        if ret is None:
            return self._hold(pos, "NO_AVG")
        qty = int(pos["qty"])
        broker = int(quote["brokerQty"])
        if _cmp(ret, FULL_STOP_PCT) <= 0:
            if style is None:
                return self._hold(pos, "OUTSIDE_REGULAR")
            sellQty = min(qty, broker)
            if sellQty < 1:
                return self._hold(pos, "SELL_QTY_ZERO")
            act, _ = self._order(pos, "SELL", sellQty, "FULL_STOP", style, px, cash)
            return act
        if _cmp(ret, FIRST_STOP_PCT) <= 0 or (
            int(pos.get("stage") or 0) >= 1 and _cmp(ret, 0) < 0
        ):
            reason = "FIRST_STOP" if _cmp(ret, FIRST_STOP_PCT) <= 0 else "ADD_LOSS"
            want = qty - 1
            if want < 1:
                return self._hold(pos, "ONE_SHARE")
            sellQty = min(want, qty, broker)
            if sellQty < 1:
                return self._hold(pos, "ONE_SHARE")
            if style is None:
                return self._hold(pos, "OUTSIDE_REGULAR")
            act, _ = self._order(pos, "SELL", sellQty, reason, style, px, cash)
            return act
        if self._profitExit(pos, quote, now, ret):
            if style is None:
                return self._hold(pos, "OUTSIDE_REGULAR")
            sellQty = min(qty, broker)
            if sellQty < 1:
                return self._hold(pos, "SELL_QTY_ZERO")
            act, _ = self._order(pos, "SELL", sellQty, "PROFIT_MA", style, px, cash)
            return act
        return None

    def _profitExit(self, pos, quote, now, ret):
        hms = _hms(now)
        if hms < "151500" or hms >= "153000":
            return False
        if _cmp(ret, 0) <= 0:
            return False
        try:
            px = float(quote.get("px"))
            opened = float(quote.get("open"))
        except (TypeError, ValueError):
            return False
        if opened <= 0 or not (px < opened):
            return False
        # 당일 확정 종가는 쓰지 않는다.
        prior = quote.get("priorMa")
        curMa = quote.get("curMa")
        prevPx = quote.get("prevPx")
        try:
            priorMa = float(prior)
            currentMa = float(curMa)
            previous = float(prevPx)
        except (TypeError, ValueError):
            return False
        if priorMa <= 0 or currentMa <= 0 or previous <= 0:
            return False
        if quote.get("maReady") is not True:
            return False
        return previous >= priorMa and px < currentMa

    def _buyMore(self, pos, quote, now, cash, owned, ymd, hms, entryOk=None):
        if not self._entryOpen(pos["stkCd"], entryOk):
            return self._hold(pos, "ENTRY_INPUT"), cash
        if pos.get("phase") == PHASE_STOP1 and pos.get("needPosCross"):
            return self._reentry(pos, quote, now, cash, owned)
        if pos.get("phase") != PHASE_OPEN:
            return None, cash
        if not owned:
            return self._hold(pos, "OWNERSHIP_BLOCK"), cash
        style = orderStyle(hms)
        if style is None:
            return None, cash
        if not self._addDayOk(pos, ymd):
            return None, cash
        if int(pos.get("addsOn", {}).get(ymd) or 0) >= 1:
            return None, cash
        ret = retPct(quote.get("px"), pos.get("avgPx"))
        stage = int(pos.get("stage") or 0)
        if stage >= 2:
            return None, cash
        if stage == 0 and _cmp(ret, ADD1_PCT) >= 0:
            reason = "ADD1"
        elif stage == 1 and _cmp(ret, ADD2_PCT) >= 0:
            reason = "ADD2"
        else:
            return None, cash
        # +10 점프여도 stage 0 에서는 ADD1 만. ADD2 는 다음 거래일.
        return self._spend(pos, quote, reason, style, cash)

    def _addDayOk(self, pos, ymd):
        entry = str(pos.get("entryYmd") or "")
        if not entry:
            return False
        if str(pos.get("entryKind") or "") == "REENTRY":
            return ymd >= entry and self._isTrading(ymd)
        nxt = self._nextTrading(entry)
        if not nxt:
            return False
        return ymd >= nxt and self._isTrading(ymd)

    def _reentry(self, pos, quote, now, cash, owned):
        if not owned:
            return self._hold(pos, "OWNERSHIP_BLOCK"), cash
        ret = retPct(quote.get("px"), pos.get("avgPx"))
        if _cmp(ret, 0) <= 0:
            return None, cash
        if not pos.get("needPosCross"):
            return None, cash
        style = orderStyle(_hms(now))
        if style is None:
            return self._hold(pos, "OUTSIDE_REGULAR"), cash
        act, left = self._spend(pos, quote, "REENTRY", style, cash)
        return act, left

    def _spend(self, pos, quote, reason, style, cash):
        px = float(quote["px"])
        qty = affordableQty(BUDGET_KRW, px, FEE_RATE)
        if qty < 1:
            return self._hold(pos, "BUDGET_FRACTION"), cash
        need = cashForQty(qty, px, FEE_RATE)
        if need is None or need - cash > 1e-6:
            return self._hold(pos, "CASH_GUARD"), cash
        limitPx = px if style.get("trdeTp") == "0" else None
        act, _ = self._order(pos, "BUY", qty, reason, style, limitPx, cash)
        if act.get("side") != "BUY":
            return act, cash
        return act, cash - need

    def _decideFlat(self, pos, row, now, quote, cash, owned, entryOk=None):
        if not self._entryOpen(pos["stkCd"], entryOk):
            return self._hold(pos, "ENTRY_INPUT"), cash
        if pos.get("frozen"):
            return self._hold(pos, pos["frozen"]), cash
        if pos.get("pending"):
            return self._hold(pos, "WAIT_RECONCILE"), cash
        hms = _hms(now)
        ymd = _ymd(now)
        if hms < "151800" or hms >= "153000":
            return None, cash
        ok, why = decisionSnapOk(row, now)
        if not ok:
            return self._hold(pos, why), cash
        qok, qwhy = quoteFresh(quote, now)
        if not qok:
            return self._hold(pos, qwhy), cash
        why = _firstSurgeBlock(pos, row, ymd, quote)
        if why:
            return self._hold(pos, why), cash
        if not owned:
            return self._hold(pos, "OWNERSHIP_BLOCK"), cash
        style = orderStyle(hms)
        if style is None:
            return self._hold(pos, "OUTSIDE_REGULAR"), cash
        pos["eventId"] = str(row.get("eventId") or "")
        pos["eventFirstYmd"] = str(row.get("eventFirstYmd") or "")
        act, left = self._spend(pos, quote, "INITIAL", style, cash)
        if act.get("side") == "BUY":
            pos["entryKind"] = "INITIAL"
        return act, left

    def _decidePendingEntry(self, pos, now, quote, cash, owned, entryOk=None):
        """미체결 초기 주문. 체결 0·미체결 없음이 확인되기 전에는 폴백을 걸지 않는다."""
        if pos.get("pending"):
            if _hms(now) >= "153000":
                return self._hold(pos, "WAIT_RECONCILE"), cash
            return self._hold(pos, "WAIT_RECONCILE"), cash
        if int(pos.get("qty") or 0) > 0:
            pos["phase"] = PHASE_OPEN
            return None, cash
        if _hms(now) < "153000":
            return self._decideFlat(
                pos,
                {
                    "stkCd": pos["stkCd"],
                    "eventId": pos.get("eventId"),
                    "eventFirstYmd": pos.get("eventFirstYmd"),
                    "confirmed": True,
                    "dailyPct": FIRST_SURGE_PCT,
                    "dailyBasis": "ka00198_base_comp_chgr",
                    "member": True,
                    "tradable": True,
                    "snapObservedAt": now,
                    "snapSlot": "1518",
                },
                now,
                quote,
                cash,
                owned,
                entryOk,
            )
        nxt = self._nextTrading(_ymd(now))
        if not nxt:
            pos["frozen"] = "NO_NEXT_SESSION"
            return self._hold(pos, "NO_NEXT_SESSION"), cash
        pos["phase"] = PHASE_FALLBACK
        pos["fallbackYmd"] = nxt
        return self._hold(pos, "ARM_FALLBACK"), cash

    def _decideFallback(self, pos, now, quote, cash, owned, entryOk=None):
        if not self._entryOpen(pos["stkCd"], entryOk):
            return self._hold(pos, "ENTRY_INPUT"), cash
        ymd = _ymd(now)
        hms = _hms(now)
        due = str(pos.get("fallbackYmd") or "")
        if not due:
            pos["phase"] = PHASE_FLAT
            return self._hold(pos, "FALLBACK_EXPIRED"), cash
        if ymd < due:
            return None, cash
        if ymd > due or (ymd == due and hms >= "153000" and int(pos.get("qty") or 0) == 0 and not pos.get("pending")):
            pos["phase"] = PHASE_FLAT
            pos["fallbackYmd"] = ""
            return self._hold(pos, "FALLBACK_EXPIRED"), cash
        if pos.get("pending"):
            return self._hold(pos, "WAIT_RECONCILE"), cash
        if hms < "090000":
            return None, cash
        qok, qwhy = quoteFresh(quote, now)
        if not qok:
            return self._hold(pos, qwhy), cash
        try:
            opened = float(quote.get("open"))
            prev = float(quote.get("prevClose"))
            px = float(quote.get("px"))
        except (TypeError, ValueError):
            return self._hold(pos, "GAP_QUOTE_MISSING"), cash
        if opened <= 0 or prev <= 0 or px <= 0:
            return self._hold(pos, "GAP_QUOTE_MISSING"), cash
        if not (opened > prev and px > prev):
            pos["phase"] = PHASE_FLAT
            pos["fallbackYmd"] = ""
            return self._hold(pos, "FALLBACK_EXPIRED"), cash
        if not owned:
            return self._hold(pos, "OWNERSHIP_BLOCK"), cash
        if str(pos.get("blockRebuy") or "") == f"{pos.get('eventId')}|{ymd}":
            return self._hold(pos, "PROFIT_FLAT_BLOCK"), cash
        style = orderStyle(hms)
        if style is None:
            return self._hold(pos, "OUTSIDE_REGULAR"), cash
        act, left = self._spend(pos, quote, "FALLBACK", style, cash)
        return act, left

    def onFill(self, stkCd, side, qty, px, intentId="", reason="", tradeYmd=""):
        """실제 체결만 반영. 평단은 체결가 가중평균으로 다시 계산한다."""
        pos = self.position(stkCd)
        fillQty = int(qty or 0)
        fillPx = float(px or 0)
        if fillQty < 1 or fillPx <= 0:
            return {"ok": False, "reason": "BAD_FILL"}
        pending = pos.get("pending") or {}
        if intentId and pending and pending.get("intentId") not in ("", intentId):
            return {"ok": False, "reason": "INTENT_MISMATCH"}
        why = reason or (pending.get("reason") if pending else "") or ""
        day = str(tradeYmd or "")[:8]
        if side == "BUY":
            prevQty = int(pos.get("qty") or 0)
            prevCost = float(pos.get("costPxQty") or 0)
            newQty = prevQty + fillQty
            newCost = prevCost + fillPx * fillQty
            pos["qty"] = newQty
            pos["costPxQty"] = newCost
            pos["avgPx"] = newCost / float(newQty)
            if prevQty == 0:
                pos["firstPx"] = fillPx
            pos["spent"] = float(pos.get("spent") or 0) + cashForQty(fillQty, fillPx)
            if why in ("INITIAL", "FALLBACK"):
                pos["phase"] = PHASE_OPEN
                pos["entryKind"] = "INITIAL"
                pos["entryYmd"] = day or pos.get("entryYmd") or ""
                pos["needPosCross"] = False
            elif why == "REENTRY":
                pos["phase"] = PHASE_OPEN
                pos["entryKind"] = "REENTRY"
                pos["needPosCross"] = False
                pos["entryYmd"] = day or pos.get("entryYmd") or ""
                if pending and not pending.get("reentryNoted"):
                    pos["stage"] = 0
                    pos["cycle"] = int(pos.get("cycle") or 1) + 1
                    pending["reentryNoted"] = True
            elif why in ("ADD1", "ADD2"):
                pos["phase"] = PHASE_OPEN
                if pending and not pending.get("stageNoted"):
                    pos["stage"] = int(pos.get("stage") or 0) + 1
                    if day:
                        pos["addsOn"][day] = int(pos["addsOn"].get(day) or 0) + 1
                    pending["stageNoted"] = True
                if day:
                    pos["entryYmd"] = pos.get("entryYmd") or day
        elif side == "SELL":
            prevQty = int(pos.get("qty") or 0)
            sell = min(fillQty, prevQty)
            if sell < 1:
                return {"ok": False, "reason": "SELL_QTY_ZERO"}
            # 매도는 평단을 바꾸지 않는다. 수량은 줄인다.
            pos["qty"] = prevQty - sell
            if pos["qty"] <= 0:
                pos["qty"] = 0
                pos["costPxQty"] = 0.0
                pos["phase"] = PHASE_CLOSED
                pos["needPosCross"] = False
                if why == "PROFIT_MA":
                    pos["blockRebuy"] = f"{pos.get('eventId')}|{day}"
            elif why in ("FIRST_STOP", "ADD_LOSS"):
                pos["phase"] = PHASE_STOP1
                pos["needPosCross"] = True
                left = int(pos["qty"])
                pos["costPxQty"] = float(pos.get("avgPx") or 0) * left
        else:
            return {"ok": False, "reason": "BAD_SIDE"}
        if pending:
            pending["filled"] = int(pending.get("filled") or 0) + fillQty
            if pending["filled"] >= int(pending.get("qty") or 0):
                pos["pending"] = None
            else:
                pos["pending"] = pending
        return {"ok": True, "qty": pos["qty"], "avgPx": pos["avgPx"], "phase": pos["phase"], "stage": pos["stage"]}

    def onReject(self, stkCd, intentId=""):
        pos = self.position(stkCd)
        pending = pos.get("pending") or {}
        if intentId and pending and pending.get("intentId") != intentId:
            return {"ok": False, "reason": "INTENT_MISMATCH"}
        ident = (pending or {}).get("intentId") or intentId
        if ident and ident not in pos["rejected"]:
            pos["rejected"].append(ident)
        pos["pending"] = None
        if pos["phase"] == PHASE_PENDING and int(pos.get("qty") or 0) == 0:
            pos["phase"] = PHASE_FLAT
        return {"ok": True, "phase": pos["phase"], "qty": pos["qty"]}

    def onCancelAck(self, stkCd, intentId=""):
        """미체결 취소 확인. 부분체결 수량은 유지하고 나머지만 버린다."""
        pos = self.position(stkCd)
        pending = pos.get("pending")
        if not pending:
            return {"ok": True, "phase": pos["phase"], "qty": pos["qty"]}
        if intentId and pending.get("intentId") != intentId:
            return {"ok": False, "reason": "INTENT_MISMATCH"}
        pos["pending"] = None
        if int(pos.get("qty") or 0) > 0:
            pos["phase"] = PHASE_OPEN if pos.get("phase") != PHASE_STOP1 else pos["phase"]
        elif pos.get("phase") == PHASE_PENDING:
            nxt = ""
            return {"ok": True, "phase": pos["phase"], "qty": 0, "stillUnfilled": True, "note": nxt}
        return {"ok": True, "phase": pos["phase"], "qty": pos["qty"]}

    def markEntryDay(self, stkCd, ymd):
        """체결 시각의 거래일. 테스트·복구에서 entryYmd 가 비었을 때."""
        pos = self.position(stkCd)
        if not pos.get("entryYmd"):
            pos["entryYmd"] = str(ymd)
        return pos["entryYmd"]

    def armFallbackIfUnfilled(self, stkCd, now):
        """장 마감 후, 미체결 주문이 없고 체결 0일 때만 다음 거래일 폴백."""
        pos = self.position(stkCd)
        if pos.get("pending"):
            return {"ok": False, "reason": "WAIT_RECONCILE"}
        if int(pos.get("qty") or 0) > 0:
            return {"ok": False, "reason": "NOT_MISSED"}
        nxt = self._nextTrading(_ymd(_asDt(now)))
        if not nxt:
            pos["frozen"] = "NO_NEXT_SESSION"
            return {"ok": False, "reason": "NO_NEXT_SESSION"}
        pos["phase"] = PHASE_FALLBACK
        pos["fallbackYmd"] = nxt
        return {"ok": True, "fallbackYmd": nxt}


def noteEntryYmd(pos, ymd):
    if pos is not None and ymd and not pos.get("entryYmd"):
        pos["entryYmd"] = str(ymd)
    return pos


def _firstSurgeBlock(pos, row, ymd, quote):
    if str(pos.get("blockRebuy") or "") == f"{row.get('eventId')}|{ymd}":
        return "PROFIT_FLAT_BLOCK"
    if row.get("confirmed") is not True:
        return "EVENT_UNCONFIRMED"
    if not str(row.get("eventId") or ""):
        return "EVENT_ID_MISSING"
    if str(row.get("eventFirstYmd") or "") != ymd:
        return "NOT_FIRST_SURGE_DAY"
    if row.get("tradable") is not True:
        return "NOT_TRADABLE"
    try:
        daily = float(row.get("dailyPct"))
    except (TypeError, ValueError):
        return "DAILY_MISSING"
    if _cmp(daily, FIRST_SURGE_PCT) < 0:
        return "DAILY_BELOW_10"
    try:
        prev = float(quote.get("prevClose"))
        px = float(quote.get("px"))
    except (TypeError, ValueError):
        return "PREV_CLOSE_MISSING"
    if prev <= 0 or px <= prev:
        return "NOT_ABOVE_PREV_CLOSE"
    published = row.get("publishedAt")
    if published:
        try:
            if _asDt(published) > _asDt(quote.get("asof") or published):
                return "EVENT_FUTURE"
        except ValueError:
            return "EVENT_TIME_BAD"
    return ""


def _copyPos(pos):
    out = dict(pos)
    out["addsOn"] = dict(pos.get("addsOn") or {})
    out["rejected"] = list(pos.get("rejected") or [])
    if pos.get("pending"):
        out["pending"] = dict(pos["pending"])
    return out


def causalMa20(priorCloses, currentPx):
    """
    직전 20개 수정종가의 평균이 priorMa.
    현재 MA 는 그중 최근 19개 + 현재가(당일 진행 봉). 확정 종가를 넣지 않는다.
    표본이 부족하거나 0·누락이 있으면 (None, None).
    """
    closes = []
    for raw in priorCloses or []:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None, None
        if val <= 0:
            return None, None
        closes.append(val)
    if len(closes) != 20:
        return None, None
    try:
        cur = float(currentPx)
    except (TypeError, ValueError):
        return None, None
    if cur <= 0:
        return None, None
    priorMa = sum(closes) / 20.0
    curMa = (sum(closes[1:]) + cur) / 20.0
    return priorMa, curMa


def buildAlgVerEvent(evtTp, stkCd, reason, tradeDt, evtTm, px=None):
    """ALG_VER 이벤트 본문. DB 에 쓰지 않는다."""
    return {
        "algCd": ALG_CD,
        "algVer": ALG_VER,
        "stratId": STRAT_ID,
        "evtTp": str(evtTp or ""),
        "stkCd": str(stkCd or ""),
        "reason": str(reason or ""),
        "tradeDt": str(tradeDt or ""),
        "evtTm": str(evtTm or ""),
        "px": px,
        "name": NAME_KR,
        "englishName": ENGLISH_NAME,
    }


def algVerHistPayload(gitHash="", ymd="2026-11-09"):
    """AI_ALG_VER_HIST 적재용 값. 이 함수는 DB 를 열지 않는다."""
    note = (
        "v37 알고리즘 관리에서 사용과 중지를 바꾼다. 주문 규칙은 그대로."
    )
    return {
        "algCd": ALG_CD,
        "stratId": STRAT_ID,
        "algVer": ALG_VER,
        "ymd": ymd,
        "note": note,
        "gitHash": str(gitHash or "")[:16],
        "src": "manual",
    }
