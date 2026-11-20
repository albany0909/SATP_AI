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
from trading.pbkOrderPlan import absPx, buildLiveActions, inBuySession, pbkChaseMaxPct


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
