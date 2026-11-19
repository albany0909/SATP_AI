"""관심종목 화면은 PBK 후보·보유만. 수집 목록과 백업은 따로 둔다."""

import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.impl.pullbackTwoStep import formatBoardLine, formatCandidateLog
from utils.pbkWatchScreen import (
    backupWatchOnce,
    bootWatchSelection,
    collectionCodes,
    ensurePbkTab,
    ensureWatchBackup,
    formatPx,
    loadScreenRows,
    readWatchBackup,
    restoreLegacyWatch,
    screenPrice,
    touchDelta,
    unionRows,
    watchScreenOn,
)
from utils.watchDayPrcLoad import collectWatchHoldCds


class PbkWatchScreenTest(unittest.TestCase):
    def test_union_labels_and_paper_guard(self):
        rows = unionRows(
            [{"stkCd": "005930", "stkNm": "삼성", "signalDt": "20261104", "expireDt": "20261202"}],
            [{"stkCd": "009830", "qty": 10, "leg1": {"qty": 1}}],
            [{"stkCd": "005930", "stkNm": "삼성", "qtyNum": 3}, {"stkCd": "000660", "qty": 0}],
        )
        by = {row["stkCd"]: row["role"] for row in rows}
        self.assertEqual(by["005930"], "후보·보유")
        self.assertEqual(by["009830"], "보유")
        self.assertNotIn("000660", by)
        self.assertEqual([row["stkCd"] for row in rows], ["009830", "005930"])
        self.assertTrue(watchScreenOn({"deskTradeMode": "paper", "stratLive": {"PBK01": {}}}))
        self.assertFalse(watchScreenOn({"deskTradeMode": "real", "stratLive": {"PBK01": {}}}))
        self.assertFalse(watchScreenOn({
            "deskTradeMode": "paper",
            "stratLive": {"PBK01": {"watchScreen": False}},
        }))
        paper = {"deskTradeMode": "paper", "stratLive": {"PBK01": {"watchScreen": True}}}
        self.assertEqual(bootWatchSelection("surge", ["pbk", "surge"], paper), "pbk")
        saved = {"deskTradeMode": "paper", "stratLive": {"PBK01": {"watchScreen": False}}}
        self.assertEqual(bootWatchSelection("surge", ["pbk", "surge"], saved), "surge")
        opened = ensurePbkTab([{"key": "surge", "name": "급등주", "bg": "#80DEEA"}], paper)
        self.assertEqual(opened[0]["key"], "pbk")
        self.assertEqual(bootWatchSelection("surge", opened, paper), "pbk")

    def test_backup_once_and_collection_stays_wide(self):
        legacy = [
            {"STK_CD": "005930", "STK_NM": "삼성전자", "GRP_CD": "WEB"},
            {"STK_CD": "000660", "STK_NM": "SK하이닉스", "GRP_CD": "RANK"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            first = ensureWatchBackup(folder, reader=lambda: legacy)
            self.assertEqual(first["cnt"], 2)
            saved = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["STK_CD"], "005930")
            self.assertEqual(saved[0]["GRP_CD"], "WEB")
            again = backupWatchOnce([{"STK_CD": "111111"}], folder)
            self.assertEqual(again["skipped"], "exists")
            self.assertEqual(readWatchBackup(folder)[1]["STK_NM"], "SK하이닉스")
            empty = ensureWatchBackup(folder, reader=lambda: [])
            self.assertEqual(empty["skipped"], "exists")
        screen = unionRows([{"stkCd": "263860"}], [], [])
        wide = collectionCodes(legacy)
        self.assertIn("005930", wide)
        self.assertIn("069500", wide)
        self.assertIn("001", wide)
        self.assertNotIn("263860", wide)
        self.assertEqual(screen[0]["stkCd"], "263860")
        self.assertIn("listActiveWatch", Path("utils/watchDayPrcLoad.py").read_text(encoding="utf-8"))
        self.assertIn("def collectWatchHoldCds", Path("utils/watchDayPrcLoad.py").read_text(encoding="utf-8"))
        self.assertTrue(callable(collectWatchHoldCds))
        delta = touchDelta(["005930", "000660"], ["263860", "005930"], ["000660"])
        self.assertEqual(delta["added"], ["263860"])
        self.assertEqual(delta["removed"], [])
        drop = touchDelta(["263860"], [], [])
        self.assertEqual(drop["removed"], ["263860"])
        self.assertIn("def restoreLegacyWatch", Path("utils/pbkWatchScreen.py").read_text(encoding="utf-8"))
        self.assertTrue(callable(restoreLegacyWatch))

    def test_screen_reads_saved_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text(json.dumps({
                "kind": "state",
                "candidates": [{"stkCd": "263860", "signalDt": "20261112", "expireDt": "20261231"}],
                "positions": [{"stkCd": "009830", "qty": 4, "leg1": {"qty": 1}}],
            }) + "\n", encoding="utf-8")
            rows = loadScreenRows(path, accountHolds=[{"stkCd": "035420", "qtyNum": 2, "stkNm": "네이버"}])
            by = {row["stkCd"]: row["role"] for row in rows}
            self.assertEqual(by["263860"], "후보")
            self.assertEqual(by["009830"], "보유")
            self.assertEqual(by["035420"], "보유")

    def test_board_line_and_live_price(self):
        line = formatBoardLine(
            [{"stkCd": "263860", "ma5": 32000}, {"stkCd": "005930"}],
            [{"stkCd": "009830", "qty": 4}],
            "08:40:12",
        )
        self.assertEqual(line, "[PBK01] 후보 2건 · 보유 1/5 · 최근 재스캔 08:40")
        self.assertNotIn("\n", line)
        self.assertNotIn("263860", line)
        detail = formatCandidateLog([{"stkCd": "263860", "ma5": 32000, "ma20": 30000, "expireDt": "20261231"}])
        self.assertIn("263860", detail)
        self.assertEqual(formatPx(13800000000), "13,800,000,000")
        self.assertEqual(formatPx(None), "-")
        live = screenPrice({"src": "0B", "curPrc": "33300"}, closePx=25800, inSession=True)
        closed = screenPrice({"src": "0B", "curPrc": "33300"}, closePx=25800, inSession=False)
        self.assertEqual(live, 33300)
        self.assertEqual(closed, 25800)
        seeded = screenPrice({"src": "dayClose", "curPrc": "25800"}, inSession=False)
        self.assertEqual(seeded, 25800)
        rows = unionRows(
            [{"stkCd": "263860", "ma5": 32000, "ma20": 30000, "expireDt": "20261231"}],
            [],
            [],
        )
        self.assertEqual(rows[0]["ma5"], 32000)
        self.assertEqual(rows[0]["ma20"], 30000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
