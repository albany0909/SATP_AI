"""MSE01 v2 — 20260827 운영 유니버스 검증 (실주문 없음)."""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import loadDayBars, loadMinBars, runBacktest
from strategies.impl.maSupportEod import MaSupportEodStrategy
from strategies.specLoader import loadStratSpec
from trading.maTouchUnivLoad import _mse01UnivOk, screenUnivFromDb
from utils.stratValRunStore import saveStratValRun
from utils.stratValidate import (
    evaluateBtGates,
    formatStaticReport,
    formatValidateReport,
    runStaticChecks,
)

_STRAT = "maSupportEod_v1"
_RANK_DT = "20260826"
_TRADE_DT = "20260827"
_CASH = 2_000_000.0
_CUT = "152000"
_STATE = Path(__file__).resolve().parents[1] / "config" / "data" / "maSupportEodState.json"


def _ohlcCut(stkCd, ymd, cutTm):
    mins = loadMinBars(stkCd, "1", ymd, ymd) or []
    if mins:
        use = [m for m in mins if str(m.get("barTm") or "").zfill(6)[:6] <= cutTm]
        use = use or mins
        return {
            "open": float(use[0]["open"]),
            "high": max(float(b["high"]) for b in use),
            "low": min(float(b["low"]) for b in use),
            "close": float(use[-1]["close"]),
            "src": f"min{len(use)}",
        }
    days = loadDayBars(stkCd, ymd, ymd) or []
    if not days:
        return None
    b = days[-1]
    return {
        "open": float(b["open"]),
        "high": float(b["high"]),
        "low": float(b["low"]),
        "close": float(b["close"]),
        "src": "day",
    }


def _eodOhlc(stkCd, ymd):
    days = loadDayBars(stkCd, ymd, ymd) or []
    if not days:
        return None
    b = days[-1]
    return {
        "open": float(b["open"]),
        "high": float(b["high"]),
        "low": float(b["low"]),
        "close": float(b["close"]),
        "src": "day",
    }


def _histBroke(stkCd, ymd, params):
    strat = MaSupportEodStrategy(params)
    start = "20260601"
    bars = loadDayBars(stkCd, start, ymd) or []
    hist = [b for b in bars if str(b.get("tradeDt")) < ymd]
    hist = strat.prepare(list(hist))
    for i in range(len(hist)):
        strat.onBar(hist, i)
    return strat, hist


def _evalBar(stkCd, ymd, ohlc, params):
    if not ohlc:
        return "NO_OHLC", None
    strat, hist = _histBroke(stkCd, ymd, params)
    if not hist:
        return "NO_HIST", None
    today = {
        "tradeDt": ymd,
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "close": ohlc["close"],
    }
    histBroke = bool(strat.brokeOut)
    bars = strat.prepare(hist + [today])
    strat.pos = None
    ev = strat.onBar(bars, len(bars) - 1) or []
    if any(e.get("type") == "BUY" for e in ev):
        reason = next(e.get("reason") for e in ev if e.get("type") == "BUY")
        return "BUY", reason
    code, note = strat.explainNoBuy(bars, len(bars) - 1, brokeOut=histBroke)
    return code or "OTHER", note


def _loadLiveScanCds():
    """8/27 실스캔 대상 (개선 전 USE_TRADE 스캔)."""
    if not _STATE.is_file():
        return []
    try:
        body = json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if str(body.get("tradeDt") or "") != _TRADE_DT:
        return []
    return sorted((body.get("brokeOut") or {}).keys())


def _classifyList(stkCds, params, label):
    reasons15 = Counter()
    reasonsEod = Counter()
    buy15 = []
    buyEod = []
    skipRows = []
    for cd in stkCds:
        c15, n15 = _evalBar(cd, _TRADE_DT, _ohlcCut(cd, _TRADE_DT, _CUT), params)
        ce, ne = _evalBar(cd, _TRADE_DT, _eodOhlc(cd, _TRADE_DT), params)
        reasons15[c15] += 1
        reasonsEod[ce] += 1
        if c15 == "BUY":
            buy15.append(f"{cd} {n15}")
        else:
            skipRows.append(f"  15:20 {cd} {c15} {n15 or ''}")
        if ce == "BUY":
            buyEod.append(f"{cd} {ne}")
    print(f"[{label} 15:20] {dict(reasons15)}")
    print("  BUY:", ", ".join(buy15) or "(없음)")
    print(f"[{label} EOD]   {dict(reasonsEod)}")
    print("  BUY:", ", ".join(buyEod) or "(없음)")
    if buy15 or buyEod:
        pass
    else:
        # 상위 스킵만 짧게 (전체는 너무 김)
        topSkip = reasons15.most_common(4)
        print("  15:20 스킵 상위:", ", ".join(f"{k} {v}" for k, v in topSkip))
    return {
        "snap1520": dict(reasons15),
        "snapEod": dict(reasonsEod),
        "buy1520": buy15,
        "buyEod": buyEod,
        "skip1520": skipRows,
    }


def main():
    spec = loadStratSpec(_STRAT) or {}
    params = spec.get("params") or {}
    static = runStaticChecks(_STRAT)
    print(formatStaticReport(_STRAT, static))
    print()

    univ = screenUnivFromDb(_RANK_DT)
    if not univ.get("ok"):
        print(f"[중단] 유니버스: {univ.get('errMsg')}")
        return
    rawPicks = univ.get("unionItems") or univ.get("picks") or []
    picks = []
    for p in rawPicks:
        cd = p.get("stkCd")
        if cd and _mse01UnivOk(cd, _RANK_DT):
            picks.append(p)
    print(
        f"[유니버스] rank16h {_RANK_DT} 합집합 {len(rawPicks)}종 "
        f"→ 52주신고가+종가>SMA20 {len(picks)}종 → BT {_TRADE_DT}"
    )
    for p in picks:
        print(f"  {p.get('stkCd')} {p.get('stkNm') or ''}")

    stkResults = []
    for p in picks:
        cd = p.get("stkCd")
        nm = p.get("stkNm") or cd
        print(f"  BT {cd} {nm} …")
        bt = runBacktest(
            _STRAT,
            cd,
            _TRADE_DT,
            _TRADE_DT,
            warmupStartDt="20260601",
            ticScope="1",
            initCash=_CASH,
            barSource="min",
        )
        if not bt.get("ok"):
            stkResults.append(
                {
                    "ok": False,
                    "stkCd": cd,
                    "stkNm": nm,
                    "errMsg": bt.get("errMsg") or "BT 실패",
                }
            )
            print(f"    fail {bt.get('errMsg')}")
        else:
            stats = bt.get("stats") or {}
            trades = bt.get("trades") or []
            stkResults.append(
                {
                    "ok": True,
                    "stkCd": cd,
                    "stkNm": nm,
                    "stats": stats,
                    "trades": trades,
                    "rounds": bt.get("rounds") or [],
                }
            )
            print(
                f"    체결 {stats.get('tradeCnt') or 0} "
                f"수익 {float(stats.get('finalRetPct') or 0):+.2f}% "
                f"MDD {float(stats.get('maxDdPct') or 0):.2f}%"
            )
            for t in trades:
                print(
                    f"      {t.get('type')} {t.get('tradeDt')} "
                    f"{float(t.get('price') or t.get('prc') or 0):,.0f} "
                    f"{t.get('reason') or ''}"
                )

    print()
    univSnap = _classifyList(
        [p.get("stkCd") for p in picks if p.get("stkCd")],
        params,
        "개선유니버스(SMA5)",
    )
    liveCds = _loadLiveScanCds()
    liveSnap = None
    if liveCds:
        print()
        print(f"[실스캔 8/27] {len(liveCds)}종에 v2 규칙 재적용")
        liveSnap = _classifyList(liveCds, params, "실스캔42")
    print()

    gateRes = evaluateBtGates(stkResults)
    label = (
        f"rank16h {_RANK_DT} SMA5돌파 {len(picks)}종 → BT {_TRADE_DT} "
        f"(매차 {int(_CASH/10000)}만원)"
    )
    report = formatValidateReport(
        _STRAT,
        spec.get("name") or _STRAT,
        static,
        gateRes,
        _TRADE_DT,
        _TRADE_DT,
        _CASH,
        label,
        stkResults=stkResults,
    )
    print(report)
    path = saveStratValRun(
        _STRAT,
        {
            "mode": "rank16h_mse01",
            "startYmd": _TRADE_DT,
            "endYmd": _TRADE_DT,
            "initCash": _CASH,
            "verdict": gateRes.get("verdict") or "FAIL",
            "static": static,
            "gates": gateRes,
            "stkCnt": len(stkResults),
            "reportText": report,
            "rankDt": _RANK_DT,
            "tradeDt": _TRADE_DT,
            "univRaw": len(rawPicks),
            "univMse": len(picks),
            "snap1520": univSnap.get("snap1520"),
            "snapEod": univSnap.get("snapEod"),
            "buy1520": univSnap.get("buy1520"),
            "buyEod": univSnap.get("buyEod"),
            "liveScan": liveSnap,
        },
    )
    print(f"\n[저장] {path}")


if __name__ == "__main__":
    main()
