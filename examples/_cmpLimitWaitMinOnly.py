# -*- coding: utf-8 -*-
"""
이평터치 지정가대기 A/B — 1분봉 있는 종목일만 (일봉 근사 제외).
막날 강제청산은 그 봉의 장중 종가다. 장마감 뒤에야 아는 신호의 당일 종가 체결은 쓰지 않는다.

A: 5분 미체결 취소만
B: 3분 미체결 → 시장가
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import (
    _MA_TOUCH_SMA_WARM_DAYS,
    buildTradeRounds,
    calcScalePnl,
    loadDayBars,
    loadMinBars,
)
from config.krHoliday import isTradingDay, nextTradingDay
from strategies.impl.maTouchScale import (
    MaTouchScaleStrategy,
    resolveBar3LimitFill,
    stampMinBarsWithDaySma,
)
from strategies.specLoader import loadStratSpec
from trading.maTouchUnivLoad import screenUnivFromDb
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd

START = "20260801"
END = datetime.now().strftime("%Y%m%d")
INIT = 1_000_000.0
# 장중 1분봉 최소 개수 (너무 적으면 일봉과 비슷한 왜곡)
_MIN_BAR_FLOOR = 60


class MaTouchLimitCancelOnly(MaTouchScaleStrategy):
    def _resolveBar3LimitOnMin(self, bar, tradeDt, cntrTm, events):
        if not bar or not self._limitWait:
            return
        barTm = self._barTm(bar)
        for step in list(self._limitWait.keys()):
            pend = self._limitWait.get(step) or {}
            fromTm = str(pend.get("fromTm") or "")
            expireTm = str(pend.get("expireTm") or "")
            if fromTm and barTm <= fromTm:
                continue
            limitPx = float(pend.get("limitPx") or 0)
            ordQty = int(pend.get("qty") or 0)
            kind, fillPx = resolveBar3LimitFill(bar, pend.get("maPx"), limitPx)
            if kind == "limit" and fillPx and fillPx > 0:
                self._limitWait.pop(int(step), None)
                ev = self._buyStep(
                    step,
                    float(fillPx),
                    tradeDt,
                    cntrTm,
                    f"ma{step}_bar3_limit",
                    ordQty=ordQty if ordQty > 0 else None,
                )
                if ev:
                    events.append(ev)
                continue
            if expireTm and barTm >= expireTm:
                self._limitWait.pop(int(step), None)


def _rankDts():
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT TRADE_DT FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT BETWEEN %s AND %s ORDER BY TRADE_DT
                """,
                (START, END),
            )
            return [r["TRADE_DT"] for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _trde(ymd, n=30):
    from trading.maTouchUnivLoad import _isKospiKosdaq, _stkMktMap

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD FROM AI_DAY_STK_PRC
                WHERE TRADE_DT=%s AND INDC_TP='0'
                ORDER BY IFNULL(AMT_MN,0) DESC LIMIT %s
                """,
                (ymd, max(n * 4, 200)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    cds, seen = [], set()
    mkt = _stkMktMap([normStkCd(r.get("STK_CD")) for r in rows])
    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if not cd or cd in seen:
            continue
        meta = mkt.get(cd) or {}
        if meta and not _isKospiKosdaq(meta.get("mrktTp"), meta.get("marketName")):
            continue
        seen.add(cd)
        cds.append(cd)
        if len(cds) >= n:
            break
    return cds


def _run(stkCd, tradeDt, stratCls, params):
    try:
        smaStart = (
            datetime.strptime(tradeDt, "%Y%m%d") - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
        ).strftime("%Y%m%d")
    except ValueError:
        smaStart = tradeDt
    dayBars = loadDayBars(stkCd, smaStart, tradeDt)
    minBars = loadMinBars(stkCd, "1", tradeDt, tradeDt)
    if not minBars or len(minBars) < _MIN_BAR_FLOOR:
        return None, "skip_no_min"
    if not dayBars:
        return None, "skip_no_day"
    tmp = MaTouchScaleStrategy(params)
    bars = stampMinBarsWithDaySma(minBars, dayBars, tmp._needPeriods())
    if not bars:
        return None, "empty"
    strat = stratCls(params)
    bars = strat.prepare(bars)
    trades = []
    for idx in range(len(bars)):
        ev = strat.onBar(bars, idx)
        if not ev:
            continue
        for e in ev if isinstance(ev, list) else [ev]:
            e["tradeDt"] = bars[idx]["tradeDt"]
            trades.append(e)
    if strat.pos is not None and bars:
        last = bars[-1]
        leftQty = int((strat.pos or {}).get("qty") or 0)
        if leftQty > 0:
            trades.append(
                {
                    "type": "SELL",
                    "price": last["close"],
                    "reason": "force_close_eod",
                    "cntrTm": last["cntrTm"],
                    "tradeDt": last["tradeDt"],
                    "qty": leftQty,
                }
            )
        strat.pos = None
    scale = calcScalePnl(trades)
    rounds = buildTradeRounds(
        [t for t in trades if t.get("type") in ("BUY", "SELL", "SELL_HALF", "ADD")]
    )
    mktAfter = sum(
        1
        for t in trades
        if t.get("type") == "BUY" and "mkt_after_limit" in str(t.get("reason") or "")
    )
    stopN = sum(
        1
        for t in trades
        if t.get("type") == "SELL" and str(t.get("reason") or "").startswith("hard_stop")
    )
    return {
        "pnl": float(scale.get("pnlWon") or 0),
        "n": len(rounds) if rounds else 0,
        "buyCnt": sum(1 for t in trades if t.get("type") == "BUY"),
        "mktAfter": mktAfter,
        "stopN": stopN,
        "minBars": len(minBars),
    }, None


def _agg(label, rows):
    pnl = sum(r["pnl"] for r in rows)
    buys = [r for r in rows if r.get("buyCnt", 0) > 0]
    wins = [r for r in buys if r["pnl"] > 0]
    loss = [r for r in buys if r["pnl"] < 0]
    return {
        "label": label,
        "stkDays": len(rows),
        "withBuy": len(buys),
        "trades": sum(r["n"] for r in rows),
        "buys": sum(r.get("buyCnt") or 0 for r in rows),
        "mktAfter": sum(r.get("mktAfter") or 0 for r in rows),
        "stops": sum(r.get("stopN") or 0 for r in rows),
        "pnl": pnl,
        "buyPnl": sum(r["pnl"] for r in buys),
        "winN": len(wins),
        "lossN": len(loss),
        "winSum": sum(r["pnl"] for r in wins),
        "lossSum": sum(r["pnl"] for r in loss),
    }


def main():
    print("시작… 1분봉≥60개 종목일만", flush=True)
    base = dict((loadStratSpec("maTouchScale_v1") or {}).get("params") or {})
    patchA = {**base, "limitWaitSec": 300, "confirmMode": "bar3BreakLimit"}
    patchB = {**base, "limitWaitSec": 180, "confirmMode": "bar3BreakLimit"}

    rowsA, rowsB = [], []
    skipNoMin = 0
    used = 0

    for rankDt in _rankDts():
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > END:
            continue
        trde = set(_trde(rankDt, 30))
        got = screenUnivFromDb(rankDt, trdePrcaCds=trde, rankMax=20)
        picks = got.get("picks") or []
        dayUsed = 0
        for p in picks:
            cd = p["stkCd"]
            nm = p.get("stkNm") or cd
            ra, ea = _run(cd, tradeDt, MaTouchLimitCancelOnly, patchA)
            if ea == "skip_no_min" or ra is None:
                skipNoMin += 1
                continue
            rb, eb = _run(cd, tradeDt, MaTouchScaleStrategy, patchB)
            if rb is None:
                skipNoMin += 1
                continue
            used += 1
            dayUsed += 1
            rowsA.append({"dt": tradeDt, "cd": cd, "nm": nm, **ra})
            rowsB.append({"dt": tradeDt, "cd": cd, "nm": nm, **rb})
        print(f"[{rankDt}→{tradeDt}] picks={len(picks)} used={dayUsed}", flush=True)

    a = _agg("A 5분취소만", rowsA)
    b = _agg("B 3분→시장가", rowsB)
    diff = b["buyPnl"] - a["buyPnl"]

    print("\n=== 왜곡제거 재집계 (1분봉만) ===", flush=True)
    print(
        f"기간 {START}~{END} · 사용 종목일 {used} · 분봉부족 스킵 {skipNoMin}",
        flush=True,
    )
    for x in (a, b):
        avgW = (x["winSum"] / x["winN"]) if x["winN"] else 0
        avgL = (x["lossSum"] / x["lossN"]) if x["lossN"] else 0
        print(
            f"{x['label']}: 매수발생 {x['withBuy']} · 매수 {x['buys']} · "
            f"시장가전환 {x['mktAfter']} · hardStop {x['stops']} · "
            f"손익 {x['buyPnl']:+,.0f}원 · +{x['winN']}/−{x['lossN']} "
            f"(평균승 {avgW:+,.0f} / 평균패 {avgL:+,.0f})",
            flush=True,
        )
    print(f"차이 B−A: {diff:+,.0f}원", flush=True)
    if a["withBuy"]:
        print(
            f"명목(매수종목일×100만) 대비 A {a['buyPnl']/(INIT*a['withBuy'])*100:+.2f}% · "
            f"B {b['buyPnl']/(INIT*b['withBuy'])*100:+.2f}%",
            flush=True,
        )

    # 일별
    byDayA = defaultdict(float)
    byDayB = defaultdict(float)
    for r in rowsA:
        if r.get("buyCnt"):
            byDayA[r["dt"]] += r["pnl"]
    for r in rowsB:
        if r.get("buyCnt"):
            byDayB[r["dt"]] += r["pnl"]
    days = sorted(set(byDayA) | set(byDayB))
    if days:
        dA = [byDayA[d] for d in days]
        dB = [byDayB[d] for d in days]
        print(
            f"일별손익 평균 A {sum(dA)/len(dA):+,.0f} · B {sum(dB)/len(dB):+,.0f}",
            flush=True,
        )

    paired = []
    for i in range(len(rowsA)):
        if rowsA[i].get("buyCnt", 0) == 0 and rowsB[i].get("buyCnt", 0) == 0:
            continue
        paired.append(
            {
                "dt": rowsA[i]["dt"],
                "nm": rowsA[i]["nm"],
                "cd": rowsA[i]["cd"],
                "a": rowsA[i]["pnl"],
                "b": rowsB[i]["pnl"],
                "d": rowsB[i]["pnl"] - rowsA[i]["pnl"],
            }
        )
    paired.sort(key=lambda x: x["d"], reverse=True)
    print("\nB유리 TOP5:", flush=True)
    for r in paired[:5]:
        print(
            f"  {r['dt']} {r['nm']}({r['cd']}) A={r['a']:+,.0f} B={r['b']:+,.0f} Δ={r['d']:+,.0f}",
            flush=True,
        )
    print("B불리 TOP5:", flush=True)
    for r in paired[-5:][::-1]:
        print(
            f"  {r['dt']} {r['nm']}({r['cd']}) A={r['a']:+,.0f} B={r['b']:+,.0f} Δ={r['d']:+,.0f}",
            flush=True,
        )

    # LIVE 참고
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) n, SUM(IFNULL(PNL_WON,0)) pnl
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='paper'
                  AND EXIT_DT BETWEEN %s AND %s
                """,
                (START, END),
            )
            live = cur.fetchone() or {}
    finally:
        conn.close()
    print(
        f"\n참고 LIVE paper: {live.get('n')}건 {float(live.get('pnl') or 0):+,.0f}원",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
