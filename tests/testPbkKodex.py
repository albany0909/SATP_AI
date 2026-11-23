"""PBK01 KODEX 병행. 실주문·실DB는 쓰지 않는다."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.impl.pullbackTwoStep import floorQty, legMoney
from trading.pbkKodexPark import (
    describePark,
    kodexReturn,
    pauseNewEntries,
    planParkOrders,
    regimeToday,
)
from trading.pbkOrderPlan import buildLiveActions


def _months(closes, year=2025):
    rows = []
    for i, close in enumerate(closes, start=1):
        month = f"{year}{i:02d}"
        rows.append({"tradeDt": f"{month}28", "close": close, "open": close})
    return rows


def _days(start, closes):
    rows = []
    day = start
    for close in closes:
        rows.append({"tradeDt": f"{day:08d}", "open": close, "close": close})
        day += 1
    return rows


def _fresh(price):
    return {"price": price, "low": price, "ask": price, "fresh": True, "src": "0B", "tickYmd": "20261120", "tm": "100000"}


class PbkKodexParkTest(unittest.TestCase):
    def test_regime_pause_and_kodex_trades(self):
        bear = _months([100] * 10)
        self.assertEqual(regimeToday(bear, "20251115"), "하락")
        bull = _months([100] * 9 + [200])
        self.assertEqual(regimeToday(bull, "20251115"), "상승")
        closes = [100] * 60 + [103]
        bars = _days(20250101, closes)
        ret = kodexReturn(bars, "20250315")
        self.assertAlmostEqual(ret, 0.03, places=6)
        self.assertTrue(pauseNewEntries(ret, "상승"))
        self.assertFalse(pauseNewEntries(ret, "하락"))
        self.assertFalse(pauseNewEntries(0.029, "상승"))

        first = planParkOrders({}, "상승", 1_000_000, 10000, 10000)
        self.assertEqual(first["orders"][0]["reason"], "최초KODEX")
        self.assertEqual(first["orders"][0]["when"], "시가")
        self.assertEqual(first["orders"][0]["method"], "시작일 시가")
        self.assertGreater(first["park"]["kodexQty"], 0)

        cash_out = planParkOrders(
            {"started": True, "regime": "상승", "kodexQty": 5},
            "하락", 0, 10000, 10000,
        )
        self.assertEqual(cash_out["orders"][0]["side"], "SELL")
        self.assertEqual(cash_out["orders"][0]["qty"], 5)
        self.assertEqual(cash_out["park"]["kodexQty"], 0)
        self.assertEqual(cash_out["orders"][0]["method"], "전환 첫 거래일 시가")

        funded = planParkOrders(
            {"started": True, "regime": "상승", "kodexQty": 10},
            "상승", 20_000, 10_000, 10_000,
            pbkBuys=[{"spend": 50_000}],
        )
        sold = [row for row in funded["orders"] if row["reason"] == "PBK매수자금"]
        self.assertEqual(len(sold), 1)
        self.assertEqual(sold[0]["method"], "지정가체결직후 KODEX 시가매도")
        self.assertLess(funded["park"]["kodexQty"], 10)

        rebuy = planParkOrders(
            {"started": True, "regime": "상승", "kodexQty": 0},
            "상승", 0, 10_000, 10_000,
            pbkSells=[{"proceeds": 100_000}],
        )
        self.assertEqual(rebuy["orders"][0]["method"], "매도대금 종가 재매수")
        self.assertEqual(rebuy["orders"][0]["when"], "종가")
        quiet = planParkOrders(
            {"started": True, "regime": "하락", "kodexQty": 0},
            "하락", 0, 10_000, 10_000,
            pbkSells=[{"proceeds": 100_000}],
        )
        self.assertEqual(quiet["orders"], [])

        text = describePark(bars, bull, "20251115", park={"kodexQty": 3}, markPx=10000)
        self.assertIn("REG01 상승", text)
        self.assertIn("쉬기", text)
        self.assertIn("3주", text)
        self.assertIn("30,000원", text)

    def test_pause_skips_new_leg_and_room_caps_boost(self):
        held = [{
            "stkCd": "300080", "qty": 10, "brokerQty": 10, "slotBoost": 1.5,
            "leg1": {"qty": 10, "filledQty": 10, "settled": True, "remainQty": 0},
        }]
        rows = [
            {"stkCd": "300120", "market": "코스닥", "ma5": 90, "ma20": 100},
            {"stkCd": "300080", "market": "코스닥", "ma5": 200, "ma20": 100},
        ]
        paused = buildLiveActions(
            rows,
            {"300120": _fresh(80), "300080": _fresh(90)},
            held,
            "100000",
            "20261123",
            pauseNew=True,
            blockedNew=["111111"],
        )
        codes = [row["stkCd"] for row in paused["actions"] if row["side"] == "BUY"]
        self.assertNotIn("300120", codes)
        self.assertIn("300080", codes)
        self.assertIn("300120", paused["paused"])
        self.assertEqual(
            [row["qty"] for row in paused["actions"] if row["leg"] == 2],
            [floorQty(legMoney(9_000_000, 40), paused["actions"][0]["limitPx"])],
        )
        capped = buildLiveActions(
            [{"stkCd": "300120", "market": "코스닥", "ma5": 90, "ma20": 100}],
            {"300120": _fresh(80)},
            [],
            "100000",
            "20261123",
            parkRoom=7_000_000,
        )
        buy = [row for row in capped["actions"] if row["leg"] == 1][0]
        full = floorQty(legMoney(9_000_000, 60), buy["limitPx"])
        self.assertLess(buy["qty"], full)
        self.assertGreater(buy["qty"], 0)

    def test_intraday_restart_buys_kodex_once_at_ask(self):
        from trading.pbkKodexPark import planParkOrders, sharesForCash

        first = planParkOrders(
            {}, "상승", 1_000_000, None, None,
            today="20261123", afterOpen=True, askPx=10000, reserved=400_000,
        )
        orders = first["orders"]
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["method"], "KODEX 장중기동 현재가 최초매수")
        self.assertEqual(orders[0]["limitPx"], 10000)
        self.assertEqual(orders[0]["qty"], sharesForCash(600_000, 10000, buy=True))
        self.assertLess(orders[0]["qty"], sharesForCash(1_000_000, 10000, buy=True))
        again = planParkOrders(
            first["park"], "상승", 1_000_000, None, None,
            today="20261123", afterOpen=True, askPx=10000, reserved=400_000,
        )
        self.assertEqual(again["orders"], [])
        bear = planParkOrders(
            {}, "하락", 1_000_000, None, None,
            today="20261123", afterOpen=True, askPx=10000,
        )
        self.assertEqual(bear["orders"], [])
        self.assertIn("하락장", bear["park"].get("bootLog") or "")
        pending = planParkOrders(
            {}, "상승", 1_000_000, None, None,
            today="20261123", afterOpen=True, askPx=10000, pending=True,
        )
        self.assertEqual(pending["orders"], [])


if __name__ == "__main__":
    unittest.main()
