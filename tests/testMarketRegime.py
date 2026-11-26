"""시장 국면 배지. 주문·설정 파일·DB를 쓰지 않는다."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime

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
    monthEndCloses,
    presentRegime,
    reg01Side,
    reg01Signal,
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
        self.assertFalse(opt["autoBackfill"])
        self.assertTrue(loadMarketRegimeOptions({"chartMarketRegime": {"autoBackfill": True}})["autoBackfill"])
        from utils.marketRegime import unjudgedLine

        self.assertEqual(
            unjudgedLine("069500", "20261116", "데이터 부족", 0),
            "[marketRegime] 판정 판정불가 20261116 데이터 부족 (069500 봉 0개)",
        )

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
        src = Path("ui/tradeDeskWin.py").read_text(encoding="utf-8")
        self.assertIn("applyNativeTitleBar", src)
        self.assertIn("self.window()", src)
        self.assertIn(".winId()", src)
        self.assertIn("regimeSettLbl", src)
        self.assertNotIn("deskHead", src)

    def test_title_bar_colors_and_dwm_log(self):
        from utils.winTitleBar import (
            DWM_COLOR_DEFAULT,
            DWMWA_CAPTION_COLOR,
            DWMWA_TEXT_COLOR,
            WIN11_MIN_BUILD,
            applyNativeTitleBar,
            colorrefFromHex,
            packDwmColor,
            titleBarColors,
            titleBarLogLine,
        )

        bear = titleBarColors("하락장")
        bull = titleBarColors("상승장")
        side = titleBarColors("횡보장")
        none = titleBarColors("판정불가")
        self.assertEqual(bear["hex"], "#BBDEFB")
        self.assertEqual(bear["caption"], colorrefFromHex("#BBDEFB"))
        self.assertEqual(bear["caption"], 0x00FBDEBB)
        self.assertEqual(bear["text"], 0)
        self.assertEqual(bull["hex"], "#FFCDD2")
        self.assertEqual(bull["caption"], colorrefFromHex("#FFCDD2"))
        self.assertEqual(side["hex"], "#FFF59D")
        self.assertEqual(side["text"], 0)
        self.assertEqual(none["caption"], DWM_COLOR_DEFAULT)
        self.assertEqual(none["text"], DWM_COLOR_DEFAULT)
        self.assertTrue(none["default"])
        self.assertEqual(titleBarColors("하락장"), bear)
        self.assertNotEqual(bear["caption"], bull["caption"])
        self.assertNotEqual(bull["caption"], side["caption"])

        _hwnd, _attr, pref, cb, buf = packDwmColor(7, DWMWA_CAPTION_COLOR, bear["caption"])
        self.assertEqual(cb, 4)
        self.assertEqual(int(buf.value) & 0xFFFFFFFF, bear["caption"])
        self.assertEqual(int(pref._obj.value) & 0xFFFFFFFF, bear["caption"])

        calls = []

        def _ok(hwnd, attr, color):
            calls.append((hwnd, attr, color))
            return 0

        with patch("utils.winTitleBar.loadDwmSetter", side_effect=AssertionError("dwmapi")):
            ok, line = applyNativeTitleBar(7, bear, setter=_ok, build=22631)
        self.assertTrue(ok)
        self.assertEqual(
            line,
            "[titleBar] 성공 하락장 build=22631 caption=#BBDEFB text=#000000",
        )
        self.assertEqual(calls[0][1], DWMWA_CAPTION_COLOR)
        self.assertEqual(calls[1][1], DWMWA_TEXT_COLOR)
        self.assertEqual(calls[0][2], bear["caption"])
        self.assertEqual(calls[1][2], 0)

        def _hr(hwnd, attr, color):
            return 0x80070057 if attr == DWMWA_CAPTION_COLOR else 0

        ok, line = applyNativeTitleBar(7, side, setter=_hr, build=22631)
        self.assertFalse(ok)
        self.assertEqual(
            line,
            "[titleBar] 실패 횡보장 build=22631 hr=0x80070057 — 커스텀 타이틀바",
        )

        skipped = []

        def _noCall(hwnd, attr, color):
            skipped.append(attr)
            return 0

        ok, line = applyNativeTitleBar(7, bear, setter=_noCall, build=19045)
        self.assertFalse(ok)
        self.assertEqual(skipped, [])
        self.assertLess(19045, WIN11_MIN_BUILD)
        self.assertEqual(
            line,
            "[titleBar] 실패 하락장 build=19045 Win10 CAPTION_COLOR 미지원 — 커스텀 타이틀바",
        )

        def _boom(hwnd, attr, color):
            raise OSError("dwm")

        ok, line = applyNativeTitleBar(7, bull, setter=_boom, build=22631)
        self.assertFalse(ok)
        self.assertIn("커스텀 타이틀바", line)
        self.assertIn("build=22631", line)
        self.assertTrue(line.startswith("[titleBar] 실패"))
        ok, line = applyNativeTitleBar(0, none, setter=_ok, build=22631)
        self.assertFalse(ok)
        self.assertIn("hwnd 없음", line)
        self.assertEqual(
            titleBarLogLine(True, titleBarColors("상승장"), build=22631),
            "[titleBar] 성공 상승장 build=22631 caption=#FFCDD2 text=#000000",
        )
        self.assertEqual(
            titleBarLogLine(True, titleBarColors("횡보장"), build=22631),
            "[titleBar] 성공 횡보장 build=22631 caption=#FFF59D text=#000000",
        )

    def test_desk_title_bar_native_then_fallback(self):
        """운영 DB·실창 없이 제목 색 경로만 본다."""
        try:
            import os

            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget
        except Exception as err:
            self.skipTest(f"PySide6 offscreen unavailable: {err}")
        app = QApplication.instance() or QApplication([])
        from ui.tradeDeskWin import TradeDeskWin

        class _Chrome(QMainWindow):
            def isVisible(self):
                return True

            def winId(self):
                return 1

            def window(self):
                return self

        win = _Chrome()
        root = QWidget()
        QVBoxLayout(root)
        win.setCentralWidget(root)
        win.regimeSettLbl = QLabel("데이터 부족")
        win.pbkBoard = QLabel("[PBK01] 후보 0건 · 보유 0/5 · 최근 재스캔 --:--")
        win._deskTitleCore = "천둥이 TEST · v0"
        win._regimeView = {"regime": "판정불가", "ok": False}
        win._titleBarColorKey = None
        win._paintRegimeChrome = TradeDeskWin._paintRegimeChrome.__get__(win, _Chrome)
        win._applyRegimeBadge = TradeDeskWin._applyRegimeBadge.__get__(win, _Chrome)
        win._showCustomTitleBar = TradeDeskWin._showCustomTitleBar.__get__(win, _Chrome)
        win._hideCustomTitleBar = TradeDeskWin._hideCustomTitleBar.__get__(win, _Chrome)
        win._ensureCustomTitleBar = TradeDeskWin._ensureCustomTitleBar.__get__(win, _Chrome)

        success = (True, "[titleBar] 성공 판정불가 build=22631 caption=default text=default")
        with patch("utils.winTitleBar.applyNativeTitleBar", return_value=success) as called:
            win._applyRegimeBadge({"regime": "판정불가", "ok": False})
            app.processEvents()
        called.assert_called()
        self.assertEqual(win.windowTitle(), "천둥이 TEST · v0 · 판정불가")
        bar = getattr(win, "customTitleBar", None)
        self.assertTrue(bar is None or bar.isHidden())
        fail = (False, "[titleBar] 실패 하락장 build=22631 hr=0x80070057 — 커스텀 타이틀바")
        with patch("utils.winTitleBar.applyNativeTitleBar", return_value=fail):
            win._applyRegimeBadge({
                "ok": True,
                "regime": "하락장",
                "reason": "종가<200일선",
                "reg01": "OFF",
            })
            app.processEvents()
        self.assertEqual(win.windowTitle(), "천둥이 TEST · v0 · 하락장")
        self.assertFalse(win.customTitleBar.isHidden())
        self.assertIn("#BBDEFB", win.customTitleBar.styleSheet())
        self.assertIn("#000000", win.customTitleLbl.styleSheet())
        self.assertIn("종가<200일선", win.regimeSettLbl.text())
        self.assertIn("REG01 OFF", win.regimeSettLbl.text())
        self.assertTrue(win.pbkBoard.text().startswith("[PBK01]"))
        win.deleteLater()
        app.processEvents()

    def test_regime_symbol_insert_only_and_backfill_gate(self):
        from utils.dayStkPrcStore import _INSERT_IGNORE_SQL
        from utils.watchDayPrcLoad import maybeBackfillRegimeBars

        self.assertIn("INSERT IGNORE", _INSERT_IGNORE_SQL)
        self.assertNotIn("ON DUPLICATE", _INSERT_IGNORE_SQL)
        maybeBackfillRegimeBars.done = False
        calls = []

        def _loader(codes, months=12, insertOnly=False, refetchLastDay=True, **_kw):
            calls.append((list(codes), months, insertOnly, refetchLastDay))
            return {"ok": True}

        off = maybeBackfillRegimeBars(
            counter=lambda _cd: 0,
            loader=_loader,
            options={"symbol": "069500", "autoBackfill": False},
        )
        self.assertEqual(off["skipped"], "autoBackfill")
        self.assertEqual(calls, [])
        maybeBackfillRegimeBars.done = False
        on = maybeBackfillRegimeBars(
            counter=lambda _cd: 12,
            loader=_loader,
            options={"symbol": "069500", "autoBackfill": True},
        )
        self.assertEqual(calls, [(["069500"], 12, True, False)])
        self.assertTrue(on["ok"])
        maybeBackfillRegimeBars.done = False
        enough = maybeBackfillRegimeBars(
            counter=lambda _cd: 200,
            loader=_loader,
            options={"symbol": "069500", "autoBackfill": True},
        )
        self.assertEqual(enough["skipped"], "enough")
        self.assertEqual(len(calls), 1)

    def test_day_price_retries_http_429(self):
        from unittest.mock import patch

        from api.getDayStkPrc import fetchDayStkPrcRange

        class _Res:
            def __init__(self, code, body, headers=None):
                self.status_code = code
                self._body = body
                self.headers = headers or {}
                self.text = ""

            def json(self):
                return self._body

        calls = {"n": 0}

        def _fake(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Res(429, {"return_code": 1, "return_msg": "too many"})
            return _Res(200, {
                "return_code": 0,
                "daly_stkpc": [{
                    "date": "20261114",
                    "open_pric": "100",
                    "high_pric": "110",
                    "low_pric": "90",
                    "close_pric": "105",
                }],
            }, {"cont-yn": "N"})

        with patch("api.getDayStkPrc.getDayStkPrc", _fake), patch("utils.chartUnifFetch.time.sleep", lambda _s: None):
            got = fetchDayStkPrcRange("http://h", "t", "069500", "20261114", "20261114", isPaper=True)
        self.assertTrue(got["ok"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(got["rows"][0]["TRADE_DT"], "20261114")

    def test_reg01_oct_2026_month_end_is_off(self):
        """2026-10-30 종가 5730.74 는 그 월말을 포함한 10개월 평균 6605.67 보다 작다."""
        months = [
            ("20260130", 6700.00),
            ("20260227", 6700.00),
            ("20260331", 6700.00),
            ("20260430", 6700.00),
            ("20260529", 6700.00),
            ("20260630", 6700.00),
            ("20260731", 6700.00),
            ("20260831", 6700.00),
            ("20260930", 6725.96),
            ("20261029", 5800.00),
            ("20261030", 5730.74),
            ("20261116", 9000.00),
            ("20261231", 1.00),
        ]
        bars = [{"tradeDt": ymd, "close": px} for ymd, px in months]
        closes = monthEndCloses(bars, "20261116")
        self.assertEqual(len(closes), 10)
        self.assertAlmostEqual(closes[-1], 5730.74)
        self.assertAlmostEqual(round(sum(closes) / 10.0, 2), 6605.67)
        self.assertEqual(reg01Signal(bars, "20261116"), "OFF")
        self.assertIsNone(reg01Signal(bars, "20261030"))
        self.assertEqual(reg01Signal(bars + [{"tradeDt": "20261117", "close": 1}], "20261116"), "OFF")

        kodex = _flat(40)
        kodex[-2]["tradeDt"] = "20261113"
        kodex[-1]["tradeDt"] = "20261116"
        view = presentRegime(kodex, "20261116", "100000", kospiBars=bars)
        self.assertEqual(view["asOf"], "20261113")
        self.assertEqual(view["reg01"], "OFF")
        self.assertEqual(detailLine(view), "데이터 부족 · REG01 OFF")
        kept = presentRegime(kodex, "20261116", "160000", kospiBars=bars, reg01="ON")
        self.assertEqual(kept["reg01"], "ON")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marketRegime.jsonl"
            first = {
                "ok": True, "asOf": "20261116", "regime": "하락장",
                "reason": "종가<200일선", "stkCd": "069500", "reg01": "OFF",
            }
            recordJudgment(first, path=path, useDb=False)
            recordJudgment({**first, "reg01": "ON"}, path=path, useDb=False)
            saved = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(saved["reg01"], "OFF")

    def test_kospi_inserts_missing_days_only(self):
        from utils.dayIndsPrcStore import _INSERT_IGNORE_SQL
        from utils.kospiIdxLoad import appendKospiIdxToday, appendKospiJsonl, loadKospiIdxDays, loadStoredKospiBars

        self.assertIn("INSERT IGNORE", _INSERT_IGNORE_SQL)
        self.assertNotIn("ON DUPLICATE", _INSERT_IGNORE_SQL)
        self.assertNotIn("UPDATE", _INSERT_IGNORE_SQL)
        self.assertNotIn("DELETE", _INSERT_IGNORE_SQL)
        desk = Path("ui/tradeDeskWin.py").read_text(encoding="utf-8")
        watch = Path("utils/watchDayPrcLoad.py").read_text(encoding="utf-8")
        self.assertNotIn("loadKospiIdxDays", desk)
        self.assertNotIn("loadKospiIdxDays", watch)
        self.assertIn("appendKospiIdxToday", watch)

        saved = []
        calls = []

        def _fetch(host, token, code, start, end, **_kw):
            calls.append((code, start, end))
            return {"ok": True, "rows": [
                {"TRADE_DT": "20261030", "CLOSE_PRC": 5730.74, "INDS_CD": "001", "MKT_TP": "KR"},
                {"TRADE_DT": "20261113", "CLOSE_PRC": 5800, "INDS_CD": "001", "MKT_TP": "KR"},
                {"TRADE_DT": "20261116", "CLOSE_PRC": 5900, "INDS_CD": "001", "MKT_TP": "KR"},
            ]}

        def _write(rows):
            saved.extend(rows)
            return {"ok": True, "upsertCnt": len(rows), "store": "AI_DAY_INDS_PRC"}

        now = datetime(2026, 11, 16, 10, 0)
        early = appendKospiIdxToday(
            "20261116", now=now, session=("paper", "http://h", "t", "bearer"),
            fetcher=_fetch, writer=_write, existing=lambda _a, _b: set(),
        )
        self.assertEqual(early["skipped"], "beforeClose")
        self.assertEqual(calls, [])

        got = loadKospiIdxDays(
            months=24, endDt="20261116", now=now,
            session=("paper", "http://h", "t", "bearer"),
            fetcher=_fetch, writer=_write, existing=lambda _a, _b: {"20261030"},
        )
        self.assertTrue(got["ok"])
        self.assertEqual(calls[0][0], "001")
        self.assertEqual([row["TRADE_DT"] for row in saved], ["20261113"])
        self.assertEqual(got["srcTr"], "ka20006")

        saved.clear()
        calls.clear()
        today = appendKospiIdxToday(
            "20261116", now=datetime(2026, 11, 16, 16, 0),
            session=("paper", "http://h", "t", "bearer"),
            fetcher=_fetch, writer=_write, existing=lambda _a, _b: {"20261113"},
        )
        self.assertEqual(calls[0][1], calls[0][2])
        self.assertEqual(calls[0][1], "20261116")
        self.assertEqual([row["TRADE_DT"] for row in saved], ["20261116"])
        self.assertTrue(today["ok"])

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "kospiDay.jsonl"
            path.write_text('{"tradeDt": "20261030", "close": 5730.74}\n', encoding="utf-8")
            added = appendKospiJsonl([
                {"TRADE_DT": "20261030", "CLOSE_PRC": 1, "INDS_CD": "001"},
                {"TRADE_DT": "20261114", "CLOSE_PRC": 5800, "INDS_CD": "001"},
            ], path)
            self.assertEqual(added, 1)
            text = path.read_text(encoding="utf-8")
            self.assertIn("5730.74", text)
            self.assertNotIn('"close": 1', text)
            self.assertEqual(appendKospiJsonl([
                {"TRADE_DT": "20261114", "CLOSE_PRC": 9999, "INDS_CD": "001"},
            ], path), 0)
            self.assertEqual(text, path.read_text(encoding="utf-8"))
            with patch(
                "utils.dayIndsPrcStore.loadKospiDayBars",
                return_value={"ok": False, "missingTable": True, "bars": []},
            ):
                stored = loadStoredKospiBars("20261116", path=path)
            self.assertEqual(stored[-1]["tradeDt"], "20261114")
            self.assertAlmostEqual(stored[0]["close"], 5730.74)

    def test_inds_day_retries_http_429(self):
        from api.getDayIndsPrc import fetchDayIndsPrcRange

        class _Res:
            def __init__(self, code, body, headers=None):
                self.status_code = code
                self._body = body
                self.headers = headers or {}
                self.text = ""

            def json(self):
                return self._body

        calls = {"n": 0}

        def _fake(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Res(429, {"return_code": 1, "return_msg": "too many"})
            return _Res(200, {
                "return_code": 0,
                "inds_dt_pole_qry": [{
                    "dt": "20261114",
                    "open_pric": "573074",
                    "high_pric": "573074",
                    "low_pric": "573074",
                    "cur_prc": "573074",
                }],
            }, {"cont-yn": "N"})

        with patch("api.getDayIndsPrc.getDayIndsPrc", _fake), patch("utils.chartUnifFetch.time.sleep", lambda _s: None):
            got = fetchDayIndsPrcRange("http://h", "t", "001", "20261114", "20261114", isPaper=True)
        self.assertTrue(got["ok"], got)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(got["rows"][0]["TRADE_DT"], "20261114")
        self.assertAlmostEqual(got["rows"][0]["CLOSE_PRC"], 5730.74)

    def test_watch_load_appends_kospi_today_only(self):
        with patch("utils.watchDayPrcLoad.collectWatchHoldCds", return_value=[]), patch(
            "utils.chartBarPlan.collectSurgeChartCds", return_value=[],
        ), patch(
            "utils.marketRegime.loadMarketRegimeOptions", return_value={"symbol": ""},
        ), patch(
            "utils.kospiIdxLoad.appendKospiIdxToday", return_value={"ok": True, "upsertCnt": 0},
        ) as app:
            from utils.watchDayPrcLoad import loadDayPrcForWatchHolds

            got = loadDayPrcForWatchHolds(endDt="20261113")
        self.assertEqual(got["summary"], "[관심일봉] 대상 없음")
        self.assertEqual(app.call_args[0][0], "20261113")


if __name__ == "__main__":
    unittest.main(verbosity=2)
