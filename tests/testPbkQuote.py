"""PBK01 시세 구독 계획. 실주문·실 WS 는 열지 않는다."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.pbk01Live import announceCandidates, touchesForQuotes
from trading.pbkQuotePlan import (
    applyPollPrice,
    classifyQuote,
    selectHoldQuote,
    formatSummary,
    maDistancePct,
    planPbkQuotes,
    rankForSubscription,
    regChunks,
    shouldBeat,
    stalePollEntries,
    summarizeQuotes,
    takeOldest,
    takePollBatch,
)


class PbkQuotePlanTest(unittest.TestCase):
    def test_rank_holdings_then_near_ma5(self):
        rows = rankForSubscription([
            {"stkCd": "000002", "holding": False, "ma5": 100, "price": 90},
            {"stkCd": "000001", "holding": True, "ma5": 10, "price": 50},
            {"stkCd": "000003", "holding": False, "ma5": 100, "price": 99},
            {"stkCd": "000004", "holding": False, "ma5": 100},
        ])
        self.assertEqual([row["stkCd"] for row in rows], ["000001", "000003", "000002", "000004"])
        self.assertAlmostEqual(maDistancePct(99, 100), -1.0)

    def test_limit_split_and_chunks(self):
        rows = [
            {"stkCd": "H1", "holding": True},
            {"stkCd": "C1", "holding": False, "ma5": 100, "price": 101},
            {"stkCd": "C2", "holding": False, "ma5": 100, "price": 150},
        ]
        plan = planPbkQuotes(
            rows,
            keptCodes=["WATCH"],
            itemSize=lambda code: 2 if code == "C2" else 1,
            usedItems=1,
            usedStocks=1,
            maxItem=4,
            maxStk=10,
        )
        self.assertEqual(plan["subscribe"], ["WATCH", "H1", "C1"])
        self.assertEqual(plan["poll"], ["C2"])
        items = [f"{i:06d}" for i in range(45)]
        added = regChunks(items, 40, replace=False)
        self.assertEqual([part["refresh"] for part in added], ["1", "1"])
        self.assertEqual(len(added[0]["items"]), 40)
        self.assertEqual(len(added[1]["items"]), 5)
        replaced = regChunks(items, 40, replace=True)
        self.assertEqual([part["refresh"] for part in replaced], ["0", "1"])
        batch, cursor = takePollBatch(["A", "B", "C"], 0, 2)
        self.assertEqual(batch, ["A", "B"])
        nxt, _cursor = takePollBatch(["A", "B", "C"], cursor, 2)
        self.assertEqual(nxt, ["C", "A"])

    def test_summary_and_heartbeat(self):
        self.assertTrue(shouldBeat(None, 100, 60))
        self.assertFalse(shouldBeat(100, 150, 60))
        self.assertTrue(shouldBeat(100, 160, 60))
        summary = summarizeQuotes(
            [
                {"stkCd": "005930", "ma5": 100, "ma20": 80},
                {"stkCd": "000660", "ma5": 200, "ma20": 180},
            ],
            {
                "005930": {"src": "0B", "tm": "093100", "price": 99},
                "000660": {"src": "dayClose", "tm": "195900", "price": 150},
            },
        )
        self.assertEqual(summary["fresh"], 1)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["top"][0]["stkCd"], "005930")
        text = formatSummary(summary)
        self.assertIn("[PBK01] 시세 1/2", text)
        self.assertIn("stale 폴링", text)
        self.assertIn("5이평", text)
        self.assertIn("20이평", text)
        self.assertNotIn("token", text.lower())
        from datetime import datetime

        freshAt = datetime(2026, 11, 20, 10, 0, 30)
        kept, changed = applyPollPrice(
            {"src": "0B", "curPrc": 10, "tm": "100000", "tickYmd": "20261120"},
            11,
            "100010",
            now=freshAt,
        )
        self.assertFalse(changed)
        self.assertEqual(kept["curPrc"], 10)
        staleAt = datetime(2026, 11, 20, 9, 52, 0)
        polled, changed = applyPollPrice(
            {"src": "0B", "curPrc": 10, "tm": "195900", "tickYmd": "20261119"},
            "12345",
            "095200",
            low="12000",
            now=staleAt,
        )
        self.assertTrue(changed)
        self.assertEqual(polled["src"], "poll")
        self.assertEqual(polled["lowPrc"], "12000")
        self.assertEqual(polled["tickYmd"], "20261120")

    def test_stale_quotes_are_polled_oldest_first(self):
        from datetime import datetime

        now = datetime(2026, 11, 20, 9, 52, 0)
        rows = [
            {"stkCd": "053800"},
            {"stkCd": "000210"},
            {"stkCd": "071280"},
            {"stkCd": "246690"},
        ]
        ticks = {
            "000210": {"src": "0B", "curPrc": 1, "tm": "195900", "tickYmd": "20261119"},
            "071280": {"src": "0B", "curPrc": 1, "tm": "094000", "tickYmd": "20261120"},
            "246690": {"src": "0B", "curPrc": 1, "tm": "095100", "tickYmd": "20261120"},
        }
        stale = stalePollEntries(rows, ticks, now, registered={"000210", "071280", "246690", "053800"})
        self.assertEqual(classifyQuote(ticks["246690"], now), "")
        self.assertEqual(classifyQuote({}, now, registered=True), "없음")
        self.assertEqual(classifyQuote({}, now, registered=False), "미등록")
        self.assertEqual(classifyQuote(ticks["000210"], now), "전일")
        self.assertEqual(classifyQuote(ticks["071280"], now), "오래됨")
        self.assertEqual([item["stkCd"] for item in stale], ["053800", "000210", "071280"])
        self.assertEqual(takeOldest(stale, 2), ["053800", "000210"])
        from trading.watchRtMonitor import _splitRtItem

        self.assertEqual(_splitRtItem("053800_NX"), ("053800", "NXT"))
        self.assertEqual(_splitRtItem("A000210"), ("000210", "KRX"))

    def test_touch_uses_polled_price(self):
        got = touchesForQuotes(
            [{"stkCd": "005930", "ma5": 100, "ma20": 90, "signalDt": "20261101"}],
            {"005930": {"price": 99}},
        )
        self.assertEqual([row["stkCd"] for row in got], ["005930"])
        self.assertTrue(got[0]["leg1"])

    def test_telegram_log_hides_token(self):
        logs = []

        def _boom(_text):
            raise RuntimeError("https://api.telegram.org/botSECRETTOKEN/sendMessage")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            first = announceCandidates(
                [{"stkCd": "005930", "signalDt": "20261101", "expireDt": "20261201"}],
                [],
                "20261120",
                sender=_boom,
                log=logs.append,
                path=path,
            )
            self.assertFalse(first["sent"])
            self.assertTrue(any("텔레그램 전송 실패" in line for line in logs))
            self.assertFalse(any("SECRET" in line or "bot" in line for line in logs))
            logs.clear()

            def _ok(_text):
                return None

            second = announceCandidates(
                [{"stkCd": "000660", "signalDt": "20261102", "expireDt": "20261202"}],
                [],
                "20261120",
                sender=_ok,
                log=logs.append,
                path=path,
            )
            self.assertTrue(second["sent"])
            self.assertTrue(any("텔레그램 전송 성공" in line for line in logs))

    def test_add_appends_and_reregister_splits_refresh(self):
        import trading.watchRtMonitor as mon

        savedCodes = list(mon._regCodes)
        savedItems = dict(mon._regItems)
        savedPbk = set(mon._pbkTouch)
        calls = []

        class _Ws:
            isLoggedIn = True

            def sendReg(self, types, items, grpNo="1", refresh="1"):
                calls.append((list(items), str(refresh), str(grpNo)))

            def sendRemove(self, types, items, grpNo="1"):
                calls.append(("REMOVE", list(items)))

        try:
            mon._regCodes[:] = []
            mon._regItems.clear()
            mon._pbkTouch.clear()
            with patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "_isNxtStk", return_value=False,
            ), patch.object(mon, "_writeState", lambda *_a, **_k: None), patch(
                "trading.maTouchLive.prepareMaTouchSession", lambda *_a, **_k: None,
            ):
                added = mon.addWatchRtCodes(["005930"], "테스트")
                self.assertEqual(added, 1)
                self.assertEqual(calls[0][1], "1")
                calls.clear()
                mon._regWatch(_Ws(), [f"{i:06d}" for i in range(45)], "재등록")
                flags = [row[1] for row in calls if row[0] != "REMOVE"]
                self.assertEqual(flags, ["0", "1"])
                self.assertEqual(len(calls[0][0]), 40)
                self.assertEqual(len(calls[1][0]), 5)
        finally:
            mon._regCodes[:] = savedCodes
            mon._regItems.clear()
            mon._regItems.update(savedItems)
            mon._pbkTouch.clear()
            mon._pbkTouch.update(savedPbk)

    def test_account_sync_keeps_pbk_subscription(self):
        import trading.watchRtMonitor as mon

        pbk = [f"{i:06d}" for i in range(1, 57)]
        watch = [f"{i:06d}" for i in range(100, 113)]
        savedCodes = list(mon._regCodes)
        savedItems = dict(mon._regItems)
        savedPbk = set(mon._pbkTouch)
        removed = []

        class _Ws:
            isLoggedIn = True

            def sendReg(self, types, items, grpNo="1", refresh="1"):
                return None

            def sendRemove(self, types, items, grpNo="1"):
                removed.append(list(items))

        try:
            mon._regCodes[:] = list(pbk)
            mon._regItems.clear()
            mon._regItems.update({code: [code] for code in pbk})
            mon._pbkTouch.clear()
            mon._pbkTouch.update(pbk)
            with patch.object(mon, "_isQuoteSessionNow", return_value=True), patch.object(
                mon, "_monCodes", return_value=list(watch),
            ), patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "_isNxtStk", return_value=False,
            ), patch.object(mon, "_writeState", lambda *_a, **_k: None), patch(
                "trading.maTouchLive.prepareMaTouchSession", lambda *_a, **_k: None,
            ):
                mon.reregWatchForExch("실계좌동기화")
            self.assertEqual(removed, [])
            for code in pbk:
                self.assertIn(code, mon._regCodes)
            for code in watch:
                self.assertIn(code, mon._regCodes)
        finally:
            mon._regCodes[:] = savedCodes
            mon._regItems.clear()
            mon._regItems.update(savedItems)
            mon._pbkTouch.clear()
            mon._pbkTouch.update(savedPbk)

    def test_holdings_polled_when_subscription_dropped(self):
        from datetime import datetime

        from trading.pbkQuotePlan import pollBatch

        now = datetime(2026, 11, 20, 10, 11, 0)
        rows = [
            {"stkCd": "900290", "holding": True},
            {"stkCd": "300080", "holding": True},
            {"stkCd": "005930", "holding": False},
            {"stkCd": "000660", "holding": True},
        ]
        ticks = {
            "000660": {"src": "poll", "curPrc": 10, "tm": "101000", "tickYmd": "20261120"},
        }
        batch = pollBatch(rows, ticks, now, registered=set(), limit=1)
        self.assertEqual(set(batch), {"900290", "300080"})
        self.assertNotIn("005930", batch)
        self.assertNotIn("000660", batch)
        wider = pollBatch(rows, ticks, now, registered=set(), limit=3)
        self.assertEqual(wider[-1], "005930")
        waited = pollBatch(
            rows, ticks, now, registered=set(), limit=1, priorityCodes=["005930"],
        )
        self.assertIn("005930", waited)
        self.assertLess(waited.index("900290"), waited.index("005930"))

    def test_holdings_polled_after_sixty_seconds_and_before_stop(self):
        from datetime import datetime

        from trading.pbkQuotePlan import pollBatch

        now = datetime(2026, 11, 20, 10, 33, 0)
        rows = [
            {"stkCd": "900290", "holding": True},
            {"stkCd": "300080", "holding": True},
            {"stkCd": "005930", "holding": False},
            {"stkCd": "000660", "holding": False},
        ]
        ticks = {
            "900290": {"src": "poll", "curPrc": 2730, "tm": "102954", "tickYmd": "20261120"},
            "300080": {"src": "0B", "curPrc": 100, "tm": "103230", "tickYmd": "20261120"},
            "005930": {"src": "poll", "curPrc": 1, "tm": "090000", "tickYmd": "20261120"},
            "000660": {"src": "poll", "curPrc": 1, "tm": "090000", "tickYmd": "20261120"},
        }
        batch = pollBatch(rows, ticks, now, registered={"300080"}, limit=4)
        self.assertEqual(batch, ["900290", "000660", "005930"])
        self.assertNotIn("300080", batch)
        stop = datetime(2026, 11, 20, 15, 14, 0)
        forced = pollBatch(rows, ticks, stop, registered={"300080"}, limit=2)
        self.assertEqual(forced, ["900290", "300080"])

    def test_off_session_ignores_live_quotes_for_touches(self):
        from datetime import datetime

        import trading.pbk01Live as live
        from trading.pbkOrderPlan import buildLiveActions

        sunday = datetime(2026, 11, 22, 9, 38, 58)
        monday = datetime(2026, 11, 23, 10, 0, 0)
        rows = [{"stkCd": "005930", "ma5": 100, "ma20": 90, "signalDt": "20261101"}]
        sunday_px = {"src": "0B", "curPrc": "90", "tm": "093858", "tickYmd": "20261122", "lowPrc": "90"}
        logs = []
        fetched = {"n": 0}

        def _fetch(_code):
            fetched["n"] += 1
            return {"price": "90", "tm": "093900", "low": "90"}

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text(
                json.dumps({"kind": "state", "candidates": rows, "positions": []}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with patch("trading.watchRtMonitor.peekWatchLastPx", return_value=sunday_px), patch(
                "trading.watchRtMonitor.syncPbkTouch",
            ) as sync, patch("trading.pbk01Live._fetchKa10001", side_effect=_fetch):
                got = live.runPbkCycle(
                    now=sunday, path=path, log=logs.append, enabled=True, force=False,
                )
            self.assertEqual(got["touches"], [])
            self.assertEqual(got["quotes"], {})
            self.assertEqual(sync.call_count, 0)
            self.assertEqual(fetched["n"], 0)
            self.assertTrue(any("장외 시세 무시" in line for line in logs))
            forced = live.runPbkCycle(
                now=sunday,
                path=path,
                log=logs.append,
                enabled=True,
                quotes={"005930": {"price": 90, "src": "0B", "fresh": True, "curPrc": "90"}},
            )
            self.assertEqual(forced["touches"], [])
            self.assertEqual(forced["quotes"], {})
            plain = live.runPbkCycle(
                now=sunday,
                path=path,
                log=logs.append,
                enabled=True,
                quotes={"005930": {"price": 90}},
            )
            self.assertEqual([row["stkCd"] for row in plain["touches"]], ["005930"])
            skipped = live.refreshPbkQuotes(rows, [], now=sunday, fetch=_fetch)
            self.assertEqual(skipped.get("skipped"), "offsession")
            self.assertEqual(fetched["n"], 0)
            monday_px = {"src": "0B", "curPrc": "90", "tm": "100000", "tickYmd": "20261123", "lowPrc": "90"}
            saved_scan = live._scanYmd
            live._scanYmd = "20261123"
            try:
                with patch("trading.watchRtMonitor.peekWatchLastPx", return_value=monday_px), patch(
                    "trading.pbk01Live.refreshPbkQuotes",
                    return_value={"plan": {"subscribe": []}, "stale": []},
                ) as refresh:
                    opened = live.runPbkCycle(now=monday, path=path, log=lambda _t: None, enabled=True)
            finally:
                live._scanYmd = saved_scan
            self.assertEqual([row["stkCd"] for row in opened["touches"]], ["005930"])
            self.assertEqual(refresh.call_count, 1)
            stale = {"price": 90, "fresh": False, "src": "0B", "ask": 90, "curPrc": "90", "tickYmd": "20261122"}
            held = buildLiveActions(rows, {"005930": stale}, [], "100000", "20261123", now=monday)
            self.assertEqual(held["actions"], [])
            fresh = {"price": 95, "fresh": True, "src": "0B", "ask": 95, "curPrc": "95", "tickYmd": "20261123"}
            deal = buildLiveActions(rows, {"005930": fresh}, [], "100000", "20261123", now=monday)
            self.assertEqual([(row["side"], row["leg"]) for row in deal["actions"]], [("BUY", 1)])

    def test_unregistered_screen_codes_are_added_with_refresh_zero(self):
        from utils.pbkWatchScreen import touchDelta

        delta = touchDelta(
            ["333333", "444444"],
            ["333333", "444444", "111111"],
            ["111111"],
            registered=["111111"],
        )
        self.assertEqual(delta["added"], ["333333", "444444"])
        self.assertEqual(delta["missing"], ["333333", "444444"])
        self.assertEqual(delta["removed"], [])
        import trading.watchRtMonitor as mon

        savedCodes = list(mon._regCodes)
        savedItems = dict(mon._regItems)
        savedPbk = set(mon._pbkTouch)
        calls = []

        class _Ws:
            isLoggedIn = True

            def sendReg(self, types, items, grpNo="1", refresh="1"):
                calls.append((list(items), str(refresh)))

            def sendRemove(self, types, items, grpNo="1"):
                return None

        try:
            mon._regCodes[:] = ["111111"]
            mon._regItems.clear()
            mon._regItems["111111"] = ["111111"]
            mon._pbkTouch.clear()
            mon._pbkTouch.update(["333333", "444444"])
            with patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "_isNxtStk", return_value=False,
            ), patch.object(mon, "_writeState", lambda *_a, **_k: None), patch.object(
                mon, "_monCodes", return_value=["111111"],
            ), patch("trading.maTouchLive.prepareMaTouchSession", lambda *_a, **_k: None):
                got = mon.syncPbkTouch(["111111", "333333", "444444"], "PBK화면")
            self.assertEqual(got["added"], ["333333", "444444"])
            self.assertTrue(calls)
            self.assertTrue(all(row[1] == "1" for row in calls))
            calls.clear()

            def _reg(ws, codes, reason):
                calls.append((list(codes), reason))

            with patch.object(mon, "_isQuoteSessionNow", return_value=True), patch.object(
                mon, "_monCodes", return_value=["005930"],
            ), patch(
                "utils.pbkWatchScreen.pbkStateCodes", return_value=["300120"],
            ), patch.object(mon, "_regWatch", side_effect=_reg):
                mon._onLogin(_Ws())
            self.assertIn("300120", calls[0][0])
            self.assertEqual(calls[0][1], "LOGIN(시세구간)")
        finally:
            mon._regCodes[:] = savedCodes
            mon._regItems.clear()
            mon._regItems.update(savedItems)
            mon._pbkTouch.clear()
            mon._pbkTouch.update(savedPbk)

    def test_session_start_includes_pbk_codes(self):
        import trading.watchRtMonitor as mon

        calls = []

        def _reg(ws, codes, reason):
            calls.append((list(codes), reason))

        opens = iter([False, True])

        def _open():
            return next(opens)

        def _sleep(_sec):
            if calls:
                raise RuntimeError("stop")

        with patch.object(mon, "_isQuoteSessionNow", side_effect=_open), patch.object(
            mon, "_monCodes", return_value=["005930"],
        ), patch(
            "utils.pbkWatchScreen.pbkStateCodes", return_value=["300120", "900290"],
        ), patch.object(mon, "_regWatch", side_effect=_reg), patch.object(
            mon, "_prepareBeforeOpen", lambda *_a, **_k: None,
        ), patch.object(mon, "_flushLastPxCache", lambda *_a, **_k: None), patch.object(
            mon, "getWsClient", return_value=type("W", (), {"isLoggedIn": True})(),
        ), patch("trading.watchRtMonitor.time.sleep", side_effect=_sleep):
            with self.assertRaises(RuntimeError):
                mon._mktLoop()
        self.assertTrue(calls)
        self.assertEqual(calls[0][1], "시세시작")
        self.assertIn("300120", calls[0][0])
        self.assertIn("900290", calls[0][0])

    def test_hold_price_skips_prior_krx_close(self):
        from ui.tradeDeskWin import _dispQuote, _holdLivePx

        prior = {
            "curPrc": 9580,
            "krxPrc": 10000,
            "krxTm": "153000",
            "krxFlu": "-7.91",
            "fluRt": "-7.91",
            "dayCloseDt": "20261123",
            "krxYmd": "20261123",
            "src": "poll",
            "tm": "091800",
            "tickYmd": "20261124",
        }
        prc, flu = selectHoldQuote(prior, "20261124", inKrx=True)
        self.assertEqual(prc, 9580)
        self.assertAlmostEqual(flu, -4.2, places=2)
        shown, shownFlu = _dispQuote(prior, inKrx=True)
        self.assertEqual(shown, 9580)
        self.assertAlmostEqual(shownFlu, -4.2, places=2)
        self.assertEqual(_holdLivePx(prior, 0, inKrx=True), 9580)
        self.assertNotEqual(round(shownFlu, 2), -7.91)

        todayKrx = {
            "curPrc": 9700,
            "krxPrc": 9700,
            "krxTm": "091800",
            "krxFlu": "-3.00",
            "krxYmd": "20261124",
            "src": "0B",
            "tm": "091800",
            "tickYmd": "20261124",
        }
        prc, flu = selectHoldQuote(todayKrx, "20261124", inKrx=True)
        self.assertEqual(prc, 9700)
        self.assertAlmostEqual(flu, -3.0, places=2)

        off = {
            "nxtPrc": 9600,
            "nxtFlu": "-1.00",
            "krxPrc": 10000,
            "krxTm": "153000",
            "krxYmd": "20261123",
            "curPrc": 9580,
            "tm": "091800",
            "tickYmd": "20261124",
            "src": "poll",
        }
        prc, flu = selectHoldQuote(off, "20261124", inKrx=False)
        self.assertEqual(prc, 9600)
        self.assertAlmostEqual(flu, -4.0, places=2)

        from datetime import datetime

        polled, changed = applyPollPrice(
            {
                "src": "dayClose",
                "curPrc": 10000,
                "krxPrc": 10000,
                "krxTm": "153000",
                "krxFlu": "-7.91",
                "dayCloseDt": "20261123",
                "tm": "153000",
            },
            9580,
            "091800",
            now=datetime(2026, 11, 24, 9, 18),
        )
        self.assertTrue(changed)
        self.assertEqual(polled["krxPrc"], 9580)
        self.assertEqual(polled["krxTm"], "091800")
        self.assertEqual(polled["krxYmd"], "20261124")
        self.assertNotIn("prevClose", polled)
        self.assertAlmostEqual(float(polled["krxFlu"]), -4.2, places=2)

    def test_stop_input_rejects_prior_close(self):
        from trading.pbkQuotePlan import stopInputPx

        stale = {
            "curPrc": 7900,
            "krxPrc": 7900,
            "krxTm": "153000",
            "krxYmd": "20261123",
            "dayCloseDt": "20261123",
            "src": "dayClose",
            "tm": "153000",
            "fresh": True,
        }
        self.assertIsNone(stopInputPx(stale, "20261124"))
        live = {
            "curPrc": 9000,
            "krxPrc": 7900,
            "krxTm": "153000",
            "krxYmd": "20261123",
            "dayCloseDt": "20261123",
            "src": "poll",
            "tm": "151400",
            "tickYmd": "20261124",
        }
        self.assertEqual(stopInputPx(live, "20261124"), 9000)
        session = {
            "krxPrc": 7900,
            "krxTm": "151500",
            "krxYmd": "20261124",
            "src": "0B",
            "tm": "151500",
            "tickYmd": "20261124",
        }
        self.assertEqual(stopInputPx(session, "20261124"), 7900)

    def test_partial_remove_reregisters_the_rest(self):
        import trading.watchRtMonitor as mon

        savedCodes = list(mon._regCodes)
        savedItems = dict(mon._regItems)
        calls = []

        class _Ws:
            isLoggedIn = True

            def sendReg(self, types, items, grpNo="1", refresh="1"):
                calls.append(("REG", list(items), str(refresh), str(grpNo)))

            def sendRemove(self, types, items, grpNo="1"):
                calls.append(("REMOVE", list(items), None, str(grpNo)))

        try:
            mon._regCodes[:] = ["111111", "222222", "333333"]
            mon._regItems.clear()
            mon._regItems.update({cd: [cd, f"{cd}_NX"] for cd in mon._regCodes})
            with patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "_writeState", lambda *_a, **_k: None,
            ), patch("trading.maTouchLive.prepareMaTouchSession", lambda *_a, **_k: None), patch.object(
                mon, "_isNxtStk", return_value=False,
            ):
                mon.unregWatchStk("222222", "테스트", resub=True)
            self.assertNotIn("222222", mon._regCodes)
            self.assertEqual(calls[0][0], "REMOVE")
            self.assertEqual(calls[0][1], ["222222", "222222_NX"])
            regs = [row for row in calls if row[0] == "REG"]
            self.assertTrue(regs)
            self.assertEqual(regs[0][2], "0")
            flat = [code for row in regs for code in row[1]]
            self.assertIn("111111", flat)
            self.assertIn("333333", flat)
            self.assertNotIn("222222", flat)
        finally:
            mon._regCodes[:] = savedCodes
            mon._regItems.clear()
            mon._regItems.update(savedItems)

    def test_login_before_session_regs_at_quote_start_and_regular_open(self):
        import trading.watchRtMonitor as mon

        calls = []
        savedPending = mon._loginPendingReg
        savedRegular = mon._regularRegYmd

        def _reg(ws, codes, reason):
            calls.append(reason)

        try:
            with patch.object(mon, "_isQuoteSessionNow", return_value=False), patch.object(
                mon, "_regWatch", side_effect=_reg,
            ):
                mon._onLogin(type("W", (), {"isLoggedIn": True})())
            self.assertTrue(mon._loginPendingReg)
            self.assertEqual(calls, [])
            with patch.object(mon, "_isQuoteSessionNow", return_value=True), patch.object(
                mon, "isTradingDay", return_value=True,
            ), patch.object(mon, "isWithinMktHours", return_value=False), patch.object(
                mon, "_regWatch", side_effect=_reg,
            ), patch.object(mon, "getWsClient", return_value=type("W", (), {"isLoggedIn": True})()), patch.object(
                mon, "_flushLastPxCache", lambda *_a, **_k: None,
            ), patch.object(mon, "_monCodes", return_value=["005930"]):
                mon._tickSession(True)
            self.assertIn("LOGIN후시세시작", calls)
            self.assertFalse(mon._loginPendingReg)
            calls.clear()
            mon._regularRegYmd = ""
            with patch.object(mon, "_isQuoteSessionNow", return_value=True), patch.object(
                mon, "isTradingDay", return_value=True,
            ), patch.object(mon, "isWithinMktHours", return_value=True), patch.object(
                mon, "_regWatch", side_effect=_reg,
            ), patch.object(mon, "getWsClient", return_value=type("W", (), {"isLoggedIn": True})()), patch.object(
                mon, "_flushLastPxCache", lambda *_a, **_k: None,
            ), patch.object(mon, "repairSilentRegs", lambda *_a, **_k: []), patch.object(
                mon, "_monCodes", return_value=["005930"],
            ):
                mon._tickSession(True)
            self.assertIn("정규장시작", calls)
        finally:
            mon._loginPendingReg = savedPending
            mon._regularRegYmd = savedRegular

    def test_close_seed_accepts_morning_tick(self):
        import trading.watchRtMonitor as mon

        seeded = {
            "src": "dayClose",
            "curPrc": "10000",
            "krxPrc": "10000",
            "tm": "153000",
            "krxTm": "153000",
            "tickYmd": "20261124",
            "dayCloseDt": "20261123",
            "krxYmd": "20261123",
        }
        self.assertFalse(mon.cachedQuoteIsNewer(seeded, "20261124", "090118"))
        savedPx = dict(mon._lastPx)
        savedCodes = list(mon._regCodes)
        try:
            mon._lastPx.clear()
            mon._regCodes[:] = ["122870"]
            mon._lastPx["122870"] = dict(seeded)
            with patch.object(mon, "_flushLastPxCache", lambda *_a, **_k: None), patch(
                "trading.maTouchLive.onMaTouchTick", lambda *_a, **_k: None,
            ):
                mon._onWsMessage({"trnm": "REAL", "data": [{
                    "type": "0B",
                    "item": "122870",
                    "values": {"10": "9580", "20": "090118"},
                }]})
            self.assertEqual(str(mon._lastPx["122870"]["curPrc"]), "9580")
            self.assertEqual(mon._lastPx["122870"]["src"], "0B")
            self.assertEqual(mon._lastPx["122870"]["krxTm"], "090118")
        finally:
            mon._lastPx.clear()
            mon._lastPx.update(savedPx)
            mon._regCodes[:] = savedCodes

    def test_silent_reregister_stops_at_the_cap(self):
        from datetime import datetime

        import trading.watchRtMonitor as mon

        savedCodes = list(mon._regCodes)
        savedItems = dict(mon._regItems)
        savedN = dict(mon._reregN)
        savedChanged = dict(mon._pollChanged)
        savedAt = dict(mon._regAt)
        calls = []

        class _Ws:
            isLoggedIn = True

            def sendReg(self, types, items, grpNo="1", refresh="1"):
                calls.append((list(items), str(refresh)))

            def sendRemove(self, types, items, grpNo="1"):
                return None

        moment = datetime(2026, 11, 24, 10, 0, 0)
        try:
            mon._regCodes[:] = ["300120"]
            mon._regItems.clear()
            mon._regItems["300120"] = ["300120"]
            mon._lastPx["300120"] = {
                "src": "poll",
                "curPrc": 9000,
                "tm": "095000",
                "tickYmd": "20261124",
                "krxPrc": 7900,
                "krxTm": "153000",
                "krxYmd": "20261123",
                "dayCloseDt": "20261123",
            }
            mon._pollChanged["300120"] = 9000
            mon._regAt["300120"] = 0
            mon._reregYmd = "20261124"
            mon._reregN["300120"] = mon._REREG_CAP
            with patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "isTradingDay", return_value=True,
            ), patch.object(mon, "isWithinMktHours", return_value=True):
                blocked = mon.repairSilentRegs(moment)
            self.assertEqual(blocked, [])
            self.assertEqual(calls, [])
            mon._reregN["300120"] = 0
            mon._reregTold.discard("300120")
            with patch.object(mon, "getWsClient", return_value=_Ws()), patch.object(
                mon, "isTradingDay", return_value=True,
            ), patch.object(mon, "isWithinMktHours", return_value=True):
                sent = mon.repairSilentRegs(moment)
            self.assertEqual(sent, ["300120"])
            self.assertEqual(calls[0][1], "1")
            self.assertEqual(mon._reregN["300120"], 1)
        finally:
            mon._regCodes[:] = savedCodes
            mon._regItems.clear()
            mon._regItems.update(savedItems)
            mon._reregN.clear()
            mon._reregN.update(savedN)
            mon._pollChanged.clear()
            mon._pollChanged.update(savedChanged)
            mon._regAt.clear()
            mon._regAt.update(savedAt)
            mon._lastPx.pop("300120", None)

    def test_poll_after_cycle_start_stays_fresh_and_stop_uses_it(self):
        from datetime import datetime

        import trading.pbk01Live as live
        import trading.watchRtMonitor as mon
        from trading.pbkOrderPlan import buildLiveActions
        from trading.pbkQuotePlan import holdPollForce, quoteAgeSec, quoteClockAfterPoll, quoteIsFresh

        start = datetime(2026, 11, 24, 9, 30, 50)
        polled = {"src": "poll", "curPrc": 9580, "tm": "093051", "tickYmd": "20261124"}
        self.assertEqual(quoteAgeSec(polled, start), 0)
        self.assertTrue(quoteIsFresh(polled, start))
        later = datetime(2026, 11, 24, 9, 30, 52)
        self.assertEqual(quoteClockAfterPoll(start, later), later)
        self.assertEqual(
            quoteClockAfterPoll(datetime(2026, 11, 23, 10, 0, 0), later),
            datetime(2026, 11, 23, 10, 0, 0),
        )
        late = {"src": "poll", "curPrc": 9580, "tm": "153000", "tickYmd": "20261124"}
        self.assertGreater(quoteAgeSec(late, start), 120)
        self.assertFalse(quoteIsFresh(late, start))
        saved = dict(mon._lastPx)
        try:
            mon._lastPx["005930"] = dict(polled)
            got = live.quotesFromCache(["005930"], now=datetime(2026, 11, 24, 9, 30, 52))
            self.assertTrue(got["005930"]["fresh"])
        finally:
            mon._lastPx.clear()
            mon._lastPx.update(saved)
        captured = {}
        after = datetime(2026, 11, 24, 9, 30, 52)

        def _cache(codes, now=None):
            captured["now"] = now
            return {"005930": {**polled, "price": 9580, "fresh": True}}

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            savedScan = live._scanYmd
            live._scanYmd = "20261124"
            try:
                with patch.object(live, "_regularSession", return_value=True), patch.object(
                    live, "refreshPbkQuotes", return_value={"plan": {"subscribe": []}, "stale": []},
                ), patch.object(live, "quotesFromCache", side_effect=_cache), patch(
                    "trading.pbkQuotePlan.quoteClockAfterPoll", return_value=after,
                ):
                    live.runPbkCycle(now=start, path=path, enabled=True, log=lambda _t: None)
            finally:
                live._scanYmd = savedScan
        self.assertEqual(captured.get("now"), after)
        stopAt = datetime(2026, 11, 24, 15, 14, 50)
        self.assertTrue(holdPollForce(stopAt))
        freshPoll = {"src": "poll", "curPrc": 7900, "price": 7900, "tm": "151451", "tickYmd": "20261124", "fresh": True}
        self.assertTrue(quoteIsFresh(freshPoll, stopAt))
        pos = [{"stkCd": "041830", "qty": 10, "avg": 10000, "leg1": {"qty": 10, "fillPx": 10000}}]
        stopped = buildLiveActions([], {"041830": freshPoll}, pos, "151500", "20261124", now=stopAt)
        self.assertEqual(stopped["actions"][0]["reason"], "손절")
        self.assertEqual(stopped["actions"][0]["fillPx"], 7900)

    def test_fid12_beats_dirty_prev_close(self):
        from trading.pbkQuotePlan import holdChangePct

        dirty = {
            "src": "0B",
            "curPrc": 9580,
            "krxPrc": 9580,
            "krxTm": "093000",
            "krxYmd": "20261125",
            "krxFlu": "-3.68",
            "fluRt": "-3.68",
            "tickYmd": "20261125",
            "tm": "093000",
            "prevClose": 8710,
        }
        flu = holdChangePct(9580, dirty, "20261125", usedKrx=True)
        self.assertAlmostEqual(flu, -3.68, places=2)
        prc, shown = selectHoldQuote(dirty, "20261125", inKrx=True)
        self.assertEqual(prc, 9580)
        self.assertAlmostEqual(shown, -3.68, places=2)

    def test_restore_drops_prev_close_and_new_day_clears_it(self):
        import trading.watchRtMonitor as mon

        savedFlag = mon._pxCacheLoaded
        savedPx = dict(mon._lastPx)
        savedCodes = list(mon._regCodes)
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "watchLastPx.json"
                path.write_text(json.dumps({
                    "quotes": {
                        "900290": {
                            "curPrc": "9580",
                            "src": "0B",
                            "tm": "093000",
                            "tickYmd": "20261124",
                            "krxPrc": "9580",
                            "krxFlu": "-3.68",
                            "krxYmd": "20261124",
                            "krxTm": "093000",
                            "prevClose": 8710,
                        },
                    },
                }), encoding="utf-8")
                mon._pxCacheLoaded = False
                mon._lastPx.clear()
                with patch.object(mon, "_PX_CACHE_PATH", path):
                    mon._loadLastPxCache()
                restored = mon._lastPx["900290"]
                self.assertNotIn("prevClose", restored)
                self.assertAlmostEqual(
                    selectHoldQuote(restored, "20261124", inKrx=True)[1],
                    -3.68,
                    places=2,
                )
            mon._lastPx.clear()
            mon._regCodes[:] = ["900290"]
            mon._lastPx["900290"] = {
                "curPrc": "10000",
                "src": "0B",
                "tm": "153000",
                "tickYmd": "20261124",
                "krxPrc": "10000",
                "krxFlu": "+9.96",
                "krxYmd": "20261124",
                "krxTm": "153000",
                "prevClose": 8710,
                "prevCloseYmd": "20261124",
            }
            with patch.object(mon, "_flushLastPxCache", lambda *_a, **_k: None), patch(
                "trading.maTouchLive.onMaTouchTick", lambda *_a, **_k: None,
            ), patch("trading.watchRtMonitor.datetime") as clock:
                clock.now.return_value.strftime.return_value = "20261125"
                mon._onWsMessage({"trnm": "REAL", "data": [{
                    "type": "0B",
                    "item": "900290",
                    "values": {"10": "9632", "12": "-3.68", "20": "090100"},
                }]})
            self.assertNotIn("prevClose", mon._lastPx["900290"])
            self.assertEqual(mon._lastPx["900290"]["krxFlu"], "-3.68")
            self.assertEqual(mon._lastPx["900290"]["tickYmd"], "20261125")
        finally:
            mon._pxCacheLoaded = savedFlag
            mon._lastPx.clear()
            mon._lastPx.update(savedPx)
            mon._regCodes[:] = savedCodes

    def test_prior_krx_seed_yields_to_today_nxt(self):
        seeded = {
            "src": "0B",
            "curPrc": 32300,
            "tm": "153000",
            "tickYmd": "20261125",
            "krxPrc": 32300,
            "krxTm": "153000",
            "krxYmd": "20261124",
            "krxFlu": "+5.73",
            "nxtPrc": 30800,
            "nxtTm": "100500",
            "nxtFlu": "-4.64",
        }
        prc, flu = selectHoldQuote(seeded, "20261125", inKrx=True)
        self.assertEqual(prc, 30800)
        self.assertAlmostEqual(flu, -4.64, places=2)
        from trading.pbkQuotePlan import stopInputPx

        self.assertIsNone(stopInputPx(seeded, "20261125"))

    def test_stop_catchup_after_1515_and_long_scan_clock(self):
        from datetime import datetime

        from trading.pbkOrderPlan import buildLiveActions, dueStopCheck
        from trading.pbkQuotePlan import quoteClockAfterPoll, quoteIsFresh

        self.assertFalse(dueStopCheck("151434", "20261125", ""))
        self.assertTrue(dueStopCheck("151531", "20261125", ""))
        self.assertTrue(dueStopCheck("151601", "20261125", ""))
        self.assertFalse(dueStopCheck("151601", "20261125", "20261125"))
        self.assertFalse(dueStopCheck("153101", "20261125", ""))
        pos = [{"stkCd": "041830", "qty": 10, "avg": 10000, "leg1": {"qty": 10, "fillPx": 10000}}]
        quote = {
            "price": 7900, "curPrc": 7900, "ask": 7900, "fresh": True,
            "src": "poll", "tickYmd": "20261125", "tm": "151601",
        }
        missed = buildLiveActions([], {"041830": quote}, pos, "151601", "20261125")
        self.assertEqual(missed["actions"], [])
        caught = buildLiveActions([], {"041830": quote}, pos, "151601", "20261125", stopCheck=True)
        self.assertEqual(caught["actions"][0]["reason"], "손절")
        self.assertEqual(caught["stopStats"]["stops"], 1)
        start = datetime(2026, 11, 25, 9, 0, 0)
        later = datetime(2026, 11, 25, 9, 3, 1)
        self.assertEqual(quoteClockAfterPoll(start, later), later)
        aged = {"src": "poll", "curPrc": 1, "tm": "090000", "tickYmd": "20261125"}
        self.assertFalse(quoteIsFresh(aged, later))
        from tools.checkSameDayReuse import kt00010Body

        body = kt00010Body()
        self.assertEqual(body["stk_cd"], "069500")
        self.assertEqual(body["trde_tp"], "2")
        self.assertTrue(body["uv"])
        self.assertNotEqual(body["stk_cd"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
