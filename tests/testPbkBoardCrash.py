"""PBK 표 48행과 상단 한 줄을 오프스크린에서 돌려도 죽지 않는지."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication


class PbkBoardCrashTest(unittest.TestCase):
    def test_forty_eight_rows_and_board_refresh(self):
        from strategies.impl.pullbackTwoStep import formatBoardLine

        self.assertIn("--:--", formatBoardLine([{"stkCd": "005930"}], [], None))
        self.assertNotIn("\n", formatBoardLine([{"stkCd": "005930"}], [], None))

        app = QApplication.instance() or QApplication([])
        from ui.tradeDeskWin import TradeDeskWin

        win = TradeDeskWin()
        win.show()
        rows = []
        for i in range(48):
            rows.append({
                "role": "후보",
                "stkCd": f"{i:06d}",
                "stkNm": f"종목{i}",
                "ma5": 1000 + i,
                "ma20": 900 + i,
                "expireDt": "20261231",
            })
        with patch("utils.pbkWatchScreen.loadScreenRows", return_value=rows), patch(
            "utils.pbkWatchScreen.watchScreenOn", return_value=True,
        ), patch("utils.pbkWatchScreen.noteScreenRefresh", return_value={}), patch(
            "utils.pbkWatchScreen.ensureWatchBackup", return_value={"ok": True},
        ), patch("trading.watchRtMonitor.getWatchLastPxMap", return_value={}), patch(
            "trading.watchRtMonitor._isQuoteSessionNow", return_value=False,
        ):
            win._watchAlgCd = "pbk"
            win._fillPbkWatch()
            win._paintPbkBoard()
        app.processEvents()
        self.assertEqual(win.tradeWatchFrozen.rowCount(), 48)
        self.assertFalse(win.tradeWatchFrozen.isSortingEnabled())
        text = win.pbkBoard.text()
        self.assertNotIn("\n", text)
        self.assertLessEqual(win.pbkBoard.height(), 22)
        self.assertTrue(text.startswith("[PBK01]"))
        win.close()

    def test_hold_color_sort_and_refresh_reentry(self):
        from PySide6.QtGui import QColor
        from utils.pbkWatchScreen import mergeScreenFills, resetScreenFills, sortRoleGroups

        resetScreenFills()
        app = QApplication.instance() or QApplication([])
        from ui.tradeDeskWin import TradeDeskWin

        win = TradeDeskWin()
        base = [
            {"role": "후보", "candidate": True, "holding": False, "stkCd": "000003", "stkNm": "다", "ma5": 100, "ma20": 1, "expireDt": "20261231"},
            {"role": "보유", "candidate": True, "holding": True, "stkCd": "000001", "stkNm": "가", "ma5": 10, "ma20": 1, "expireDt": "20261231"},
            {"role": "후보", "candidate": True, "holding": False, "stkCd": "000002", "stkNm": "나", "ma5": 50, "ma20": 1, "expireDt": "20261231"},
        ]

        def _rows(*_a, **_k):
            return mergeScreenFills([dict(row) for row in base])

        with patch("utils.pbkWatchScreen.loadScreenRows", side_effect=_rows), patch(
            "utils.pbkWatchScreen.watchScreenOn", return_value=True,
        ), patch("utils.pbkWatchScreen.noteScreenRefresh", return_value={}), patch(
            "utils.pbkWatchScreen.ensureWatchBackup", return_value={"ok": True},
        ), patch("trading.watchRtMonitor.getWatchLastPxMap", return_value={}), patch(
            "trading.watchRtMonitor._isQuoteSessionNow", return_value=False,
        ):
            win._watchAlgCd = "pbk"
            from utils.pbkWatchScreen import applyScreenFill

            self.assertEqual(applyScreenFill("매수", "000002", 2, known=["000001", "000002", "000003"]), 2)
            win._fillPbkWatch()
            app.processEvents()
            frz = win.tradeWatchFrozen
            self.assertEqual([frz.item(i, 1).text() for i in range(frz.rowCount())], ["000001", "000002", "000003"])
            self.assertEqual(frz.item(0, 0).text(), "보유")
            self.assertEqual(frz.item(1, 0).text(), "보유")
            self.assertEqual(frz.item(1, 0).background().color().name().lower(), "#e8f5e9")
            self.assertEqual(frz.item(1, 0).foreground().color(), QColor("#212121"))
            self.assertEqual(applyScreenFill("매도", "000002", 2, known=["000001", "000002", "000003"]), 0)
            win._fillPbkWatch()
            self.assertEqual(frz.item(0, 1).text(), "000001")
            self.assertEqual(frz.item(0, 0).text(), "보유")
            self.assertEqual(frz.item(1, 0).text(), "후보")
            self.assertEqual(frz.item(2, 0).text(), "후보")
            frz.horizontalHeader().sectionClicked.emit(3)
            app.processEvents()
            self.assertEqual([frz.item(i, 1).text() for i in range(3)], ["000001", "000002", "000003"])
            frz.horizontalHeader().sectionClicked.emit(3)
            self.assertEqual(frz.item(0, 1).text(), "000001")
            self.assertEqual(frz.item(1, 1).text(), "000003")
            self.assertFalse(frz.isSortingEnabled())
            calls = []

            def _fill():
                calls.append(win._watchRefreshBusy)
                if len(calls) == 1:
                    win.refreshTradeWatch()

            win._fillTradeWatch = _fill
            win._watchRefreshBusy = False
            win._watchRefreshPending = False
            win.refreshTradeWatch()
            app.processEvents()
            self.assertEqual(calls, [True, True])
            self.assertFalse(win._watchRefreshBusy)
            ordered = sortRoleGroups(
                [{"role": "후보", "v": 1}, {"role": "보유", "v": 0}],
                lambda row: row["v"],
                descending=True,
            )
            self.assertEqual([row["role"] for row in ordered], ["보유", "후보"])
        resetScreenFills()
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
