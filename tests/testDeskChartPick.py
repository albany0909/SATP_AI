"""관심종목 행 클릭은 선택을 유지하고, 차트 탭이 열려 있을 때만 그린다."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.liveGuard  # noqa: E402

from ui.tradeDeskWin import TradeDeskWin
from utils.chartBarPlan import chartActionForWatchPick


class _Btn(object):
    def __init__(self, checked=False):
        self.checked = checked
        self.sets = []

    def isChecked(self):
        return self.checked

    def setChecked(self, value):
        self.checked = bool(value)
        self.sets.append(self.checked)


class _Table(object):
    def __init__(self, row):
        self.row = row
        self.selected = []

    def currentRow(self):
        return self.row

    def selectRow(self, row):
        self.row = row
        self.selected.append(row)


class _Host(object):
    def __init__(self, side):
        self._sideKey = side
        self._chartStkCd = "000660"
        self._chartStkNm = "하이닉스"
        self._watchRefreshBusy = False
        self._chartSelBusy = False
        self._chartFetchTried = set()
        self.chartTab = _Btn(checked=side == "chart")
        self.reloads = []
        self.tabs = []
        self.subs = []
        self.tradeWatchFrozen = _Table(2)
        self.tradeWatchTbl = _Table(-1)
        self._sender = self.tradeWatchFrozen

    def sender(self):
        return self._sender

    def _selectedWatchStk(self):
        return "005930", "삼성전자"

    def _reloadDeskChart(self):
        self.reloads.append(self._sideKey)

    def _onSideTab(self, key, checked):
        self.tabs.append((key, checked))
        if checked:
            self._sideKey = key
            self.chartTab.checked = True

    def _subscribeChartQuote(self, code):
        self.subs.append(code)

    _applyWatchChartPick = TradeDeskWin._applyWatchChartPick


class DeskChartPickTest(unittest.TestCase):
    def test_other_tab_click_selects_row_only(self):
        self.assertEqual(chartActionForWatchPick(True, "005930", chartOpen=False), "ignore")
        host = _Host("acct")
        TradeDeskWin._onWatchRowPicked(host)
        self.assertEqual(host.tradeWatchTbl.selected, [2])
        self.assertEqual(host.tradeWatchFrozen.currentRow(), 2)
        self.assertEqual(host._sideKey, "acct")
        self.assertEqual(host.tabs, [])
        self.assertEqual(host.reloads, [])
        self.assertEqual(host.subs, [])
        self.assertEqual(host.chartTab.sets, [])
        self.assertFalse(host.chartTab.isChecked())
        self.assertEqual(host._chartStkCd, "000660")

    def test_open_chart_tab_redraws_without_switching(self):
        self.assertEqual(
            chartActionForWatchPick(True, "005930", currentCode="000660", chartOpen=True),
            "reload",
        )
        host = _Host("chart")
        TradeDeskWin._onWatchRowPicked(host)
        self.assertEqual(host.tradeWatchTbl.selected, [2])
        self.assertEqual(host._sideKey, "chart")
        self.assertEqual(host.tabs, [])
        self.assertEqual(host.reloads, ["chart"])
        self.assertEqual(host.subs, ["005930"])
        self.assertEqual(host.chartTab.sets, [])
        self.assertTrue(host.chartTab.isChecked())
        self.assertEqual(host._chartStkCd, "005930")
        self.assertEqual(host._chartStkNm, "삼성전자")


if __name__ == "__main__":
    unittest.main(verbosity=2)
