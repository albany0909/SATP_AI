"""PBK01 v9 한도 누적 수정. stkBase 기준, 2차 예약 이중계산 제거, 실체결 기준 오늘 노출. 주문·DB 는 열지 않는다."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.pbkCap70 import reservedWon, settleCapDay, todayBuys

DAY = "20261126"
BODY = {
    "d2_entra": "26809945",
    "ord_alow_amt": "26809945",
    "stk_acnt_evlt_prst": [{"stk_cd": "900290", "rmnd_qty": "10", "evlt_amt": "21829380", "cur_prc": "1"}],
}
ACCT = {"body": BODY, "holds": BODY["stk_acnt_evlt_prst"]}
E = 26_809_945 + 21_829_380


def _new028050(brokerQty=100, brokerAvg=59_440.0, withBroker=True):
    pos = {
        "stkCd": "028050",
        "qty": 100,
        "leg1": {"ymd": DAY, "qty": 60, "filledQty": 0, "fillPx": 59_900.0, "limitPx": 59_600.0, "settled": False, "ordNo": "0012220"},
        "leg2": {"ymd": DAY, "qty": 40, "filledQty": 0, "fillPx": 59_900.0, "limitPx": 59_700.0, "settled": False, "ordNo": "0012317"},
    }
    if withBroker:
        pos["brokerQty"] = brokerQty
        pos["brokerAvg"] = brokerAvg
    return pos


def _old(code="300120"):
    return {
        "stkCd": code,
        "qty": 672,
        "leg1": {"ymd": "20261120", "qty": 1472, "filledQty": 672, "remainQty": 800, "fillPx": 2445.0, "settled": True},
        "leg2": {"ymd": "20261120", "qty": 981, "filledQty": 0, "remainQty": 981, "fillPx": 2445.0, "settled": True},
    }


class TodayBuysTest(unittest.TestCase):
    def test_actual_fill_basis(self):
        got = todayBuys([_new028050()], DAY)
        self.assertEqual(got["filled"], 100 * 59_440)
        self.assertEqual(got["total"], 100 * 59_440)

    def test_partial_fill_counts_open_part_at_plan_price(self):
        got = todayBuys([_new028050(brokerQty=60, brokerAvg=59_400.0)], DAY)
        self.assertEqual(got["filled"], 60 * 59_400)
        self.assertEqual(got["total"], 60 * 59_400 + 40 * 59_900)

    def test_fallback_to_fill_px_when_broker_unknown(self):
        got = todayBuys([_new028050(withBroker=False)], DAY)
        self.assertEqual(got, {"total": 100 * 59_900, "filled": 0})

    def test_old_positions_and_closed_are_ignored(self):
        closed = _new028050()
        closed["closed"] = True
        self.assertEqual(todayBuys([_old(), closed], DAY), {"total": 0, "filled": 0})

    def test_leg2_today_on_old_position_uses_only_today_qty(self):
        pos = _old()
        pos["leg2"] = {"ymd": DAY, "qty": 100, "filledQty": 0, "fillPx": 2500.0, "limitPx": 2500.0, "settled": False}
        pos["brokerQty"] = 772
        pos["brokerAvg"] = (672 * 2445.0 + 100 * 2500.0) / 772
        got = todayBuys([pos], DAY)
        self.assertAlmostEqual(got["filled"], 250_000, delta=2)
        self.assertEqual(got["total"], got["filled"])


class ReservedTest(unittest.TestCase):
    def test_leg1_only_keeps_reserve(self):
        pos = {"stkCd": "111111", "leg1": {"ymd": DAY, "qty": 60}, "slotBoost": 1}
        self.assertEqual(reservedWon([pos], today=DAY), 2_400_000)

    def test_leg1_and_leg2_today_has_no_reserve(self):
        self.assertEqual(reservedWon([_new028050()], today=DAY), 0)
        # 날짜를 안 주면 예전 동작
        self.assertEqual(reservedWon([_new028050()]), 2_400_000)

    def test_old_unfilled_leg2_still_reserved(self):
        self.assertEqual(reservedWon([_old()], today=DAY), 2_400_000)


class SettleStableTest(unittest.TestCase):
    def _run(self, saved, expo, filled=None, hint=None, hm="100000", acct=ACCT):
        return settleCapDay(saved, DAY, hm, acct, "하락", [100] * 25, 4_800_000, True, 0, todayExpo=expo, todayFilled=filled, baseHint=hint)

    def test_three_calls_do_not_accumulate(self):
        first = self._run({}, 0, 0, hm="085000")
        self.assertEqual(first["cap70"]["stkExpo"], 21_829_380)
        self.assertEqual(first["cap70"]["stkBase"], 21_829_380)
        state = first["state"]
        seen = []
        for _ in range(3):
            got = self._run(state, 5_944_000, 5_944_000, acct={})
            seen.append(got["cap70"]["stkExpo"])
            self.assertEqual(got["cap70"]["stkBase"], 21_829_380)
            self.assertEqual(got["cap70"]["E"], E)
            state = got["state"]
        self.assertEqual(seen, [21_829_380 + 5_944_000] * 3)

    def test_late_first_compute_removes_todays_fills_from_base(self):
        # 재기동으로 오늘 첫 계산이 장중에 되면 계좌 평가에는 오늘 체결이 이미 들어 있다.
        got = self._run({}, 5_944_000, 5_944_000)
        self.assertEqual(got["cap70"]["stkBase"], 21_829_380 - 5_944_000)
        self.assertEqual(got["cap70"]["stkExpo"], 21_829_380)

    def test_legacy_snapshot_without_base_is_not_reused_without_hint(self):
        bloated = {"cap70": {"ok": True, "ymd": DAY, "E": E, "stkExpo": 99_699_380, "kodexVal": 0, "orderable": 1, "cashD2": 1, "L": 0}}
        got = self._run(bloated, 5_944_000, 5_944_000, hm="154000")
        self.assertTrue(got["ok"])
        self.assertNotEqual(got["cap70"]["stkExpo"], 99_699_380 + 5_944_000)
        self.assertEqual(got["cap70"]["stkBase"], 21_829_380 - 5_944_000)
        # 계좌도 없으면 신규를 멈춘다. 부풀린 값을 쓰지 않는다.
        none = self._run(bloated, 5_944_000, 5_944_000, hm="154000", acct={})
        self.assertFalse(none["ok"])

    def test_legacy_snapshot_uses_hint_not_bloated_value(self):
        bloated = {"cap70": {"ok": True, "ymd": DAY, "E": E, "stkExpo": 99_699_380, "kodexVal": 0, "orderable": 1, "cashD2": 1, "L": 0}}
        got = self._run(bloated, 5_944_000, 5_944_000, hint=21_829_380, acct={})
        self.assertEqual(got["cap70"]["stkBase"], 21_829_380)
        self.assertEqual(got["cap70"]["stkExpo"], 21_829_380 + 5_944_000)
        self.assertEqual(got["cap70"]["E"], E)


class RefreshCap70Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "pbk01_state.jsonl"
        self.patch = mock.patch("utils.pbk01Store.pbkPath", lambda name: Path(self.tmp.name) / name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        import trading.pbk01Live as live

        self.live = live
        live._capLoggedYmd = ""
        self.logs = []
        logPatch = mock.patch.object(live, "_safeLog", lambda log, text: self.logs.append(str(text)))
        logPatch.start()
        self.addCleanup(logPatch.stop)

    def _cap(self):
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row["park"]["cap70"] for row in rows if row.get("kind") == "state" and (row.get("park") or {}).get("cap70")]

    def test_repeated_refresh_is_stable(self):
        at = lambda hm: datetime.strptime(DAY + hm, "%Y%m%d%H%M%S")
        got = self.live.refreshCap70(at("085010"), positions=[], regime="하락", account=ACCT)
        self.assertEqual(got["cap70"]["stkExpo"], 21_829_380)
        positions = [_old(), _new028050()]
        seen = []
        for hm in ("090249", "090421", "090604", "090739", "090914"):
            got = self.live.refreshCap70(at(hm), positions=positions, regime="하락", account=ACCT)
            seen.append((got["cap70"]["stkBase"], got["cap70"]["stkExpo"], got["cap70"]["reserved"]))
        self.assertEqual(set(seen), {(21_829_380, 21_829_380 + 5_944_000, 2_400_000)})
        self.assertEqual(sum(1 for line in self.logs if line.startswith("[PBK01] CAP70|")), 1)

    def test_bloated_state_is_rebuilt_from_first_row_of_day(self):
        base = {"ok": True, "ymd": DAY, "E": E, "cap": int(E * 0.7), "stkExpo": 21_829_380, "reserved": 4_800_000, "kodexVal": 0, "room": 1, "L": 0, "cnt": 0, "orderable": 1, "cashD2": 1}
        rows = []
        for stk in (21_829_380, 21_829_380, 27_819_380, 33_809_380, 99_699_380):
            rows.append({"kind": "state", "positions": [_old(), _new028050()], "park": {"cap70": {**base, "stkExpo": stk}}})
        self.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        at = datetime.strptime(DAY + "154500", "%Y%m%d%H%M%S")
        out = []
        for _ in range(3):
            got = self.live.refreshCap70(at, positions=[_old(), _new028050()], regime="하락")
            out.append((got["cap70"]["stkBase"], got["cap70"]["stkExpo"], got["cap70"]["E"]))
        self.assertEqual(set(out), {(21_829_380, 21_829_380 + 5_944_000, E)})
        self.assertEqual(self._cap()[-1]["stkBase"], 21_829_380)
        # 옛 줄은 고치지 않았다.
        first = self.path.read_text(encoding="utf-8").splitlines()[:5]
        self.assertEqual([json.loads(line)["park"]["cap70"]["stkExpo"] for line in first], [21_829_380, 21_829_380, 27_819_380, 33_809_380, 99_699_380])

    def test_bloated_state_without_first_row_recomputes_from_account(self):
        bloated = {"ok": True, "ymd": DAY, "E": 1, "stkExpo": 99_699_380, "reserved": 0, "kodexVal": 0, "L": 0, "cnt": 0}
        self.path.write_text(json.dumps({"kind": "state", "positions": [], "park": {"cap70": bloated}}) + "\n", encoding="utf-8")
        with mock.patch.object(self.live, "_firstCapBase", lambda path, day: None):
            at = datetime.strptime(DAY + "154500", "%Y%m%d%H%M%S")
            got = self.live.refreshCap70(at, positions=[_new028050()], regime="하락", account=ACCT)
        self.assertTrue(got["ok"])
        self.assertEqual(got["cap70"]["stkBase"], 21_829_380 - 5_944_000)
        self.assertEqual(got["cap70"]["stkExpo"], 21_829_380)


if __name__ == "__main__":
    unittest.main()
