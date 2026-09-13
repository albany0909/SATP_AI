"""
bullBodyMa2050 검증 — 사용자 제공 조건 포착일·종목
"""
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from telegram.commands.loginCommand import handleLogin
from utils.dbConn import openDbConn
from utils.btEnsureMinData import ensureMinBarsForBt, countMinBarsByDay
from backtest.engine import runBacktest, loadMinBars
from utils.stratValidate import (
    evaluateBtGates,
    formatValidateReport,
    runStaticChecks,
    formatStaticReport,
)
from strategies.impl.bullBodyMa2050 import (
    aggDayBars,
    findSetupDts,
)
from strategies.specLoader import loadStratSpec
from config import runtimeState

CATCH_MAP = {
    "20260702": [("045100", "한영이엔지"), ("119850", "지엔씨에너지")],
    "20260703": [("017000", "신원종합개발")],
    "20260706": [("095500", "미래나노텍")],
    "20260707": [("037070", "파세코")],
    "20260714": [("037070", "파세코")],
    "20260716": [("252990", "샘씨엔에스")],
    "20260720": [
        ("004540", "깨끗한나라"),
        ("006660", "삼성공조"),
        ("012690", "모나리자"),
    ],
}

STRAT = "bullBodyMa2050_v1"
INIT_CASH = 10_000_000.0


def warmStart(ymd, days=100):
    d = datetime.strptime(ymd, "%Y%m%d") - timedelta(days=days)
    return d.strftime("%Y%m%d")


def uniqueStks():
    out = {}
    for dt, rows in CATCH_MAP.items():
        for cd, nm in rows:
            if cd not in out:
                out[cd] = {"stkCd": cd, "stkNm": nm, "catchDts": []}
            out[cd]["catchDts"].append(dt)
    return out


def loadDayBarsDb(stkCd, startDt, endDt):
    """AI_DAY_STK_PRC 일봉 (수량)."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC,
                       TRDE_QTY, AMT_MN
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0'
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    days = []
    for r in rows:
        close = float(r["CLOSE_PRC"] or 0)
        qty = float(r["TRDE_QTY"] or 0)
        amt = r.get("AMT_MN")
        if amt is None:
            amt = (qty * close) / 1_000_000.0
        else:
            amt = float(amt)
        days.append(
            {
                "dt": r["TRADE_DT"],
                "open": float(r["OPEN_PRC"] or 0),
                "high": float(r["HIGH_PRC"] or 0),
                "low": float(r["LOW_PRC"] or 0),
                "close": close,
                "qty": qty,
                "amtMn": amt,
            }
        )
    return days


def main():
    print(handleLogin("paper"))
    print()

    static = runStaticChecks(STRAT)
    print(formatStaticReport(STRAT, static))
    print()

    stks = uniqueStks()
    allCatch = sorted(CATCH_MAP.keys())
    startDt = allCatch[0]
    endDt = (
        datetime.strptime(allCatch[-1], "%Y%m%d") + timedelta(days=14)
    ).strftime("%Y%m%d")
    warm = warmStart(startDt, 120)
    print(f"[period] warm={warm} sim={startDt}~{endDt} cash={INIT_CASH:,.0f}")
    print(f"[login] paper={runtimeState.isPaperLoggedIn()}")
    print()

    spec = loadStratSpec(STRAT) or {}
    p = spec.get("params") or {}
    minAmt = float(p.get("minAmtMn", 10000))
    volLb = int(p.get("volLookback", 20))
    smaF = int(p.get("smaFast", 20))
    smaM = int(p.get("smaMid", 50))

    # 1) 일봉 기준 조건식 근사 vs 사용자 포착일
    print("=== 포착일 vs 조건식 근사(일봉 DB / 없으면 분봉집계) ===")
    hitTable = []
    for dt, rows in CATCH_MAP.items():
        for cd, nm in rows:
            dayWarm = warmStart(dt, 150)
            days = loadDayBarsDb(cd, dayWarm, endDt)
            src = "DAY_DB"
            if len(days) < 60:
                # 분봉 확보 후 집계 (필요 시)
                ens = ensureMinBarsForBt(
                    cd, dt, dt, warmupStartDt=dayWarm, ticScope="1"
                )
                print(f"  ensure {nm}({cd}): ok={ens.get('ok')} loaded={ens.get('loaded')} {ens.get('errMsg') or ''}")
                bars = loadMinBars(cd, "1", dayWarm, endDt)
                days = aggDayBars(bars)
                src = "MIN_AGG"
            found = findSetupDts(days, minAmt, volLb, smaF, smaM)
            hit = dt in found
            near = [x for x in found if abs(
                (datetime.strptime(x, "%Y%m%d") - datetime.strptime(dt, "%Y%m%d")).days
            ) <= 3]
            flag = "HIT" if hit else "MISS"
            hitTable.append((dt, nm, cd, flag, src, len(days), near[:5]))
            print(
                f"  {dt} {nm}({cd}) {flag} src={src} dayBars={len(days)} "
                f"near3d={near[:5]}"
            )
    print()

    # 2) 종목별 백테스트
    print("=== 종목별 BT (bullBodyMa2050_v1) ===")
    stkResults = []
    for cd, info in stks.items():
        nm = info["stkNm"]
        print(f"--- {nm}({cd}) catch={info['catchDts']} ---")
        ens = ensureMinBarsForBt(
            cd, startDt, endDt, warmupStartDt=warm, ticScope="1"
        )
        if not ens.get("ok"):
            print(f"  skip: {ens.get('errMsg')}")
            stkResults.append(
                {
                    "ok": False,
                    "stkCd": cd,
                    "stkNm": nm,
                    "errMsg": ens.get("errMsg"),
                    "rounds": [],
                    "stats": {},
                }
            )
            continue
        print(
            f"  min ensure loaded={ens.get('loaded')} "
            f"barHint={ens.get('barCnt')}"
        )
        result = runBacktest(
            STRAT,
            cd,
            startDt,
            endDt,
            warmupStartDt=warm,
            ticScope="1",
            initCash=INIT_CASH,
            barSource="min",
        )
        if not result.get("ok"):
            print(f"  BT fail: {result.get('errMsg')}")
            stkResults.append(
                {
                    "ok": False,
                    "stkCd": cd,
                    "stkNm": nm,
                    "errMsg": result.get("errMsg"),
                    "rounds": [],
                    "stats": {},
                }
            )
            continue
        stats = result.get("stats") or {}
        print(
            f"  bars={result.get('barCnt')} trades={stats.get('tradeCnt')} "
            f"win={stats.get('winCnt')} ret={stats.get('finalRetPct'):+.2f}% "
            f"mdd={stats.get('maxDdPct'):.2f}%"
        )
        for rd in result.get("rounds") or []:
            print(
                f"    {rd.get('result')} catch={rd.get('catchDt')} "
                f"buy={rd.get('buyDt')} sell={rd.get('sellDt')} "
                f"{rd.get('buyPrc'):,.0f}->{rd.get('sellPrc'):,.0f} "
                f"{rd.get('retPct'):+.2f}% {rd.get('sellReason')}"
            )
        # 사용자 포착일을 catchDt 로 보강한 라운드 사본 (리포트용)
        rounds = list(result.get("rounds") or [])
        # 포착일 일치 여부가 보이도록 catchHit 메타
        btCatch = {str(r.get("catchDt")) for r in rounds}
        userHit = [d for d in info["catchDts"] if d in btCatch]
        stkResults.append(
            {
                "ok": True,
                "stkCd": cd,
                "stkNm": nm,
                "rounds": rounds,
                "stats": stats,
                "userCatch": info["catchDts"],
                "userCatchInTrades": userHit,
            }
        )
        print()

    gate = evaluateBtGates(stkResults)
    report = formatValidateReport(
        STRAT,
        "장대양봉 2050이평매매",
        static,
        gate,
        startDt,
        endDt,
        INIT_CASH,
        universeLabel=(
            f"user catch list "
            f"{sum(len(v) for v in CATCH_MAP.values())}건 / {len(stks)}종"
        ),
        stkResults=stkResults,
    )
    print("=== 종합 리포트 ===")
    print(report)
    print()
    print("=== HIT/MISS 요약 (조건 근사) ===")
    hitN = sum(1 for x in hitTable if x[3] == "HIT")
    print(f"일치 {hitN}/{len(hitTable)}")
    for row in hitTable:
        print(f"  {row[0]} {row[1]}({row[2]}) {row[3]} ({row[4]}) near={row[6]}")


if __name__ == "__main__":
    main()
