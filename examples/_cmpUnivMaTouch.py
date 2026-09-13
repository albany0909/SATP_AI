# -*- coding: utf-8 -*-
"""
이평터치 유니버스 A/B 비교 BT (실주문 없음).

A: 조회순위 TOP20 ∪ 대금(일봉 AMT_MN 대리) TOP30  — 현재 운영(합집합)
B: 조회순위 TOP20 ∩ 대금(일봉 AMT_MN 대리) TOP100 — 요청 교집합 대리
   ※ ka00198/DB 상한 ~20 → 요청 '순위50' 불가. 순위는 20으로 대체.
   ※ 과거 ka10032 스냅샷 없음 → AMT_MN(적재된 일봉 종목 내) 대리.

기간: rankDt 20260801~직전, 매매일=nextTradingDay(rankDt)
sma20keep 제외 (선별 규칙만 비교).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import runBacktest
from config.krHoliday import isTradingDay, nextTradingDay
from trading.maTouchUnivLoad import _isKospiKosdaq, _stkMktMap, screenUnivFromDb
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd

STRAT = "maTouchScale_v1"
START = "20260801"
END = "20260910"
INIT_CASH = 1_000_000.0
TRDE_A = 30
TRDE_B = 100


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
    """일봉 AMT_MN 상위 (적재분·코스피/코스닥)."""
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


def _rankCds(ymd):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT=%s
                ORDER BY RANK_NO ASC
                """,
                (ymd,),
            )
            return [
                normStkCd(r["STK_CD"])
                for r in (cur.fetchall() or [])
                if normStkCd(r.get("STK_CD"))
            ]
    finally:
        conn.close()


def _picksA(rankDt):
    trde = _trdeTopCds(rankDt, TRDE_A)
    got = screenUnivFromDb(rankDt, trdePrcaCds=set(trde), rankMax=20)
    if not got.get("ok"):
        return [], got.get("errMsg")
    return got.get("picks") or [], None


def _picksB(rankDt):
    """순위∩대금100 후 SMA20·수량 필터."""
    trde = set(_trdeTopCds(rankDt, TRDE_B))
    rank = set(_rankCds(rankDt))
    inter = rank & trde
    if not inter:
        return [], None
    got = screenUnivFromDb(rankDt, trdePrcaCds=trde, rankMax=20)
    if not got.get("ok"):
        return [], got.get("errMsg")
    picks = [p for p in (got.get("picks") or []) if p.get("stkCd") in inter]
    return picks, None


def _btOne(stkCd, stkNm, tradeDt, cache):
    key = (stkCd, tradeDt)
    if key in cache:
        r = dict(cache[key])
        r["stkNm"] = stkNm or r.get("stkNm")
        return r
    try:
        res = runBacktest(
            STRAT,
            stkCd,
            tradeDt,
            tradeDt,
            initCash=INIT_CASH,
            barSource="min",
        )
    except Exception as err:
        r = {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": str(err),
            "pnl": 0,
            "n": 0,
        }
        cache[key] = r
        return r
    if not isinstance(res, dict) or not res.get("ok"):
        r = {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": (res or {}).get("errMsg") if isinstance(res, dict) else "fail",
            "pnl": 0,
            "n": 0,
        }
        cache[key] = r
        return r
    scale = res.get("scalePnl") or {}
    pnl = float(scale.get("pnlWon") or 0)
    rounds = res.get("rounds") or []
    n = len(rounds) if isinstance(rounds, list) else 0
    if n <= 0:
        n = int(res.get("allTradeCnt") or 0)
    r = {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "pnl": pnl,
        "n": n,
        "src": res.get("srcLabel"),
    }
    cache[key] = r
    return r


def _agg(label, dayRows):
    pnl = sum(r["pnl"] for r in dayRows)
    n = sum(r["n"] for r in dayRows)
    wins = sum(1 for r in dayRows if r["pnl"] > 0)
    loss = sum(1 for r in dayRows if r["pnl"] < 0)
    return {
        "label": label,
        "stkDays": len(dayRows),
        "trades": n,
        "pnl": pnl,
        "winStk": wins,
        "lossStk": loss,
    }


def main():
    t0 = time.time()
    print("시작…", flush=True)
    rankDts = _listRankDts(START, END)
    print("=== 이평터치 유니버스 A/B 비교 (DB 대리) ===", flush=True)
    print(f"기간 rankDt {START}~{END} · 종목당 시드 {INIT_CASH:,.0f}원", flush=True)
    print(f"A: 조회20 ∪ 대금(AMT_MN){TRDE_A}  (=현재 운영 합집합)", flush=True)
    print(
        f"B: 조회20 ∩ 대금(AMT_MN){TRDE_B}  "
        f"(요청 순위50∩대금100 대리 · 순위이력20만)",
        flush=True,
    )
    print(f"순위일 {len(rankDts)}일", flush=True)
    print(flush=True)

    rowsA = []
    rowsB = []
    univStat = []
    btCache = {}

    for rankDt in rankDts:
        if not isTradingDay(rankDt):
            continue
        tradeDt = nextTradingDay(rankDt)
        if not tradeDt or tradeDt > "20260911":
            continue
        pa, ea = _picksA(rankDt)
        pb, eb = _picksB(rankDt)
        univStat.append(
            {
                "rankDt": rankDt,
                "tradeDt": tradeDt,
                "nA": len(pa),
                "nB": len(pb),
                "errA": ea,
                "errB": eb,
            }
        )
        print(
            f"[{rankDt}→{tradeDt}] A={len(pa)}종 B={len(pb)}종 "
            f"cache={len(btCache)}"
            + (f" errA={ea}" if ea else "")
            + (f" errB={eb}" if eb else ""),
            flush=True,
        )
        for p in pa:
            r = _btOne(p["stkCd"], p.get("stkNm") or p["stkCd"], tradeDt, btCache)
            rowsA.append(
                {
                    "rankDt": rankDt,
                    "tradeDt": tradeDt,
                    "stkCd": r["stkCd"],
                    "stkNm": r.get("stkNm"),
                    "pnl": float(r.get("pnl") or 0),
                    "n": int(r.get("n") or 0),
                }
            )
        for p in pb:
            r = _btOne(p["stkCd"], p.get("stkNm") or p["stkCd"], tradeDt, btCache)
            rowsB.append(
                {
                    "rankDt": rankDt,
                    "tradeDt": tradeDt,
                    "stkCd": r["stkCd"],
                    "stkNm": r.get("stkNm"),
                    "pnl": float(r.get("pnl") or 0),
                    "n": int(r.get("n") or 0),
                }
            )

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n, SUM(IFNULL(PNL_WON,0)) AS pnl,
                       SUM(CASE WHEN IFNULL(PNL_WON,0)>0 THEN 1 ELSE 0 END) AS w
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='paper'
                  AND EXIT_DT BETWEEN %s AND %s
                """,
                (START, END),
            )
            liveP = cur.fetchone() or {}
            cur.execute(
                """
                SELECT COUNT(*) AS n, SUM(IFNULL(PNL_WON,0)) AS pnl
                FROM AI_STRAT_LIVE_TRADE
                WHERE ALG_CD='MTS01' AND MODE='real'
                  AND EXIT_DT BETWEEN %s AND %s
                """,
                (START, END),
            )
            liveR = cur.fetchone() or {}
    finally:
        conn.close()

    a = _agg("A 20∪대금30(AMT)", rowsA)
    b = _agg("B 20∩대금100(AMT)", rowsB)
    avgA = sum(u["nA"] for u in univStat) / max(1, len(univStat))
    avgB = sum(u["nB"] for u in univStat) / max(1, len(univStat))

    print(flush=True)
    print("=== 결과 요약 ===", flush=True)
    print(f"유니버스 평균 종수  A={avgA:.1f}  B={avgB:.1f}", flush=True)
    for x in (a, b):
        print(
            f"{x['label']}: 종목일 {x['stkDays']} · 거래추정 {x['trades']} · "
            f"손익합 {x['pnl']:+,.0f}원 · +종목일 {x['winStk']} / -종목일 {x['lossStk']}",
            flush=True,
        )
    print(
        f"차이 B-A: {b['pnl'] - a['pnl']:+,.0f}원 "
        f"({((b['pnl'] - a['pnl']) / abs(a['pnl']) * 100) if a['pnl'] else 0:+.1f}% of |A|)",
        flush=True,
    )
    print(flush=True)
    print(
        f"참고 LIVE paper 청산: {liveP.get('n')}건 손익 {float(liveP.get('pnl') or 0):+,.0f}원 "
        f"(승 {liveP.get('w')}) — sma20keep·실체결 포함",
        flush=True,
    )
    print(
        f"참고 LIVE real 청산: {liveR.get('n')}건 손익 {float(liveR.get('pnl') or 0):+,.0f}원",
        flush=True,
    )
    print(f"소요 {time.time() - t0:.0f}초 · BT캐시 {len(btCache)}", flush=True)
    print(flush=True)
    print("한계:", flush=True)
    print("  · 조회순위 이력 TOP20만 (요청 50 불가)", flush=True)
    print("  · 대금=일봉 AMT_MN(적재 종목 내), 과거 ka10032 스냅샷 없음", flush=True)
    print("  · sma20keep 제외 · 체결모델은 BT(분봉/일봉근사)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
