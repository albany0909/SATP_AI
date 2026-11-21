# -*- coding: utf-8 -*-
"""이평터치 BT 손실 원인 진단 — 청산사유·승패·시드합산 왜곡.
막날 강제청산은 그 봉의 장중 종가다. 장마감 뒤에야 아는 신호의 당일 종가 체결은 쓰지 않는다."""
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
    calcScalePnl,
    loadDayBars,
    loadMinBars,
)
from config.krHoliday import isTradingDay, nextTradingDay
from strategies.impl.maTouchScale import MaTouchScaleStrategy, stampMinBarsWithDaySma
from strategies.specLoader import loadStratSpec
from trading.maTouchUnivLoad import screenUnivFromDb
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd

START = "20260801"
END = datetime.now().strftime("%Y%m%d")
INIT = 1_000_000.0


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


def _run(stkCd, tradeDt, params):
    try:
        smaStart = (
            datetime.strptime(tradeDt, "%Y%m%d") - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
        ).strftime("%Y%m%d")
    except ValueError:
        smaStart = tradeDt
    dayBars = loadDayBars(stkCd, smaStart, tradeDt)
    minBars = loadMinBars(stkCd, "1", tradeDt, tradeDt)
    if not dayBars and not minBars:
        return None, "nobar"
    tmp = MaTouchScaleStrategy(params)
    bars = (
        stampMinBarsWithDaySma(minBars, dayBars, tmp._needPeriods())
        if minBars
        else dayBars
    )
    if not bars:
        return None, "empty"
    src = "min" if minBars else "day"
    strat = MaTouchScaleStrategy(params)
    bars = strat.prepare(bars)
    trades = []
    for idx in range(len(bars)):
        ev = strat.onBar(bars, idx)
        if not ev:
            continue
        for e in ev if isinstance(ev, list) else [ev]:
            e["tradeDt"] = bars[idx]["tradeDt"]
            trades.append(e)
    forced = False
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
            forced = True
        strat.pos = None
    return {"trades": trades, "src": src, "forced": forced, "scale": calcScalePnl(trades)}, None


def _exitBuckets(trades):
    """전량 SELL reason → pnl 기여(대략: 직전 미청산 매수대비는 어려워 reason별 건수만 + 라운드식)."""
    # 단순: 각 전량 SELL 을 하나의 청산으로 보고, 그 시점까지 열린 BUY qty 대비
    # 더 정확히는 reason별 SELL 금액 - 대응 BUY 를 FIFO
    buys = []  # (qty, px)
    halfPnl = 0.0
    full = []  # (reason, pnl)
    for t in trades:
        tp = t.get("type")
        px = float(t.get("price") or 0)
        qty = int(t.get("qty") or 0)
        if px <= 0 or qty <= 0:
            continue
        if tp in ("BUY", "ADD"):
            buys.append([qty, px])
        elif tp == "SELL_HALF":
            left = qty
            cost = 0.0
            sold = 0
            while left > 0 and buys:
                q, bp = buys[0]
                take = min(q, left)
                cost += take * bp
                sold += take
                left -= take
                q -= take
                if q <= 0:
                    buys.pop(0)
                else:
                    buys[0][0] = q
            halfPnl += px * sold - cost
        elif tp == "SELL":
            left = qty
            cost = 0.0
            sold = 0
            while left > 0 and buys:
                q, bp = buys[0]
                take = min(q, left)
                cost += take * bp
                sold += take
                left -= take
                q -= take
                if q <= 0:
                    buys.pop(0)
                else:
                    buys[0][0] = q
            pnl = px * sold - cost
            rsn = str(t.get("reason") or "?")
            # normalize
            if rsn.startswith("hard_stop") or rsn.startswith("stop_"):
                bucket = "STOP"
            elif rsn.startswith("tp") or rsn.startswith("rsi"):
                bucket = "TAKE"
            elif "flat" in rsn or "eod_flat" in rsn:
                bucket = "FLAT"
            elif rsn == "force_close_eod":
                bucket = "FORCE_EOD"
            else:
                bucket = rsn
            full.append((bucket, rsn, pnl, sold))
    return halfPnl, full


def main():
    spec = loadStratSpec("maTouchScale_v1") or {}
    params = dict(spec.get("params") or {})
    params["limitWaitSec"] = 180
    params["confirmMode"] = "bar3BreakLimit"

    byRsn = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    byBucket = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    srcCnt = defaultdict(int)
    pnlList = []
    halfTot = 0.0
    tradeDays = 0
    stkDays = 0
    withBuy = 0
    forcedN = 0
    forcedPnl = 0.0

    for rankDt in _rankDts():
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > END:
            continue
        tradeDays += 1
        trde = set(_trde(rankDt, 30))
        got = screenUnivFromDb(rankDt, trdePrcaCds=trde, rankMax=20)
        picks = got.get("picks") or []
        print(f"[{rankDt}→{tradeDt}] {len(picks)}종", flush=True)
        for p in picks:
            stkDays += 1
            cd = p["stkCd"]
            res, err = _run(cd, tradeDt, params)
            if not res:
                continue
            srcCnt[res["src"]] += 1
            trades = res["trades"]
            buys = [t for t in trades if t.get("type") == "BUY"]
            if not buys:
                continue
            withBuy += 1
            pnl = float(res["scale"].get("pnlWon") or 0)
            pnlList.append(
                {
                    "dt": tradeDt,
                    "cd": cd,
                    "nm": p.get("stkNm") or cd,
                    "pnl": pnl,
                    "src": res["src"],
                    "forced": res["forced"],
                }
            )
            half, fulls = _exitBuckets(trades)
            halfTot += half
            for bucket, rsn, epnl, _ in fulls:
                byBucket[bucket]["n"] += 1
                byBucket[bucket]["pnl"] += epnl
                byRsn[rsn]["n"] += 1
                byRsn[rsn]["pnl"] += epnl
            if res["forced"]:
                forcedN += 1
                # force row pnl from fulls
                for bucket, rsn, epnl, _ in fulls:
                    if rsn == "force_close_eod":
                        forcedPnl += epnl

    totPnl = sum(x["pnl"] for x in pnlList)
    wins = [x for x in pnlList if x["pnl"] > 0]
    loss = [x for x in pnlList if x["pnl"] < 0]
    flat = [x for x in pnlList if x["pnl"] == 0]

    print("\n=== 진단 요약 (B=3분→시장가) ===", flush=True)
    print(f"매매일 {tradeDays} · 종목일 {stkDays} · 매수발생 {withBuy}", flush=True)
    print(f"데이터: {dict(srcCnt)}", flush=True)
    print(
        f"손익합 {totPnl:+,.0f} · 승 {len(wins)}/{sum(x['pnl'] for x in wins):+,.0f} · "
        f"패 {len(loss)}/{sum(x['pnl'] for x in loss):+,.0f} · 0원 {len(flat)}",
        flush=True,
    )
    if wins:
        print(
            f"평균승 {sum(x['pnl'] for x in wins)/len(wins):+,.0f} · "
            f"평균패 {sum(x['pnl'] for x in loss)/len(loss):+,.0f}",
            flush=True,
        )
    print(f"부분익절합(SELL_HALF) {halfTot:+,.0f}", flush=True)
    print(f"엔진 force_close_eod {forcedN}건 손익 {forcedPnl:+,.0f}", flush=True)
    print("\n청산 버킷:", flush=True)
    for k, v in sorted(byBucket.items(), key=lambda x: x[1]["pnl"]):
        print(f"  {k}: {v['n']}건 {v['pnl']:+,.0f}", flush=True)
    print("\n청산 reason TOP:", flush=True)
    for k, v in sorted(byRsn.items(), key=lambda x: x[1]["pnl"])[:12]:
        print(f"  {k}: {v['n']}건 {v['pnl']:+,.0f}", flush=True)
    print("\n최악 10:", flush=True)
    for x in sorted(pnlList, key=lambda z: z["pnl"])[:10]:
        print(
            f"  {x['dt']} {x['nm']}({x['cd']}) {x['pnl']:+,.0f} [{x['src']}"
            f"{' force' if x['forced'] else ''}]",
            flush=True,
        )
    print("\n최고 5:", flush=True)
    for x in sorted(pnlList, key=lambda z: z["pnl"], reverse=True)[:5]:
        print(f"  {x['dt']} {x['nm']}({x['cd']}) {x['pnl']:+,.0f}", flush=True)

    # 집계 왜곡: 동시 다종목 합산 vs 일평균
    byDay = defaultdict(float)
    for x in pnlList:
        byDay[x["dt"]] += x["pnl"]
    dayPnls = list(byDay.values())
    print(
        f"\n일별손익합 평균 {sum(dayPnls)/max(1,len(dayPnls)):+,.0f} · "
        f"최악일 {min(dayPnls) if dayPnls else 0:+,.0f} · 최고일 {max(dayPnls) if dayPnls else 0:+,.0f}",
        flush=True,
    )
    print(
        f"참고: 종목일 독립 100만원×{withBuy}회 합산 → "
        f"명목투입 ≈ {INIT * withBuy:,.0f}원 규모 손실률 "
        f"{(totPnl / (INIT * withBuy) * 100) if withBuy else 0:+.2f}%",
        flush=True,
    )

    # LIVE 대조
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) n, SUM(IFNULL(PNL_WON,0)) pnl,
                       SUM(CASE WHEN IFNULL(PNL_WON,0)>0 THEN 1 ELSE 0 END) w,
                       SUM(CASE WHEN IFNULL(PNL_WON,0)<0 THEN 1 ELSE 0 END) l
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='paper'
                  AND EXIT_DT BETWEEN %s AND %s
                """,
                (START, END),
            )
            live = cur.fetchone() or {}
            cur.execute(
                """
                SELECT EXIT_REASON, COUNT(*) n, SUM(IFNULL(PNL_WON,0)) pnl
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='paper'
                  AND EXIT_DT BETWEEN %s AND %s
                GROUP BY EXIT_REASON
                """,
                (START, END),
            )
            liveR = cur.fetchall() or []
            cur.execute(
                """
                SELECT STK_NM, STK_CD, EXIT_DT, PNL_WON, EXIT_REASON
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='paper'
                  AND EXIT_DT BETWEEN %s AND %s
                ORDER BY PNL_WON ASC LIMIT 8
                """,
                (START, END),
            )
            worst = cur.fetchall() or []
    finally:
        conn.close()
    print("\n=== LIVE paper 동일기간 ===", flush=True)
    print(
        f"청산 {live.get('n')}건 손익 {float(live.get('pnl') or 0):+,.0f} "
        f"(승 {live.get('w')} / 패 {live.get('l')})",
        flush=True,
    )
    for r in liveR:
        print(f"  {r['EXIT_REASON']}: {r['n']}건 {float(r['pnl'] or 0):+,.0f}", flush=True)
    print("최악:", flush=True)
    for r in worst:
        print(
            f"  {r['EXIT_DT']} {r['STK_NM']}({r['STK_CD']}) "
            f"{float(r['PNL_WON'] or 0):+,.0f} {r['EXIT_REASON']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
