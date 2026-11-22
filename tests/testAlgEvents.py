"""알고리즘 차트 마커와 청산 이력. 주문·DB·설정 파일은 쓰지 않는다."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.algEventLog import (
    chartMarks,
    closedRows,
    eventsForChart,
    eventsFromPbkLedger,
    exitLabel,
    firstMaTouches,
    legendLine,
    loadDeskEvents,
)
from utils.pbkWatchScreen import ensureClosedTab


def _state():
    return {
        "candidates": [{"stkCd": "028050", "stkNm": "삼성E&A", "signalDt": "20261101"}],
        "positions": [
            {
                "stkCd": "300120",
                "stkNm": "라온피플",
                "signalDt": "20261104",
                "qty": 672,
                "avg": 2445,
                "leg1": {"qty": 400, "fillPx": 2500, "ymd": "20261120"},
                "leg2": {"qty": 272, "fillPx": 2360, "ymd": "20261121"},
            },
            {
                "stkCd": "028050",
                "stkNm": "삼성E&A",
                "signalDt": "20261101",
                "qty": 0,
                "closed": True,
                "avg": 20000,
                "sellPx": 20860,
                "sellDt": "20261118",
                "soldQty": 10,
                "exitReason": "f+10",
                "leg1": {"filledQty": 10, "fillPx": 20000, "ymd": "20261104"},
            },
            {
                "stkCd": "000001",
                "stkNm": "손절예",
                "signalDt": "20261001",
                "qty": 0,
                "released": True,
                "avg": 1000,
                "sellPx": 800,
                "sellDt": "20261020",
                "soldQty": 5,
                "exitReason": "손절",
                "leg1": {"filledQty": 5, "fillPx": 1000, "ymd": "20261006"},
            },
        ],
    }


class AlgEventLogTest(unittest.TestCase):
    def test_ledger_becomes_signal_buy_and_closed_rows(self):
        fills = [{
            "stkCd": "300120",
            "stkNm": "라온피플",
            "leg": 1,
            "ymd": "20261120",
            "fillPx": 2500,
            "brokerQty": 400,
        }]
        events = eventsFromPbkLedger(_state(), fills)
        kinds = {(row["stkCd"], row["kind"], row.get("leg")) for row in events}
        self.assertIn(("300120", "signal", None), kinds)
        self.assertIn(("300120", "buy", 1), kinds)
        self.assertIn(("300120", "buy", 2), kinds)
        self.assertEqual(sum(1 for row in events if row["stkCd"] == "300120" and row["kind"] == "buy"), 2)
        self.assertNotIn("300120", {row["stkCd"] for row in closedRows(events)})
        closed = {row["stkCd"]: row for row in closedRows(events)}
        self.assertEqual(closed["028050"]["reason"], "f+10 시가")
        self.assertEqual(closed["028050"]["pnlText"], "+4.3% / +8,600")
        self.assertEqual(closed["028050"]["buyText"], "2026-11-04 · 20,000")
        self.assertEqual(closed["028050"]["sellText"], "2026-11-18 · 20,860")
        self.assertEqual(closed["028050"]["holdDays"], "14")
        self.assertEqual(closed["000001"]["reason"], "손절")
        self.assertTrue(closed["000001"]["pnlText"].startswith("-20.0%"))
        self.assertEqual(exitLabel("손절", -20), "손절 -20.0%")
        self.assertEqual(exitLabel("f+10", 4.3), "매도 +4.3%")
        self.assertIn("PBK01", legendLine(events))

    def test_file_and_ledger_merge_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "pbk01_state.jsonl").write_text(
                json.dumps({"kind": "state", **_state()}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (base / "pbk01_fills_20261120.jsonl").write_text(
                json.dumps({
                    "stkCd": "300120", "leg": 1, "ymd": "20261120",
                    "fillPx": 2500, "brokerQty": 400,
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (base / "algEvents.jsonl").write_text(
                json.dumps({
                    "algCd": "PBK01", "stkCd": "300120", "stkNm": "라온피플",
                    "kind": "buy", "dt": "20261121", "px": 2360, "qty": 272, "leg": 2,
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            before = (base / "algEvents.jsonl").read_text(encoding="utf-8")
            rows = eventsForChart("300120", folder)
            self.assertEqual((base / "algEvents.jsonl").read_text(encoding="utf-8"), before)
            self.assertFalse((base / "algEvents-written.jsonl").exists())
            self.assertEqual(
                sum(1 for row in rows if row["kind"] == "buy" and row["leg"] == 2),
                1,
            )
            self.assertTrue(any(row["kind"] == "signal" for row in rows))
            self.assertEqual(loadDeskEvents(folder)[0]["algCd"], "PBK01")

    def test_touch_and_chart_marks_follow_the_signal(self):
        bars = []
        for i in range(1, 31):
            close = 100.0
            low = 100.0
            if i == 26:
                low = 90.0
            bars.append({
                "tradeDt": f"202611{i:02d}",
                "open": close,
                "high": close + 1,
                "low": low,
                "close": close,
            })
        touches = firstMaTouches(bars, "20261120")
        self.assertEqual([row["line"] for row in touches if row], [5, 20])
        self.assertTrue(all(row["dt"] > "20261120" for row in touches if row))
        events = eventsFromPbkLedger(_state(), [])
        marks = chartMarks(
            [row for row in events if row["stkCd"] == "028050"],
            [
                {"tradeDt": "20261101", "open": 19000, "high": 21000, "low": 19000, "close": 20000},
                {"tradeDt": "20261104", "open": 19800, "high": 20200, "low": 19700, "close": 20000},
                {"tradeDt": "20261118", "open": 20500, "high": 21000, "low": 20400, "close": 20860},
            ],
        )
        labels = [row["label"] for row in marks]
        self.assertTrue(any("기준" in label for label in labels))
        self.assertTrue(any(label.startswith("매수1") for label in labels))
        self.assertTrue(any(label.startswith("매도 +4.3%") for label in labels))
        paper = {"deskTradeMode": "paper", "stratLive": {"PBK01": {"watchScreen": True}}}
        tabs = ensureClosedTab([{"key": "pbk", "name": "PBK"}], paper)
        self.assertEqual([row["key"] for row in tabs], ["pbk", "closed"])
        self.assertEqual(
            ensureClosedTab([{"key": "surge", "name": "급등주"}], {"deskTradeMode": "real"}),
            [{"key": "surge", "name": "급등주"}],
        )

    def test_closed_tab_keeps_history_and_does_not_subscribe(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from ui.deskStkChart import DeskStkChartPane
        from ui.tradeDeskWin import TradeDeskWin

        events = eventsFromPbkLedger(_state(), [])
        pane = DeskStkChartPane()
        day = []
        for i, px in ((1, 19000), (4, 20000), (18, 20860)):
            day.append({
                "tradeDt": f"202611{i:02d}",
                "open": px, "high": px + 100, "low": px - 100, "close": px,
            })
        pane.setAlgEvents([row for row in events if row["stkCd"] == "028050"])
        pane.setChartBundle("028050", "삼성E&A", {"day": day})
        kinds = [row.get("kind") for row in pane.candleView._timingMarks]
        self.assertIn("algSignal", kinds)
        self.assertIn("algBuy", kinds)
        self.assertIn("algSell", kinds)
        self.assertFalse(pane.algLegend.isHidden())
        self.assertIn("PBK01", pane.algLegend.text())
        self.assertIn("pull", pane._sigBoxes)

        win = TradeDeskWin()
        calls = []
        win._subscribeChartQuote = lambda code: calls.append(("sub", code))
        win._reloadDeskChart = lambda *args, **kwargs: calls.append(("reload", kwargs))
        with patch("utils.algEventLog.loadDeskEvents", return_value=events):
            win._watchAlgCd = "closed"
            win._fillClosedWatch()
        frz = win.tradeWatchFrozen
        headers = [frz.horizontalHeaderItem(i).text() for i in range(8)]
        self.assertEqual(headers[0], "알고리즘")
        self.assertEqual(headers[7], "청산사유")
        codes = [frz.item(i, 1).data(Qt.ItemDataRole.UserRole) for i in range(frz.rowCount())]
        self.assertEqual(codes, ["028050", "000001"])
        self.assertEqual(frz.item(0, 7).text(), "f+10 시가")
        self.assertEqual(frz.item(1, 7).text(), "손절")
        win._sideKey = "acct"
        win._applyWatchChartPick("028050", "삼성E&A")
        self.assertEqual(calls, [])
        self.assertEqual(win._sideKey, "acct")
        win._sideKey = "chart"
        win._chartStkCd = ""
        win._applyWatchChartPick("028050", "삼성E&A")
        self.assertEqual(calls, [("reload", {"allowFetch": False})])
        app.processEvents()
        win.close()


if __name__ == "__main__":
    unittest.main()
