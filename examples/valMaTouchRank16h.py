"""
이평선터치 검증 — 전 거래일 장마감 실시간조회순위 유니버스로 다음 거래일 BT.

운영 규칙(v2)과 같은 순서로 검증한다.
  1) 기준일(rankDt) 16시 순위 스냅샷 → 코스피/코스닥 + 매차 최소수량 필터
     (거래대금 상위30은 실선정 시 ka10032. 이 스크립트는 DB만 써서 생략)
  2) 그 목록으로 검증일(tradeDt) 1분봉 백테스트 (이평 터치 + 현재가>SMA20)
  3) 정적검증 + 게이트 리포트

사용:
  python examples/valMaTouchRank16h.py [rankDt] [tradeDt] [투자금만원]
  기본: rankDt=20260814 tradeDt=20260818 투자금=1000만원

실주문·TR 호출을 하지 않는다 (DB 에 있는 봉만 사용).
  · 1분봉 있음 → 1분+일SMA 정밀 시뮬 (14:00 매수금지·15:18 청산 반영)
  · 1분봉 없고 일봉 있음 → 엔진 기본 동작인 **일봉 근사** (시각 조건 없음)
  · 둘 다 없음 → 데이터없음으로 분리 (성공으로 세지 않는다)
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import runBacktest
from config import runtimeState
from strategies.specLoader import loadStratSpec
from trading.maTouchUnivLoad import screenUnivFromDb
from utils.btEnsureMinData import countMinBarsByDay
from utils.dbConn import openDbConn
from utils.stratValidate import (
    evaluateBtGates,
    formatStaticReport,
    formatValidateReport,
    runStaticChecks,
)

STRAT = "maTouchScale_v1"
_DEF_RANK_DT = "20260814"
_DEF_TRADE_DT = "20260818"
_DEF_MANWON = 1000.0
# 정규장 1분봉이 이보다 적으면 불완전으로 보고 보강
_MIN_BARS_DAY = 50


def _argYmd(idx, default):
    if len(sys.argv) > idx:
        text = str(sys.argv[idx]).strip().replace("-", "")
        if len(text) == 8 and text.isdigit():
            return text
    return default


def _argCash(idx, defaultManWon):
    if len(sys.argv) > idx:
        try:
            return float(str(sys.argv[idx]).replace(",", "")) * 10_000.0
        except ValueError:
            pass
    return defaultManWon * 10_000.0


def _hasDayBar(stkCd, ymd):
    """검증일 확정 일봉 보유 여부 (일봉 근사 시뮬 가능 판정)."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT=%s
                """,
                (str(stkCd), str(ymd)),
            )
            return int((cur.fetchone() or {}).get("c") or 0) > 0
    finally:
        conn.close()


def _pickUniverse(rankDt):
    """운영과 동일한 선별 (TR 없이 DB 스냅샷)."""
    got = screenUnivFromDb(rankDt)
    if not got.get("ok"):
        return None, got.get("errMsg")
    return got, None


def main():
    rankDt = _argYmd(1, _DEF_RANK_DT)
    tradeDt = _argYmd(2, _DEF_TRADE_DT)
    initCash = _argCash(3, _DEF_MANWON)

    print(f"[검증] {STRAT} · 유니버스 기준일 {rankDt} → 검증일 {tradeDt}")
    print(f"[투자금] {initCash / 10000:,.0f}만원 (종목별 동일 조건)")
    print()

    static = runStaticChecks(STRAT)
    print(formatStaticReport(STRAT, static))
    print()

    univ, errMsg = _pickUniverse(rankDt)
    if univ is None:
        print(f"[중단] 유니버스 없음: {errMsg}")
        return
    picks = univ.get("picks") or []
    skips = univ.get("skips") or []
    print(
        f"=== 유니버스 ({rankDt} 순위 {univ.get('rankCnt')}종 → 선정 {len(picks)}종) ==="
    )
    for p in picks:
        print(
            f"  {p.get('rankNo'):>2} {p['stkCd']} {p.get('stkNm')} "
            f"{p.get('marketName') or '-'} 종가 {p.get('closePrc') or 0:,.0f} "
            f"SMA20 {p.get('sma20') or 0:,.0f} "
            f"{'20선위' if p.get('aboveSma') else '20선아래'} "
            f"매차 {p.get('stepQty')}주"
        )
    for s in skips:
        print(f"  -- 제외 {s['stkCd']} {s.get('stkNm')} ({s.get('reason')})")
    print()

    spec = loadStratSpec(STRAT) or {}
    stratNm = spec.get("name") or "이평선터치 매매"
    print(f"[세션] TR 미사용 (DB 봉만 사용) · login={runtimeState.isPaperLoggedIn()}")
    print()

    print(f"=== 종목별 BT ({tradeDt}) ===")
    stkResults = []
    for p in picks:
        cd = p["stkCd"]
        nm = p.get("stkNm") or cd
        byDay = countMinBarsByDay(cd, "1", tradeDt, tradeDt)
        barCnt = int(byDay.get(tradeDt) or 0)
        if barCnt < _MIN_BARS_DAY and not _hasDayBar(cd, tradeDt):
            print(f"  {nm}({cd}) 데이터없음: 1분봉·{tradeDt} 일봉 모두 없음")
            stkResults.append(
                {
                    "ok": False,
                    "stkCd": cd,
                    "stkNm": nm,
                    "errMsg": f"봉 없음(1분·일봉) {tradeDt}",
                    "rounds": [],
                    "stats": {},
                }
            )
            continue

        result = runBacktest(
            STRAT,
            cd,
            tradeDt,
            tradeDt,
            warmupStartDt=tradeDt,
            ticScope="1",
            initCash=initCash,
            barSource="min",
        )
        if not result.get("ok"):
            print(f"  {nm}({cd}) BT fail: {result.get('errMsg')}")
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
        trades = result.get("trades") or []
        srcLabel = result.get("srcLabel") or "-"
        print(
            f"  {nm}({cd}) [{srcLabel}] 바 {result.get('barCnt')} "
            f"체결 {stats.get('tradeCnt') or 0} "
            f"수익 {float(stats.get('finalRetPct') or 0):+.2f}% "
            f"MDD {float(stats.get('maxDdPct') or 0):.2f}% "
            f"({'20선위' if p.get('aboveSma') else '20선아래'})"
        )
        for t in trades:
            print(
                f"      {t.get('side')} {t.get('tradeDt')} {t.get('tm') or ''} "
                f"{float(t.get('prc') or 0):,.0f} x{t.get('qty')} "
                f"{t.get('reason') or ''}"
            )
        stkResults.append(
            {
                "ok": True,
                "stkCd": cd,
                "stkNm": nm,
                "rounds": result.get("rounds") or [],
                "stats": stats,
                "barCnt": result.get("barCnt"),
                "srcLabel": srcLabel,
                "trades": trades,
                "aboveSma": bool(p.get("aboveSma")),
            }
        )
    print()

    gate = evaluateBtGates(stkResults)
    report = formatValidateReport(
        STRAT,
        stratNm,
        static,
        gate,
        tradeDt,
        tradeDt,
        initCash,
        universeLabel=(
            f"rank16h {rankDt} → {tradeDt} · 코스피/닥 {len(picks)}종"
        ),
        stkResults=stkResults,
    )
    print("=== 종합 리포트 ===")
    print(report)

    noTrade = [r for r in stkResults if r.get("ok") and not (r.get("trades") or [])]
    if noTrade:
        print()
        print(f"[무매매 {len(noTrade)}종] 터치·20선 조건 미충족")
        print("  " + ", ".join(r["stkNm"] for r in noTrade))

    minOk = [r for r in stkResults if r.get("srcLabel") == "1분+일SMA"]
    dayOk = [r for r in stkResults if r.get("srcLabel") == "일봉"]
    noData = [r for r in stkResults if not r.get("ok")]
    print()
    print(
        f"[데이터] 1분정밀 {len(minOk)}종 · 일봉근사 {len(dayOk)}종 · "
        f"데이터없음 {len(noData)}종"
    )
    if dayOk:
        print("  일봉근사(시각조건 미반영): " + ", ".join(r["stkNm"] for r in dayOk))
    if noData:
        print("  데이터없음: " + ", ".join(r["stkNm"] for r in noData))


if __name__ == "__main__":
    main()
