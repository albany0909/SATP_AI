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
        self.assertTrue(win.tradeWatchFrozen.isSortingEnabled())
        text = win.pbkBoard.text()
        self.assertNotIn("\n", text)
        self.assertLessEqual(win.pbkBoard.height(), 22)
        self.assertTrue(text.startswith("[PBK01]"))
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
