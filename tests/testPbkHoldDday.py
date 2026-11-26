"""PBK01 관리종목 구분 칸 D-day. 날짜는 exitDtFromFirst·buySpan 만 쓰고 주문·DB 는 열지 않는다."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.impl.pullbackTwoStep import HOLD_DAYS, buySpan, holidayCalendar
from trading.pbkOrderPlan import exitDtFromFirst
from utils.pbkWatchScreen import holdRoleLabel, holdingExitDay, unionRows


# 2026-11-16(월) ~ 12-11(금), 주말만 빠진 달력. 수요일 휴장(11/25)을 넣기 위한 별도 목록도 둔다.
CAL = [
    "20261116", "20261117", "20261118", "20261119", "20261120",
    "20261123", "20261124", "20261125", "20261126", "20261127",
    "20261130", "20261201", "20261202", "20261203", "20261204",
    "20261207", "20261208", "20261209", "20261210", "20261211",
]
# 같은 기간에서 수요일 20261125를 휴장으로 뺀 달력
CAL_HOL = [d for d in CAL if d != "20261125"]


def _hold(first="20261120", exitDt="", leg2=None, **extra):
    row = {
        "role": "보유",
        "holding": True,
        "firstBuyDt": first,
        "exitDt": exitDt,
        "leg1": {"ymd": first, "qty": 60},
        "stkCd": "028050",
    }
    if leg2 is not None:
        row["leg2"] = leg2
    row.update(extra)
    return row


class HoldRoleLabelTest(unittest.TestCase):
    def test_exit_uses_first_buy_as_day_zero(self):
        self.assertEqual(HOLD_DAYS, 10)
        self.assertEqual(exitDtFromFirst("20261120", CAL), "20261204")
        self.assertEqual(holdingExitDay(_hold("20261120"), CAL), "20261204")
        self.assertEqual(holdingExitDay(_hold("20261120", exitDt="20261207"), CAL), "20261207")

    def test_split_legs_follow_first_fill_not_second(self):
        row = _hold("20261120", leg2={"ymd": "20261126", "qty": 40})
        self.assertEqual(holdingExitDay(row, CAL), "20261204")
        noFirst = _hold("", leg2={"ymd": "20261126", "qty": 40})
        noFirst["firstBuyDt"] = ""
        noFirst["leg1"] = {"ymd": "20261120", "qty": 60}
        self.assertEqual(holdingExitDay(noFirst, CAL), "20261204")
        self.assertNotEqual(holdingExitDay(noFirst, CAL), exitDtFromFirst("20261126", CAL))
        merged = unionRows([], [noFirst], [])
        self.assertEqual(holdingExitDay(merged[0], CAL), "20261204")

    def test_weekend_is_skipped_in_remaining_days(self):
        row = _hold("20261120")
        self.assertEqual(holdRoleLabel(row, "20261127", CAL)["text"], "D-5")
        sat = holdRoleLabel(row, "20261128", CAL)
        sun = holdRoleLabel(row, "20261129", CAL)
        mon = holdRoleLabel(row, "20261130", CAL)
        self.assertEqual(sat["text"], mon["text"])
        self.assertEqual(sun["text"], "D-4")
        self.assertEqual(mon["text"], "D-4")
        self.assertEqual(buySpan("20261127", "20261204", CAL), 5)

    def test_midweek_holiday_shifts_sell_day_and_countdown(self):
        self.assertEqual(exitDtFromFirst("20261120", CAL_HOL), "20261207")
        row = _hold("20261120")
        self.assertEqual(holdRoleLabel(row, "20261124", CAL)["text"], "D-8")
        self.assertEqual(holdRoleLabel(row, "20261124", CAL_HOL)["text"], "D-8")
        self.assertEqual(holdRoleLabel(row, "20261125", CAL_HOL)["text"], "D-7")
        self.assertEqual(holdRoleLabel(row, "20261126", CAL_HOL)["text"], "D-7")
        exitMay = exitDtFromFirst("20260428")
        self.assertEqual(exitMay, "20260513")
        real = {"role": "보유", "firstBuyDt": "20260428"}
        mon = holdRoleLabel(real, "20260504")
        hol = holdRoleLabel(real, "20260505")
        wed = holdRoleLabel(real, "20260506")
        self.assertEqual(hol["text"], wed["text"])
        self.assertNotEqual(hol["text"], mon["text"])
        self.assertEqual(wed["remain"] + 1, mon["remain"])

    def test_d_minus_one_and_sell_day_and_overdue(self):
        row = _hold("20261120")
        d1 = holdRoleLabel(row, "20261203", CAL)
        self.assertEqual(d1, {"text": "D-1", "alert": True, "bold": False, "remain": 1, "late": 0})
        d0 = holdRoleLabel(row, "20261204", CAL)
        self.assertEqual(d0, {"text": "매도일", "alert": True, "bold": True, "remain": 0, "late": 0})
        late1 = holdRoleLabel(row, "20261207", CAL)
        self.assertEqual(late1, {"text": "매도지연 +1", "alert": True, "bold": True, "remain": 0, "late": 1})
        late2 = holdRoleLabel(row, "20261208", CAL)
        self.assertEqual(late2["text"], "매도지연 +2")
        weekend = holdRoleLabel(row, "20261205", CAL)
        self.assertEqual(weekend, {"text": "매도지연", "alert": True, "bold": True, "remain": 0, "late": 0})
        self.assertEqual(holdRoleLabel(row, "20261206", CAL)["text"], "매도지연")

    def test_buy_day_is_d_minus_hold_days(self):
        row = _hold("20261126")
        got = holdRoleLabel(row, "20261126", CAL)
        self.assertEqual(got["text"], "D-10")
        self.assertEqual(got["remain"], 10)
        self.assertFalse(got["alert"])
        self.assertEqual(exitDtFromFirst("20261126", CAL), "20261210")

    def test_candidates_unchanged_and_missing_dates_stay_held(self):
        cand = holdRoleLabel({"role": "후보"}, "20261126", CAL)
        self.assertEqual(cand["text"], "후보")
        self.assertFalse(cand["alert"])
        boost = holdRoleLabel({"role": "후보", "boost": "1.5x"}, "20261126", CAL)
        self.assertEqual(boost["text"], "후보 1.5x")
        empty = holdRoleLabel({"role": "보유", "holding": True}, "20261126", CAL)
        self.assertEqual(empty["text"], "보유")
        self.assertFalse(empty["alert"])

    def test_real_holiday_calendar_matches_exit_function(self):
        first = "20261120"
        exitDt = exitDtFromFirst(first)
        self.assertEqual(exitDt, "20261204")
        cal = holidayCalendar("20261120", "20261220")
        row = _hold(first)
        self.assertEqual(holdingExitDay(row), exitDt)
        self.assertEqual(holdRoleLabel(row, "20261126")["remain"], buySpan("20261126", exitDt, cal))
        self.assertEqual(holdRoleLabel(row, exitDt)["text"], "매도일")


if __name__ == "__main__":
    unittest.main()
