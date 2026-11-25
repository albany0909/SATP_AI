"""PBK01 v7 한도·슬롯·KODEX 단계. 주문과 DB 는 열지 않는다."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.pbkCap70 import (
    admitNew,
    advanceStep,
    capLine,
    equityFromBody,
    fundSellEnabled,
    headroom,
    maFlags,
    planKodexOrders,
    reservedWon,
    restoreLevel,
    settleCapDay,
    shadowV6,
    skipLine,
)
from trading.pbkOrderPlan import buildLiveActions


def _fresh(price):
    return {
        "price": price,
        "low": price,
        "ask": price,
        "fresh": True,
        "src": "0B",
        "tickYmd": "20261125",
        "tm": "100000",
    }


class PbkCap70Test(unittest.TestCase):
    def test_equity_uses_d2_and_refuses_zero(self):
        body = {
            "d2_entra": "00000020000000",
            "prsm_dpst_aset_amt": "00000049250000",
            "ord_alow_amt": "00000008000000",
            "stk_acnt_evlt_prst": [
                {"stk_cd": "300120", "rmnd_qty": "10", "evlt_amt": "20000000", "cur_prc": "2000000"},
                {"stk_cd": "069500", "rmnd_qty": "100", "evlt_amt": "9250000", "cur_prc": "92500"},
            ],
        }
        got = equityFromBody(body)
        self.assertTrue(got["ok"])
        self.assertEqual(got["E"], 20_000_000 + 20_000_000 + 9_250_000)
        self.assertEqual(got["stkExpo"], 20_000_000)
        self.assertEqual(got["kodexVal"], 9_250_000)
        self.assertEqual(got["orderable"], 8_000_000)
        self.assertFalse(equityFromBody({})["ok"])
        self.assertFalse(equityFromBody({"d2_entra": "0", "tot_est_amt": "0"})["ok"])
        self.assertEqual(headroom(49_250_000, 20_000_000), int(49_250_000 * 0.7) - 20_000_000)

    def test_new_slot_rules_and_fund_switch(self):
        full = admitNew(6, 49_250_000, 10_000_000, 1, 20_000_000, n=4, code="111111")
        self.assertEqual(full["skip"], "MAX_SLOT")
        tight = admitNew(3, 10_000_000, 2_000_000, 1.5, 9_000_000, n=2, code="300120")
        self.assertEqual(tight["skip"], "CAP_FULL(5000000,9000000)")
        self.assertIn("H=", skipLine(tight))
        roomy = admitNew(5, 49_250_000, 10_000_000, 1, 3_000_000, kodexQty=400, kodexPx=10000, n=6, code="900290")
        self.assertTrue(roomy["take"])
        self.assertEqual(roomy["budget"], 6_000_000)
        self.assertTrue(roomy["slot6"])
        self.assertGreater(roomy["fundSellQty"], 0)
        off = admitNew(5, 49_250_000, 10_000_000, 1, 3_000_000, kodexQty=100, kodexPx=10000, fundSell=False, code="900290")
        self.assertEqual(off["skip"], "NO_FUND")
        self.assertTrue(fundSellEnabled({}))
        self.assertFalse(fundSellEnabled({"pbkFundSellKodex": False}))
        blocked = reservedWon(
            [{"stkCd": "300120", "amtOk": False, "leg1": {"qty": 1}, "slotBoost": 1}],
            ["300120"],
        )
        self.assertEqual(blocked, 0)

    def test_kodex_steps_and_no_cap_only_sell(self):
        self.assertEqual(maFlags([1] * 19 + [2])[0], True)
        self.assertFalse(maFlags([1] * 19 + [2])[1])
        state, logs = advanceStep({"regime": "하락", "L": 0, "cnt": 0}, "상승", True, False, "20261125")
        self.assertEqual(state["L"], 1)
        self.assertEqual(state["cnt"], 0)
        self.assertEqual(logs[0]["kind"], "KODEX_STEP_UP")
        cur = dict(state)
        for day in range(1, 5):
            cur, _logs = advanceStep(cur, "상승", True, False, f"2026112{day}")
        self.assertEqual(cur["cnt"], 4)
        cur, up = advanceStep(cur, "상승", True, False, "20261130")
        self.assertEqual(cur["L"], 2)
        self.assertEqual(cur["cnt"], 0)
        self.assertEqual(up[0]["kind"], "KODEX_STEP_UP")
        weak, down = advanceStep({**cur, "cnt": 4, "stepYmd": ""}, "상승", False, True, "20261207")
        self.assertEqual(weak["L"], 1)
        self.assertEqual(down[0]["reason"], "WEAK")
        bear, sold = advanceStep(weak, "하락", False, True, "20261208")
        self.assertEqual(bear["L"], 0)
        self.assertEqual(sold[0]["kind"], "KODEX_STEP_DOWN")
        level, ok = restoreLevel(2_000_000, 3_000_000)
        self.assertTrue(ok)
        self.assertEqual(level, 2)
        self.assertEqual(restoreLevel(0, 0), (0, False))
        buy, target = planKodexOrders(2, 2, 3_000_000, 0, 10000, 1_500_000, bear=False)
        self.assertEqual(target, 2_000_000)
        self.assertEqual(buy[0]["side"], "BUY")
        hold, _same = planKodexOrders(2, 2, 1_000_000, 100, 10000, 0, bear=False)
        self.assertEqual(hold, [])
        down, _zero = planKodexOrders(0, 2, 3_000_000, 10, 10000, 0, bear=True, levelFell=True)
        self.assertEqual(down[0]["side"], "SELL")
        sameDay = settleCapDay(
            {"cap70": {"ok": True, "ymd": "20261125", "E": 49_250_000, "stkExpo": 10_000_000, "kodexVal": 0, "orderable": 8_000_000, "cashD2": 8_000_000, "L": 1}},
            "20261125",
            "103000",
            {"d2_entra": "1"},
            "상승",
            [100] * 25,
            0,
            todayExpo=6_000_000,
        )
        self.assertEqual(sameDay["cap70"]["E"], 49_250_000)
        self.assertEqual(sameDay["cap70"]["stkExpo"], 16_000_000)
        line = capLine(sameDay["cap70"])
        self.assertTrue(line.startswith("CAP70|20261125|49250000|"))
        self.assertIn("orders=0", shadowV6(["111111", "222222"], "상승", 1_000_000, 10000, ["111111"]))

    def test_gate_blocks_sixth_only_after_five_and_live_tag(self):
        snap = {
            "ok": True,
            "E": 49_250_000,
            "stkExpo": 0,
            "reserved": 0,
            "kodexVal": 0,
            "orderable": 40_000_000,
            "fundSell": True,
            "L": 0,
        }
        from trading.pbkCap70 import makeCapGate

        gate = makeCapGate(snap, slots=5)
        rows = []
        for i in range(2):
            rows.append({"stkCd": f"{i+1:06d}", "ma5": 90, "ma20": 100})
        quotes = {row["stkCd"]: _fresh(80) for row in rows}
        plan = buildLiveActions(rows, quotes, [], "100000", "20261125", slotLimit=6, capGate=gate)
        buys = [row for row in plan["actions"] if row["side"] == "BUY"]
        self.assertEqual({row["stkCd"] for row in buys}, {"000001"})
        self.assertEqual(buys[0]["slot6"], 1)
        self.assertTrue(any(str(row["reason"]).startswith("MAX_SLOT") for row in plan["skips"]))


if __name__ == "__main__":
    unittest.main()
