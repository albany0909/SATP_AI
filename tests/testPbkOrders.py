"""PBK01 모의 주문 계획. 실주문·실전 호스트는 부르지 않는다."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.impl.pullbackTwoStep import PaperOnlyError, floorQty, legMoney, submitPbkOrder
from trading.pbkOrderPlan import (
    absPx,
    applyAccountQty,
    buildLiveActions,
    inBuySession,
    noteBrokerFill,
    paperHoldRows,
    parseOpenOrders,
    pbkChaseMaxPct,
    reconcilePositions,
    unfilledRemainder,
    visibleHoldRows,
)


def _fresh(price, low=None, openPx=None):
    return {
        "price": price,
        "low": low,
        "open": openPx,
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
            cand, {"900290": _fresh(10300, low=9900)}, [], "100000", "20261120", chaseMaxPct=2,
        )
        self.assertEqual([row for row in far["actions"] if row["side"] == "BUY"], [])
        self.assertEqual(far["missed"][0]["reason"], "당일 터치 놓침")
        self.assertGreater(far["missed"][0]["gapPct"], 2)

    def test_low_rebound_is_miss_not_backfill(self):
        cand = [{"stkCd": "489790", "stkNm": "한화비전", "ma5": 10000, "ma20": 8000}]
        got = buildLiveActions(
            cand,
            {"489790": _fresh(11000, low=9900)},
            [],
            "100500",
            "20261120",
            chaseMaxPct=2,
        )
        self.assertEqual(got["actions"], [])
        self.assertEqual(got["missed"][0]["basis"], "저가")
        self.assertEqual(got["missed"][0]["nowPx"], 11000)
        self.assertNotIn("fillPx", got["missed"][0])

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
        self.assertEqual(pbkChaseMaxPct({}), 2.0)
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "settings.json"
            live.write_text(json.dumps({"deskTradeMode": "paper", "keep": 1}), encoding="utf-8")
            import config.settingsStore as store

            with patch.object(store, "_SETTINGS_PATH", live), patch.object(
                store, "_DEFAULT_PATH", Path(folder) / "nope.json",
            ):
                self.assertEqual(pbkChaseMaxPct(store.loadSettings()), 2.0)
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["keep"], 1)
            from trading.pbk01Live import placePaperCycle

            sent = []
            result = {
                "enabled": True,
                "ymd": "20261120",
                "hm": "100000",
                "candidates": [{"stkCd": "900290", "stkNm": "GRT", "ma5": 10000, "ma20": 9000}],
                "positions": [],
                "quotes": {"900290": _fresh(11000, low=9900)},
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
            self.assertEqual(row["reason"], "당일 터치 놓침")

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
        self.assertEqual(sum(int(row["purNum"]) for row in rows), 927 * 2730 + 70000 + 10 * 1000)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
