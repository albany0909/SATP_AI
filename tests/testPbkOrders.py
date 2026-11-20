"""PBK01 모의 주문 계획. 실주문·실전 호스트는 부르지 않는다."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.liveGuard  # noqa: E402 운영 로그·DB·키움 차단

from strategies.impl.pullbackTwoStep import PaperOnlyError, floorQty, legMoney, submitPbkOrder
from trading.pbkOrderPlan import (
    absPx,
    applyAccountQty,
    buildLiveActions,
    displayOpenQty,
    fillHoldNames,
    inBuySession,
    noteBrokerFill,
    paperHoldRows,
    parseOpenOrders,
    pbkChaseMaxPct,
    reconcilePositions,
    unfilledRemainder,
    visibleHoldRows,
)


def _fresh(price, low=None, openPx=None, ask=None):
    return {
        "price": price,
        "low": low,
        "open": openPx,
        "ask": price if ask is None else ask,
        "fresh": True,
        "src": "0B",
        "tickYmd": "20261120",
        "tm": "100000",
    }


class PbkOrderPlanTest(unittest.TestCase):
    def test_signed_price_and_chase_band(self):
        self.assertEqual(absPx("-77000"), 77000.0)
        self.assertTrue(inBuySession("090000"))
        self.assertTrue(inBuySession("151459"))
        self.assertFalse(inBuySession("151500"))
        self.assertFalse(inBuySession("080000"))
        cand = [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}]
        near = buildLiveActions(
            cand, {"900290": _fresh(10100, low=9900)}, [], "100000", "20261120", chaseMaxPct=2,
        )
        buy = [row for row in near["actions"] if row["side"] == "BUY"]
        self.assertEqual(len(buy), 1)
        self.assertEqual(buy[0]["leg"], 1)
        self.assertEqual(buy[0]["fillPx"], 10100)
        self.assertEqual(buy[0]["qty"], floorQty(legMoney(6_000_000, 60), 10100))
        self.assertEqual(near["missed"], [])
        far = buildLiveActions(
            cand, {"900290": _fresh(10300, low=9900)}, [], "100000", "20261120",
        )
        capped = [row for row in far["actions"] if row["side"] == "BUY"]
        self.assertEqual(len(capped), 1)
        self.assertEqual(capped[0]["limitPx"], 10000)
        self.assertLessEqual(capped[0]["limitPx"], 10000)
        self.assertEqual(far["missed"], [])

    def test_low_rebound_is_miss_not_backfill(self):
        cand = [{"stkCd": "489790", "stkNm": "한화비전", "ma5": 10000, "ma20": 8000}]
        got = buildLiveActions(
            cand,
            {"489790": _fresh(11000, low=9900)},
            [],
            "100500",
            "20261120",
        )
        buy = [row for row in got["actions"] if row["side"] == "BUY"]
        self.assertEqual(len(buy), 1)
        self.assertEqual(buy[0]["limitPx"], 10000)
        self.assertNotEqual(buy[0]["limitPx"], 11000)
        self.assertEqual(buy[0]["fillPx"], 11000)

    def test_stale_quote_does_not_buy(self):
        cand = [{"stkCd": "322000", "ma5": 10000, "ma20": 9000}]
        stale = _fresh(9000)
        stale["fresh"] = False
        stale["tickYmd"] = "20261119"
        stale["tm"] = "195900"
        got = buildLiveActions(cand, {"322000": stale}, [], "100000", "20261120", chaseMaxPct=2)
        self.assertEqual(got["actions"], [])
        self.assertEqual(got["missed"], [])
        self.assertEqual(got["skips"][0]["reason"], "시세 대기")

    def test_leg_once_slot_and_restart(self):
        cand = [
            {"stkCd": "041830", "ma5": 100, "ma20": 80, "signalDt": "20261101"},
            {"stkCd": "000001", "ma5": 100, "ma20": 80, "signalDt": "20261102"},
        ]
        held = [{"stkCd": "041830", "qty": 10, "leg1": {"qty": 10, "fillPx": 100}, "leg2": None}]
        again = buildLiveActions(
            cand[:1],
            {"041830": _fresh(70)},
            held,
            "110000",
            "20261120",
        )
        buys = [row for row in again["actions"] if row["side"] == "BUY"]
        self.assertEqual([row["leg"] for row in buys], [2])
        pending = [{"stkCd": "041830", "pending": {"2": {"ordNo": "1"}}, "leg1": {"qty": 1, "fillPx": 100}, "qty": 1}]
        blocked = buildLiveActions(cand[:1], {"041830": _fresh(70)}, pending, "110000", "20261120")
        self.assertEqual([row for row in blocked["actions"] if row["side"] == "BUY"], [])
        full = [{"stkCd": f"{i:06d}", "qty": 1, "leg1": {"qty": 1, "fillPx": 1}} for i in range(5)]
        sixth = buildLiveActions(
            [{"stkCd": "999999", "ma5": 10, "ma20": 9}],
            {"999999": _fresh(9)},
            full,
            "110000",
            "20261120",
        )
        self.assertEqual(sixth["actions"], [])
        self.assertIn("슬롯한도", [row["reason"] for row in sixth["skips"]])

    def test_sells_and_real_guard(self):
        pos = [{
            "stkCd": "041830",
            "qty": 10,
            "avg": 10000,
            "exitDt": "20261120",
            "leg1": {"qty": 10, "fillPx": 10000},
        }]
        sold = buildLiveActions([], {"041830": _fresh(10100, openPx=9900)}, pos, "090500", "20261120")
        self.assertEqual(sold["actions"][0]["reason"], "f+10")
        self.assertEqual(sold["actions"][0]["side"], "SELL")
        stopped = buildLiveActions([], {"041830": _fresh(7900)}, pos, "151500", "20261120")
        self.assertEqual(stopped["actions"][0]["reason"], "손절")
        called = []

        def _boom(*_a, **_k):
            called.append(1)
            raise AssertionError("real order")

        with patch("api.buyStk.placeBuyOrd", _boom), patch("api.sellStk.placeSellOrd", _boom), patch(
            "config.config.paper_host_url", "https://mockapi.kiwoom.com",
        ), patch("config.config.real_host_url", "https://api.kiwoom.com"):
            with self.assertRaises(PaperOnlyError):
                submitPbkOrder(
                    {"side": "BUY", "qty": 1, "trdeTp": "0", "limitPx": 100},
                    {"mode": "paper", "host": "https://api.kiwoom.com"},
                    lambda *_a, **_k: {"ok": True},
                    paperOnly=True,
                )
        self.assertEqual(called, [])

    def test_chase_setting_is_not_written_and_miss_file(self):
        self.assertEqual(pbkChaseMaxPct({}), 0.0)
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "settings.json"
            live.write_text(json.dumps({"deskTradeMode": "paper", "keep": 1}), encoding="utf-8")
            import config.settingsStore as store

            with patch.object(store, "_SETTINGS_PATH", live), patch.object(
                store, "_DEFAULT_PATH", Path(folder) / "nope.json",
            ):
                self.assertEqual(pbkChaseMaxPct(store.loadSettings()), 0.0)
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["keep"], 1)
            from trading.pbk01Live import placePaperCycle

            sent = []
            result = {
                "enabled": True,
                "ymd": "20261120",
                "hm": "100000",
                "candidates": [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
                "positions": [],
                "quotes": {"900290": {**_fresh(11000, low=9900), "ask": None}},
            }
            with patch("utils.pbk01Store.pbkPath", lambda name: Path(folder) / name), patch(
                "trading.pbk01Live._missedFile",
                lambda ymd: Path(folder) / f"pbk01_missed_{ymd}.jsonl",
            ):
                plan = placePaperCycle(
                    result,
                    broker=lambda *_a, **_k: sent.append(1),
                    session={"mode": "paper", "host": "https://mockapi.kiwoom.com"},
                    chaseMaxPct=2,
                    now=datetime(2026, 11, 20, 10, 0, 0),
                )
            self.assertEqual(sent, [])
            self.assertEqual(plan["positions"], [])
            missed = Path(folder) / "pbk01_missed_20261120.jsonl"
            row = json.loads(missed.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["stkCd"], "900290")
            self.assertEqual(row["lineName"], "5이평")
            self.assertEqual(row["basis"], "저가")
            self.assertEqual(row["reason"], "호가없음")

    def test_broker_fill_is_separate_and_sell_uses_it(self):
        pos = [{
            "stkCd": "900290",
            "stkNm": "GRT",
            "qty": 2000,
            "avg": 2800,
            "leg1": {"qty": 2000, "fillPx": 2800, "ordNo": "0070310"},
            "firstBuyDt": "20261110",
            "exitDt": "20261120",
        }]
        updated, matched = noteBrokerFill(pos, "900290", "매수", 927, 2730, "0070310")
        self.assertTrue(matched)
        self.assertEqual(updated[0]["leg1"]["fillPx"], 2800)
        self.assertEqual(updated[0]["brokerQty"], 927)
        self.assertEqual(updated[0]["brokerAvg"], 2730)
        again, _ok = noteBrokerFill(updated, "900290", "매수", 100, 2740, "0070310")
        self.assertEqual(again[0]["brokerQty"], 1027)
        acct, changed = applyAccountQty(again, [{"stkCd": "900290", "qtyNum": 927, "avgNum": 2730}])
        self.assertTrue(changed)
        self.assertEqual(acct[0]["brokerQty"], 927)
        same, changed2 = applyAccountQty(acct, [{"stkCd": "900290", "qtyNum": 927, "avgNum": 2730}])
        self.assertFalse(changed2)
        self.assertEqual(same[0]["leg1"]["fillPx"], 2800)
        plan = buildLiveActions(
            [],
            {"900290": _fresh(2700, openPx=2710)},
            acct,
            "100000",
            "20261120",
        )
        sells = [row for row in plan["actions"] if row["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["qty"], 927)
        left = unfilledRemainder(acct)
        self.assertEqual(left[0]["leftQty"], 2000 - 927)
        extra = buildLiveActions(
            [{"stkCd": "900290", "ma5": 2800, "ma20": 2600}],
            {"900290": _fresh(2700, low=2500)},
            acct,
            "100000",
            "20261120",
        )
        buys = [row for row in extra["actions"] if row["side"] == "BUY" and row["leg"] == 1]
        self.assertEqual(buys, [])

    def test_paper_holds_include_account_and_pbk(self):
        account = [
            {"stkCd": "900290", "stkNm": "GRT", "qtyNum": 927, "avgNum": 2730, "purNum": 927 * 2730, "algCds": []},
            {"stkCd": "005930", "stkNm": "삼성", "qtyNum": 1, "avgNum": 70000, "purNum": 70000, "algCds": []},
        ]
        pbk = [
            {"stkCd": "900290", "stkNm": "GRT", "qty": 2000, "brokerQty": 927, "avg": 2800},
            {"stkCd": "000001", "stkNm": "상태만", "qty": 10, "avg": 1000},
        ]
        rows = visibleHoldRows("paper", account, pbk, lambda _cd: False)
        by = {row["stkCd"]: row for row in rows}
        self.assertEqual(set(by), {"900290", "005930", "000001"})
        self.assertIn("PBK01", by["900290"]["algCds"])
        self.assertEqual(by["900290"]["qtyNum"], 927)
        self.assertEqual(by["000001"]["algLabel"], "PBK01")
        self.assertEqual(by["000001"]["qtyNum"], 0)
        self.assertEqual(by["000001"]["openText"], "미체결 10주")
        self.assertEqual(by["000001"]["purNum"], 0)
        self.assertEqual(sum(int(row["purNum"]) for row in rows if int(row["qtyNum"]) > 0), 927 * 2730 + 70000)
        real = visibleHoldRows(
            "real",
            account + [{"stkCd": "000660", "qtyNum": 3, "algCds": []}],
            pbk,
            lambda cd: cd == "005930",
        )
        self.assertEqual([row["stkCd"] for row in real], ["005930"])
        self.assertEqual(real[0]["algLabel"], "실계좌")
        self.assertEqual(paperHoldRows(account, [])[0]["stkCd"], "900290")

    def test_eod_unfilled_log_once(self):
        import trading.pbk01Live as live

        live._unfilledLoggedYmd = ""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text(json.dumps({
                "kind": "state",
                "positions": [{
                    "stkCd": "900290",
                    "leg1": {"qty": 2000, "fillPx": 2800},
                    "brokerQty": 927,
                }],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(live.logUnfilledRemainder(path, "152959", "20261120"), [])
            rows = live.logUnfilledRemainder(path, "153000", "20261120")
            self.assertEqual(rows[0]["leftQty"], 1073)
            self.assertEqual(live.logUnfilledRemainder(path, "153100", "20261120"), [])

    def test_restart_old_state_does_not_reorder(self):
        import trading.pbk01Live as live
        from config.config import paper_host_url

        names = [
            ("900290", "GRT", "0070310", 2000, 927, 1073),
            ("300080", "플리토", "0070312", 1500, 1500, 0),
            ("300120", "라온피플", "0070313", 800, 800, 0),
            ("071970", "HD현대마린엔진", "0070314", 400, 200, 200),
            ("094840", "슈프리마", "0070315", 300, 300, 0),
        ]
        positions = []
        for code, name, ordNo, plan, _filled, _left in names:
            positions.append({
                "stkCd": code,
                "stkNm": name,
                "qty": plan,
                "avg": 2800,
                "leg1": {"qty": plan, "fillPx": 2800, "ordNo": ordNo, "ymd": "20261120"},
                "leg2": {"qty": 10, "fillPx": 2800, "ordNo": str(int(ordNo) + 1), "ymd": "20261120"},
                "pending": {},
                "firstBuyDt": "20261110",
                "exitDt": "20261204",
            })
        accountBody = {
            "stk_acnt_evlt_prst": [
                {"stk_cd": code, "stk_nm": name, "rmnd_qty": str(filled), "avg_prc": "2730"}
                for code, name, _ord, _plan, filled, _left in names
                if filled > 0
            ]
        }
        openBody = {
            "oso": [
                {
                    "stk_cd": "A900290",
                    "ord_no": "0070310",
                    "io_tp_nm": "+매수",
                    "ord_qty": "0000000002000",
                    "oso_qty": "0000000001073",
                    "cntr_qty": "0000000000927",
                    "cntr_pric": "000000002730",
                },
                {
                    "stk_cd": "071970",
                    "ord_no": "0070314",
                    "io_tp_nm": "매수",
                    "ord_qty": "400",
                    "oso_qty": "200",
                    "cntr_qty": "200",
                    "cntr_pric": "2730",
                },
            ]
        }
        parsed = parseOpenOrders(openBody)
        self.assertEqual(parsed[0]["stkCd"], "900290")
        self.assertEqual(parsed[0]["rmnQty"], 1073)
        called = []
        blocked = live.fetchPaperBook(
            {"mode": "real", "host": "https://api.kiwoom.com"},
            accountFetch=lambda: called.append("acct"),
            openFetch=lambda: called.append("open"),
        )
        self.assertEqual(called, [])
        self.assertEqual(blocked["reason"], "PAPER_ONLY")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text(json.dumps({
                "kind": "state",
                "announcedYmd": "20261120",
                "candidates": [{"stkCd": code, "stkNm": name, "ma5": 3000, "ma20": 2500} for code, name, *_rest in names]
                + [{"stkCd": "005930", "stkNm": "삼성", "ma5": 70000, "ma20": 60000}],
                "positions": positions,
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            restored = live.bootPaperBook(
                path=path,
                session={"mode": "paper", "host": paper_host_url},
                accountFetch=lambda: {"ok": True, "body": accountBody},
                openFetch=lambda: {"ok": True, "body": openBody},
                now=datetime(2026, 11, 20, 10, 30, 0),
            )
            self.assertTrue(restored["reconciled"])
            self.assertEqual(restored["held"], 5)
            grt = [row for row in restored["positions"] if row["stkCd"] == "900290"][0]
            self.assertEqual(grt["leg1"]["fillPx"], 2800)
            self.assertEqual(grt["leg1"]["ordNo"], "0070310")
            self.assertEqual(grt["leg1"]["rmnQty"], 1073)
            self.assertEqual(grt["brokerQty"], 927)
            self.assertEqual(grt["brokerAvg"], 2730)
            again, matched = noteBrokerFill(
                restored["positions"], "900290", "매수", 10, 2730, "0070310", cumQty=927,
            )
            self.assertTrue(matched)
            self.assertEqual([row for row in again if row["stkCd"] == "900290"][0]["brokerQty"], 927)
            sent = []
            quotes = {code: _fresh(2400, low=2400) for code, *_rest in names}
            quotes["005930"] = _fresh(50000, low=50000)
            plan = live.placePaperCycle(
                {
                    "enabled": True,
                    "ymd": "20261120",
                    "hm": "100800",
                    "positions": [],
                    "quotes": quotes,
                },
                broker=lambda *_a, **_k: sent.append(1),
                path=path,
                requireBroker=True,
                chaseMaxPct=2,
            )
            self.assertEqual(sent, [])
            self.assertEqual([row for row in plan["actions"] if row["side"] == "BUY"], [])
            self.assertTrue(any(row.get("reason") == "슬롯한도" for row in plan["skips"]))
            kept = reconcilePositions(positions, accountKnown=False)
            self.assertEqual(len(kept), 5)
            self.assertEqual(kept[0]["leg1"]["qty"], 2000)

    def test_zero_fill_stays_on_screen_but_out_of_total(self):
        pos = [{
            "stkCd": "300120",
            "stkNm": "300120",
            "qty": 2453,
            "avg": 2445,
            "brokerQty": 0,
            "leg1": {"qty": 2453, "fillPx": 2445, "ordNo": "0070400", "rmnQty": 2453},
        }]
        rows = paperHoldRows([], pos)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["qtyNum"], 0)
        self.assertEqual(row["qty"], "0")
        self.assertEqual(row["avgNum"], 0)
        self.assertEqual(row["purNum"], 0)
        self.assertEqual(row["evltNum"], 0)
        self.assertEqual(row["openText"], "미체결 2453주")
        self.assertEqual(row["algText"], "PBK01")
        self.assertEqual(displayOpenQty(pos[0]), 2453)
        named = fillHoldNames(rows, lookup=lambda codes: {"300120": "라온피플"})
        self.assertEqual(named[0]["stkNm"], "라온피플")
        self.assertEqual(named[0]["algText"], "PBK01")
        filled = paperHoldRows(
            [{"stkCd": "300120", "stkNm": "라온피플", "qtyNum": 100, "avgNum": 2500, "purNum": 250000}],
            [{"stkCd": "300120", "stkNm": "라온피플", "qty": 2453, "brokerQty": 100, "brokerAvg": 2500,
              "leg1": {"qty": 2453, "rmnQty": 2353}}],
        )
        self.assertEqual(filled[0]["qtyNum"], 100)
        self.assertEqual(filled[0]["openText"], "미체결 2353주")
        self.assertIn("PBK01", filled[0]["algText"])

    def test_partial_remainder_waits_for_next_touch(self):
        from trading.pbkOrderPlan import settlePositions

        old = [{
            "stkCd": "900290",
            "stkNm": "GRT",
            "qty": 1316,
            "avg": 2800,
            "brokerQty": 481,
            "brokerAvg": 2730,
            "firstBuyDt": "20261120",
            "exitDt": "20261204",
            "leg1": {"qty": 1316, "fillPx": 2800, "limitPx": 2730, "ordNo": "0070310", "ymd": "20261120"},
        }]
        same, _logs = settlePositions(old, "20261120", includeToday=False)
        self.assertFalse(same[0].get("settledYmd"))
        blocked = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 3000, "ma20": 2000, "expireDt": "20270115"}],
            {"900290": _fresh(2400, low=2400)},
            same,
            "100000",
            "20261120",
        )
        self.assertEqual([row for row in blocked["actions"] if row["side"] == "BUY"], [])
        settled, logs = settlePositions(old, "20261121", includeToday=False)
        self.assertEqual(settled[0]["qty"], 481)
        self.assertEqual(settled[0]["avg"], 2730)
        self.assertEqual(settled[0]["leg1"]["remainQty"], 835)
        self.assertEqual(settled[0]["leg1"]["expiredQty"], 835)
        self.assertEqual(settled[0]["firstBuyDt"], "20261120")
        self.assertEqual(logs[0]["ordNo"], "0070310")
        self.assertEqual(logs[0]["fillPx"], 2800)
        self.assertEqual(logs[0]["brokerQty"], 481)
        self.assertIsNone(logs[0]["ask1"])
        quiet = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 2000, "ma20": 1500, "expireDt": "20270115"}],
            {"900290": _fresh(2500, low=2200)},
            settled,
            "100000",
            "20261121",
        )
        self.assertEqual(quiet["actions"], [])
        again = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 3000, "ma20": 2000, "expireDt": "20270115"}],
            {"900290": _fresh(2900, low=2900)},
            settled,
            "100000",
            "20261121",
        )
        buys = [row for row in again["actions"] if row["side"] == "BUY"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["leg"], 1)
        self.assertEqual(buys[0]["qty"], 835)

    def test_zero_fill_releases_slot_and_does_not_reorder(self):
        from strategies.impl.pullbackTwoStep import candidateRows
        from trading.pbkOrderPlan import settlePositions

        old = [{
            "stkCd": "300120",
            "stkNm": "라온피플",
            "qty": 2453,
            "avg": 2445,
            "brokerQty": 0,
            "firstBuyDt": "20261120",
            "exitDt": "20261204",
            "leg1": {"qty": 2453, "fillPx": 2445, "ordNo": "0070400", "ymd": "20261120"},
        }]
        settled, logs = settlePositions(old, "20261121", includeToday=False)
        self.assertTrue(settled[0]["released"])
        self.assertEqual(settled[0]["qty"], 0)
        self.assertEqual(settled[0]["exitDt"], "")
        self.assertEqual(logs[0]["expiredQty"], 2453)
        self.assertEqual(paperHoldRows([], settled), [])
        events = [{"stkCd": "300120", "signalDt": "20261118", "receiptDt": "20261117"}]
        bars = [{"tradeDt": f"202611{day:02d}", "open": 3000, "high": 4000, "low": 2500, "close": 3500, "volume": 100}
                for day in range(1, 21)]
        rows = candidateRows(events, settled, {"300120": bars}, "20261121", calendar=[f"202611{day:02d}" for day in range(1, 29)])
        self.assertIn("300120", [row["stkCd"] for row in rows])
        noTouch = buildLiveActions(
            [{"stkCd": "300120", "stkNm": "라온피플", "ma5": 2000, "ma20": 1500, "expireDt": "20270115"}],
            {"300120": _fresh(4000, low=3100)},
            settled,
            "100000",
            "20261121",
        )
        self.assertEqual(noTouch["actions"], [])
        touched = buildLiveActions(
            [{"stkCd": "300120", "stkNm": "라온피플", "ma5": 3000, "ma20": 2200, "expireDt": "20270115"}],
            {"300120": _fresh(2900, low=2400)},
            settled,
            "100000",
            "20261121",
        )
        buys = [row for row in touched["actions"] if row["side"] == "BUY"]
        self.assertEqual([row["leg"] for row in buys], [1])
        others = [{
            "stkCd": f"{i:06d}",
            "qty": 1,
            "brokerQty": 1,
            "leg1": {"qty": 1, "fillPx": 10, "ordNo": str(i), "ymd": "20261120", "settled": True, "filledQty": 1, "remainQty": 0},
        } for i in range(1, 5)]
        freed = buildLiveActions(
            [{"stkCd": "300120", "ma5": 3000, "ma20": 2500, "expireDt": "20270115"}],
            {"300120": _fresh(2900, low=2400)},
            others + settled,
            "100000",
            "20261121",
        )
        self.assertTrue(any(row["stkCd"] == "300120" and row["side"] == "BUY" for row in freed["actions"]))

    def test_settle_uses_balance_not_stale_fill(self):
        from trading.pbkOrderPlan import balanceMismatches, settlePositions

        old = [{
            "stkCd": "300080",
            "stkNm": "플리토",
            "qty": 876,
            "brokerQty": 876,
            "brokerAvg": 3200,
            "ordFill": {"1": 171},
            "openOrders": [{"stkCd": "300080", "ordNo": "0000001", "side": "BUY", "rmnQty": 355}],
            "leg1": {"qty": 526, "fillPx": 3300, "ordNo": "0000001", "ymd": "20261120"},
            "leg2": {"qty": 350, "fillPx": 3100, "ordNo": "0000002", "ymd": "20261120"},
        }]
        settled, logs = settlePositions(
            old, "20261121", includeToday=False, orderFills={"1": 526, "2": 350},
        )
        self.assertEqual(settled[0]["leg1"]["filledQty"], 526)
        self.assertEqual(settled[0]["leg1"]["expiredQty"], 0)
        self.assertEqual(settled[0]["leg1"]["remainQty"], 0)
        self.assertEqual(settled[0]["leg2"]["filledQty"], 350)
        self.assertFalse(settled[0].get("buyHold"))
        self.assertEqual(sum(row["brokerQty"] for row in logs), 876)
        self.assertEqual(paperHoldRows([], settled)[0]["openText"], "")
        stale, _logs = settlePositions(old, "20261121", includeToday=False)
        self.assertEqual(stale[0]["leg1"]["filledQty"], 526)
        self.assertEqual(stale[0]["leg1"]["expiredQty"], 0)
        bad = balanceMismatches(old, [{"stkCd": "300080", "qtyNum": 171}])
        self.assertEqual(bad[0]["acctQty"], 171)
        held = buildLiveActions(
            [{"stkCd": "300080", "stkNm": "플리토", "ma5": 4000, "ma20": 3000}],
            {"300080": _fresh(3500, low=3500)},
            [{**old[0], "buyHold": True}],
            "100000",
            "20261121",
        )
        self.assertEqual([row for row in held["actions"] if row["side"] == "BUY"], [])

    def test_missing_ask_does_not_buy(self):
        quote = _fresh(10000, low=9900)
        quote.pop("ask")
        plan = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
            {"900290": quote},
            [],
            "100000",
            "20261120",
        )
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["missed"][0]["reason"], "호가없음")
        capped = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
            {"900290": _fresh(10050, low=9900, ask=10300)},
            [],
            "100000",
            "20261120",
        )
        capBuy = [row for row in capped["actions"] if row["side"] == "BUY"][0]
        self.assertEqual(capBuy["limitPx"], 10000)
        self.assertEqual(capBuy["orderGapPct"], 0)
        self.assertEqual(capBuy["qty"], floorQty(legMoney(6_000_000, 60), 10000))
        gapDown = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
            {"900290": _fresh(9600, low=9500, ask=9700)},
            [],
            "100000",
            "20261120",
        )
        downBuy = [row for row in gapDown["actions"] if row["side"] == "BUY"][0]
        self.assertEqual(downBuy["limitPx"], 9700)
        self.assertLess(downBuy["orderGapPct"], 0)
        near = buildLiveActions(
            [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
            {"900290": _fresh(10050, low=9900, ask=10100)},
            [],
            "100000",
            "20261120",
            chaseMaxPct=2,
        )
        buy = [row for row in near["actions"] if row["side"] == "BUY"][0]
        self.assertEqual(buy["limitPx"], 10100)
        self.assertEqual(buy["trdeTp"], "0")
        self.assertEqual(buy["ask1"], 10100)

    def test_held_name_failing_amount_filter_blocks_adds(self):
        pos = [{
            "stkCd": "300120",
            "stkNm": "라온피플",
            "qty": 672,
            "brokerQty": 672,
            "avg": 2445,
            "leg1": {"qty": 672, "fillPx": 2445, "ordNo": "1", "ymd": "20261120"},
            "exitDt": "20261204",
        }]
        cand = [{"stkCd": "300120", "stkNm": "라온피플", "ma5": 2500, "ma20": 2000, "amtOk": False}]
        blocked = buildLiveActions(
            cand, {"300120": _fresh(1900, low=1800, ask=1900)}, pos, "100000", "20261120",
        )
        self.assertEqual([row for row in blocked["actions"] if row["side"] == "BUY"], [])
        self.assertIn("대금미달", [row["reason"] for row in blocked["skips"]])
        sold = buildLiveActions(
            cand, {"300120": _fresh(1900, low=1800, ask=1900)}, pos, "100000", "20261204",
        )
        self.assertEqual([row["reason"] for row in sold["actions"] if row["side"] == "SELL"], ["f+10"])

    def test_reconcile_script_fixes_fills_and_blocks_reentry(self):
        from tools.pbkReconcileOnce import main

        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "pbk01_state.jsonl"
            fills = Path(folder) / "pbk01_fills_20261120.jsonl"
            state.write_text(json.dumps({
                "kind": "state",
                "positions": [{
                    "stkCd": "300080",
                    "stkNm": "플리토",
                    "qty": 171,
                    "brokerQty": 171,
                    "leg1": {
                        "qty": 526, "filledQty": 171, "remainQty": 355, "expiredQty": 355,
                        "ordNo": "0000001", "ymd": "20261120", "settled": True,
                    },
                }],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            fills.write_text(json.dumps({
                "stkCd": "300080", "stkNm": "플리토", "leg": 1, "legName": "1차",
                "ordNo": "0000001", "ordQty": 526, "brokerQty": 171, "expiredQty": 355,
                "ymd": "20261120",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(main(["--state", str(state), "--set", "300080:1=526"]), 0)
            self.assertIn("171", fills.read_text(encoding="utf-8"))
            self.assertEqual(main(["--state", str(state), "--set", "300080:1=526", "--apply"]), 0)
            fixed = json.loads(fills.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(fixed["brokerQty"], 526)
            self.assertEqual(fixed["expiredQty"], 0)
            from utils.pbk01Store import latestByKind

            pos = latestByKind(state, "state")["positions"][0]
            self.assertEqual(pos["leg1"]["remainQty"], 0)
            self.assertEqual(pos["leg1"]["filledQty"], 526)
            again = buildLiveActions(
                [{"stkCd": "300080", "stkNm": "플리토", "ma5": 4000, "ma20": 3000}],
                {"300080": _fresh(3500, low=3000, ask=3500)},
                [pos],
                "100000",
                "20261121",
            )
            self.assertEqual([row for row in again["actions"] if row["side"] == "BUY" and row["leg"] == 1], [])

    def test_live_log_guard(self):
        from tests.liveGuard import LiveGuardError
        from utils.deskFileLog import appendDeskFileLog

        with self.assertRaises(LiveGuardError):
            appendDeskFileLog("미체결 잔량 900290 2000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
