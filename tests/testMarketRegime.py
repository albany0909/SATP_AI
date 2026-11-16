"""시장 국면 배지. 주문·설정 파일·DB를 쓰지 않는다."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.marketRegime import (
    annVol,
    badgeColors,
    changeLine,
    classifySnapshot,
    detailLine,
    drawdown,
    judgeBars,
    judgmentLine,
    loadMarketRegimeOptions,
    presentRegime,
    reg01Side,
)
from utils.marketRegimeStore import recordJudgment


def _snap(**over):
    base = {
        "close": 110.0,
        "sma200": 100.0,
        "sma20": 108.0,
        "sma60": 104.0,
        "sma120": 101.0,
        "sma60Prev": 103.0,
        "adx": 25.0,
        "plusDi": 30.0,
        "minusDi": 15.0,
        "reg01": "ON",
    }
    base.update(over)
    return base


def _flat(n, px=100.0, day0=20200101):
    rows = []
    for i in range(n):
        rows.append({
            "tradeDt": str(day0 + i),
            "open": px,
            "high": px,
            "low": px,
            "close": px,
        })
    return rows


class MarketRegimeTest(unittest.TestCase):
    def test_fixture_20261116_is_bear(self):
        """매니저가 준 11/16 장중 참고값. 60일선은 5일 전보다 낮다."""
        got = classifySnapshot({
            "close": 79625,
            "sma200": 101457,
            "sma20": 86631,
            "sma60": 99493,
            "sma120": 109866,
            "sma60Prev": 101000,
            "adx": 27.8,
            "plusDi": 17.4,
            "minusDi": 31.5,
            "reg01": "OFF",
            "vol20Ann": 0.59,
            "peak": 147847,
            "peakDt": "20260622",
            "ddPct": (79625 / 147847 - 1.0) * 100.0,
        })
        self.assertEqual(got["regime"], "하락장")
        self.assertEqual(got["reg01"], "OFF")
        self.assertAlmostEqual(got["vol20Ann"], 0.59)
        self.assertEqual(round(got["ddPct"]), -46)
        self.assertIn("60일선 하락", got["reason"])
        self.assertIn("-DI>+DI", got["reason"])
        text = detailLine(got)
        self.assertIn("REG01 OFF", text)
        self.assertIn("59%", text)
        self.assertIn("-46%", text)

    def test_adx_below_20_is_sideways_even_if_sma60_falls(self):
        """ADX<20 이고 종가<200일선·60일선 하락이면 횡보다. DI만으로 하락이 되지 않는다."""
        got = classifySnapshot(_snap(
            close=90, sma200=100, sma60=80, sma60Prev=90,
            adx=19.9, plusDi=10, minusDi=40, reg01="OFF",
        ))
        self.assertEqual(got["regime"], "횡보장")
        self.assertIn("DI만으로", got["reason"])
        diOnly = classifySnapshot(_snap(
            close=90, sma200=100, sma60=90, sma60Prev=90,
            adx=15, plusDi=10, minusDi=40,
        ))
        self.assertEqual(diOnly["regime"], "횡보장")

    def test_bear_when_adx_is_strong_and_di_or_sma_falls(self):
        byDi = classifySnapshot(_snap(
            close=90, sma200=100, sma60=90, sma60Prev=90,
            adx=22, plusDi=10, minusDi=40,
        ))
        self.assertEqual(byDi["regime"], "하락장")
        bySma = classifySnapshot(_snap(
            close=90, sma200=100, sma60=80, sma60Prev=90,
            adx=22, plusDi=40, minusDi=10,
        ))
        self.assertEqual(bySma["regime"], "하락장")

    def test_bull_needs_price_and_plus_di(self):
        self.assertEqual(classifySnapshot(_snap())["regime"], "상승장")
        blocked = classifySnapshot(_snap(plusDi=10, minusDi=30))
        self.assertNotEqual(blocked["regime"], "상승장")

    def test_short_history_and_no_future(self):
        self.assertEqual(judgeBars(_flat(30))["regime"], "판정불가")
        base = _flat(220, px=100.0)
        before = judgeBars(base)
        spike = dict(base[-1])
        spike["tradeDt"] = "20200901"
        spike["close"] = 500
        spike["high"] = 500
        spike["low"] = 500
        shown = presentRegime(base + [spike], "20200901", "100000", livePx=500)
        self.assertTrue(shown["intraday"])
        self.assertEqual(shown["regime"], before["regime"])
        self.assertEqual(shown["livePx"], 500)
        self.assertNotEqual(shown["asOf"], "20200901")

    def test_reg01_side_and_options_do_not_require_settings(self):
        self.assertIsNone(reg01Side([1, 2, 3]))
        self.assertEqual(reg01Side([1.0] * 9 + [2.0]), "ON")
        self.assertEqual(reg01Side([3.0] * 9 + [1.0]), "OFF")
        opt = loadMarketRegimeOptions({})
        self.assertEqual(opt["smaSlopeBars"], 5)
        self.assertEqual(opt["adxMin"], 20.0)
        self.assertEqual(opt["symbol"], "069500")

    def test_drawdown_and_vol(self):
        bars = _flat(5, px=100)
        bars[-1]["high"] = 147847
        bars[-1]["close"] = 79625
        bars[-1]["tradeDt"] = "20260622"
        # peak is the max high seen; last close is lower only if a later bar exists
        later = dict(bars[-1])
        later["tradeDt"] = "20261116"
        later["high"] = 80000
        later["close"] = 79625
        dd = drawdown(bars + [later])
        self.assertEqual(dd["peakDt"], "20260622")
        self.assertEqual(round(dd["ddPct"]), -46)
        closes = [100.0]
        for i in range(20):
            closes.append(closes[-1] * (1.02 if i % 2 == 0 else 0.99))
        self.assertGreater(annVol(closes, 20), 0)

    def test_store_appends_jsonl_and_skips_missing_table(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marketRegime.jsonl"
            sent = []
            first = {"ok": True, "asOf": "20261113", "regime": "횡보장", "reason": "ADX", "stkCd": "069500"}
            second = {"ok": True, "asOf": "20261116", "regime": "하락장", "reason": "종가<200일선", "stkCd": "069500"}
            self.assertTrue(recordJudgment(first, path=path, sender=sent.append, useDb=False)["saved"])
            self.assertFalse(recordJudgment(first, path=path, sender=sent.append, useDb=False)["saved"])
            changed = recordJudgment(second, path=path, sender=sent.append, useDb=False)
            self.assertTrue(changed["changed"])
            self.assertEqual(sent, [changeLine("횡보장", "하락장", "20261116", "종가<200일선")])
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["regime"], "횡보장")
            self.assertIn("판정", judgmentLine("하락장", "20261116", "x"))

            def _missing(_row):
                raise Exception("1146 Table 'AI_MKT_REGIME' doesn't exist")

            with patch("utils.marketRegimeStore._insertDb", _missing):
                third = {"ok": True, "asOf": "20261117", "regime": "하락장", "reason": "유지", "stkCd": "069500"}
                recordJudgment(third, path=path, useDb=True)
            self.assertTrue(path.is_file())
            self.assertEqual(badgeColors("상승장")[0], "#C62828")
            self.assertEqual(badgeColors("하락장")[0], "#1565C0")
            self.assertEqual(badgeColors("횡보장")[0], "#757575")

    def test_badge_style_offscreen(self):
        try:
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication, QLabel
        except Exception as err:
            self.skipTest(f"PySide6 offscreen unavailable: {err}")
        app = QApplication.instance() or QApplication([])
        label = QLabel()
        bg, fg = badgeColors("하락장")
        label.setText("하락장")
        label.setStyleSheet(
            f"font-size: 22px; font-weight: 800; color: {fg}; background: {bg};"
        )
        label.resize(160, 48)
        label.show()
        app.processEvents()
        self.assertEqual(label.text(), "하락장")
        self.assertIn("#1565C0", label.styleSheet())
        self.assertIn("regimeBadge", Path("ui/tradeDeskWin.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
