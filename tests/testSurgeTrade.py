# -*- coding: utf-8 -*-
"""SRG01 급등주매매. 실주문·DB쓰기·텔레그램 없음.

python tests/testSurgeTrade.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import paper_host_url, real_host_url
from strategies.impl.surgeTrade import (
    ALG_CD,
    ALG_VER,
    BUDGET_KRW,
    FEE_RATE,
    SurgeBook,
    affordableQty,
    algVerHistPayload,
    buildAlgVerEvent,
    causalMa20,
    cashForQty,
    orderStyle,
    retPct,
)
from strategies.indicators import calcRsiSeries
from strategies.specLoader import loadStratSpec
from trading.deskLiveAlgs import (
    algWatchSubtabVisible,
    effectiveAlgEnabled,
    filterVisibleAlgTabs,
    formatOpsAlgBar,
    monitorTabCatalog,
    operatingDeskRows,
    resolveInfoSelection,
    resolveMonitorSelection,
    standaloneMonitorTabs,
    visibleMonitorTabs,
)
from trading.surgeTradeData import (
    ENTRY_MIN_DAILY_PCT,
    SnapStore,
    TAB_COLUMNS,
    TAB_MIN_DAILY_PCT,
    absorbFirstSurge,
    buildTabRows,
    dueSlot,
    entryUniverseRows,
    loadFirstSurgeBook,
    queryStampLabel,
    selectTabDisplay,
    fifteenMinuteSlots,
    filterRankRows,
    formatFirstSurgeVs,
    latestDisplaySnapshot,
    nextRetryDelaySec,
    notCollectedSnapshot,
    parseDartList,
    parseKa00198Body,
    parseKa10059Flow,
    rsi14,
    tabRankRows,
    turnoverEok,
    validateEventAudit,
    yearLowRisePct,
    yellowName,
)
from trading.surgeTradeGate import (
    evaluateMarketRead,
    evaluateOrderSession,
    lossSellRequiresTelegramApproval,
    submitSurgeOrder,
)
from trading.surgeTradeLive import (
    enqueueManualRefresh,
    isSurgeEnabled,
    loadBook,
    pullPaperRank,
    runDecisionCycle,
    runManualRankSave,
    saveBook,
    surgeInquiryVisible,
)


class Cal:
    def __init__(self, holidays=()):
        self.holidays = set(holidays)

    def isTrading(self, ymd):
        day = datetime.strptime(str(ymd), "%Y%m%d")
        return day.weekday() < 5 and str(ymd) not in self.holidays

    def nextTrading(self, ymd):
        day = datetime.strptime(str(ymd), "%Y%m%d")
        for _ in range(14):
            day += timedelta(days=1)
            key = day.strftime("%Y%m%d")
            if self.isTrading(key):
                return key
        return ""


def paperSession(**over):
    base = {
        "mode": "paper",
        "host": paper_host_url,
        "accountEnv": "paper",
        "accountVerified": True,
        "tokenPresent": True,
        "purpose": "order",
    }
    base.update(over)
    return base


def q(px, now, prev=10000, broker=0, openPx=None, **extra):
    row = {
        "px": px,
        "asof": now,
        "prevClose": prev,
        "open": px if openPx is None else openPx,
        "brokerQty": broker,
        "stale": False,
    }
    row.update(extra)
    return row


def cand(now, daily=12.0, event="EVT1", ymd=None, **extra):
    day = ymd or now.strftime("%Y%m%d")
    row = {
        "stkCd": "005930",
        "eventId": event,
        "eventFirstYmd": day,
        "confirmed": True,
        "dailyPct": daily,
        "dailyBasis": "ka00198_base_comp_chgr",
        "member": True,
        "tradable": True,
        "snapObservedAt": now,
        "snapSlot": "1518",
        "publishedAt": "2026-11-06 09:00:00",
    }
    row.update(extra)
    return row


def buys(actions):
    return [a for a in actions if a.get("side") == "BUY"]


def sells(actions):
    return [a for a in actions if a.get("side") == "SELL"]


SPEC_EXAMPLE = {
    "item_inq_rank": [
        {
            "stk_nm": "키움증권",
            "bigd_rank": "1",
            "rank_chg": "0",
            "rank_chg_sign": "N",
            "past_curr_prc": "+70700",
            "base_comp_sign": "2",
            "base_comp_chgr": "+0.57",
            "prev_base_sign": "3",
            "prev_base_chgr": "0.00",
            "dt": "20250827",
            "tm": "085900",
            "stk_cd": "005930",
        },
        {
            "stk_nm": "키움증권",
            "bigd_rank": "2",
            "rank_chg": "-1",
            "rank_chg_sign": "-",
            "past_curr_prc": "+206000",
            "base_comp_sign": "2",
            "base_comp_chgr": "+0.49",
            "prev_base_sign": "3",
            "prev_base_chgr": "0.00",
            "dt": "20250827",
            "tm": "085900",
            "stk_cd": "039490",
        },
    ],
    "return_code": 0,
    "return_msg": "정상적으로 처리되었습니다",
}


class SurgeTradeTest(unittest.TestCase):
    def setUp(self):
        self.cal = Cal(("20261225",))
        self.book = SurgeBook(self.cal)
        self.now = datetime(2026, 11, 6, 15, 18, 10)

    def _initial(self, px=11000, cash=5_000_000, broker=0):
        decided = self.book.decide(
            self.now,
            {"005930": q(px, self.now, prev=10000, broker=broker)},
            [cand(self.now)],
            cash,
            {"005930": True},
        )
        return decided

    def test_spec_identity_and_default_off(self):
        spec = loadStratSpec("surgeTrade_v1")
        self.assertEqual(spec["algCd"], "SRG01")
        self.assertEqual(spec["englishName"], "Surge Trade")
        self.assertEqual(spec["name"], "급등주매매")
        self.assertEqual(int(spec["version"]), 37)
        self.assertFalse(spec["enabled"])
        self.assertTrue(spec["params"]["paperOnly"])
        self.assertFalse(isSurgeEnabled())
        self.assertIsNone(loadStratSpec("rcs01_v1"))
        ldr = loadStratSpec("leaderScale_v1")
        ram = loadStratSpec("realAcntMgr_v1")
        self.assertEqual(ldr["algCd"], "LDR01")
        self.assertEqual(ram["algCd"], "RAM01")
        self.assertFalse(lossSellRequiresTelegramApproval("SRG01"))
        self.assertTrue(lossSellRequiresTelegramApproval("TPX01"))
        self.assertIsNone(lossSellRequiresTelegramApproval("MTS01"))
        payload = algVerHistPayload("abc1234")
        self.assertEqual(payload["algVer"], 37)
        evt = buildAlgVerEvent("BUY", "005930", "INITIAL", "20261106", "151810")
        self.assertEqual(evt["algVer"], ALG_VER)
        self.assertEqual(evt["algCd"], ALG_CD)

    def test_affordable_and_boundaries(self):
        qty = affordableQty(BUDGET_KRW, 11000, FEE_RATE)
        self.assertEqual(qty, int(BUDGET_KRW // (11000 * (1 + FEE_RATE))))
        self.assertGreaterEqual(qty, 1)
        self.assertEqual(affordableQty(BUDGET_KRW, 1_000_000, FEE_RATE), 0)
        self.assertAlmostEqual(retPct(10500, 10000), 5.0)
        self.assertAlmostEqual(retPct(11000, 10000), 10.0)
        self.assertAlmostEqual(retPct(9500, 10000), -5.0)
        self.assertAlmostEqual(retPct(9000, 10000), -10.0)

    def test_initial_freshness_and_no_future_snap(self):
        got = self._initial()
        actions = buys(got["actions"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["reason"], "INITIAL")
        self.assertEqual(actions[0]["trdeTp"], "3")
        self.assertTrue(actions[0]["exemptLossApproval"])
        early = datetime(2026, 11, 6, 15, 0, 0)
        stale = self.book.decide(
            early,
            {"005930": q(11000, early, broker=0)},
            [cand(early)],
            5_000_000,
            {"005930": True},
        )
        # pending from the first order blocks a second initial
        self.assertTrue(any(a.get("reason") == "WAIT_RECONCILE" for a in stale["actions"]) or not buys(stale["actions"]))
        book = SurgeBook(self.cal)
        oldSnap = cand(self.now, extra_ignored=None)
        oldSnap["snapObservedAt"] = datetime(2026, 11, 6, 15, 0, 0)
        denied = book.decide(self.now, {"005930": q(11000, self.now)}, [oldSnap], 5_000_000, {"005930": True})
        self.assertTrue(any(a.get("reason") == "SNAP_BEFORE_1518" for a in denied["actions"]))
        final = cand(self.now)
        final["snapSlot"] = "1600"
        final["snapObservedAt"] = datetime(2026, 11, 6, 16, 0, 0)
        denied2 = SurgeBook(self.cal).decide(
            self.now, {"005930": q(11000, self.now)}, [final], 5_000_000, {"005930": True}
        )
        self.assertTrue(any(a.get("reason") in ("SNAP_FUTURE", "FUTURE_OR_FINAL_SNAP") for a in denied2["actions"]))
        basis = cand(self.now)
        basis["dailyBasis"] = "ka00198_base_comp_unverified"
        denied3 = SurgeBook(self.cal).decide(
            self.now, {"005930": q(11000, self.now)}, [basis], 5_000_000, {"005930": True}
        )
        self.assertTrue(any(a.get("reason") == "DAILY_BASIS_UNVERIFIED" for a in denied3["actions"]))

    def test_threshold_and_one_initial(self):
        low = SurgeBook(self.cal).decide(
            self.now,
            {"005930": q(10900, self.now, prev=10000)},
            [cand(self.now, daily=9.99)],
            5_000_000,
            {"005930": True},
        )
        self.assertFalse(buys(low["actions"]))
        self.assertTrue(any(a.get("reason") == "DAILY_BELOW_10" for a in low["actions"]))
        edge = SurgeBook(self.cal).decide(
            self.now,
            {"005930": q(11000, self.now, prev=10000)},
            [cand(self.now, daily=10.0)],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual(len(buys(edge["actions"])), 1)
        again = self.book.decide(
            self.now,
            {"005930": q(11000, self.now, prev=10000, broker=0)},
            [cand(self.now)],
            5_000_000,
            {"005930": True},
        )
        # fresh book was not used; self.book has no position yet if test order differs.
        # Use a dedicated book for duplicate pending.
        book = SurgeBook(self.cal)
        first = book.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        second = book.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        self.assertEqual(len(buys(first["actions"])), 1)
        self.assertEqual(buys(second["actions"]), [])
        self.assertTrue(any(a.get("reason") == "WAIT_RECONCILE" for a in second["actions"]))

    def test_partial_fill_is_not_missed(self):
        book = SurgeBook(self.cal)
        decided = book.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        action = buys(decided["actions"])[0]
        book.onFill("005930", "BUY", 4, 11000, action["intentId"], "INITIAL", "20261106")
        self.assertGreater(book.position("005930")["qty"], 0)
        self.assertIsNotNone(book.position("005930")["pending"])
        nxt = book.decide(self.now, {"005930": q(11000, self.now, broker=4)}, [cand(self.now)], 5_000_000, {"005930": True})
        self.assertEqual(buys(nxt["actions"]), [])
        book.onCancelAck("005930", action["intentId"])
        self.assertEqual(book.position("005930")["qty"], 4)
        self.assertEqual(book.position("005930")["phase"], "OPEN")
        armed = book.armFallbackIfUnfilled("005930", datetime(2026, 11, 6, 15, 31))
        self.assertEqual(armed["reason"], "NOT_MISSED")

    def test_reject_and_fallback_calendar(self):
        book = SurgeBook(self.cal)
        decided = book.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        action = buys(decided["actions"])[0]
        book.onReject("005930", action["intentId"])
        retry = book.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        self.assertEqual(buys(retry["actions"]), [])
        close = datetime(2026, 11, 6, 15, 31)
        book2 = SurgeBook(self.cal)
        placed = book2.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        ident = buys(placed["actions"])[0]["intentId"]
        book2.onCancelAck("005930", ident)
        armed = book2.decide(close, {"005930": q(11000, close)}, [], 5_000_000, {"005930": True})
        self.assertTrue(any(a.get("reason") == "ARM_FALLBACK" for a in armed["actions"]))
        self.assertEqual(book2.position("005930")["fallbackYmd"], "20261109")
        # weekend is not a chase day
        sat = datetime(2026, 11, 7, 9, 1)
        satRun = book2.decide(sat, {"005930": q(12000, sat, prev=10000, openPx=12000)}, [], 5_000_000, {"005930": True})
        self.assertEqual(satRun["reason"], "NOT_TRADING_DAY")
        mon = datetime(2026, 11, 9, 9, 1)
        gap = book2.decide(
            mon,
            {"005930": q(10100, mon, prev=10000, openPx=10050, broker=0)},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual([a["reason"] for a in buys(gap["actions"])], ["FALLBACK"])
        flat = SurgeBook(self.cal)
        flat.position("005930")["phase"] = "PENDING_FALLBACK"
        flat.position("005930")["fallbackYmd"] = "20261109"
        flat.position("005930")["eventId"] = "EVT1"
        noGap = flat.decide(
            mon,
            {"005930": q(10000, mon, prev=10000, openPx=10000, broker=0)},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertTrue(any(a.get("reason") == "FALLBACK_EXPIRED" for a in noGap["actions"]))
        later = SurgeBook(self.cal)
        later.position("005930")["phase"] = "PENDING_FALLBACK"
        later.position("005930")["fallbackYmd"] = "20261109"
        tue = datetime(2026, 11, 10, 9, 1)
        chased = later.decide(
            tue,
            {"005930": q(12000, tue, prev=10000, openPx=11000, broker=0)},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual(buys(chased["actions"]), [])
        self.assertTrue(any(a.get("reason") == "FALLBACK_EXPIRED" for a in chased["actions"]))

    def test_adds_jump_day_cap_restart_and_reentry(self):
        book = SurgeBook(self.cal)
        placed = book.decide(self.now, {"005930": q(10000, self.now, prev=9000)}, [cand(self.now, daily=12)], 5_000_000, {"005930": True})
        action = buys(placed["actions"])[0]
        qty = action["qty"]
        book.onFill("005930", "BUY", qty, 10000, action["intentId"], "INITIAL", "20261106")
        self.assertEqual(book.position("005930")["avgPx"], 10000)
        book.position("005930")["firstPx"] = 9000
        mon = datetime(2026, 11, 9, 10, 0)
        jump = book.decide(
            mon,
            {"005930": q(11000, mon, broker=qty)},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual([a["reason"] for a in buys(jump["actions"])], ["ADD1"])
        add = buys(jump["actions"])[0]
        book.onFill("005930", "BUY", add["qty"], 11000, add["intentId"], "ADD1", "20261109")
        self.assertEqual(book.position("005930")["stage"], 1)
        self.assertNotEqual(book.position("005930")["avgPx"], 9000)
        same = book.decide(mon, {"005930": q(book.position("005930")["avgPx"] * 1.2, mon, broker=book.position("005930")["qty"])}, [], 5_000_000, {"005930": True})
        self.assertEqual(buys(same["actions"]), [])
        tue = datetime(2026, 11, 10, 10, 0)
        avg = book.position("005930")["avgPx"]
        stage2 = book.decide(
            tue,
            {"005930": q(avg * 1.10, tue, broker=book.position("005930")["qty"])},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual([a["reason"] for a in buys(stage2["actions"])], ["ADD2"])
        book.onCancelAck("005930", buys(stage2["actions"])[0]["intentId"])
        raw = book.snapshot()
        restored = SurgeBook(self.cal)
        restored.restore(raw)
        self.assertEqual(restored.position("005930")["stage"], 1)
        self.assertEqual(restored.position("005930")["addsOn"].get("20261109"), 1)
        # stop to 1 share then reentry does not clear the day cap
        held = book.position("005930")["qty"]
        stopPx = book.position("005930")["avgPx"] * 0.94
        stopped = book.decide(tue, {"005930": q(stopPx, tue, broker=held)}, [], 5_000_000, {"005930": True})
        self.assertEqual(sells(stopped["actions"])[0]["reason"], "FIRST_STOP")
        sell = sells(stopped["actions"])[0]
        book.onFill("005930", "SELL", sell["qty"], stopPx, sell["intentId"], "FIRST_STOP", "20261110")
        self.assertEqual(book.position("005930")["qty"], 1)
        self.assertTrue(book.position("005930")["needPosCross"])
        flatPx = book.position("005930")["avgPx"]
        noCross = book.decide(tue, {"005930": q(flatPx, tue, broker=1)}, [], 5_000_000, {"005930": True})
        self.assertEqual(buys(noCross["actions"]), [])
        upPx = flatPx * 1.01
        re = book.decide(tue, {"005930": q(upPx, tue, broker=1)}, [], 5_000_000, {"005930": True})
        self.assertEqual([a["reason"] for a in buys(re["actions"])], ["REENTRY"])
        self.assertEqual(len(re["actions"]), 1)
        reAct = buys(re["actions"])[0]
        book.onFill("005930", "BUY", 1, upPx, reAct["intentId"], "REENTRY", "20261110")
        book.onFill("005930", "BUY", reAct["qty"] - 1, upPx, reAct["intentId"], "REENTRY", "20261110")
        self.assertEqual(book.position("005930")["stage"], 0)
        self.assertEqual(book.position("005930")["addsOn"].get("20261109"), 1)
        # same-day cap was not used on the 10th before the stop's add; ADD1 may be allowed
        # only on a later poll, and only if the new average is still +5%. Buying at upPx
        # pulls the average toward upPx, so the return is about 0 and must not add now.
        after = book.decide(tue, {"005930": q(upPx, tue, broker=book.position("005930")["qty"])}, [], 5_000_000, {"005930": True})
        self.assertEqual(buys(after["actions"]), [])
        self.assertLess(abs(retPct(upPx, book.position("005930")["avgPx"])), 5)
        pxAdd = book.position("005930")["avgPx"] * 1.06
        addSame = book.decide(
            tue,
            {"005930": q(pxAdd, tue, broker=book.position("005930")["qty"])},
            [],
            5_000_000,
            {"005930": True},
        )
        self.assertEqual([a["reason"] for a in buys(addSame["actions"])], ["ADD1"])

    def test_stop_priority_one_share_and_add_loss(self):
        book = SurgeBook(self.cal)
        pos = book.position("005930")
        pos.update({"phase": "OPEN", "qty": 10, "avgPx": 10000, "costPxQty": 100000, "entryYmd": "20261106", "entryKind": "INITIAL", "stage": 0})
        now = datetime(2026, 11, 9, 10, 0)
        full = book.decide(now, {"005930": q(9000, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(len(sells(full["actions"])), 1)
        self.assertEqual(sells(full["actions"])[0]["reason"], "FULL_STOP")
        self.assertEqual(sells(full["actions"])[0]["qty"], 10)
        book.onReject("005930", sells(full["actions"])[0]["intentId"])
        again = book.decide(now, {"005930": q(9000, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(sells(again["actions"])[0]["reason"], "FULL_STOP")
        book.onCancelAck("005930", sells(again["actions"])[0]["intentId"])
        book = SurgeBook(self.cal)
        pos = book.position("005930")
        pos.update({"phase": "OPEN", "qty": 10, "avgPx": 10000, "costPxQty": 100000, "entryYmd": "20261106", "entryKind": "INITIAL", "stage": 1})
        gap = book.decide(now, {"005930": q(8900, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(sells(gap["actions"])[0]["reason"], "FULL_STOP")
        self.assertEqual(len(sells(gap["actions"])), 1)
        book.onCancelAck("005930", sells(gap["actions"])[0]["intentId"])
        first = book.decide(now, {"005930": q(9500, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(sells(first["actions"])[0]["reason"], "FIRST_STOP")
        self.assertEqual(sells(first["actions"])[0]["qty"], 9)
        book.onCancelAck("005930", sells(first["actions"])[0]["intentId"])
        pos["qty"] = 1
        one = book.decide(now, {"005930": q(9400, now, broker=1)}, [], 0, {"005930": True})
        self.assertEqual(sells(one["actions"]), [])
        self.assertTrue(any(a.get("reason") == "ONE_SHARE" for a in one["actions"]))
        pos["qty"] = 10
        pos["stage"] = 1
        mild = book.decide(now, {"005930": q(9900, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(sells(mild["actions"])[0]["reason"], "ADD_LOSS")
        book.onCancelAck("005930", sells(mild["actions"])[0]["intentId"])
        zero = book.decide(now, {"005930": q(10000, now, broker=10)}, [], 0, {"005930": True})
        self.assertEqual(sells(zero["actions"]), [])

    def test_profit_ma_uses_developing_price(self):
        closes = [100 + i for i in range(20)]
        prior, cur = causalMa20(closes, 90)
        self.assertIsNotNone(prior)
        self.assertLess(cur, prior)
        shortPrior, shortCur = causalMa20(closes[:19], 90)
        self.assertIsNone(shortPrior)
        book = SurgeBook(self.cal)
        pos = book.position("005930")
        pos.update({"phase": "OPEN", "qty": 8, "avgPx": 100, "costPxQty": 800, "entryYmd": "20261106", "entryKind": "INITIAL", "stage": 0, "eventId": "EVT1"})
        now = datetime(2026, 11, 6, 15, 16)
        quote = q(110, now, broker=8, openPx=120, prevPx=prior + 1, priorMa=prior, curMa=cur, maReady=True, finalClose=50)
        # current 110 may not be below curMa. Force the cross with an explicit curMa above price.
        quote["curMa"] = 111
        quote["priorMa"] = 100
        quote["prevPx"] = 100
        hit = book.decide(now, {"005930": quote}, [], 0, {"005930": True})
        self.assertEqual(sells(hit["actions"])[0]["reason"], "PROFIT_MA")
        self.assertEqual(sells(hit["actions"])[0]["qty"], 8)
        book.onFill("005930", "SELL", 8, 110, sells(hit["actions"])[0]["intentId"], "PROFIT_MA", "20261106")
        self.assertEqual(book.position("005930")["blockRebuy"], "EVT1|20261106")
        again = book.decide(
            self.now,
            {"005930": q(12000, self.now, prev=10000)},
            [cand(self.now, event="EVT1")],
            5_000_000,
            {"005930": True},
        )
        self.assertTrue(any(a.get("reason") == "PROFIT_FLAT_BLOCK" for a in again["actions"]))
        early = datetime(2026, 11, 6, 15, 14)
        book2 = SurgeBook(self.cal)
        book2.position("005930").update(pos)
        book2.position("005930")["phase"] = "OPEN"
        book2.position("005930")["qty"] = 8
        missed = book2.decide(early, {"005930": quote}, [], 0, {"005930": True})
        self.assertEqual(sells(missed["actions"]), [])
        loss = SurgeBook(self.cal)
        loss.position("005930").update({"phase": "OPEN", "qty": 8, "avgPx": 200, "costPxQty": 1600, "entryYmd": "20261106", "entryKind": "INITIAL", "stage": 0})
        lossQ = dict(quote)
        lossQ["px"] = 110
        no = loss.decide(now, {"005930": lossQ}, [], 0, {"005930": True})
        self.assertFalse(any(a.get("reason") == "PROFIT_MA" for a in no["actions"]))

    def test_cash_fraction_partial_reject_concurrency_crash(self):
        poor = SurgeBook(self.cal).decide(
            self.now, {"005930": q(11000, self.now)}, [cand(self.now)], cashForQty(1, 11000) - 1, {"005930": True}
        )
        self.assertEqual(buys(poor["actions"]), [])
        self.assertTrue(any(a.get("reason") in ("CASH_GUARD", "BUDGET_FRACTION") for a in poor["actions"]))
        huge = SurgeBook(self.cal).decide(
            self.now, {"005930": q(2_000_000, self.now, prev=1000)}, [cand(self.now)], 5_000_000, {"005930": True}
        )
        self.assertTrue(any(a.get("reason") == "BUDGET_FRACTION" for a in huge["actions"]))
        book = SurgeBook(self.cal)
        self.assertTrue(book.tryBegin())
        book.end()
        held = []

        def locker():
            self.assertTrue(book.tryBegin())
            held.append(True)
            event.wait()
            book.end()

        event = threading.Event()
        thread = threading.Thread(target=locker)
        thread.start()
        while not held:
            pass
        dup = book.decide(self.now, {}, [], 0, {})
        self.assertEqual(dup["reason"], "DUPLICATE_CYCLE")
        event.set()
        thread.join()
        book2 = SurgeBook(self.cal)
        placed = book2.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "surgeTradeState.json"
            saveBook(book2, path)
            loaded = loadBook(path)
        self.assertIsNotNone(loaded.position("005930")["pending"])
        restored = loaded.decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True})
        self.assertEqual(buys(restored["actions"]), [])
        mismatch = SurgeBook(self.cal)
        mismatch.position("005930").update({"phase": "OPEN", "qty": 5, "avgPx": 10000, "costPxQty": 50000, "entryYmd": "20261106", "entryKind": "INITIAL"})
        bad = mismatch.decide(datetime(2026, 11, 9, 10, 0), {"005930": q(9000, datetime(2026, 11, 9, 10, 0), broker=1)}, [], 0, {"005930": True})
        self.assertTrue(any(a.get("reason") == "RECONCILE_MISMATCH" for a in bad["actions"]))
        self.assertEqual(sells(bad["actions"]), [])

    def test_order_gate_and_disabled_cycle(self):
        calls = []

        def broker(action, session):
            calls.append((action["side"], session["host"]))
            return {"ok": True, "ordNo": "1", "sent": True}

        action = {"algCd": "SRG01", "side": "SELL", "qty": 1, "stkCd": "005930", "exemptLossApproval": True, "reason": "FULL_STOP", "trdeTp": "3"}
        blocked = submitSurgeOrder(action, paperSession(mode="real"), broker)
        self.assertFalse(blocked["sent"])
        self.assertEqual(calls, [])
        blocked2 = submitSurgeOrder(action, paperSession(host=real_host_url), broker)
        self.assertEqual(blocked2["reason"], "HOST_NOT_PAPER")
        blocked3 = submitSurgeOrder(action, paperSession(accountVerified=False), broker)
        self.assertEqual(blocked3["reason"], "ACCOUNT_UNVERIFIED")
        blocked4 = submitSurgeOrder(action, paperSession(purpose="market_read"), broker)
        self.assertEqual(blocked4["reason"], "MARKET_READ_CANNOT_ORDER")
        read = evaluateMarketRead(paperSession(purpose="market_read"))
        self.assertTrue(read["ok"])
        self.assertFalse(read["ordersAllowed"])
        ok = submitSurgeOrder(action, paperSession(), broker)
        self.assertTrue(ok["ok"])
        self.assertEqual(calls[0][0], "SELL")
        book = SurgeBook(self.cal)
        off = runDecisionCycle(book, self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": True}, paperSession(), broker, enabled=False)
        self.assertEqual(off["reason"], "DISABLED")
        self.assertEqual(len(calls), 1)
        on = runDecisionCycle(
            SurgeBook(self.cal),
            self.now,
            {"005930": q(11000, self.now)},
            [cand(self.now)],
            5_000_000,
            {"005930": True},
            paperSession(),
            broker,
            enabled=True,
        )
        self.assertTrue(on["orders"][0]["ok"])
        self.assertEqual(on["events"][0]["algVer"], ALG_VER)
        stale = SurgeBook(self.cal)
        stale.position("005930").update({"phase": "OPEN", "qty": 3, "avgPx": 10000, "costPxQty": 30000, "entryYmd": "20261106", "entryKind": "INITIAL"})
        old = datetime(2026, 11, 9, 10, 0)
        quote = q(9000, old - timedelta(seconds=60), broker=3)
        held = stale.decide(old, {"005930": quote}, [], 0, {"005930": True})
        self.assertTrue(any(a.get("reason") == "STALE_QUOTE" for a in held["actions"]))
        self.assertEqual(sells(held["actions"]), [])
        missing = enqueueManualRefresh(None, transport=lambda: {"no": True})
        self.assertEqual(missing["state"], "FAIL")
        self.assertEqual(missing["orders"], 0)
        self.assertIn("실패", missing["label"])
        calls = []

        def transport():
            calls.append("net")
            return {"item_inq_rank": []}

        with tempfile.TemporaryDirectory() as tmp:
            blockedRank = pullPaperRank(
                paperSession(mode="real"),
                self.now,
                SnapStore(tmp),
                True,
                transport=transport,
            )
        self.assertFalse(blockedRank["called"])
        self.assertEqual(calls, [])

    def test_rank_flow_rsi_year_poll_tab(self):
        snap = parseKa00198Body(SPEC_EXAMPLE, "2025-08-27T08:59:00", "0900", "spec_example")
        self.assertEqual(snap["provenance"], "spec_example")
        self.assertEqual(snap["sourceCount"], 2)
        self.assertFalse(snap["exhaustive"])
        self.assertEqual(snap["qryTp"], "4")
        rows = [
            {"originalRank": 1, "stkCd": "000001", "name": "미만", "price": 10, "baseCompChgr": 9.99},
            {"originalRank": 2, "stkCd": "000002", "name": "경계", "price": 9, "baseCompChgr": 10.0},
            {"originalRank": 8, "stkCd": "000003", "name": "통과", "price": 11, "base_comp_chgr": "+10.01"},
            {"originalRank": 3, "stkCd": "000004", "name": "빈칸", "price": 12, "base_comp_chgr": "--"},
            {"originalRank": 4, "stkCd": "000005", "name": "하락", "price": 8, "baseCompChgr": "--1.20"},
        ]
        kept = entryUniverseRows(rows)
        self.assertEqual([r["originalRank"] for r in kept["rows"]], [2, 8])
        shown = tabRankRows(
            [
                {"originalRank": 1, "stkCd": "000009", "baseCompChgr": -0.01, "price": 1},
                {"originalRank": 2, "stkCd": "000008", "baseCompChgr": 0.0, "price": 1},
                {"originalRank": 3, "stkCd": "000007", "baseCompChgr": 4.99, "price": 1},
                {"originalRank": 4, "stkCd": "000006", "baseCompChgr": "--", "price": 1},
            ]
        )
        self.assertEqual([r["stkCd"] for r in shown["rows"]], ["000008", "000007"])
        self.assertEqual(TAB_MIN_DAILY_PCT, 0.0)
        self.assertEqual(ENTRY_MIN_DAILY_PCT, 10.0)
        self.assertEqual(kept["rows"][0]["dailyBasis"], "ka00198_base_comp_chgr")
        self.assertAlmostEqual(kept["rows"][1]["dailyPct"], 10.01)
        self.assertEqual(kept["sourceCount"], 5)
        self.assertFalse(kept["exhaustive"])
        eok, note = turnoverEok(64215, "백만원")
        self.assertAlmostEqual(eok, 64215 * 1_000_000 / 100_000_000)
        self.assertIsNone(turnoverEok(100, "")[0])
        flow = parseKa10059Flow(
            {"dt": "20261106", "orgn": "10", "frgnr_invsr": "-3", "natfor": "99", "acc_trde_prica": "100"},
            {"amtQtyTp": "1", "trdeTp": "0"},
        )
        self.assertEqual(flow["orgnNetMillionKrw"], 10)
        self.assertEqual(flow["frgnNetMillionKrw"], -3)
        self.assertNotIn("99", str(flow["frgnNetMillionKrw"]))
        self.assertEqual(flow["flowStatus"], "provisional_not_krx_final")
        self.assertIn("외국인투자자", flow["foreignDefinition"])
        self.assertEqual(parseKa10059Flow({"orgn": "1"}, {"amtQtyTp": "2", "trdeTp": "0"})["state"], "N/A")
        closes = [float(100 + i) for i in range(14)]
        self.assertIsNone(rsi14(closes))
        closes.append(120)
        self.assertAlmostEqual(rsi14(closes), calcRsiSeries(closes, 14)[-1])
        self.assertIsNone(rsi14(closes, adjusted=False))
        bars = []
        day = datetime(2025, 11, 6)
        for i in range(400):
            dt = (day + timedelta(days=i)).strftime("%Y%m%d")
            bars.append({"dt": dt, "low": 100 if i else 80})
        rise, why = yearLowRisePct(120, bars, "20261106", adjusted=True)
        self.assertEqual(why, "OK")
        self.assertAlmostEqual(rise, (120 / 80 - 1) * 100)
        lateBars = []
        startLate = datetime(2026, 6, 1)
        for i in range(160):
            lateBars.append({"dt": (startLate + timedelta(days=i)).strftime("%Y%m%d"), "low": 50})
        short, whyShort = yearLowRisePct(120, lateBars, "20261106")
        self.assertIsNone(short)
        self.assertEqual(whyShort, "SHORT_HISTORY")
        suspended, whySus = yearLowRisePct(120, bars + [{"dt": "20261101", "low": 1, "suspended": True}], "20261106")
        self.assertIsNone(suspended)
        slots = fifteenMinuteSlots()
        self.assertIn("0900", slots)
        self.assertIn("1515", slots)
        self.assertNotIn("1530", slots)
        self.assertEqual(dueSlot(datetime(2026, 11, 6, 15, 18, 5), True), "1518")
        self.assertEqual(dueSlot(datetime(2026, 11, 6, 16, 0, 10), True), "1600")
        self.assertIsNone(dueSlot(datetime(2026, 11, 7, 9, 0), False))
        self.assertIsNone(nextRetryDelaySec(3))
        self.assertEqual(nextRetryDelaySec(1), 2)
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            late = store.recordLate("20261106", "2026-11-06T16:40:00", [{"originalRank": 1, "stkCd": "005930"}], "synthetic")
            missed = store.slot("20261106", "1600")
            self.assertEqual(missed["state"], "NOT_COLLECTED")
            self.assertIsNone(missed["observedAt"])
            self.assertEqual(late["slot"], "ADHOC")
            self.assertEqual(late["observedAt"], "2026-11-06T16:40:00")
            self.assertEqual(late["rows"][0]["stkCd"], "005930")
            self.assertNotEqual(missed.get("rows"), late["rows"])
        dart = parseDartList(
            {"status": "000", "list": [{"stock_code": "005930", "report_nm": "전쟁", "rcept_no": "2026", "rcept_dt": "20261106"}, {"stock_code": "000660", "report_nm": "무관", "rcept_no": "9", "rcept_dt": "20261106"}]},
            "005930",
        )
        self.assertEqual(len(dart), 1)
        self.assertEqual(dart[0]["tag"], "")
        self.assertFalse(dart[0]["confirmed"])
        bad = validateEventAudit({"eventId": "1", "stkCd": "005930", "eventDate": "20261109", "tag": "전쟁", "sourceUrl": "https://dart.fss.or.kr/x", "publishedAt": "20261106"}, "20261106")
        self.assertFalse(bad["ok"])
        good = validateEventAudit({"eventId": "R1", "stkCd": "005930", "eventDate": "20261106", "tag": "지분분쟁", "sourceUrl": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", "publishedAt": "20261106"}, "20261106")
        self.assertTrue(good["ok"])
        self.assertFalse(good["eligible"])
        self.assertTrue(yellowName(True, 10, "20261106", "20261106"))
        self.assertFalse(yellowName(True, 10, "20261109", "20261106"))
        self.assertFalse(yellowName(False, 12, "20261106", "20261106"))
        view = buildTabRows(
            {"state": "COLLECTED", "observedAt": "2026-11-06T15:18:00", "sourceCount": 5, "provenance": "synthetic", "rows": rows},
            "20261106",
            {"byCode": {"000003": {"issue": good["record"], "rsi14": None, "dataState": "PARTIAL"}}},
        )
        self.assertEqual([r["code"] for r in view["rows"]], ["000001", "000002", "000003"])
        self.assertNotIn("originalRank", view["rows"][0])
        self.assertNotIn("yearLowRisePct", view["rows"][0])
        self.assertEqual(view["rows"][0]["firstSurgeVs"], "-")
        labels = [label for _key, label in TAB_COLUMNS]
        self.assertEqual(
            labels,
            [
                "코드",
                "종목명",
                "현재가",
                "일간%",
                "RSI14",
                "거래대금(억)",
                "이슈",
                "외국인",
                "기관",
                "연기금",
                "최근1년",
                "상태",
            ],
        )
        self.assertNotIn("원순위", labels)
        self.assertNotIn("1년저점대비%", labels)
        self.assertNotIn("시장", labels)
        self.assertIn("상태", labels)
        self.assertIn("최근1년", labels)
        self.assertNotIn("첫급등대비", labels)
        self.assertTrue(view["rows"][2]["yellowName"])
        morning = {
            "tradeYmd": "20261106",
            "slot": "0900",
            "state": "COLLECTED",
            "rows": [{"stkCd": "000003", "price": 100, "baseCompChgr": 10.5, "originalRank": 1}],
        }
        close = {
            "tradeYmd": "20261106",
            "slot": "1600",
            "state": "COLLECTED",
            "rows": [{"stkCd": "000003", "price": 110, "baseCompChgr": 11, "originalRank": 1}],
        }
        book = absorbFirstSurge(absorbFirstSurge({}, morning), close)
        self.assertEqual(book["000003"]["price"], 110)
        self.assertEqual(book["000003"]["source"], "snapshot_1600")
        later = buildTabRows(
            {
                "state": "COLLECTED",
                "observedAt": "2026-11-09T10:00:00",
                "rows": [{"stkCd": "000003", "name": "통과", "price": 121, "baseCompChgr": 5.5, "originalRank": 1}],
            },
            "20261109",
            {"firstSurge": book},
        )
        self.assertEqual(later["rows"][0]["firstSurgeVs"], "+10.00")
        self.assertEqual(later["rows"][0]["firstSurgeSign"], "up")
        downText, downSign = formatFirstSurgeVs(99, 110)
        self.assertEqual(downText, "-10.00")
        self.assertEqual(downSign, "down")
        missingText, _missingSign = formatFirstSurgeVs(None, 110)
        self.assertEqual(missingText, "-")
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            store.save(dict(close, observedAt="2026-11-06T16:00:00"))
            saved = loadFirstSurgeBook(tmp)
            self.assertEqual(saved["000003"]["price"], 110)
            self.assertEqual(saved["000003"]["ymd"], "20261106")
        self.assertFalse(view["exhaustive"])
        empty = buildTabRows(notCollectedSnapshot("20261106", "1600", "2026-11-06T18:00:00"), "20261106")
        self.assertEqual(empty["state"], "NOT_COLLECTED")
        self.assertEqual(empty["rows"], [])
        names = [row["key"] for row in monitorTabCatalog()]
        self.assertEqual(names, ["acnt", "leader", "surge"])
        self.assertEqual(names, [row["key"] for row in standaloneMonitorTabs()])
        shownTabs = [row["key"] for row in visibleMonitorTabs(settings={})]
        self.assertEqual(shownTabs, ["surge"])
        self.assertNotIn("acnt", shownTabs)
        self.assertNotIn("leader", shownTabs)
        rowsAlg = [
            {"algCd": "LDR01", "enabled": False},
            {"algCd": "RAM01", "enabled": False},
            {"algCd": "SRG01", "enabled": False},
        ]
        shown = filterVisibleAlgTabs(rowsAlg, {"LDR01": {"known": True, "openPos": 0, "openOrders": 0}, "RAM01": {"known": False}, "SRG01": {"known": True, "openPos": 2, "openOrders": 0}})
        self.assertEqual([r["algCd"] for r in shown], ["RAM01", "SRG01"])
        self.assertFalse(algWatchSubtabVisible(False, {"known": True, "openPos": 0, "openOrders": 0}))
        self.assertTrue(algWatchSubtabVisible(False, {"known": False}))
        self.assertTrue(algWatchSubtabVisible(True, {"known": True, "openPos": 0, "openOrders": 0}))
        style = orderStyle("151900")
        self.assertEqual(style["trdeTp"], "3")
        auction = orderStyle("152000")
        self.assertEqual(auction["trdeTp"], "0")
        self.assertNotEqual(auction["trdeTp"], "81")
        self.assertIsNone(orderStyle("153000"))
        holiday = SurgeBook()
        self.assertEqual(holiday.decide(datetime(2026, 12, 25, 10, 0), {}, [], 0, {})["reason"], "NOT_TRADING_DAY")

    def test_closing_auction_initial_and_ownership(self):
        now = datetime(2026, 11, 6, 15, 21)
        book = SurgeBook(self.cal)
        got = book.decide(now, {"005930": q(11000, now, prev=10000)}, [cand(now)], 5_000_000, {"005930": True})
        self.assertEqual(buys(got["actions"])[0]["trdeTp"], "0")
        self.assertEqual(buys(got["actions"])[0]["orderStyle"], "LIMIT_CLOSING_AUCTION")
        blocked = SurgeBook(self.cal).decide(self.now, {"005930": q(11000, self.now)}, [cand(self.now)], 5_000_000, {"005930": False})
        self.assertTrue(any(a.get("reason") == "OWNERSHIP_BLOCK" for a in blocked["actions"]))
        self.assertEqual(buys(blocked["actions"]), [])
        unknown = SurgeBook(self.cal)
        unknown.position("005930").update({"phase": "OPEN", "qty": 2, "avgPx": 10000, "costPxQty": 20000, "entryYmd": "20261106", "entryKind": "INITIAL"})
        missingQuote = q(9000, datetime(2026, 11, 9, 10, 0))
        missingQuote.pop("brokerQty")
        noBroker = unknown.decide(datetime(2026, 11, 9, 10, 0), {"005930": missingQuote}, [], 0, {})
        self.assertTrue(any(a.get("reason") == "BROKER_QTY_UNKNOWN" for a in noBroker["actions"]))

    def test_monitor_tabs_reversible_and_operating_bar(self):
        catalog = [row["key"] for row in monitorTabCatalog()]
        self.assertEqual(catalog, ["acnt", "leader", "surge"])
        hidden = [row["key"] for row in visibleMonitorTabs(settings={})]
        self.assertEqual(hidden, ["surge"])
        restored = [
            row["key"]
            for row in visibleMonitorTabs(
                settings={"deskMonitorTabs": ["acnt", "leader", "surge"]}
            )
        ]
        self.assertEqual(restored, ["acnt", "leader", "surge"])
        partial = [
            row["key"]
            for row in visibleMonitorTabs(
                settings={"deskMonitorTabs": ["nope", "surge", "acnt"]}
            )
        ]
        self.assertEqual(partial, ["surge", "acnt"])
        self.assertNotIn("leader", partial)
        self.assertEqual(resolveMonitorSelection("acnt", ["surge"]), "surge")
        self.assertEqual(resolveMonitorSelection("surge", ["surge"]), "surge")
        self.assertEqual(resolveMonitorSelection("leader", ["acnt", "surge"]), "acnt")
        self.assertEqual(resolveMonitorSelection("acnt", []), "")

        specs = {
            "MTS01": {"algCd": "MTS01", "enabled": True, "name": "이평선터치"},
            "LDR01": {"algCd": "LDR01", "enabled": True, "name": "주도주"},
            "MMG01": {"algCd": "MMG01", "enabled": True, "name": "수동정찰"},
            "SRG01": {
                "algCd": "SRG01",
                "enabled": False,
                "name": "급등주",
                "params": {"paperOnly": True},
            },
        }
        rows = [
            {"algCd": "MTS01", "name": "이평선터치", "enabled": True, "qty": 10},
            {"algCd": "LDR01", "name": "주도주", "enabled": True, "qty": 3},
            {"algCd": "MMG01", "name": "수동정찰", "enabled": True},
            {"algCd": "SRG01", "name": "급등주", "enabled": True},
            {"algCd": "NOPE1", "name": "미등록", "enabled": True, "qty": 9},
        ]
        allOff = {
            "maTouchLive": {"enabled": False},
            "stratLive": {
                "LDR01": {"enabled": False},
                "MMG01": {"enabled": False},
                "SRG01": {"enabled": True},
            },
        }
        none = operatingDeskRows(rows, allOff, specs)
        self.assertEqual(none, [])
        emptyBar = formatOpsAlgBar(none)
        self.assertFalse(emptyBar["visible"])
        self.assertEqual(emptyBar["text"], "")
        self.assertEqual(emptyBar["algs"], [])
        self.assertFalse(effectiveAlgEnabled("NOPE1", None, {"stratLive": {"NOPE1": {"enabled": True}}}))
        self.assertFalse(
            effectiveAlgEnabled("SRG01", specs["SRG01"], allOff)
        )

        mixedSettings = {
            "maTouchLive": {"enabled": False},
            "stratLive": {
                "LDR01": {"enabled": True},
                "MMG01": {"enabled": False},
                "SRG01": {"enabled": True},
            },
        }
        mixed = operatingDeskRows(rows, mixedSettings, specs)
        self.assertEqual([r["algCd"] for r in mixed], ["LDR01"])
        bar = formatOpsAlgBar(mixed)
        self.assertTrue(bar["visible"])
        self.assertIn("LDR01", bar["text"])
        self.assertNotIn("MMG01", bar["text"])
        self.assertNotIn("MTS01", bar["text"])
        self.assertNotIn("OFF", bar["text"])
        self.assertNotIn("NOPE1", bar["text"])
        self.assertEqual(resolveInfoSelection("MMG01", bar["algs"]), "LDR01")
        self.assertEqual(resolveInfoSelection("LDR01", bar["algs"]), "LDR01")

        surged = dict(specs["SRG01"], enabled=True)
        specsOn = dict(specs, SRG01=surged)
        both = operatingDeskRows(rows, mixedSettings, specsOn)
        self.assertEqual([r["algCd"] for r in both], ["LDR01", "SRG01"])
        toggledOff = {
            "maTouchLive": {"enabled": False},
            "stratLive": {
                "LDR01": {"enabled": False},
                "MMG01": {"enabled": False},
                "SRG01": {"enabled": False},
            },
        }
        backOff = operatingDeskRows(rows, toggledOff, specsOn)
        self.assertEqual([r["algCd"] for r in backOff], [])
        self.assertEqual(resolveInfoSelection("SRG01", []), "")
        self.assertEqual(resolveInfoSelection("LDR01", ["SRG01"]), "SRG01")

    def test_rank_collector_and_manual_refresh(self):
        self.assertFalse(isSurgeEnabled())
        self.assertTrue(surgeInquiryVisible(settings={}))
        self.assertFalse(surgeInquiryVisible(settings={"deskMonitorTabs": ["acnt"]}))
        body = {
            "item_inq_rank": [
                {
                    "bigd_rank": "3",
                    "stk_cd": "005930",
                    "stk_nm": "삼성전자",
                    "past_curr_prc": "+70000",
                    "base_comp_chgr": "+1.20",
                }
            ]
        }
        calls = []

        def transport():
            calls.append("ka00198")
            return body

        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            morning = datetime(2026, 11, 6, 9, 0, 20)
            saved = pullPaperRank(
                paperSession(purpose="market_read"),
                morning,
                store,
                True,
                transport,
            )
            self.assertEqual(saved["slot"], "0900")
            self.assertEqual(saved["orders"], 0)
            self.assertTrue(saved["called"])
            self.assertEqual(store.slot("20261106", "0900")["observedAt"], "2026-11-06T09:00:20")
            store.save(
                {
                    "ok": False,
                    "tradeYmd": "20261106",
                    "slot": "0915",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-06T09:15:01",
                    "truncated": True,
                    "rows": [],
                }
            )
            beforeRetry = len(calls)
            retry = pullPaperRank(
                paperSession(purpose="market_read"),
                datetime(2026, 11, 6, 9, 15, 20),
                store,
                True,
                transport,
            )
            self.assertTrue(retry["called"])
            self.assertEqual(retry["orders"], 0)
            self.assertGreater(len(calls), beforeRetry)
            again = pullPaperRank(
                paperSession(purpose="market_read"),
                datetime(2026, 11, 6, 9, 0, 40),
                store,
                True,
                transport,
            )
            self.assertFalse(again["called"])
            self.assertEqual(again["orders"], 0)
            late = datetime(2026, 11, 6, 16, 5, 30)
            final = pullPaperRank(
                paperSession(purpose="market_read"),
                late,
                store,
                True,
                transport,
            )
            self.assertEqual(final["slot"], "1600")
            self.assertEqual(final["orders"], 0)
            self.assertEqual(store.slot("20261106", "1600")["observedAt"], "2026-11-06T16:05:30")
            before = len(calls)
            done = pullPaperRank(
                paperSession(purpose="market_read"),
                datetime(2026, 11, 6, 16, 8, 0),
                store,
                True,
                transport,
            )
            self.assertFalse(done["called"])
            self.assertEqual(done["reason"], "FINAL_DONE")
            self.assertEqual(len(calls), before)
            weekend = pullPaperRank(
                paperSession(purpose="market_read"),
                datetime(2026, 11, 7, 10, 0, 0),
                store,
                False,
                transport,
            )
            self.assertFalse(weekend["called"])
            self.assertEqual(weekend["orders"], 0)
            night = datetime(2026, 11, 6, 21, 12, 0)
            manual = runManualRankSave(
                paperSession(purpose="market_read"),
                store,
                night,
                transport,
            )
            self.assertEqual(manual["state"], "DONE")
            self.assertEqual(manual["orders"], 0)
            self.assertEqual(manual["slot"], "ADHOC")
            self.assertEqual(manual["label"], "완료 21:12")
            shown = store.latestForDay("20261106")
            self.assertEqual(shown["slot"], "ADHOC")
            self.assertEqual(shown["observedAt"], "2026-11-06T21:12:00")
            older = {
                "state": "COLLECTED",
                "slot": "ADHOC",
                "observedAt": "2026-11-06T15:00:00",
            }
            finalSnap = {
                "state": "COLLECTED",
                "slot": "1600",
                "observedAt": "2026-11-06T16:05:30",
            }
            self.assertEqual(latestDisplaySnapshot([older, finalSnap])["slot"], "1600")
            newer = {
                "state": "COLLECTED",
                "slot": "ADHOC",
                "observedAt": "2026-11-06T21:12:00",
            }
            self.assertEqual(latestDisplaySnapshot([finalSnap, newer])["slot"], "ADHOC")
        blocked = runManualRankSave(
            paperSession(mode="real"),
            SnapStore(tempfile.mkdtemp()),
            datetime(2026, 11, 6, 21, 0, 0),
            transport,
        )
        self.assertEqual(blocked["orders"], 0)
        self.assertFalse(blocked["called"])
        self.assertTrue(blocked["label"].startswith("실패"))

    def test_entry_inputs_fail_closed_exits_use_quote(self):
        from trading.surgeTradeInputs import (
            entryGate,
            firstSurgeRecord,
            issueFromDartBody,
            issueFromNews,
            membershipFromKa20002,
            membershipLookupBook,
            membershipRefreshDue,
            quoteFromSources,
            readinessChecks,
            rebalanceAnchor,
        )

        self.assertEqual(rebalanceAnchor("20261109"), "20260601")
        freshBook = membershipFromKa20002(
            {"inds_stkpc": [{"stk_cd": "005930"}, {"stk_cd": "000660"}]},
            "20260615",
            "20260601",
            "20261130",
            truncated=False,
        )
        self.assertFalse(membershipRefreshDue(freshBook, "20261109"))
        self.assertTrue(membershipRefreshDue(freshBook, "20261202"))
        self.assertEqual(membershipLookupBook(freshBook)["005930"]["index"], "KOSPI200")
        partial = membershipFromKa20002(
            {"inds_stkpc": [{"stk_cd": "005930"}]},
            "20260615",
            "20260601",
            "20261130",
            truncated=True,
        )
        self.assertEqual(membershipLookupBook(partial), {})
        dartBody = {
            "status": "000",
            "list": [
                {
                    "stock_code": "005930",
                    "report_nm": "지분분쟁 관련 주요사항보고서",
                    "rcept_no": "20261106000111",
                    "rcept_dt": "20261106",
                }
            ],
        }
        issues = issueFromDartBody(dartBody, "005930")
        self.assertEqual(issues[0]["tag"], "지분분쟁")
        self.assertTrue(issues[0]["confirmed"])
        vague = issueFromDartBody(
            {
                "status": "000",
                "list": [
                    {
                        "stock_code": "005930",
                        "report_nm": "주주총회소집공고",
                        "rcept_no": "20261106000999",
                        "rcept_dt": "20261106",
                    }
                ],
            },
            "005930",
        )
        self.assertFalse(vague[0]["confirmed"])
        news = issueFromNews(
            {
                "stkCd": "005930",
                "title": "관세 영향",
                "newsUrl": "https://news.example/a",
                "publishedAt": "20261106",
                "newsId": "n1",
            }
        )
        self.assertTrue(news["confirmed"])
        self.assertEqual(news["tag"], "관세")
        self.assertTrue(firstSurgeRecord(issues, "005930", "20261106", 12)["ok"])
        self.assertEqual(
            firstSurgeRecord(issues, "005930", "20261109", 12)["reason"],
            "NOT_FIRST_SURGE_DAY",
        )
        now = datetime(2026, 11, 6, 15, 18, 10)
        live = quoteFromSources(
            "005930",
            now,
            {"src": "0B", "curPrc": 11000, "asof": datetime(2026, 11, 6, 15, 18, 5)},
        )
        self.assertTrue(live["ok"])
        self.assertEqual(live["source"], "0B")
        old = quoteFromSources(
            "005930",
            now,
            {"src": "0B", "curPrc": 11000, "asof": datetime(2026, 11, 6, 15, 10, 0)},
        )
        self.assertTrue(old["stale"])
        gate = entryGate("005930", "20261106", 12, issues, True)
        self.assertTrue(gate["ok"])
        self.assertNotIn("MEMBERSHIP", gate["reasons"])
        blocked = entryGate("000660", "20261106", 12, issues, True)
        lowDaily = entryGate("005930", "20261106", 9.99, issues, True)
        self.assertIn("DAILY_BELOW_10", lowDaily["reasons"])
        self.assertIn("ISSUE_UNCONFIRMED", blocked["reasons"])
        held = SurgeBook(self.cal)
        held.position("005930").update(
            {
                "phase": "OPEN",
                "qty": 10,
                "avgPx": 10000,
                "costPxQty": 100000,
                "entryYmd": "20261106",
                "entryKind": "INITIAL",
                "stage": 0,
            }
        )
        stopNow = datetime(2026, 11, 9, 10, 0)
        stopped = held.decide(
            stopNow,
            {"005930": q(9000, stopNow, broker=10)},
            [],
            0,
            {"005930": True},
            entryOk={"005930": False},
        )
        self.assertEqual(sells(stopped["actions"])[0]["reason"], "FULL_STOP")
        adding = SurgeBook(self.cal)
        adding.position("005930").update(
            {
                "phase": "OPEN",
                "qty": 10,
                "avgPx": 10000,
                "costPxQty": 100000,
                "entryYmd": "20261106",
                "entryKind": "INITIAL",
                "stage": 0,
            }
        )
        blockedAdd = adding.decide(
            stopNow,
            {"005930": q(11000, stopNow, broker=10)},
            [],
            5_000_000,
            {"005930": True},
            entryOk={"005930": False},
        )
        self.assertEqual(buys(blockedAdd["actions"]), [])
        self.assertTrue(any(a.get("reason") == "ENTRY_INPUT" for a in blockedAdd["actions"]))
        report = readinessChecks(
            env={"DART_API_KEY": "do-not-print"},
            membership=freshBook,
            issueCount=1,
            deskMode="paper",
            quoteSeen=True,
        )
        text = " ".join(row["detail"] for row in report["checks"])
        self.assertNotIn("do-not-print", text)
        byName = {row["name"]: row["status"] for row in report["checks"]}
        self.assertNotIn("kospi200Membership", byName)
        self.assertNotIn("kosdaq150Membership", byName)
        self.assertEqual(byName["dartApiKey"], "PASS")
        self.assertEqual(byName["srg01TradingEnabled"], "FAIL")
        from trading.surgeTradeInputs import (
            applyKospiPages,
            emptyMembership,
            indexCodesFromFinder,
            pagesToCodes,
            tagFromTitle,
        )

        rows = [{"stk_cd": f"{i:06d}"} for i in range(1, 201)]
        codes, truncated = pagesToCodes([{"rows": rows, "contYn": "N"}])
        self.assertEqual(len(codes), 200)
        self.assertFalse(truncated)
        built = applyKospiPages(emptyMembership("20261109"), [{"rows": rows, "contYn": "N"}], "20261109", "20260601", "20261130")
        self.assertTrue(built["indexes"]["KOSPI200"]["complete"])
        short = pagesToCodes([{"rows": rows, "contYn": "Y"}])
        self.assertTrue(short[1])
        self.assertEqual(
            indexCodesFromFinder(
                [{"codeName": "코스닥 150", "short_code": "203", "full_code": "2"}],
                "코스닥 150",
            )["indIdx2"],
            "203",
        )
        self.assertIsNone(
            indexCodesFromFinder(
                [{"codeName": "코스닥 150 레버리지", "short_code": "1", "full_code": "9"}],
                "코스닥 150",
            )
        )
        self.assertEqual(tagFromTitle("깜짝실적 공시"), "어닝서프라이즈")
        from scripts.srgMembershipRefresh import fetchKospiPaperPages, refreshIfDue
        from trading.surgeTradeLive import paperLoginPlan

        self.assertEqual(refreshIfDue()["reason"], "MEMBERSHIP_DISABLED")
        self.assertEqual(refreshIfDue()["orders"], 0)
        self.assertEqual(fetchKospiPaperPages()["pages"], [])
        plan = paperLoginPlan()
        self.assertNotIn("token", plan)
        self.assertNotIn("appKey", plan)

    def test_preopen_tab_keeps_prior_day_list(self):
        def snap(ymd, observed, slot, rows):
            return {
                "ok": True,
                "tradeYmd": ymd,
                "slot": slot,
                "state": "COLLECTED",
                "observedAt": observed,
                "rows": rows,
                "sourceCount": len(rows),
            }

        priorRow = {
            "originalRank": 2,
            "stkCd": "005930",
            "name": "삼성전자",
            "price": 110,
            "baseCompChgr": 6.0,
        }
        zeroRow = {
            "originalRank": 1,
            "stkCd": "000660",
            "name": "하이닉스",
            "price": 1,
            "baseCompChgr": 0.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            bookPath = store.dir / "firstSurge.json"
            bookPath.write_text("{}\n", encoding="utf-8")
            store.save(snap("20261109", "2026-11-09T16:00:10", "1600", [dict(priorRow, price=110)]))
            store.save(snap("20261109", "2026-11-09T22:25:06", "ADHOC", [priorRow]))
            store.save(snap("20261110", "2026-11-10T07:36:00", "ADHOC", [zeroRow]))
            before = bookPath.read_text(encoding="utf-8")
            pre = selectTabDisplay(store, datetime(2026, 11, 10, 7, 36))
            self.assertTrue(pre["fallback"])
            self.assertEqual(pre["orders"], 0)
            self.assertEqual(pre["snap"]["slot"], "1600")
            self.assertEqual(pre["snap"]["rows"][0]["stkCd"], "005930")
            self.assertEqual(pre["queryLabel"], "조회일시 2026-11-09 16:00 (전일 기준)")
            self.assertEqual(bookPath.read_text(encoding="utf-8"), before)
            today = entryUniverseRows(store.snaps("20261110")[-1]["rows"])
            self.assertEqual(today["rows"], [])
            self.assertNotIn("005930", [r["stkCd"] for r in today["rows"]])
            view = buildTabRows(
                pre["snap"],
                pre["asof"],
                {"firstSurge": {"005930": {"ymd": "20261101", "price": 100, "source": "snapshot_1600"}}},
            )
            self.assertEqual(view["rows"][0]["firstSurgeVs"], "+10.00")
            onlyAdhoc = SnapStore(tmp + "-adhoc")
            onlyAdhoc.save(snap("20261109", "2026-11-09T22:25:06", "ADHOC", [priorRow]))
            onlyAdhoc.save(snap("20261110", "2026-11-10T07:30:00", "ADHOC", [zeroRow, zeroRow]))
            adhoc = selectTabDisplay(onlyAdhoc, datetime(2026, 11, 10, 7, 30))
            self.assertEqual(adhoc["snap"]["observedAt"], "2026-11-09T22:25:06")
            self.assertEqual(adhoc["queryLabel"], "조회일시 2026-11-09 22:25 (전일 기준)")
            emptyToday = SnapStore(tmp + "-empty")
            emptyToday.save(snap("20261109", "2026-11-09T22:25:06", "ADHOC", [priorRow]))
            emptyToday.save(snap("20261110", "2026-11-10T09:15:00", "0915", [zeroRow]))
            opened = selectTabDisplay(emptyToday, datetime(2026, 11, 10, 9, 15))
            self.assertFalse(opened["fallback"])
            self.assertEqual(opened["queryLabel"], "조회일시 2026-11-10 09:15")
            openedView = buildTabRows(opened["snap"], opened["asof"], {})
            self.assertEqual(openedView["state"], "READY")
            self.assertEqual(openedView["rows"][0]["code"], "000660")
            self.assertEqual(openedView["rows"][0]["dailyPct"], 0.0)
            weekend = SnapStore(tmp + "-weekend")
            weekend.save(snap("20261112", "2026-11-12T22:25:06", "ADHOC", [priorRow]))
            sat = selectTabDisplay(weekend, datetime(2026, 11, 14, 10, 0))
            self.assertTrue(sat["fallback"])
            self.assertEqual(sat["snap"]["tradeYmd"], "20261112")
            self.assertEqual(queryStampLabel("2026-11-10T09:15:00", False), "조회일시 2026-11-10 09:15")

    def test_column_cache_fills_and_failure_stays_blank(self):
        import json

        from trading.surgeTradeEnrich import displayExtras, enrichDisplayed

        now = datetime(2026, 11, 10, 10, 0)
        row = {
            "stkCd": "180640",
            "name": "한진칼",
            "price": 10000,
            "baseCompChgr": 6.0,
            "originalRank": 1,
        }
        snap = {
            "ok": True,
            "tradeYmd": "20261110",
            "slot": "1000",
            "state": "COLLECTED",
            "observedAt": "2026-11-10T10:00:00",
            "rows": [row],
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            (store.dir / "firstSurge.json").write_text(
                json.dumps(
                    {"180640": {"ymd": "20261109", "price": 10000, "source": "snapshot_intraday"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store.save(snap)
            wired = buildTabRows(store.latestForDay("20261110"), "20261110", displayExtras(store.dir, "20261110"))
            self.assertEqual(wired["rows"][0]["firstSurgeVs"], "+0.00")
            self.assertEqual(entryUniverseRows(snap["rows"])["rows"], [])

            def flow(_code):
                return {"dt": "20261110", "orgn": "10", "frgnr_invsr": "-3", "acc_trde_prica": "100"}

            def bars(_code):
                return [float(100 + i) for i in range(15)]

            result = enrichDisplayed(
                now,
                store,
                {
                    "flow": flow,
                    "bars": bars,
                    "market": lambda _code: "KOSDAQ",
                    "news": lambda _code: [
                        {"title": "한진칼 관세 영향", "url": "https://news.example/a", "publishedAt": "20261110"}
                    ],
                },
            )
            self.assertEqual(result["orders"], 0)
            self.assertEqual(result["calls"]["ka10059"], 1)
            self.assertEqual(result["calls"]["ka10081"], 1)
            filled = buildTabRows(
                store.latestForDay("20261110"), "20261110", displayExtras(store.dir, "20261110")
            )["rows"][0]
            self.assertNotIn("index", [key for key, _label in TAB_COLUMNS])
            self.assertEqual(
                displayExtras(store.dir, "20261110")["byCode"]["180640"]["market"],
                "코스닥",
            )
            self.assertEqual(filled["turnoverEok"], 1.0)
            self.assertEqual(filled["orgnNetMillionKrw"], 10)
            self.assertEqual(filled["frgnNetMillionKrw"], -3)
            self.assertIsNotNone(filled["rsi14"])
            self.assertEqual(filled["issues"], "관세")

        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            store.save(dict(snap, rows=[{"stkCd": "000001", "price": 10, "baseCompChgr": 5.0, "originalRank": 1}]))

            def boom(_code):
                raise RuntimeError("no token")

            failed = enrichDisplayed(
                now, store, {"flow": boom, "bars": boom, "market": boom, "news": boom}
            )
            self.assertEqual(failed["orders"], 0)
            blank = displayExtras(store.dir, "20261110")["byCode"]["000001"]
            self.assertIsNone(blank["flow"]["turnoverEok"])
            self.assertIsNone(blank["rsi14"])
            self.assertEqual(blank["issue"]["tag"], "")
            self.assertEqual(blank["market"], "")

    def test_issue_color_and_mobile_headlines(self):
        from research.naverStkNews import headlinesFromMobileBody
        from trading.surgeTradeData import issueTagColor
        from trading.surgeTradeEnrich import enrichDisplayed, newsFailSummary

        self.assertEqual(issueTagColor(""), "")
        from trading.surgeTradeData import rsiCellColor

        self.assertEqual(rsiCellColor(30), "#D6E8FF")
        self.assertEqual(rsiCellColor(29.99), "#D6E8FF")
        self.assertEqual(rsiCellColor(70), "#FFD6D6")
        self.assertEqual(rsiCellColor(70.01), "#FFD6D6")
        self.assertEqual(rsiCellColor(30.01), "")
        self.assertEqual(rsiCellColor(69.99), "")
        self.assertEqual(rsiCellColor(None), "")
        self.assertEqual(rsiCellColor(""), "")
        from trading.surgeTradeData import DAILY_PCT_BG_10, DAILY_PCT_BG_20, dailyPctCellColor

        self.assertEqual(DAILY_PCT_BG_20, "#FFD6D6")
        self.assertEqual(DAILY_PCT_BG_10, "#FFF3C4")
        self.assertEqual(dailyPctCellColor(20), "#FFD6D6")
        self.assertEqual(dailyPctCellColor(20.01), "#FFD6D6")
        self.assertEqual(dailyPctCellColor(19.99), "#FFF3C4")
        self.assertEqual(dailyPctCellColor(10), "#FFF3C4")
        self.assertEqual(dailyPctCellColor(10.01), "#FFF3C4")
        self.assertEqual(dailyPctCellColor(9.99), "")
        self.assertEqual(dailyPctCellColor(0), "")
        self.assertEqual(dailyPctCellColor(-3.2), "")
        self.assertEqual(dailyPctCellColor(None), "")
        self.assertEqual(dailyPctCellColor(""), "")
        self.assertTrue(issueTagColor("지분분쟁").startswith("#"))
        self.assertNotEqual(issueTagColor("지분분쟁"), issueTagColor("관세"))
        self.assertEqual(issueTagColor("기타이슈"), "")
        colored = buildTabRows(
            {
                "state": "COLLECTED",
                "rows": [
                    {"stkCd": "000001", "name": "가", "price": 10, "baseCompChgr": 6, "originalRank": 1},
                    {"stkCd": "000002", "name": "나", "price": 10, "baseCompChgr": 6, "originalRank": 2},
                    {"stkCd": "000003", "name": "다", "price": 10, "baseCompChgr": 6, "originalRank": 3},
                ],
            },
            "20261110",
            {
                "byCode": {
                    "000001": {"issue": {"tag": "지분분쟁", "confirmed": True, "sourceUrl": "https://n.news.naver.com/a"}},
                    "000002": {"issue": {"tag": "지분분쟁", "confirmed": True, "sourceUrl": "https://n.news.naver.com/b"}},
                    "000003": {"issue": {"tag": "", "confirmed": False}},
                }
            },
        )
        self.assertEqual(colored["rows"][0]["issueColor"], colored["rows"][1]["issueColor"])
        self.assertEqual(colored["rows"][2]["issueColor"], "")
        self.assertNotEqual(colored["rows"][0]["issueColor"], "")
        parsed = headlinesFromMobileBody(
            [
                {
                    "items": [
                        {
                            "officeId": "001",
                            "articleId": "0016432774",
                            "title": "관세 영향 &quot;협상&quot;",
                            "datetime": "202611101028",
                        }
                    ]
                }
            ]
        )
        self.assertEqual(parsed[0]["title"], '관세 영향 "협상"')
        self.assertEqual(
            parsed[0]["newsUrl"],
            "https://n.news.naver.com/mnews/article/001/0016432774",
        )
        self.assertNotIn("005930", newsFailSummary(2))
        now = datetime(2026, 11, 10, 10, 0)
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1000",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:00:00",
                    "rows": [
                        {"stkCd": "000001", "price": 10, "baseCompChgr": 6, "originalRank": 1},
                        {"stkCd": "000002", "price": 10, "baseCompChgr": 7, "originalRank": 2},
                    ],
                }
            )

            def down(_code):
                return {"ok": False, "items": [], "errMsg": "HTTP Error 410"}

            failed = enrichDisplayed(
                now,
                store,
                {"flow": lambda _c: None, "bars": lambda _c: [], "market": lambda _c: "", "news": down},
            )
            self.assertEqual(failed["newsFailures"], 2)
            self.assertEqual(failed["orders"], 0)

    def test_new_high_badge_uses_prior_bars_only(self):
        from trading.surgeTradeEnrich import NEW_HIGH_FETCH_BARS, dailyNewHigh, displayExtras, enrichDisplayed

        prior = [{"dt": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}", "high": 100, "close": 90} for i in range(250)]
        flag, used, high = dailyNewHigh(prior, 100, "20261110")
        self.assertTrue(flag)
        self.assertEqual(used, 250)
        self.assertEqual(high, 100)
        self.assertFalse(dailyNewHigh(prior, 99.99, "20261110")[0])
        short = [{"dt": f"202501{i:02d}", "high": 50, "close": 40} for i in range(1, 11)]
        self.assertEqual(dailyNewHigh(short, 50, "20261110"), (True, 10, 50))
        self.assertEqual(dailyNewHigh([], 50, "20261110"), (False, 0, None))
        withToday = short + [{"dt": "20261110", "high": 999, "close": 90}]
        self.assertFalse(dailyNewHigh(withToday, 49, "20261110")[0])
        old = [{"dt": "20200101", "high": 999, "close": 1}] + [
            {"dt": f"2025{(i // 28) + 1:02d}{(i % 28) + 1:02d}", "high": 100, "close": 90} for i in range(250)
        ]
        self.assertTrue(dailyNewHigh(old, 100, "20261110")[0])
        self.assertEqual(dailyNewHigh(old, 100, "20261110")[1], 250)
        self.assertEqual(NEW_HIGH_FETCH_BARS, 251)
        view = buildTabRows(
            {
                "state": "COLLECTED",
                "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 110, "baseCompChgr": 6, "originalRank": 1}],
            },
            "20261110",
            {"byCode": {"005930": {"newHigh": True, "issue": {}}}},
        )
        self.assertEqual(view["rows"][0]["name"], "삼성전자")
        self.assertNotIn("신", view["rows"][0]["name"])
        self.assertTrue(view["rows"][0]["newHigh"])
        now = datetime(2026, 11, 10, 10, 0)
        calls = {"bars": 0}
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1000",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:00:00",
                    "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 80, "baseCompChgr": 6, "originalRank": 1}],
                }
            )

            def bars(_code):
                calls["bars"] += 1
                return short

            enrichDisplayed(
                now,
                store,
                {"flow": lambda _c: None, "bars": bars, "market": lambda _c: "", "news": lambda _c: []},
            )
            cached = displayExtras(store.dir, "20261110")["byCode"]["005930"]
            self.assertTrue(cached["newHigh"])
            self.assertEqual(cached["newHighLookback"], 10)
            self.assertEqual(calls["bars"], 1)
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1015",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:15:00",
                    "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 40, "baseCompChgr": 6, "originalRank": 1}],
                }
            )
            enrichDisplayed(
                datetime(2026, 11, 10, 10, 15),
                store,
                {"flow": lambda _c: None, "bars": bars, "market": lambda _c: "", "news": lambda _c: []},
            )
            self.assertEqual(calls["bars"], 1)
            again = displayExtras(store.dir, "20261110")["byCode"]["005930"]
            self.assertFalse(again["newHigh"])
            self.assertEqual(again["newHighLookback"], 10)

    def test_chart_load_includes_surge_day_week_month(self):
        from utils.chartBarPlan import (
            CHART_PERIODS,
            POLE_API,
            codesFromSurgeRows,
            collectSurgeChartCds,
            liveTickRedraws,
            mergeLoadCodes,
            needsAutoFetch,
            poleFetchDecision,
            seriesFor,
        )

        rows = [
            {"stkCd": "003490", "baseCompChgr": 11, "price": 100, "originalRank": 1},
            {"stkCd": "000001", "baseCompChgr": -1, "price": 10, "originalRank": 2},
            {"stkCd": "267850", "baseCompChgr": 6, "price": 20, "originalRank": 3},
        ]
        surge = codesFromSurgeRows(rows)
        self.assertEqual(surge, ["003490", "267850"])
        merged = mergeLoadCodes(["005930"], ["005930", "000660"], surge)
        self.assertEqual(merged, ["005930", "000660", "003490", "267850"])
        self.assertEqual(CHART_PERIODS, ("day", "week", "month"))
        self.assertEqual(POLE_API["week"], "ka10082")
        self.assertEqual(POLE_API["month"], "ka10083")
        bundle = {
            "day": [],
            "week": [{"tradeDt": "20261107", "close": 3}],
            "month": [{"tradeDt": "20261101", "close": 4}],
        }
        self.assertTrue(needsAutoFetch(bundle, "day"))
        self.assertFalse(needsAutoFetch(bundle, "week"))
        self.assertEqual(seriesFor(bundle, "month")[0]["close"], 4)
        self.assertNotEqual(seriesFor(bundle, "week"), seriesFor(bundle, "month"))
        self.assertTrue(liveTickRedraws("day"))
        self.assertFalse(liveTickRedraws("week"))
        self.assertFalse(liveTickRedraws("month"))
        self.assertEqual(poleFetchDecision({"cnt": 0, "maxDt": ""}, "20261111", "week"), "fetch")
        self.assertEqual(
            poleFetchDecision({"cnt": 20, "maxDt": "20261110"}, "20261111", "week"),
            "skip",
        )
        self.assertEqual(
            poleFetchDecision({"cnt": 20, "maxDt": "20261110"}, "20261111", "week", refetch=True),
            "fetch",
        )
        self.assertEqual(collectSurgeChartCds(directory="/tmp/satp-no-such-surge"), [])
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1000",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:00:00",
                    "rows": rows,
                }
            )
            shown = collectSurgeChartCds(datetime(2026, 11, 10, 10, 30), tmp)
            self.assertEqual(shown, ["003490", "267850"])

    def test_intraday_chart_load_and_fetch_timeout(self):
        import inspect

        from utils.chartBarLoad import loadDeskChartBundle
        from utils.chartBarPlan import (
            CHART_FETCH_TIMEOUT_SEC,
            chartFetchExpired,
            chartFetchStartLine,
            codesFromSurgeRows,
            intradayChartWanted,
        )

        rows = [
            {"stkCd": "002320", "baseCompChgr": 3, "price": 10, "originalRank": 1},
            {"stkCd": "053800", "baseCompChgr": 0, "price": 10, "originalRank": 2},
            {"stkCd": "199430", "baseCompChgr": -1, "price": 10, "originalRank": 3},
        ]
        shown = codesFromSurgeRows(rows)
        self.assertEqual(shown, ["002320", "053800"])
        self.assertTrue(intradayChartWanted("startup"))
        self.assertTrue(intradayChartWanted("slot"))
        self.assertTrue(intradayChartWanted("manual"))
        self.assertFalse(intradayChartWanted(""))
        line = chartFetchStartLine("002320", "조회")
        self.assertIn("[deskChart]", line)
        self.assertIn("002320", line)
        self.assertFalse(chartFetchExpired(0, 10, CHART_FETCH_TIMEOUT_SEC))
        self.assertTrue(chartFetchExpired(0, CHART_FETCH_TIMEOUT_SEC, CHART_FETCH_TIMEOUT_SEC))
        src = inspect.getsource(loadDeskChartBundle)
        self.assertNotIn("deskStkChart", src)
        self.assertNotIn("backtest.engine", src)
        from utils.chartBarPlan import chartActionForWatchPick

        self.assertEqual(chartActionForWatchPick(True, "002320", chartOpen=False), "open")
        self.assertEqual(
            chartActionForWatchPick(True, "053800", currentCode="002320", chartOpen=True),
            "reload",
        )
        self.assertEqual(
            chartActionForWatchPick(True, "002320", currentCode="002320", chartOpen=True),
            "ignore",
        )
        self.assertEqual(chartActionForWatchPick(True, "", chartOpen=False), "ignore")
        self.assertEqual(chartActionForWatchPick(False, "002320", chartOpen=False), "ignore")
        self.assertEqual(
            chartActionForWatchPick(True, "002320", refreshBusy=True, chartOpen=False),
            "ignore",
        )
        self.assertEqual(
            chartActionForWatchPick(True, "002320", syncBusy=True, chartOpen=False),
            "ignore",
        )
        from utils.chartBarPlan import deskSideSplitSizes

        wide = deskSideSplitSizes(1400, chartOpen=True)
        self.assertGreater(wide[1], wide[0])
        self.assertGreaterEqual(wide[0], 360)
        self.assertGreaterEqual(wide[1], 380)
        kept = deskSideSplitSizes(1600, saved=[400, 1000], chartOpen=True)
        self.assertGreater(kept[1], kept[0])
        self.assertEqual(sum(kept), 1600)
        narrow = deskSideSplitSizes(1400, chartOpen=False)
        self.assertGreaterEqual(narrow[0], 360)
        self.assertGreaterEqual(narrow[1], 300)
        self.assertLess(narrow[1], wide[1])
        from utils.chartBarPlan import CHART_PRICE_AXIS_RIGHT

        self.assertTrue(CHART_PRICE_AXIS_RIGHT)
        from strategies.indicators import calcRsiSeries
        from utils.chartBarPlan import (
            CHART_LOOKBACK_DAYS,
            applyChartLive,
            chartTailNeedsRefresh,
            nextShowCount,
            unifiedLivePx,
            unifiedStkCd,
        )

        self.assertEqual(unifiedStkCd("222800"), "222800_AL")
        self.assertEqual(CHART_LOOKBACK_DAYS, 365 * 30)
        todayBar = [{"tradeDt": "20261111", "open": 108000, "high": 110000, "low": 107600, "close": 109000, "qty": 10}]
        live = applyChartLive(todayBar, "20261111", 123300, "20261111")
        self.assertEqual(live[0]["open"], 108000)
        self.assertEqual(live[0]["close"], 123300)
        self.assertEqual(live[0]["high"], 123300)
        self.assertEqual(live[0]["low"], 107600)
        self.assertIsNone(applyChartLive(todayBar, "20261111", 97700, "20261106"))
        self.assertIsNone(applyChartLive(
            [{"tradeDt": "20261110", "open": 1, "high": 1, "low": 1, "close": 1, "qty": 1}],
            "20261111", 123300, "20261111",
        ))
        self.assertTrue(chartTailNeedsRefresh("20261111", "20261111"))
        self.assertTrue(chartTailNeedsRefresh("", "20261111"))
        self.assertIsNone(unifiedLivePx({"tickYmd": "20261106", "curPrc": 97700}, "20261111"))
        self.assertEqual(
            unifiedLivePx({"tickYmd": "20261111", "krxPrc": 123600, "krxTm": "153000", "nxtPrc": 123300, "nxtTm": "181700"}, "20261111"),
            123300,
        )
        self.assertEqual(nextShowCount(60, 7500, True, step=20, minShow=20), 80)
        self.assertEqual(nextShowCount(60, 7500, False, step=20, minShow=20), 40)
        self.assertEqual(nextShowCount(7400, 7500, True, step=20, minShow=20), 7500)
        closes = [float(100 + i) for i in range(20)]
        self.assertEqual(calcRsiSeries(closes[:10], 14), [None] * 10)
        self.assertIsNotNone(calcRsiSeries(closes, 14)[-1])

    def test_recent_year_uses_daily_low_without_extra_fetch(self):
        from trading.surgeTradeData import formatYearRisePct, recentYearLow, yearRisePct
        from trading.surgeTradeEnrich import displayExtras, enrichDisplayed

        self.assertEqual(formatYearRisePct(yearRisePct(335.4, 100)), ("+235.4%", "up"))
        self.assertEqual(formatYearRisePct(yearRisePct(90, 100)), ("-10.0%", "down"))
        self.assertEqual(formatYearRisePct(None), ("-", ""))
        self.assertIsNone(recentYearLow([{"dt": "20261110", "close": 1, "high": 9}], "20261110"))
        self.assertEqual(recentYearLow([{"dt": "20261110", "lowPric": 100, "close": 1}], "20261110"), 100)
        short = [
            {"dt": "20261101", "low": 50, "close": 999},
            {"dt": "20261110", "low": 70, "close": 1},
        ]
        self.assertEqual(recentYearLow(short, "20261110"), 50)
        self.assertIsNone(recentYearLow([], "20261110"))
        day = datetime(2026, 11, 10)
        bars = []
        for i in range(251, 0, -1):
            bars.append({"dt": (day - timedelta(days=i)).strftime("%Y%m%d"), "low": 10 if i == 251 else 80, "close": 1})
        bars.append({"dt": "20261110", "low": 90, "close": 1})
        self.assertEqual(recentYearLow(bars, "20261110"), 80)
        shown = buildTabRows(
            {
                "state": "COLLECTED",
                "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 335.4, "baseCompChgr": 6, "originalRank": 1}],
            },
            "20261110",
            {"byCode": {"005930": {"yearLow": 100}}, "firstSurge": {"005930": {"ymd": "20261101", "price": 200}}},
        )
        self.assertAlmostEqual(shown["rows"][0]["yearRisePct"], 235.4)
        self.assertEqual(shown["rows"][0]["firstSurgeVs"], "+67.70")
        blank = buildTabRows(
            {
                "state": "COLLECTED",
                "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 335.4, "baseCompChgr": 6, "originalRank": 1}],
            },
            "20261110",
            {"byCode": {"005930": {}}},
        )
        self.assertIsNone(blank["rows"][0]["yearRisePct"])
        calls = {"bars": 0}
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            (store.dir / "firstSurge.json").write_text(
                '{"005930": {"ymd": "20261101", "price": 200, "source": "snapshot_1600"}}',
                encoding="utf-8",
            )
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1000",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:00:00",
                    "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 335.4, "baseCompChgr": 6, "originalRank": 1}],
                }
            )

            def load_bars(_code):
                calls["bars"] += 1
                return short

            enrichDisplayed(
                datetime(2026, 11, 10, 10, 0),
                store,
                {"flow": lambda _c: None, "bars": load_bars, "market": lambda _c: "", "news": lambda _c: []},
            )
            cached = displayExtras(store.dir, "20261110")["byCode"]["005930"]
            self.assertEqual(cached["yearLow"], 50)
            self.assertEqual(calls["bars"], 1)
            self.assertIn("005930", loadFirstSurgeBook(store.dir))
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1015",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T10:15:00",
                    "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 400, "baseCompChgr": 6, "originalRank": 1}],
                }
            )
            enrichDisplayed(
                datetime(2026, 11, 10, 10, 15),
                store,
                {"flow": lambda _c: None, "bars": load_bars, "market": lambda _c: "", "news": lambda _c: []},
            )
            self.assertEqual(calls["bars"], 1)
            view = buildTabRows(store.latestForDay("20261110"), "20261110", displayExtras(store.dir, "20261110"))
            self.assertEqual(formatYearRisePct(view["rows"][0]["yearRisePct"]), ("+700.0%", "up"))
            self.assertEqual(view["rows"][0]["firstSurgeVs"], "+100.00")

    def test_new_surge_badge_window(self):
        from trading.surgeTradeData import nameBadges
        from trading.surgeTradeEnrich import firstNewSurge

        def closes(start, n, jumpAt):
            out = []
            px = 100.0
            day = datetime(2025, 1, 1) + timedelta(days=start)
            for i in range(n):
                if i in jumpAt:
                    px *= 1.12
                out.append({"dt": (day + timedelta(days=i)).strftime("%Y%m%d"), "close": px})
            return out

        only = firstNewSurge(closes(0, 30, {25}), "20251231", 1)
        self.assertTrue(only[0])
        self.assertEqual(only[1], closes(0, 30, {25})[25]["dt"])
        earlier = firstNewSurge(closes(0, 30, {2, 25}), "20251231", 1)
        self.assertFalse(earlier[0])
        self.assertEqual(earlier[1], closes(0, 30, {2})[2]["dt"])
        none = firstNewSurge(closes(0, 30, set()), "20251231", 1)
        self.assertFalse(none[0])
        self.assertEqual(none[1], "")
        short = firstNewSurge(closes(0, 25, {20}), "20251231", 1)
        self.assertTrue(short[0])
        today = firstNewSurge(closes(0, 30, set()), "20261110", 10)
        self.assertTrue(today[0])
        self.assertEqual(today[1], "20261110")
        booked = firstNewSurge(closes(0, 30, set()), "20251231", 1, closes(0, 30, set())[-1]["dt"])
        self.assertTrue(booked[0])
        self.assertEqual(nameBadges(True, True), "신뉴")
        self.assertEqual(nameBadges(False, True), "뉴")
        from trading.surgeTradeData import badgeFillColor

        from trading.surgeTradeData import formatGroupedInt, formatNetEok, netFlowColor

        self.assertEqual(formatGroupedInt(123400), "123,400")
        self.assertEqual(formatGroupedInt(123400.9), "123,400")
        self.assertEqual(formatGroupedInt(1234.9), "1,234")
        self.assertEqual(formatNetEok(123400), ("+1,234억", "up"))
        self.assertEqual(formatNetEok(-5600), ("-56억", "down"))
        self.assertEqual(formatNetEok(50), ("0억", ""))
        self.assertEqual(formatNetEok(None), ("-", ""))
        self.assertEqual(netFlowColor("up"), "#D32F2F")
        self.assertEqual(netFlowColor("down"), "#1565C0")
        self.assertEqual(netFlowColor(""), "")
        from trading.leaderIssueWords import LEADER_EVENT_MAP, LEADER_ISSUE_MAP, REASON_MAP
        from trading.surgeTradeData import assignIssueColors, issueTagColor

        known = []
        for canon, _words in REASON_MAP:
            if canon not in known:
                known.append(canon)
        for _key, lab in list(LEADER_ISSUE_MAP) + list(LEADER_EVENT_MAP):
            if lab not in known:
                known.append(lab)
        colors = [issueTagColor(tag) for tag in known]
        self.assertEqual(len(colors), len(set(colors)))
        self.assertNotIn("", colors)
        self.assertNotIn("#D6E8FF", colors)
        self.assertNotIn("#FFD6D6", colors)
        self.assertNotIn("#D4F5D4", colors)

        def _dist(a, b):
            pa = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
            pb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
            return sum((x - y) ** 2 for x, y in zip(pa, pb)) ** 0.5

        core = []
        for canon, _words in REASON_MAP:
            if canon not in core:
                core.append(canon)
        for _key, lab in LEADER_ISSUE_MAP:
            if lab not in core:
                core.append(lab)
        core_colors = [issueTagColor(tag) for tag in core]
        closest = min(
            _dist(core_colors[i], core_colors[j])
            for i in range(len(core_colors))
            for j in range(i + 1, len(core_colors))
        )
        self.assertGreaterEqual(closest, 24)
        screen = assignIssueColors(known + ["없는이슈A", "없는이슈B", "없는이슈A", ""])
        filled = [color for color in screen if color]
        self.assertEqual(len(set(filled)), len(known) + 2)
        self.assertEqual(screen[-2], screen[-4])
        self.assertNotEqual(screen[-3], screen[-4])
        self.assertEqual(screen[-1], "")
        flow = parseKa10059Flow(
            {"dt": "20261110", "orgn": "200", "frgnr_invsr": "-50", "penfnd_etc": "80"},
            {"amtQtyTp": "1", "trdeTp": "0"},
        )
        self.assertEqual(flow["orgnNetMillionKrw"], 200)
        self.assertEqual(flow["penfndNetMillionKrw"], 80)
        missing = parseKa10059Flow(
            {"dt": "20261110", "orgn": "10", "frgnr_invsr": "1"},
            {"amtQtyTp": "1", "trdeTp": "0"},
        )
        self.assertIsNone(missing["penfndNetMillionKrw"])
        self.assertEqual(badgeFillColor("신"), "#FFD6D6")
        self.assertEqual(badgeFillColor("뉴"), "#D4F5D4")
        self.assertEqual(badgeFillColor(""), "")
        view = buildTabRows(
            {
                "state": "COLLECTED",
                "rows": [{"stkCd": "005930", "name": "삼성전자", "price": 110, "baseCompChgr": 12, "originalRank": 1}],
            },
            "20261110",
            {"byCode": {"005930": {"newHigh": True, "newSurge": True}}},
        )
        self.assertEqual(view["rows"][0]["name"], "삼성전자")
        self.assertTrue(view["rows"][0]["newSurge"])

    def test_issue_cache_refetch_and_leader_words(self):
        from trading.leaderIssueWords import leaderIssueTag
        from trading.surgeTradeEnrich import enrichDisplayed, issueFromHeadlines, issueNeedsFetch

        self.assertEqual(leaderIssueTag("지분분쟁 격화"), "경영권분쟁")
        named = issueFromHeadlines(
            [
                {"title": "코스피 반도체 훈풍", "url": "https://n.news.naver.com/m", "publishedAt": "202611101300"},
                {"title": "한진칼 수주 확대", "url": "https://n.news.naver.com/s", "publishedAt": "202611101200"},
                {"title": "한진칼 경영권 분쟁", "url": "https://n.news.naver.com/o", "publishedAt": "202611100900"},
            ],
            "한진칼",
            "180640",
        )
        self.assertEqual(named["tag"], "지분분쟁")
        self.assertEqual(named["kind"], "reason")
        newer = issueFromHeadlines(
            [
                {"title": "한진칼 수주", "url": "https://n.news.naver.com/a", "publishedAt": "202611100800"},
                {"title": "한진칼 공급계약", "url": "https://n.news.naver.com/b", "publishedAt": "202611101500"},
            ],
            "한진칼",
        )
        self.assertEqual(newer["tag"], "수주")
        self.assertEqual(newer["firstSurgeDate"], "20261110")
        theme = issueFromHeadlines(
            [{"title": "한진칼 위성 발사", "url": "https://n.news.naver.com/t", "publishedAt": "20261110"}],
            "한진칼",
        )
        self.assertEqual(theme["kind"], "theme")
        self.assertEqual(theme["tag"], "우주항공")
        name_only = issueFromHeadlines(
            [{"title": "대한항공 주가 보합", "url": "https://n.news.naver.com/air", "publishedAt": "20261110"}],
            "대한항공",
        )
        self.assertNotEqual(name_only["tag"], "우주항공")
        self.assertEqual(name_only["tag"], "")
        idt = issueFromHeadlines(
            [{"title": "[특징주] 대한항공·아시아나 통합 시너지 시동… 아시아나IDT '上'", "url": "https://n.news.naver.com/idt", "publishedAt": "20261111"}],
            "아시아나IDT",
            "267850",
        )
        self.assertEqual(idt["tag"], "인수합병")
        self.assertEqual(idt["kind"], "reason")
        merge = issueFromHeadlines(
            [{"title": "대한항공 항공사 통합 발표", "url": "https://n.news.naver.com/mg", "publishedAt": "20261111"}],
            "대한항공",
        )
        self.assertEqual(merge["tag"], "인수합병")
        self.assertEqual(merge["kind"], "reason")
        oil = issueFromHeadlines(
            [{"title": "[특징주] 국제유가 급락에 항공주 급등…대한항공 11%대↑(종합)", "url": "https://n.news.naver.com/oil", "publishedAt": "20261111"}],
            "대한항공",
        )
        self.assertEqual(oil["tag"], "유가")
        self.assertEqual(oil["kind"], "reason")
        self.assertNotEqual(oil["tag"], "우주항공")
        space = issueFromHeadlines(
            [{"title": "한진칼 우주 발사체", "url": "https://n.news.naver.com/sp", "publishedAt": "20261111"}],
            "한진칼",
        )
        self.assertEqual(space["tag"], "우주항공")
        airline = issueFromHeadlines(
            [{"title": "대한항공 항공주 동반 강세", "url": "https://n.news.naver.com/al", "publishedAt": "20261111"}],
            "대한항공",
        )
        self.assertEqual(airline["tag"], "항공")
        self.assertEqual(airline["kind"], "theme")
        for phrase in ("항공업계 훈풍 대한항공", "대한항공 항공사 전망"):
            row = issueFromHeadlines(
                [{"title": phrase, "url": "https://n.news.naver.com/ph", "publishedAt": "20261111"}],
                "대한항공",
            )
            self.assertEqual(row["tag"], "항공", phrase)
            self.assertEqual(row["kind"], "theme", phrase)
        stake = issueFromHeadlines(
            [{"title": "한진칼 지분 경쟁 재점화", "url": "https://n.news.naver.com/st", "publishedAt": "20261111"}],
            "한진칼",
            "180640",
        )
        self.assertEqual(stake["tag"], "지분분쟁")
        self.assertEqual(stake["kind"], "reason")
        self.assertEqual(
            issueFromHeadlines(
                [{"title": "한진칼 통합검색 개편", "url": "https://n.news.naver.com/ns", "publishedAt": "20261111"}],
                "한진칼",
            )["tag"],
            "",
        )
        skipped = issueFromHeadlines(
            [{"title": "반도체 훈풍에 코스피 급등", "url": "https://n.news.naver.com/z", "publishedAt": "20261110"}],
            "한진칼",
        )
        self.assertEqual(skipped["tag"], "")
        self.assertIn("0건", skipped["note"])
        self.assertEqual(leaderIssueTag("대규모 수주 공시"), "수주")
        self.assertEqual(leaderIssueTag("관세 협상"), "관세")
        self.assertEqual(leaderIssueTag("어닝서프라이즈"), "어닝서프라이즈")
        blank = issueFromHeadlines(
            [
                {"title": "한진칼 날씨 맑음", "url": "https://n.news.naver.com/a"},
                {"title": "한진칼 주가 보합", "url": "https://n.news.naver.com/b"},
            ],
            "한진칼",
        )
        self.assertEqual(blank["tag"], "")
        self.assertIn("2건", blank["note"])
        now = datetime(2026, 11, 10, 12, 44)
        self.assertTrue(
            issueNeedsFetch(
                {"issueAt": "2026-11-10T12:40:00", "issueNote": "확인된 제목·URL 이 없다"},
                now,
            )
        )
        self.assertFalse(
            issueNeedsFetch(
                {"issueAt": "2026-11-10T12:40:00", "issueNote": "headline+url", "issues": "관세"},
                now,
            )
        )
        self.assertTrue(
            issueNeedsFetch(
                {"issueAt": "2026-11-10T12:40:00", "issueNote": "headline+url"},
                now,
                force=True,
            )
        )
        calls = {"news": 0}
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapStore(tmp)
            (store.dir / "columns_20261110.json").write_text(
                json.dumps(
                    {
                        "180640": {
                            "issueAt": "2026-11-10T10:28:25",
                            "issueNote": "확인된 제목·URL 이 없다",
                            "issues": "",
                            "issueConfirmed": False,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store.save(
                {
                    "ok": True,
                    "tradeYmd": "20261110",
                    "slot": "1244",
                    "state": "COLLECTED",
                    "observedAt": "2026-11-10T12:44:00",
                    "rows": [{"stkCd": "180640", "name": "한진칼", "price": 10, "baseCompChgr": 6, "originalRank": 1}],
                }
            )

            def news(_code):
                calls["news"] += 1
                return {
                    "ok": True,
                    "items": [{"title": "한진칼 경영권 분쟁", "url": "https://n.news.naver.com/x", "publishedAt": "20261110"}],
                }

            enrichDisplayed(
                now,
                store,
                {"flow": lambda _c: None, "bars": lambda _c: [], "market": lambda _c: "", "news": news},
            )
            self.assertEqual(calls["news"], 1)
            saved = json.loads((store.dir / "columns_20261110.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["180640"]["issues"], "지분분쟁")
            self.assertEqual(saved["180640"]["issueKind"], "reason")

    def test_rsi_signal9_ema_and_crosses(self):
        from strategies.indicators import calcEmaSeries, calcRsiSeries, rsiCrossMarks, rsiSignalSeries

        short = calcRsiSeries([float(100 + i) for i in range(14)], 14)
        self.assertEqual(rsiSignalSeries(short, 9), [None] * len(short))
        self.assertEqual(rsiCrossMarks(short, rsiSignalSeries(short, 9)), [None] * len(short))

        closes = [float(100 + i) for i in range(23)]
        rsi = calcRsiSeries(closes, 14)
        signal = rsiSignalSeries(rsi, 9)
        valid = [v for v in rsi if v is not None]
        self.assertEqual(len(valid), 9)
        ema = calcEmaSeries(valid, 9)
        self.assertEqual(signal[:14], [None] * 14)
        self.assertEqual(signal[14:22], [None] * 8)
        self.assertAlmostEqual(signal[22], ema[8])
        self.assertIsNone(rsiCrossMarks(rsi, signal)[22])

        stillBelow = rsiCrossMarks([20.0, 30.0], [40.0, 35.0])
        self.assertEqual(stillBelow, [None, None])

        golden = rsiCrossMarks(
            [30.0, 55.0],
            [40.0, 50.0],
        )
        self.assertEqual(golden, [None, "golden"])
        dead = rsiCrossMarks(
            [60.0, 40.0],
            [50.0, 48.0],
        )
        self.assertEqual(dead, [None, "dead"])
        equalTouch = rsiCrossMarks([30.0, 48.0], [40.0, 48.0])
        self.assertEqual(equalTouch, [None, None])
        stayAbove = rsiCrossMarks([60.0, 62.0], [50.0, 51.0])
        self.assertEqual(stayAbove, [None, None])
        fromEqual = rsiCrossMarks([40.0, 41.0], [40.0, 40.0])
        self.assertEqual(fromEqual, [None, "golden"])

        rising = [100.0 + i for i in range(30)]
        base = calcRsiSeries(rising, 14)
        baseSig = rsiSignalSeries(base, 9)
        jumped = list(rising)
        jumped[-1] = 90.0
        live = calcRsiSeries(jumped, 14)
        liveSig = rsiSignalSeries(live, 9)
        self.assertNotEqual(round(live[-1], 4), round(base[-1], 4))
        self.assertNotEqual(round(liveSig[-1], 4), round(baseSig[-1], 4))
        self.assertEqual(len(rsiCrossMarks(live, liveSig)), len(live))

    def test_ma_macd_and_breakout_marks(self):
        from strategies.indicators import (
            breakoutMarks,
            calcEmaSeries,
            calcSmaSeries,
            macdSeries,
            seriesCrossMarks,
        )

        short = [10.0] * 19
        sma5 = calcSmaSeries(short, 5)
        sma20 = calcSmaSeries(short, 20)
        self.assertTrue(all(v is None for v in sma20))
        self.assertEqual(seriesCrossMarks(sma5, sma20), [None] * 19)

        crossed = [10.0] * 20 + [20.0]
        marks = seriesCrossMarks(calcSmaSeries(crossed, 5), calcSmaSeries(crossed, 20))
        self.assertEqual(marks[20], "golden")
        self.assertTrue(all(v is None for v in marks[:20]))
        self.assertEqual(seriesCrossMarks([1.0, 2.0], [2.0, 2.0]), [None, None])
        self.assertEqual(seriesCrossMarks([3.0, 1.0], [2.0, 2.0]), [None, "dead"])
        self.assertEqual(seriesCrossMarks([3.0, 4.0], [2.0, 2.0]), [None, None])

        tooFew = [float(100 + i) for i in range(25)]
        macd, sig = macdSeries(tooFew)
        self.assertTrue(all(v is None for v in macd))
        self.assertTrue(all(v is None for v in sig))
        self.assertEqual(seriesCrossMarks(macd, sig), [None] * 25)
        wave = [100.0 + (i % 5) for i in range(40)]
        macd, sig = macdSeries(wave)
        fast = calcEmaSeries(wave, 12)
        slow = calcEmaSeries(wave, 26)
        self.assertAlmostEqual(macd[25], fast[25] - slow[25])
        self.assertIsNone(macd[24])
        self.assertIsNone(sig[32])
        self.assertIsNotNone(sig[33])
        self.assertEqual(seriesCrossMarks([1.0, 2.0], [3.0, 2.0]), [None, None])
        moved = list(wave)
        moved[-1] = 80.0
        live, liveSig = macdSeries(moved)
        self.assertNotEqual(round(live[-1], 5), round(macd[-1], 5))
        self.assertEqual(len(seriesCrossMarks(live, liveSig)), len(live))

        self.assertEqual(breakoutMarks([10.0], [9.0], 20), [None])
        self.assertEqual(breakoutMarks([10.0, 10.0], [8.0, 8.0], 20), [None, None])
        self.assertEqual(breakoutMarks([5.0, 6.0], [4.0, 4.0], 20)[1], "high")
        self.assertEqual(breakoutMarks([5.0, 5.0], [4.0, 3.0], 20)[1], "low")
        highs = [100.0] + [10.0] * 20 + [50.0]
        lows = [1.0] + [5.0] * 20 + [4.0]
        self.assertEqual(breakoutMarks(highs, lows, 20)[-1], "both")
        self.assertIsNone(breakoutMarks([10.0, 9.0], [4.0, 4.0], 20)[1])

    def test_chart_signal_toggle_state(self):
        from utils.chartBarPlan import (
            CHART_SIGNAL_GROUPS,
            chartSignalDefaults,
            applyAllChartSignals,
            chartSignalAllState,
            filterAtrPlans,
            filterCrossMarks,
            filterPriceSignalMarks,
            nextAllChartSignals,
            setChartSignal,
        )

        flags = chartSignalDefaults()
        self.assertEqual(
            [key for key, _label in CHART_SIGNAL_GROUPS],
            ["ma", "brk", "rsi", "macd", "pull", "atr"],
        )
        self.assertEqual(
            [label for _key, label in CHART_SIGNAL_GROUPS],
            ["이평크로스", "돌파", "RSI크로스", "MACD크로스", "눌림", "ATR선"],
        )
        self.assertTrue(all(flags.values()))
        rows = [
            {"i": 1, "kind": "maGolden", "px": 10},
            {"i": 2, "kind": "brkHigh", "px": 12},
            {"i": 3, "kind": "maDead", "px": 9},
            {"i": 4, "kind": "brkLow", "px": 8},
        ]
        hiddenMa = filterPriceSignalMarks(rows, setChartSignal(flags, "ma", False))
        self.assertEqual([row["kind"] for row in hiddenMa], ["brkHigh", "brkLow"])
        self.assertEqual(len(rows), 4)
        hiddenBrk = filterPriceSignalMarks(rows, setChartSignal(flags, "brk", False))
        self.assertEqual([row["kind"] for row in hiddenBrk], ["maGolden", "maDead"])
        again = setChartSignal(setChartSignal(flags, "rsi", False), "rsi", True)
        self.assertTrue(again["rsi"])
        self.assertTrue(again["macd"])
        marks = ["golden", None, "dead"]
        self.assertEqual(filterCrossMarks(marks, False), [None, None, None])
        self.assertEqual(filterCrossMarks(marks, True), marks)
        self.assertEqual(marks, ["golden", None, "dead"])
        self.assertEqual(setChartSignal(flags, "nope", False), flags)
        hiddenPull = filterPriceSignalMarks(
            [{"i": 1, "kind": "pullback", "px": 10, "line": 10}],
            setChartSignal(flags, "pull", False),
        )
        self.assertEqual(hiddenPull, [])
        self.assertEqual(filterAtrPlans([{"entry": 1}], setChartSignal(flags, "atr", False)), [])
        self.assertEqual(filterAtrPlans([{"entry": 1}], setChartSignal(flags, "pull", False)), [])
        self.assertEqual(len(filterAtrPlans([{"entry": 1}], flags)), 1)
        self.assertEqual(chartSignalAllState(flags), "checked")
        mixed = setChartSignal(flags, "atr", False)
        self.assertEqual(chartSignalAllState(mixed), "partial")
        self.assertEqual(nextAllChartSignals(mixed), applyAllChartSignals(True))
        off = applyAllChartSignals(False)
        self.assertEqual(chartSignalAllState(off), "unchecked")
        self.assertEqual(nextAllChartSignals(flags), off)
        self.assertEqual(nextAllChartSignals(off), applyAllChartSignals(True))
        self.assertEqual(filterPriceSignalMarks(rows, off), [])
        self.assertEqual(filterAtrPlans([{"entry": 1}], off), [])
        from utils.chartBarPlan import atrLineSpan, priceSpanForPlans, signalFlags

        self.assertTrue(signalFlags(None)["atr"])
        self.assertTrue(signalFlags({})["pull"])
        self.assertTrue(signalFlags({"ma": False})["atr"])
        self.assertEqual(len(filterAtrPlans([{"entry": 1}], None)), 1)
        hidden = {"extend": False, "entryBar": 10, "endBar": 20, "signal": 9}
        self.assertIsNone(atrLineSpan(hidden, 100, 60))
        shown = {"extend": False, "entryBar": 110, "endBar": 120, "signal": 109}
        self.assertEqual(atrLineSpan(shown, 100, 60), (10.0, 20.0))
        extended = {"extend": True, "entryBar": 10, "endBar": 19, "signal": 9, "stop": 40.0, "target": 250.0, "entry": 130.0}
        self.assertEqual(atrLineSpan(extended, 100, 60)[1], 59.5)
        low, high = priceSpanForPlans(100.0, 180.0, [extended], 100, 60)
        self.assertLessEqual(low, 40.0)
        self.assertGreaterEqual(high, 250.0)
        self.assertEqual(priceSpanForPlans(100.0, 180.0, [hidden], 100, 60), (100.0, 180.0))

    def test_alg_mgr_toggle_and_specs(self):
        import tempfile
        from pathlib import Path

        from strategies.specLoader import listStratSpecs, loadStratSpec
        from trading.deskLiveAlgs import effectiveAlgEnabled
        from utils.algMgr import appendAlgMgrLog, applyAlgRunToggle
        from utils.algSpecDoc import specFilePath

        codes = []
        for row in listStratSpecs():
            spec = loadStratSpec(row["stratId"])
            code = spec.get("algCd")
            codes.append(code)
            self.assertTrue(specFilePath(code).is_file(), code)
            text = specFilePath(code).read_text(encoding="utf-8")
            self.assertIn(code, text)
            self.assertIn("## 목적", text)
        self.assertIn("SRG01", codes)
        self.assertIn("MMG01", codes)
        self.assertIn("MTS01", codes)

        settings = {
            "maTouchLive": {"enabled": True, "paperOnly": True},
            "stratLive": {
                "MMG01": {"enabled": True, "paperOnly": True},
                "MTS01": {"enabled": True, "paperOnly": True},
                "SRG01": {"enabled": False, "paperOnly": True},
            },
        }
        mmg = loadStratSpec("manualMgr_v1")
        stopped = applyAlgRunToggle(settings, "MMG01", False, reason="중단", now="2026-11-14 10:00:00")
        self.assertTrue(stopped["ok"])
        self.assertFalse(stopped["settings"]["stratLive"]["MMG01"]["enabled"])
        self.assertTrue(stopped["settings"]["stratLive"]["MMG01"]["paperOnly"])
        self.assertFalse(effectiveAlgEnabled("MMG01", mmg, stopped["settings"]))
        self.assertTrue(settings["stratLive"]["MMG01"]["enabled"])
        again = applyAlgRunToggle(stopped["settings"], "MMG01", True, reason="다시 사용")
        self.assertTrue(effectiveAlgEnabled("MMG01", mmg, again["settings"]))
        self.assertTrue(again["settings"]["stratLive"]["MMG01"]["paperOnly"])

        mts = loadStratSpec("maTouchScale_v1")
        mtsOff = applyAlgRunToggle(settings, "MTS01", False)
        self.assertFalse(mtsOff["settings"]["maTouchLive"]["enabled"])
        self.assertTrue(mtsOff["settings"]["maTouchLive"]["paperOnly"])
        self.assertFalse(effectiveAlgEnabled("MTS01", mts, mtsOff["settings"]))

        srg = loadStratSpec("surgeTrade_v1")
        srgOn = applyAlgRunToggle(settings, "SRG01", True, reason="다시 사용")
        self.assertTrue(srgOn["settings"]["stratLive"]["SRG01"]["enabled"])
        self.assertTrue(srgOn["settings"]["stratLive"]["SRG01"]["paperOnly"])
        self.assertFalse(srg.get("enabled"))
        self.assertFalse(effectiveAlgEnabled("SRG01", srg, srgOn["settings"]))

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "algMgrLog.json"
            appendAlgMgrLog(stopped["log"], path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["algCd"], "MMG01")
            self.assertFalse(saved[0]["new"])
            self.assertIn("팔지 않는다", saved[0]["positions"])

    def test_atr_lines_render_offscreen(self):
        try:
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtGui import QColor
            from PySide6.QtWidgets import QApplication
            from strategies.indicators import calcSmaSeries
            from ui.deskStkChart import DeskStkChartPane
        except Exception as err:
            self.skipTest(f"PySide6 offscreen unavailable: {err}")
        app = QApplication.instance() or QApplication([])
        bars = []
        px = 100.0
        for _i in range(40):
            opened = px
            closed = px + 2.0
            bars.append({
                "tradeDt": "20260101",
                "open": opened,
                "high": closed + 0.4,
                "low": opened - 0.2,
                "close": closed,
                "qty": 1000,
            })
            px = closed
        last = bars[-1]["close"]
        for _k in range(3):
            opened = last
            closed = last - 1.2
            bars.append({
                "tradeDt": "20260102",
                "open": opened,
                "high": opened + 0.1,
                "low": closed,
                "close": closed,
                "qty": 200,
            })
            last = closed
        opened = last
        closed = last + 0.8
        bars.append({
            "tradeDt": "20260103",
            "open": opened,
            "high": closed + 0.3,
            "low": closed,
            "close": closed,
            "qty": 200,
        })
        level = calcSmaSeries([bar["close"] for bar in bars], 10)[-1]
        bars[-1]["low"] = level * 1.002
        bars[-1]["high"] = max(bars[-1]["high"], bars[-1]["close"], bars[-1]["open"])
        pane = DeskStkChartPane()
        pane.resize(980, 720)
        pane.show()
        pane.setChartBundle("222800", "심텍", {"day": bars}, liveYmd="20261113")
        app.processEvents()
        dashBlue = 0
        for series in pane.candleChart.series():
            color = series.pen().color().name().upper()
            if color == "#1565C0" and int(series.pen().style().value) == 2:
                dashBlue += 1
        self.assertGreaterEqual(dashBlue, 1)
        self.assertGreaterEqual(len(pane.candleView._atrPlans), 1)
        img = pane.grab().toImage()
        hits = 0
        for y in range(img.height()):
            for x in range(0, img.width(), 2):
                pixel = QColor(img.pixel(x, y))
                if pixel.blue() > pixel.red() + 25 and pixel.blue() > 90:
                    hits += 1
        self.assertGreater(hits, 20)
        img.save("/tmp/atr-day-lines.png")

    def test_atr_stop_plans(self):
        from utils.chartBarPlan import atrStopPlans, atrWilderSeries

        def flat(n, **over):
            bar = {"open": 105.0, "high": 110.0, "low": 100.0, "close": 105.0, "qty": 1}
            bar.update(over)
            return [dict(bar) for _i in range(n)]

        bars = flat(40)
        atr = atrWilderSeries(bars)
        self.assertIsNone(atr[13])
        self.assertAlmostEqual(atr[14], 10.0)
        self.assertAlmostEqual(atr[20], 10.0)

        def one(line, n=40):
            marks = [None] * n
            marks[20] = {"line": line}
            return atrStopPlans(flat(n), marks, "day")

        plan5 = one(5)[0]
        self.assertEqual(plan5["entryBar"], 21)
        self.assertAlmostEqual(plan5["entry"], 105.0)
        self.assertAlmostEqual(plan5["stop"], 105.0 - 15.0)
        self.assertAlmostEqual(plan5["target"], 105.0 + 20.0)
        plan10 = one(10)[0]
        self.assertAlmostEqual(plan10["stop"], 105.0 - 20.0)
        self.assertAlmostEqual(plan10["target"], 105.0 + 30.0)
        plan20 = one(20)[0]
        self.assertAlmostEqual(plan20["stop"], 105.0 - 20.0)
        self.assertAlmostEqual(plan20["target"], 105.0 + 20.0)

        both = flat(40)
        both[22] = {"open": 105.0, "high": 140.0, "low": 80.0, "close": 105.0, "qty": 1}
        marks = [None] * 40
        marks[20] = {"line": 5}
        hit = atrStopPlans(both, marks, "day")[0]
        self.assertEqual(hit["outcome"], "stop")
        self.assertEqual(hit["exitBar"], 22)
        self.assertEqual(hit["endBar"], 22)

        gapStop = flat(40)
        gapStop[22] = {"open": 80.0, "high": 140.0, "low": 80.0, "close": 100.0, "qty": 1}
        self.assertEqual(atrStopPlans(gapStop, marks, "day")[0]["outcome"], "stop")
        gapTarget = flat(40)
        gapTarget[22] = {"open": 130.0, "high": 140.0, "low": 100.0, "close": 130.0, "qty": 1}
        self.assertEqual(atrStopPlans(gapTarget, marks, "day")[0]["outcome"], "target")

        late = flat(45)
        late[31] = {"open": 105.0, "high": 110.0, "low": 80.0, "close": 100.0, "qty": 1}
        marks45 = [None] * 45
        marks45[20] = {"line": 5}
        capped = atrStopPlans(late, marks45, "day")[0]
        self.assertIsNone(capped["outcome"])
        self.assertEqual(capped["endBar"], 30)
        self.assertFalse(capped["extend"])

        last = flat(30)
        lastMarks = [None] * 30
        lastMarks[-1] = {"line": 10}
        prov = atrStopPlans(last, lastMarks, "day")[0]
        self.assertTrue(prov["provisional"])
        self.assertIsNone(prov["entryBar"])
        self.assertAlmostEqual(prov["entry"], 105.0)
        self.assertTrue(prov["extend"])
        self.assertFalse(prov["label"])

        live = flat(25)
        liveMarks = [None] * 25
        liveMarks[20] = {"line": 5}
        active = atrStopPlans(live, liveMarks, "day")[0]
        self.assertIsNone(active["outcome"])
        self.assertTrue(active["extend"])
        self.assertTrue(active["label"])

        self.assertEqual(atrStopPlans(bars, marks, "week"), [])
        self.assertEqual(atrStopPlans(bars, marks, "month"), [])
        early = [None] * 40
        early[5] = {"line": 5}
        self.assertEqual(atrStopPlans(bars, early, "day"), [])

    def test_pullback_mark_rule(self):
        from strategies.indicators import calcSmaSeries
        from utils.chartBarPlan import pullbackMarks

        def climb(n=40, start=100.0, step=2.0, vol=1000.0):
            bars = []
            px = start
            for _i in range(n):
                opened = px
                closed = px + step
                bars.append({
                    "open": opened,
                    "high": closed + 0.4,
                    "low": opened - 0.2,
                    "close": closed,
                    "qty": vol,
                })
                px = closed
            return bars

        bars = climb()
        last = bars[-1]["close"]
        for _k in range(3):
            opened = last
            closed = last - 1.2
            bars.append({
                "open": opened,
                "high": opened + 0.1,
                "low": closed,
                "close": closed,
                "qty": 200,
            })
            last = closed
        opened = last
        closed = last + 0.8
        bars.append({"open": opened, "high": closed + 0.3, "low": closed, "close": closed, "qty": 200})
        level = calcSmaSeries([bar["close"] for bar in bars], 10)[-1]
        bars[-1]["low"] = level * 1.002
        bars[-1]["high"] = max(bars[-1]["high"], bars[-1]["close"], bars[-1]["open"])
        follow = bars[-1]["close"]
        bars.append({
            "open": follow - 0.2,
            "high": follow + 0.5,
            "low": follow,
            "close": follow + 0.3,
            "qty": 200,
        })
        level2 = calcSmaSeries([bar["close"] for bar in bars], 10)[-1]
        bars[-1]["low"] = level2 * 1.002
        bars[-1]["high"] = max(bars[-1]["high"], bars[-1]["close"], bars[-1]["open"])
        got = [mark for mark in pullbackMarks(bars) if mark]
        self.assertEqual(got, [{"line": 10}])

        loud = climb()
        last = loud[-1]["close"]
        for _k in range(3):
            opened = last
            closed = last - 1.2
            loud.append({
                "open": opened,
                "high": opened + 0.1,
                "low": closed,
                "close": closed,
                "qty": 8000,
            })
            last = closed
        opened = last
        closed = last + 0.8
        loud.append({"open": opened, "high": closed + 0.3, "low": closed, "close": closed, "qty": 8000})
        level = calcSmaSeries([bar["close"] for bar in loud], 10)[-1]
        loud[-1]["low"] = level * 1.002
        loud[-1]["high"] = max(loud[-1]["high"], loud[-1]["close"], loud[-1]["open"])
        self.assertEqual([mark for mark in pullbackMarks(loud) if mark], [])

        down = []
        px = 200.0
        for _i in range(40):
            opened = px
            closed = px - 2.0
            down.append({
                "open": opened,
                "high": opened + 0.2,
                "low": closed - 0.2,
                "close": closed,
                "qty": 200,
            })
            px = closed
        self.assertEqual([mark for mark in pullbackMarks(down) if mark], [])

        deep = []
        px = 100.0
        for _i in range(36):
            opened = px
            closed = px + 0.4
            deep.append({
                "open": opened,
                "high": closed + 0.15,
                "low": opened - 0.1,
                "close": closed,
                "qty": 1000,
            })
            px = closed
        deep.append({"open": px, "high": px + 30.0, "low": px - 0.1, "close": px + 0.3, "qty": 1000})
        px = deep[-1]["close"]
        for _k in range(3):
            opened = px
            closed = px - 0.8
            deep.append({"open": opened, "high": opened + 0.05, "low": closed, "close": closed, "qty": 150})
            px = closed
        closed = px + 0.6
        deep.append({"open": px, "high": closed + 0.2, "low": closed, "close": closed, "qty": 150})
        sma20 = calcSmaSeries([bar["close"] for bar in deep], 20)[-1]
        deep[-1]["low"] = sma20 * 0.99
        self.assertGreaterEqual(deep[-1]["close"], sma20)
        self.assertEqual([mark for mark in pullbackMarks(deep) if mark], [])

    def test_chart_signal_checkbox_session(self):
        try:
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication
            from ui.deskStkChart import DeskStkChartPane
        except Exception as err:
            self.skipTest(f"PySide6 offscreen unavailable: {err}")
        app = QApplication.instance() or QApplication([])
        pane = DeskStkChartPane()
        self.assertTrue(all(box.isChecked() for box in pane._sigBoxes.values()))
        pane._sigBoxes["ma"].setChecked(False)
        pane.setChartBundle(
            "222800",
            "심텍",
            {"day": [{"tradeDt": "20261111", "open": 1, "high": 2, "low": 1, "close": 2, "qty": 1}]},
        )
        self.assertFalse(pane._sigOn["ma"])
        self.assertTrue(pane._sigOn["brk"])
        self.assertTrue(pane._sigOn["rsi"])
        self.assertTrue(pane._sigOn["macd"])
        app.processEvents()

    def test_offscreen_yellow_cell(self):
        try:
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtGui import QBrush, QColor
            from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
        except Exception as err:
            self.skipTest(f"PySide6 offscreen unavailable: {err}")
        app = QApplication.instance() or QApplication([])
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("통과")
        item.setBackground(QBrush(QColor("#FFF59D")))
        table.setItem(0, 0, item)
        self.assertEqual(table.item(0, 0).background().color().name().upper(), "#FFF59D")
        app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
