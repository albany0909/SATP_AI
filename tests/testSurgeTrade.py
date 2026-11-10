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
    SnapStore,
    TAB_COLUMNS,
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
        self.assertEqual(int(spec["version"]), 15)
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
        self.assertEqual(payload["algVer"], 15)
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
                {"originalRank": 1, "stkCd": "000009", "baseCompChgr": 4.99, "price": 1},
                {"originalRank": 2, "stkCd": "000008", "baseCompChgr": 5.0, "price": 1},
            ]
        )
        self.assertEqual([r["stkCd"] for r in shown["rows"]], ["000008"])
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
                "기관순매수",
                "외국인순매수",
                "첫급등대비",
                "상태",
            ],
        )
        self.assertNotIn("원순위", labels)
        self.assertNotIn("1년저점대비%", labels)
        self.assertNotIn("시장", labels)
        self.assertIn("상태", labels)
        self.assertIn("첫급등대비", labels)
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
            self.assertEqual(openedView["state"], "EMPTY")
            self.assertEqual(openedView["rows"], [])
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
                        {"title": "관세 영향", "url": "https://news.example/a", "publishedAt": "20261110"}
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
        self.assertEqual(issueTagColor("지분분쟁"), "#E8F5C8")
        self.assertEqual(issueTagColor("관세"), "#D3E6FF")
        self.assertNotEqual(issueTagColor("지분분쟁"), issueTagColor("관세"))
        self.assertEqual(issueTagColor("기타이슈"), issueTagColor("기타이슈"))
        self.assertTrue(issueTagColor("기타이슈").startswith("#"))
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
        self.assertEqual(leaderIssueTag("대규모 수주 공시"), "수주")
        self.assertEqual(leaderIssueTag("관세 협상"), "관세")
        self.assertEqual(leaderIssueTag("어닝서프라이즈"), "어닝서프라이즈")
        blank = issueFromHeadlines(
            [
                {"title": "날씨 맑음", "url": "https://n.news.naver.com/a"},
                {"title": "주가 보합", "url": "https://n.news.naver.com/b"},
            ]
        )
        self.assertEqual(blank["tag"], "")
        self.assertIn("헤드라인 2건", blank["note"])
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
                    "items": [{"title": "경영권 분쟁", "url": "https://n.news.naver.com/x", "publishedAt": "20261110"}],
                }

            enrichDisplayed(
                now,
                store,
                {"flow": lambda _c: None, "bars": lambda _c: [], "market": lambda _c: "", "news": news},
            )
            self.assertEqual(calls["news"], 1)
            saved = json.loads((store.dir / "columns_20261110.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["180640"]["issues"], "경영권분쟁")

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
