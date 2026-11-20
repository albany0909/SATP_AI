"""관심종목 화면은 PBK 후보·보유만. 수집 목록과 백업은 따로 둔다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.impl.pullbackTwoStep import formatBoardLine, formatCandidateLog
from utils.pbkWatchScreen import (
    HOLD_BG,
    NEAR_BG,
    TOUCH_BG,
    applyScreenFill,
    leg1Filled,
    nextBuyLine,
    pbkNearPct,
    pbkRowTone,
    backupWatchOnce,
    bootWatchSelection,
    collectionCodes,
    ensurePbkTab,
    ensureWatchBackup,
    formatPx,
    loadScreenRows,
    lookupStkNames,
    mergeScreenFills,
    readWatchBackup,
    resetScreenFills,
    restoreLegacyWatch,
    screenPrice,
    sortRoleGroups,
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
        self.assertEqual(by["005930"], "보유")
        self.assertEqual(by["009830"], "보유")
        self.assertNotIn("000660", by)
        self.assertEqual([row["stkCd"] for row in rows], ["005930", "009830"])
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

    def test_name_falls_through_master_watch_then_kiwoom(self):
        calls = []

        def master(codes):
            calls.append(("master", list(codes)))
            return {"005930": "삼성전자"}

        def watch(codes):
            calls.append(("watch", list(codes)))
            return {"000660": "SK하이닉스"}

        def kiwoom(codes):
            calls.append(("kiwoom", list(codes)))
            return {"263860": "지니언스"}

        got = lookupStkNames(
            ["005930", "000660", "263860", "999999"],
            masterFn=master,
            watchFn=watch,
            kiwoomFn=kiwoom,
            cache={},
        )
        self.assertEqual(got["005930"], "삼성전자")
        self.assertEqual(got["000660"], "SK하이닉스")
        self.assertEqual(got["263860"], "지니언스")
        self.assertEqual(got["999999"], "999999")
        self.assertEqual(calls[0][0], "master")
        self.assertNotIn("005930", calls[1][1])
        self.assertEqual(calls[2][1], ["263860", "999999"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text(json.dumps({
                "kind": "state",
                "candidates": [{"stkCd": "263860", "ma5": 32000}],
                "positions": [],
            }) + "\n", encoding="utf-8")
            rows = loadScreenRows(path, names={"263860": "지니언스"})
            self.assertEqual(rows[0]["stkNm"], "지니언스")
            kept = loadScreenRows(path, names={})
            self.assertEqual(kept[0]["stkNm"], "263860")

    def test_fill_flips_to_hold_and_flat_keeps_candidate(self):
        resetScreenFills()
        base = unionRows(
            [
                {"stkCd": "005930", "stkNm": "삼성", "ma5": 70000, "expireDt": "20261231"},
                {"stkCd": "000660", "stkNm": "하이닉스", "ma5": 200000, "expireDt": "20261231"},
            ],
            [],
            [],
        )
        self.assertEqual([row["role"] for row in base], ["후보", "후보"])
        known = [row["stkCd"] for row in base]
        self.assertEqual(applyScreenFill("매수", "005930", 3, known=known), 3)
        self.assertIsNone(applyScreenFill("매수", "999999", 1, known=known))
        held = mergeScreenFills(base)
        by = {row["stkCd"]: row["role"] for row in held}
        self.assertEqual(by["005930"], "보유")
        self.assertEqual(by["000660"], "후보")
        self.assertEqual(held[0]["stkCd"], "005930")
        self.assertEqual(applyScreenFill("매도", "005930", 3, known=known), 0)
        flat = mergeScreenFills(base)
        self.assertEqual([row["stkCd"] for row in flat], ["000660", "005930"])
        self.assertEqual({row["stkCd"]: row["role"] for row in flat}["005930"], "후보")
        only = unionRows([], [{"stkCd": "009830", "qty": 4, "leg1": {"qty": 1}}], [])
        self.assertEqual(applyScreenFill("매도", "009830", 4, known=["009830"]), 0)
        gone = mergeScreenFills(only)
        self.assertEqual(gone, [])
        priced = sortRoleGroups(
            [
                {"role": "후보", "stkCd": "000660", "ma5": 9},
                {"role": "보유", "stkCd": "005930", "ma5": 1},
                {"role": "보유", "stkCd": "035420", "ma5": 5},
            ],
            lambda row: row["ma5"],
            descending=True,
        )
        self.assertEqual([row["stkCd"] for row in priced], ["035420", "005930", "000660"])
        resetScreenFills()

    def test_row_tone_near_touch_and_hold_priority(self):
        cand = {"role": "후보", "holding": False, "ma5": 100, "ma20": 80}
        self.assertEqual(nextBuyLine(cand)[0], "ma5")
        self.assertEqual(pbkRowTone(cand, 103, None, 3)["tone"], "near")
        self.assertEqual(pbkRowTone(cand, 103, None, 3)["bg"], NEAR_BG)
        self.assertEqual(pbkRowTone(cand, 103.1, None, 3)["tone"], "none")
        self.assertEqual(pbkRowTone(cand, 100, None, 3)["tone"], "touch")
        self.assertEqual(pbkRowTone(cand, 110, 99, 3)["bg"], TOUCH_BG)
        held = {"role": "보유", "holding": True, "ma5": 100, "ma20": 80, "leg1": {"qty": 1}}
        self.assertTrue(leg1Filled(held))
        self.assertEqual(nextBuyLine(held)[0], "ma20")
        self.assertEqual(pbkRowTone(held, 70, 70, 3)["tone"], "hold")
        self.assertEqual(pbkRowTone(held, 70, 70, 3)["bg"], HOLD_BG)
        second = {"role": "후보", "holding": False, "ma5": 100, "ma20": 80, "leg1": {"qty": 2}}
        self.assertEqual(nextBuyLine(second)[0], "ma20")
        self.assertEqual(pbkRowTone(second, 82, None, 3)["tone"], "near")
        self.assertEqual(pbkRowTone(second, 70, None, 3)["tone"], "touch")
        self.assertEqual(pbkNearPct({}), 3.0)
        self.assertEqual(pbkNearPct({"pbkNearPct": 1.5}), 1.5)
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "settings.json"
            live.write_text(json.dumps({"deskTradeMode": "paper", "keep": 1}), encoding="utf-8")
            import config.settingsStore as store

            with patch.object(store, "_SETTINGS_PATH", live), patch.object(
                store, "_DEFAULT_PATH", Path(folder) / "missing-default.json",
            ):
                self.assertEqual(pbkNearPct(store.loadSettings()), 3.0)
            self.assertEqual(json.loads(live.read_text(encoding="utf-8")), {"deskTradeMode": "paper", "keep": 1})
        self.assertEqual(pbkRowTone(cand, 101.2, None, 1.0)["tone"], "none")
        self.assertEqual(pbkRowTone(cand, 101, None, 1.0)["tone"], "near")


if __name__ == "__main__":
    unittest.main(verbosity=2)
