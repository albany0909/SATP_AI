"""PBK01 실적 장대양봉 눌림. 주문·DB·실호스트를 쓰지 않는다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deployPackUtil import shouldSkipRel
from strategies.impl.pullbackTwoStep import (
    PaperOnlyError,
    allocateSlotFills,
    disclosureAccepted,
    exitOn,
    findCandleEvent,
    floorQty,
    formatCandidateLog,
    inBuyWindow,
    planTouches,
    stopTriggered,
    submitPbkOrder,
)
from utils.pbk01Store import appendRow, readRows


def _day(i):
    month = i // 28 + 1
    day = i % 28 + 1
    return f"2026{month:02d}{day:02d}"


def _flat(n=40, close=1000.0, vol=100.0, amount=400_000_000.0):
    rows = []
    for i in range(n):
        rows.append({
            "tradeDt": _day(i),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": vol,
            "amount": amount,
        })
    return rows


class Pbk01Test(unittest.TestCase):
    def test_disclosure_filter(self):
        self.assertTrue(disclosureAccepted("영업(잠정)실적"))
        self.assertTrue(disclosureAccepted("연결 잠정실적 공시"))
        self.assertTrue(disclosureAccepted("손익구조 변경"))
        self.assertFalse(disclosureAccepted("영업실적 전망"))
        self.assertFalse(disclosureAccepted("잠정실적 예고"))
        self.assertFalse(disclosureAccepted("영업실적 정정신고"))
        self.assertFalse(disclosureAccepted("유상증자"))
        self.assertFalse(disclosureAccepted("최대주주변경"))
        self.assertFalse(disclosureAccepted("단일판매"))

    def test_candle_rules_and_one_event(self):
        bars = _flat()
        bars[25]["open"] = 1000
        bars[25]["close"] = 1200
        bars[25]["high"] = 1200
        bars[25]["volume"] = 400
        event = findCandleEvent(bars, [{"title": "영업(잠정)실적", "date": bars[20]["tradeDt"]}], "005930")
        self.assertEqual(event["signalDt"], bars[25]["tradeDt"])
        self.assertFalse(inBuyWindow(event["signalIdx"], event["signalIdx"]))
        self.assertTrue(inBuyWindow(event["signalIdx"], event["signalIdx"] + 1))
        self.assertTrue(inBuyWindow(event["signalIdx"], event["signalIdx"] + 40))
        self.assertFalse(inBuyWindow(event["signalIdx"], event["signalIdx"] + 41))

        late = _flat()
        late[25]["open"] = 1000
        late[25]["close"] = 1200
        late[25]["volume"] = 400
        self.assertIsNone(findCandleEvent(
            late, [{"title": "영업실적", "date": late[5]["tradeDt"]}], "005930",
        ))
        weak = _flat()
        weak[25]["open"] = 1000
        weak[25]["close"] = 1090
        weak[25]["volume"] = 1000
        self.assertIsNone(findCandleEvent(weak, [{"title": "잠정실적", "date": weak[25]["tradeDt"]}], "005930"))
        bear = _flat()
        bear[25]["open"] = 1300
        bear[25]["close"] = 1200
        bear[25]["volume"] = 1000
        self.assertIsNone(findCandleEvent(bear, [{"title": "손익구조", "date": bear[25]["tradeDt"]}], "005930"))
        thin = _flat()
        thin[25]["open"] = 1000
        thin[25]["close"] = 1200
        thin[25]["volume"] = 299
        self.assertIsNone(findCandleEvent(thin, [{"title": "영업실적", "date": thin[25]["tradeDt"]}], "005930"))
        poor = _flat(amount=100_000_000)
        poor[25]["open"] = 1000
        poor[25]["close"] = 1200
        poor[25]["volume"] = 1000
        self.assertIsNone(findCandleEvent(poor, [{"title": "영업실적", "date": poor[25]["tradeDt"]}], "005930"))
        halted = _flat()
        halted[24]["open"] = 1000
        halted[24]["close"] = 1200
        halted[24]["volume"] = 100
        halted[24]["halted"] = True
        halted[25]["open"] = 1200
        halted[25]["close"] = 1400
        halted[25]["volume"] = 400
        got = findCandleEvent(halted, [{"title": "영업실적", "date": halted[20]["tradeDt"]}], "005930")
        self.assertEqual(got["signalDt"], halted[25]["tradeDt"])
        future = findCandleEvent(
            bars, [{"title": "영업(잠정)실적", "date": bars[20]["tradeDt"]}], "005930", asOf=bars[24]["tradeDt"],
        )
        self.assertIsNone(future)

    def test_two_legs_fill_and_f10_exit(self):
        bars = _flat(30, close=100)
        bars[20]["open"] = 90
        bars[20]["low"] = 80
        bars[20]["close"] = 95
        touch = planTouches(bars, 20)
        self.assertAlmostEqual(touch["ma5"], 100)
        self.assertAlmostEqual(touch["ma20"], 100)
        self.assertAlmostEqual(touch["leg1"]["fillPx"], 90)
        self.assertAlmostEqual(touch["leg2"]["fillPx"], 90)
        self.assertEqual(touch["leg1"]["qty"], floorQty(3_600_000, 90))
        self.assertEqual(touch["leg2"]["qty"], floorQty(2_400_000, 90))
        self.assertEqual(touch["leg1"]["qty"], 40000)
        self.assertEqual(touch["leg2"]["qty"], 26666)
        gap = _flat(30, close=100)
        gap[20]["open"] = 70
        gap[20]["low"] = 60
        gapFill = planTouches(gap, 20)
        self.assertAlmostEqual(gapFill["leg1"]["fillPx"], 70)
        days = [_day(i) for i in range(15)]
        self.assertEqual(exitOn(days, 0), days[10])
        self.assertNotEqual(exitOn(days, 0), days[11])

    def test_slots_skip_new_names_when_full(self):
        held = [{"stkCd": f"00000{i}", "signalDt": "20260101", "hasLeg1": True, "hitLeg2": i == 0} for i in range(5)]
        newbie = {"stkCd": "999999", "signalDt": "20260102", "hitLeg1": True, "hitLeg2": True}
        older = {"stkCd": "111111", "signalDt": "20251201", "hitLeg1": True}
        got = allocateSlotFills(held + [newbie, older], slotLimit=5)
        byCode = {row["stkCd"]: row for row in got}
        self.assertFalse(byCode["999999"]["taken"])
        self.assertEqual(byCode["999999"]["reason"], "NO_SLOT")
        self.assertTrue(byCode["000000"]["leg2"])
        self.assertFalse(byCode["111111"]["taken"])
        oneFree = allocateSlotFills(
            [
                {"stkCd": "000001", "signalDt": "20260102", "hitLeg1": True},
                {"stkCd": "000002", "signalDt": "20260101", "hitLeg1": True},
            ],
            slotLimit=1,
        )
        self.assertEqual([row["stkCd"] for row in oneFree if row["taken"]], ["000002"])

    def test_stop_only_at_1515(self):
        self.assertTrue(stopTriggered("151500", 8000, 10000))
        self.assertFalse(stopTriggered("151500", 8100, 10000))
        self.assertFalse(stopTriggered("100000", 7500, 10000))
        self.assertTrue(stopTriggered("151530", 8000, 10000))

    def test_paper_only_refuses_real_path(self):
        called = []

        def _broker(action, session):
            called.append(action)
            return {"ok": True, "sent": True}

        with self.assertRaises(PaperOnlyError):
            submitPbkOrder({"side": "BUY", "qty": 1}, {"mode": "paper", "host": "https://mockapi.kiwoom.com"}, _broker, paperOnly=False)
        self.assertEqual(called, [])
        with self.assertRaises(PaperOnlyError):
            submitPbkOrder({"side": "BUY", "qty": 1}, {"mode": "real", "host": "https://mockapi.kiwoom.com"}, _broker, paperOnly=True)
        self.assertEqual(called, [])
        with patch("config.config.paper_host_url", "https://mockapi.kiwoom.com"), patch(
            "config.config.real_host_url", "https://api.kiwoom.com",
        ):
            got = submitPbkOrder(
                {"side": "BUY", "qty": 1},
                {"mode": "paper", "host": "https://mockapi.kiwoom.com"},
                _broker,
                paperOnly=True,
            )
        self.assertTrue(got["sent"])
        self.assertEqual(len(called), 1)

    def test_state_jsonl_appends_without_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            path.write_text('{"kind":"state","candidates":[{"stkCd":"005930"}]}\n', encoding="utf-8")
            appendRow(path, {"kind": "state", "candidates": []})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["candidates"][0]["stkCd"], "005930")
            self.assertEqual(readRows(path)[0]["candidates"][0]["stkCd"], "005930")

    def test_registered_disabled_and_log_line(self):
        from strategies.specLoader import loadStratSpec
        from utils.algMgr import algMgrRows

        spec = loadStratSpec("pullbackTwoStep_v1")
        self.assertEqual(spec["algCd"], "PBK01")
        self.assertEqual(spec["version"], 1)
        self.assertFalse(spec["enabled"])
        self.assertTrue(spec["params"]["paperOnly"])
        text = formatCandidateLog([{
            "stkCd": "005930", "ma5": 1000, "ma20": 900, "expireDt": "20260301",
        }])
        self.assertTrue(text.startswith("[PBK01] 후보 1건"))
        self.assertIn("MA5 1000", text)
        self.assertIn("만료 20260301", text)
        rows = algMgrRows(
            [spec],
            {"stratLive": {"PBK01": {"enabled": False, "paperOnly": True}}},
            holds={},
            runs={},
        )
        self.assertEqual(rows[0]["algCd"], "PBK01")
        self.assertFalse(rows[0]["enabled"])
        self.assertFalse(rows[0]["specGate"])
        from trading.deskLiveAlgs import effectiveAlgEnabled
        from trading.pbk01Live import isPbkEnabled

        on = {"stratLive": {"PBK01": {"enabled": True, "paperOnly": True}}}
        self.assertTrue(effectiveAlgEnabled("PBK01", spec, on))
        with patch("config.settingsStore.loadSettings", return_value=on):
            self.assertTrue(isPbkEnabled())
        off = {"stratLive": {"PBK01": {"enabled": False, "paperOnly": True}}}
        self.assertFalse(effectiveAlgEnabled("PBK01", spec, off))

    def test_startup_scan_announces_once_and_touch_uses_candidates(self):
        from datetime import datetime

        import trading.pbk01Live as live

        bars = _flat()
        bars[25]["open"] = 1000
        bars[25]["close"] = 1200
        bars[25]["high"] = 1200
        bars[25]["volume"] = 400
        day = bars[26]["tradeDt"]
        sent = []

        def _loader(_ymd):
            return {
                "bars": {"005930": bars},
                "disclosures": [{"title": "영업(잠정)실적", "date": bars[20]["tradeDt"]}],
                "positions": [],
                "quotes": {},
            }

        # disclosures must be keyed by code
        def _loader2(_ymd):
            pack = _loader(_ymd)
            pack["disclosures"] = {"005930": pack["disclosures"]}
            return pack

        live._announcedYmd = ""
        live._startupDone = False
        live._scanYmd = ""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pbk01_state.jsonl"
            now = datetime.strptime(day, "%Y%m%d").replace(hour=16)
            first = live.runPbkCycle(
                now=now,
                loader=_loader2,
                sender=sent.append,
                log=lambda _t: None,
                path=path,
                quotes={"005930": {"price": 900}, "999999": {"price": 1}},
                force=True,
                enabled=True,
            )
            self.assertEqual(len(sent), 1)
            self.assertIn("[PBK01] 후보", sent[0])
            self.assertIn("005930", sent[0])
            self.assertEqual([row["stkCd"] for row in first["touches"]], ["005930"])
            self.assertNotIn("999999", [row["stkCd"] for row in first["touches"]])
            board = live.deskBoardText(path)
            self.assertIn("후보", board)
            self.assertIn("005930", board)
            again = live.runPbkCycle(
                now=now,
                loader=_loader2,
                sender=sent.append,
                log=lambda _t: None,
                path=path,
                quotes={"005930": {"price": 900}},
                force=True,
                enabled=True,
            )
            self.assertEqual(len(sent), 1)
            self.assertEqual(again["touches"][0]["stkCd"], "005930")
        live._announcedYmd = ""
        live._startupDone = False
        live._scanYmd = ""
        calls = []

        def _empty(_ymd):
            calls.append(_ymd)
            return {"bars": {}, "disclosures": {}, "positions": []}

        with tempfile.TemporaryDirectory() as folder2:
            early = Path(folder2) / "pbk01_state.jsonl"
            with patch.object(live, "scanHm", lambda: "084000"):
                live.runPbkCycle(
                    now=datetime(2026, 11, 19, 7, 0),
                    loader=_empty,
                    sender=lambda _t: None,
                    log=lambda _t: None,
                    path=early,
                    force=True,
                    enabled=False,
                )
                live.runPbkCycle(
                    now=datetime(2026, 11, 19, 8, 40),
                    loader=_empty,
                    sender=lambda _t: None,
                    log=lambda _t: None,
                    path=early,
                    force=False,
                    enabled=False,
                )
        self.assertEqual(len(calls), 2)
        live._startupDone = False
        live._scanYmd = ""
        self.assertIn("pbkBoard", Path("ui/tradeDeskWin.py").read_text(encoding="utf-8"))
        self.assertIn("v1", Path("strategies/docs/pullbackTwoStep_v1.md").read_text(encoding="utf-8"))

    def test_real_boot_blocked_and_universe_rules(self):
        from utils.dartSecret import maskDartSecret
        from utils.pbkUniverseLoad import estimateDayTr, liquidCodes, planDayFetch
        from utils.realBootGuard import decideBootMode

        blocked = decideBootMode("real", pbkEnabled=True, settingsRecovered=False)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["mode"], "paper")
        recovered = decideBootMode("real", pbkEnabled=False, settingsRecovered=True)
        self.assertTrue(recovered["blocked"])
        self.assertEqual(decideBootMode("paper", pbkEnabled=True)["blocked"], False)
        self.assertEqual(decideBootMode("real", pbkEnabled=False, settingsRecovered=False)["mode"], "real")
        secret = "SECRETKEY99"
        leaked = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={secret}&page_no=1"
        masked = maskDartSecret(leaked, secret)
        self.assertNotIn(secret, masked)
        self.assertIn("crtfc_key=***", masked)
        from research.dartDiscFetch import fetchMarketDisclosures

        def _boom(_url, params, _timeout=25):
            raise RuntimeError("boom crtfc_key=" + params["crtfc_key"])

        with patch("research.dartDiscFetch.dart_api_key", secret):
            hidden = fetchMarketDisclosures("20261118", "20261119", getter=_boom)
        self.assertNotIn(secret, hidden.get("errMsg") or "")
        self.assertNotIn("crtfc_key=" + secret, json.dumps(hidden))
        fresh = planDayFetch({"cnt": 0, "maxDt": ""}, "20261119")
        self.assertEqual(fresh["kind"], "backfill")
        done = planDayFetch({"cnt": 80, "maxDt": "20261119"}, "20261119")
        self.assertIsNone(done)
        gap = planDayFetch({"cnt": 80, "maxDt": "20261118"}, "20261119")
        self.assertEqual(gap["kind"], "increment")
        self.assertEqual(gap["start"], "20261119")
        names = liquidCodes(
            [{"stkCd": "005930", "tradeDt": f"202611{i:02d}", "amtMn": 300} for i in range(1, 21)]
            + [{"stkCd": "000660", "tradeDt": f"202611{i:02d}", "amtMn": 10} for i in range(1, 21)]
        )
        self.assertEqual(names, ["005930"])
        est = estimateDayTr(2500)
        self.assertEqual(est["calls"], 2500)
        self.assertGreater(est["seconds"], 2400)
        from utils.pbkUniverseLoad import isCommonStock, loadMissingDayBars, planDartRange

        window = planDartRange("", "", "20261119", 120)
        self.assertLessEqual(window["start"], "20261028")
        self.assertGreaterEqual(window["end"], "20261103")
        self.assertTrue(disclosureAccepted("영업(잠정)실적"))
        filled = planDartRange("20260722", "20261118", "20261119", 120)
        self.assertEqual(filled["kind"], "increment")
        self.assertEqual(filled["start"], "20261119")
        self.assertTrue(isCommonStock("005930", "삼성전자", "거래소", "0"))
        self.assertFalse(isCommonStock("069500", "KODEX 200", "거래소", "0"))
        self.assertFalse(isCommonStock("000995", "동부건설우", "거래소", "0"))
        self.assertFalse(isCommonStock("012340", "스팩합병", "코스닥", "10"))
        self.assertFalse(isCommonStock("357120", "코람코에너지리츠", "거래소", "0"))
        calls = []
        skipped = loadMissingDayBars(
            ["005930"],
            "20261119",
            lambda _cd: {"cnt": 80, "maxDt": "20261119"},
            lambda *_a: calls.append(_a),
            lambda _rows: {"ok": True},
        )
        self.assertEqual(calls, [])
        self.assertEqual(skipped["skipCnt"], 1)
        self.assertTrue(shouldSkipRel("config/data/_bak/settings.json.20261119"))
        self.assertTrue(shouldSkipRel("config/data/settings.json"))
        self.assertTrue(shouldSkipRel("config/data/pbk01_state.jsonl"))
        self.assertFalse(shouldSkipRel("config/data/settings-default.json"))
        default = Path("config/data/settings-default.json").read_text(encoding="utf-8")
        self.assertIn('"deskTradeMode": "paper"', default)


if __name__ == "__main__":
    unittest.main(verbosity=2)
