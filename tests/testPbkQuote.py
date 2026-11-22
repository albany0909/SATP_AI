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
        self.assertEqual([part["refresh"] for part in added], ["0", "0"])
        self.assertEqual(len(added[0]["items"]), 40)
        self.assertEqual(len(added[1]["items"]), 5)
        replaced = regChunks(items, 40, replace=True)
        self.assertEqual([part["refresh"] for part in replaced], ["1", "0"])
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
                self.assertEqual(calls[0][1], "0")
                calls.clear()
                mon._regWatch(_Ws(), [f"{i:06d}" for i in range(45)], "재등록")
                flags = [row[1] for row in calls if row[0] != "REMOVE"]
                self.assertEqual(flags, ["1", "0"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
