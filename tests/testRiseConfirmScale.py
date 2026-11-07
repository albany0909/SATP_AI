# -*- coding: utf-8 -*-
"""RCS01 격리 리플레이. 주문·설정 저장·reference engine 호출 없음.

python tests/testRiseConfirmScale.py
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.impl.riseConfirmReplay import (  # noqa: E402
    DEFAULT_LOSS_KRW,
    DataError,
    load_loss100k_fixture,
    load_parity_fixture,
    run_october_loss100k,
    run_october_parity,
)
from strategies.impl.riseConfirmScale import LOSS_BUDGET, RiseConfirmBook  # noqa: E402
from utils.stratVerHist import gitHashFromVerNote  # noqa: E402

FIX_300 = ROOT / "tests" / "fixtures" / "rcs01" / "OCT_parity_fixture.json"
FIX_100 = ROOT / "tests" / "fixtures" / "rcs01" / "OCT_100K_fixture.json"
AUDIT = ROOT / "tests" / "fixtures" / "rcs01" / "OCT_audit.json"
REF_ENGINE_SHA = "0ad3e36bcf3f9361891c2f9d2fcd65fe20925b268453ba5b41cc5f6cad4db916"


class OctoberParityTests(unittest.TestCase):
    def test_default_loss_is_100k(self):
        spec = json.loads((ROOT / "strategies" / "specs" / "riseConfirmScale_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["version"], 2)
        self.assertEqual(spec["nameEn"], "Rise Confirmation Scale Trading")
        self.assertEqual(spec["stratId"], "riseConfirmScale_v1")
        self.assertEqual(spec["params"]["lossKrw"], 100000)
        self.assertFalse(spec["enabled"])
        self.assertEqual(spec["algCd"], "RCS01")
        self.assertEqual(LOSS_BUDGET, 100_000)
        self.assertEqual(DEFAULT_LOSS_KRW, 100_000)

    def test_300k_fixture_matches_app_path(self):
        historical = load_parity_fixture(FIX_300)
        self.assertEqual(historical["config"]["loss"], 300000)
        self.assertNotEqual(historical["config"]["loss"], LOSS_BUDGET)
        report = run_october_parity(FIX_300)
        self.assertEqual(report["status"], "PASS", report["diffs"][:12])
        self.assertTrue(report["passed"])
        self.assertEqual(report["causality"], [])
        self.assertEqual(report["tracks"]["A"]["final_equity"], 9_616_400)
        self.assertEqual(report["tracks"]["B"]["final_equity"], 20_300_996)
        self.assertFalse(report["provenance_approved"])
        self.assertFalse(report["relabelled_synthetic"])

    def test_300k_rankings_match_audit(self):
        report = run_october_parity(FIX_300)
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        got = {row["day"]: row for row in report["tracks"]["A"]["rankings"]}
        for exp in audit["all_daily_rankings"]:
            row = got[exp["day"]]
            self.assertEqual(row["qualified"], exp["qualified"], exp["day"])
            self.assertEqual([item[2] for item in row["top3"]], [item[2] for item in exp["top3"]], exp["day"])

    def test_default_spec_october_100k_parity(self):
        report = run_october_loss100k(FIX_100)
        self.assertEqual(report["status"], "PASS", report["diffs"][:12])
        self.assertTrue(report["passed"])
        self.assertEqual(report["loss_krw"], 100_000)
        self.assertEqual(report["default_loss_krw"], 100_000)
        self.assertFalse(report["default_changed"])
        self.assertEqual(report["final_equity"], 9_846_798)
        self.assertAlmostEqual(report["return_percent"], -1.53202, places=5)
        self.assertEqual(report["pending"]["reason"], "profit_ma20")
        self.assertEqual(report["pending"]["signal_date"], "20261106")
        self.assertEqual(report["pending"]["code"], "033780")
        self.assertTrue(all(value > 0 for value in report["overshoots"]))
        spec = json.loads((ROOT / "strategies" / "specs" / "riseConfirmScale_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["params"]["lossKrw"], 100000)
        fixture = load_loss100k_fixture(FIX_100)
        cfg = dict(fixture["config"])
        cfg["loss"] = spec["params"]["lossKrw"]
        book = RiseConfirmBook(fixture, cfg, fixture["initial_states"]["A"], research_auto_exit=True)
        book.run()
        self.assertEqual(book.loss, 100_000)
        self.assertIsNotNone(book.pending)
        self.assertEqual(book.pending["reason"], "profit_ma20")
        self.assertEqual(book.pending["signal_date"], "20261106")
        self.assertFalse(any(row["kind"] == "sell" and row["date"] == "20261106" for row in book.ledger))

    def test_schemas_are_not_interchangeable(self):
        with self.assertRaises(DataError):
            load_parity_fixture(FIX_100)
        with self.assertRaises(DataError):
            load_loss100k_fixture(FIX_300)

    def test_live_loss_confirmation_under_100k(self):
        fixture = load_loss100k_fixture(FIX_100)
        book = RiseConfirmBook(
            fixture,
            fixture["config"],
            fixture["initial_states"]["A"],
            research_auto_exit=False,
        )
        self.assertEqual(book.loss, 100_000)
        book.run()
        self.assertFalse(any(row["kind"] == "sell" for row in book.ledger))
        self.assertTrue(book.confirms)
        self.assertGreater(book.confirms[0]["expected_loss"], 100_000)
        self.assertFalse(book.confirms[0]["confirmed"])

    def test_live_loss_is_not_the_research_fill(self):
        fixture = load_parity_fixture(FIX_300)
        book = RiseConfirmBook(
            fixture,
            fixture["config"],
            fixture["initial_states"]["A"],
            research_auto_exit=False,
        )
        book.run()
        self.assertFalse(any(row["kind"] == "sell" for row in book.ledger))
        self.assertTrue(book.confirms)
        self.assertGreater(book.confirms[0]["expected_loss"], 0)
        self.assertFalse(book.confirms[0]["confirmed"])

    def test_decision_module_is_not_the_reference_engine(self):
        impl = ROOT / "strategies" / "impl" / "riseConfirmScale.py"
        digest = hashlib.sha256(impl.read_bytes()).hexdigest()
        self.assertNotEqual(digest, REF_ENGINE_SHA)
        replay = (ROOT / "strategies" / "impl" / "riseConfirmReplay.py").read_text(encoding="utf-8")
        self.assertNotIn("reference_engine", replay)

    def test_settings_file_stays_off_paper(self):
        settings = json.loads((ROOT / "config" / "data" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["deskTradeMode"], "paper")
        self.assertFalse(settings["maTouchLive"]["enabled"])
        self.assertTrue(settings["maTouchLive"]["paperOnly"])
        for alg in ("MTS01", "MSE01", "LDR01", "MMG01", "RAM01"):
            self.assertFalse(settings["stratLive"][alg]["enabled"])
        self.assertNotIn("RCS01", settings.get("stratLive") or {})

    def test_tabs_hidden_until_flag_turned_on(self):
        from config.deskFeatureFlags import isDeskTabEnabled, resolveDeskFeatureFlags, watchTabSpecs

        self.assertEqual(resolveDeskFeatureFlags({}), {"RAM01": False, "LDR01": False})
        self.assertEqual(watchTabSpecs({}), [])
        self.assertTrue(isDeskTabEnabled("MMG01", {}))
        shown = watchTabSpecs({"deskFeatureFlags": {"RAM01": True}})
        self.assertEqual([row[0] for row in shown], ["acnt"])

    def test_backtest_without_fixture_claims_nothing(self):
        from strategies.impl.riseConfirmReplay import pending_without_fixture

        got = pending_without_fixture("20261001", "20261106")
        self.assertEqual(got["status"], "PARITY_PENDING")
        self.assertFalse(got["passed"])
        self.assertIsNone(got["summary"])

    def test_version_note_keeps_git_hash_without_db(self):
        from utils.stratVerHist import parseDocVerHist

        self.assertEqual(gitHashFromVerNote("v1 git abcdef1234567890abcd"), "abcdef1234567890")
        self.assertEqual(gitHashFromVerNote("해시 없음"), "")
        hist = parseDocVerHist((ROOT / "strategies" / "docs" / "riseConfirmScale_v1.md").read_text(encoding="utf-8"))
        self.assertEqual(hist[-1]["ver"], 2)
        self.assertIn("100000", hist[-1]["note"])
        self.assertEqual(gitHashFromVerNote(hist[-1]["note"]), "059d13f01e69ba19")
        self.assertEqual(hist[0]["ver"], 1)

    def test_desk_text_uses_spec_loss_and_not_a_hard_cap(self):
        from utils.stratDeskSummary import buildRiseConfirmDeskText

        text = buildRiseConfirmDeskText("RCS01", "riseConfirmScale_v1")
        self.assertIn("100,000원", text)
        self.assertIn("보장 상한 아님", text)
        self.assertIn("v2", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
