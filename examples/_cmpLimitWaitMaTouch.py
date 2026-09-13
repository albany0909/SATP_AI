# -*- coding: utf-8 -*-
"""
이평터치 지정가 대기 A/B 비교 BT (실주문 없음).

A(이전 v35): limitWaitSec=300, 미체결 시 취소만 (시장가 전환 없음)
B(현재 v43): limitWaitSec=180, 미체결 시 취소 후 종가 시장가

유니버스: 조회순위 TOP20 ∪ 대금(AMT_MN) TOP30 — 현재 운영과 동일
기간: rankDt 20260801~직전, 매매일=nextTradingDay(rankDt)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

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

STRAT = "maTouchScale_v1"
START = "20260801"
END = datetime.now().strftime("%Y%m%d")
INIT_CASH = 1_000_000.0
TRDE_TOP = 30


class MaTouchLimitCancelOnly(MaTouchScaleStrategy):
    """v35: 지정가 만료 시 취소만 (시장가 전환 없음)."""

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


def _listRankDts(startYmd, endYmd):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT TRADE_DT
                FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT
                """,
                (startYmd, endYmd),
            )
            return [r["TRADE_DT"] for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def _trdeTopCds(ymd, topN):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, AMT_MN
                FROM AI_DAY_STK_PRC
                WHERE TRADE_DT=%s AND INDC_TP='0'
                ORDER BY IFNULL(AMT_MN,0) DESC
                LIMIT %s
                """,
                (ymd, max(topN * 4, 200)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    from trading.maTouchUnivLoad import _isKospiKosdaq, _stkMktMap

    cds = []
    seen = set()
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
        if len(cds) >= topN:
            break
    return cds


def _picks(rankDt):
    trde = _trdeTopCds(rankDt, TRDE_TOP)
    got = screenUnivFromDb(rankDt, trdePrcaCds=set(trde), rankMax=20)
    if not got.get("ok"):
        return [], got.get("errMsg")
    return got.get("picks") or [], None


def _runOne(stkCd, tradeDt, stratCls, paramPatch, cache):
    """종목·일·모드별 BT. 캐시 키에 모드 포함."""
    mode = paramPatch.get("_mode") or "x"
    key = (stkCd, tradeDt, mode)
    if key in cache:
        return dict(cache[key])

    spec = loadStratSpec(STRAT) or {}
    params = dict(spec.get("params") or {})
    params.update({k: v for k, v in paramPatch.items() if not str(k).startswith("_")})

    try:
        smaStart = (
            datetime.strptime(str(tradeDt), "%Y%m%d")
            - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
        ).strftime("%Y%m%d")
    except ValueError:
        smaStart = tradeDt

    dayBars = loadDayBars(stkCd, smaStart, tradeDt)
    minBars = loadMinBars(stkCd, "1", tradeDt, tradeDt)
    if not dayBars and not minBars:
        r = {"ok": False, "pnl": 0, "n": 0, "mktAfter": 0, "errMsg": "no bars"}
        cache[key] = r
        return r

    tmp = MaTouchScaleStrategy(params)
    if minBars:
        bars = stampMinBarsWithDaySma(minBars, dayBars, tmp._needPeriods())
    else:
        bars = dayBars

    if not bars:
        r = {"ok": False, "pnl": 0, "n": 0, "mktAfter": 0, "errMsg": "empty bars"}
        cache[key] = r
        return r

    strat = stratCls(params)
    bars = strat.prepare(bars)
    trades = []
    for idx in range(len(bars)):
        ev = strat.onBar(bars, idx)
        if not ev:
            continue
        if isinstance(ev, list):
            for e in ev:
                e["tradeDt"] = bars[idx]["tradeDt"]
                trades.append(e)
        else:
            ev["tradeDt"] = bars[idx]["tradeDt"]
            trades.append(ev)

    if strat.pos is not None and bars:
        last = bars[-1]
        leftQty = int((strat.pos or {}).get("qty") or 0)
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

    filtered = []
    i = 0
    while i < len(trades):
        t = trades[i]
        if t["type"] == "BUY" and tradeDt <= t.get("tradeDt", "") <= tradeDt:
            j = i + 1
            while j < len(trades) and trades[j]["type"] not in ("BUY", "SELL"):
                j += 1
            if j < len(trades) and trades[j]["type"] == "SELL":
                filtered.extend(trades[i : j + 1])
                i = j + 1
                continue
            filtered.append(t)
        i += 1

    scale = calcScalePnl(trades)
    rounds = buildTradeRounds(filtered)
    mktAfter = sum(
        1
        for t in trades
        if t.get("type") == "BUY"
        and "mkt_after_limit" in str(t.get("reason") or "")
    )
    r = {
        "ok": True,
        "pnl": float(scale.get("pnlWon") or 0),
        "n": len(rounds) if rounds else int(len([t for t in trades if t.get("type") == "BUY"])),
        "mktAfter": mktAfter,
        "buyCnt": sum(1 for t in trades if t.get("type") == "BUY"),
    }
    cache[key] = r
    return r


def _agg(label, rows):
    pnl = sum(r["pnl"] for r in rows)
    n = sum(r["n"] for r in rows)
    buys = sum(r.get("buyCnt") or 0 for r in rows)
    mkt = sum(r.get("mktAfter") or 0 for r in rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    loss = sum(1 for r in rows if r["pnl"] < 0)
    return {
        "label": label,
        "stkDays": len(rows),
        "trades": n,
        "buys": buys,
        "mktAfter": mkt,
        "pnl": pnl,
        "winStk": wins,
        "lossStk": loss,
    }


def main():
    t0 = time.time()
    print("시작…", flush=True)
    rankDts = _listRankDts(START, END)
    print("=== 이평터치 지정가대기 A/B (v35 vs v43) ===", flush=True)
    print(f"기간 rankDt {START}~{END} · 종목당 시드 {INIT_CASH:,.0f}원", flush=True)
    print(f"유니버스: 조회20 ∪ 대금(AMT_MN){TRDE_TOP}", flush=True)
    print("A: 5분 미체결 취소만 (시장가 전환 없음)", flush=True)
    print("B: 3분 미체결 취소 후 시장가", flush=True)
    print(f"순위일 {len(rankDts)}일", flush=True)
    print(flush=True)

    patchA = {"_mode": "A5m", "limitWaitSec": 300, "confirmMode": "bar3BreakLimit"}
    patchB = {"_mode": "B3m", "limitWaitSec": 180, "confirmMode": "bar3BreakLimit"}

    rowsA = []
    rowsB = []
    cache = {}
    nDays = 0

    for rankDt in rankDts:
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > END:
            continue
        picks, err = _picks(rankDt)
        nDays += 1
        print(
            f"[{rankDt}→{tradeDt}] {len(picks)}종 cache={len(cache)}"
            + (f" err={err}" if err else ""),
            flush=True,
        )
        for p in picks:
            cd = p["stkCd"]
            nm = p.get("stkNm") or cd
            ra = _runOne(cd, tradeDt, MaTouchLimitCancelOnly, patchA, cache)
            rb = _runOne(cd, tradeDt, MaTouchScaleStrategy, patchB, cache)
            rowsA.append(
                {
                    "tradeDt": tradeDt,
                    "stkCd": cd,
                    "stkNm": nm,
                    "pnl": float(ra.get("pnl") or 0),
                    "n": int(ra.get("n") or 0),
                    "buyCnt": int(ra.get("buyCnt") or 0),
                    "mktAfter": 0,
                }
            )
            rowsB.append(
                {
                    "tradeDt": tradeDt,
                    "stkCd": cd,
                    "stkNm": nm,
                    "pnl": float(rb.get("pnl") or 0),
                    "n": int(rb.get("n") or 0),
                    "buyCnt": int(rb.get("buyCnt") or 0),
                    "mktAfter": int(rb.get("mktAfter") or 0),
                }
            )

    a = _agg("A 5분취소만", rowsA)
    b = _agg("B 3분→시장가", rowsB)
    diff = b["pnl"] - a["pnl"]

    # 차이가 큰 종목일 TOP/BOTTOM
    paired = []
    for i in range(len(rowsA)):
        da = rowsA[i]
        db = rowsB[i]
        paired.append(
            {
                "tradeDt": da["tradeDt"],
                "stkCd": da["stkCd"],
                "stkNm": da["stkNm"],
                "pnlA": da["pnl"],
                "pnlB": db["pnl"],
                "d": db["pnl"] - da["pnl"],
                "mkt": db.get("mktAfter") or 0,
            }
        )
    paired.sort(key=lambda x: x["d"], reverse=True)

    print(flush=True)
    print("=== 결과 요약 ===", flush=True)
    print(f"매매일수(순위일) {nDays}", flush=True)
    for x in (a, b):
        print(
            f"{x['label']}: 종목일 {x['stkDays']} · 라운드≈{x['trades']} · "
            f"매수체결 {x['buys']} · 시장가전환 {x['mktAfter']} · "
            f"손익합 {x['pnl']:+,.0f}원 · +종목일 {x['winStk']} / -종목일 {x['lossStk']}",
            flush=True,
        )
    print(
        f"차이 B-A: {diff:+,.0f}원 "
        f"({(diff / abs(a['pnl']) * 100) if a['pnl'] else 0:+.1f}% of |A|)",
        flush=True,
    )
    print(flush=True)
    print("B가 더 나은 종목일 TOP5:", flush=True)
    for row in paired[:5]:
        if row["d"] == 0:
            break
        print(
            f"  {row['tradeDt']} {row['stkNm']}({row['stkCd']}) "
            f"A={row['pnlA']:+,.0f} B={row['pnlB']:+,.0f} Δ={row['d']:+,.0f}"
            f"{' ·mkt' if row['mkt'] else ''}",
            flush=True,
        )
    print("B가 더 나쁜 종목일 TOP5:", flush=True)
    for row in paired[-5:][::-1]:
        if row["d"] == 0:
            break
        print(
            f"  {row['tradeDt']} {row['stkNm']}({row['stkCd']}) "
            f"A={row['pnlA']:+,.0f} B={row['pnlB']:+,.0f} Δ={row['d']:+,.0f}"
            f"{' ·mkt' if row['mkt'] else ''}",
            flush=True,
        )
    print(f"소요 {time.time() - t0:.0f}초 · 캐시 {len(cache)}", flush=True)
    print(
        "한계: 대금=일봉 AMT_MN 대리 · BT 분봉 체결모형 · 종목당 시드 독립합산",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
