"""
전략 검증 전용 CLI — 수정 후 실주문 없이 게이트만 확인.

사용:
  python examples/valStrat.py list
  python examples/valStrat.py static {stratId}
  python examples/valStrat.py {stratId} watch {시작일} {종료일} [투자금만원]
  python examples/valStrat.py {stratId} rank16h {rankDt} {tradeDt} [투자금만원]
    · maTouchScale_v1 전용 — 운영 16시 유니버스와 동일 DB 선별

결과: config/data/stratValRun/{stratId}_{runTs}.json
텔레그램 val 과 동일 게이트 (PASS / CONDITIONAL / FAIL).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from backtest.engine import runBacktest
from trading.maTouchUnivLoad import screenUnivFromDb
from utils.btEnsureMinData import ensureMinBarsForBt
from utils.stratValRunStore import saveStratValRun
from utils.stratValidate import (
    evaluateBtGates,
    formatStaticReport,
    formatValidateReport,
    resolveValidateStrat,
    runStaticChecks,
)
from utils.watchStkStore import listActiveWatch

_DEF_MANWON = 1000.0
_MANWON_TO_WON = 10_000.0


def _usage():
    from strategies.specLoader import listStratSpecs

    specs = listStratSpecs() or []
    ids = ", ".join(f"{s.get('seq')}:{s.get('stratId')}" for s in specs) or "(없음)"
    return (
        "사용법:\n"
        "  python examples/valStrat.py list\n"
        "  python examples/valStrat.py static {stratId}\n"
        "  python examples/valStrat.py {stratId} watch {시작} {종료} [만원]\n"
        "  python examples/valStrat.py {stratId} rank16h {rankDt} {tradeDt} [만원]\n"
        f"등록: {ids}\n"
        "※ 실주문·TR 호출 없음 (DB 봉만 사용, 부족 시 ensureMinBarsForBt)"
    )


def _normYmd(raw):
    text = str(raw or "").strip().replace("-", "").replace(".", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else None


def _parseCash(raw, defaultMan=_DEF_MANWON):
    if raw is None or str(raw).strip() == "":
        return defaultMan * _MANWON_TO_WON
    try:
        return float(str(raw).replace(",", "")) * _MANWON_TO_WON
    except ValueError:
        return defaultMan * _MANWON_TO_WON


def _resolve(raw):
    got = resolveValidateStrat(raw)
    if not got.get("ok"):
        print(f"[오류] {got.get('errMsg')}")
        sys.exit(1)
    return got


def _warmStart(stratId, startDt):
    if str(stratId).startswith("maTouchScale"):
        return startDt
    return (datetime.strptime(startDt, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")


def _runOneStkBt(stratId, stkCd, stkNm, startDt, endDt, initCash):
    warmDt = _warmStart(stratId, startDt)
    ensured = ensureMinBarsForBt(
        stkCd,
        startDt,
        endDt,
        warmupStartDt=warmDt,
        ticScope="1",
    )
    if not ensured.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": ensured.get("errMsg") or "분봉 보강 실패",
        }

    result = runBacktest(
        stratId,
        stkCd,
        startDt,
        endDt,
        warmupStartDt=warmDt,
        ticScope="1",
        initCash=initCash,
        barSource="min",
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": result.get("errMsg") or "BT 실패",
        }
    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "stats": result.get("stats") or {},
        "trades": result.get("trades") or [],
        "rounds": result.get("rounds") or [],
        "scalePnl": result.get("scalePnl") or {},
    }


def _finishReport(stratId, stratName, static, gateRes, stkResults, startDt, endDt, initCash, universeLabel, mode, extra=None):
    report = formatValidateReport(
        stratId,
        stratName,
        static,
        gateRes,
        startDt,
        endDt,
        initCash,
        universeLabel,
        stkResults=stkResults,
    )
    print(report)
    payload = {
        "mode": mode,
        "startYmd": startDt,
        "endYmd": endDt,
        "initCash": initCash,
        "verdict": gateRes.get("verdict") or "FAIL",
        "static": static,
        "gates": gateRes,
        "stkCnt": len(stkResults or []),
        "reportText": report,
    }
    if extra:
        payload.update(extra)
    path = saveStratValRun(stratId, payload)
    print(f"\n[저장] {path}")
    return gateRes.get("verdict")


def _cmdList():
    from strategies.specLoader import listStratSpecs

    print("=== 등록 전략 ===")
    for s in listStratSpecs() or []:
        print(
            f"  {s.get('seq'):>2}. {s.get('stratId')} "
            f"({s.get('algCd') or '-'}) {s.get('name')} "
            f"status={s.get('status')} enabled={s.get('enabled')}"
        )


def _cmdStatic(stratId):
    got = _resolve(stratId)
    sid = got["stratId"]
    static = runStaticChecks(sid)
    text = formatStaticReport(sid, static)
    print(text)
    path = saveStratValRun(
        sid,
        {
            "mode": "static",
            "verdict": static.get("grade") or "FAIL",
            "static": static,
            "reportText": text,
        },
    )
    print(f"\n[저장] {path}")


def _runWatchBt(stratId, stratName, startYmd, endYmd, initCash):
    watchRows = listActiveWatch(useBtOnly=True) or []
    if not watchRows:
        print("[중단] USE_BT=Y 관심종목 없음")
        sys.exit(1)

    static = runStaticChecks(stratId)
    stkResults = []
    for row in watchRows:
        stkCd = row.get("STK_CD") or ""
        stkNm = row.get("STK_NM") or stkCd
        if not stkCd:
            continue
        print(f"  BT {stkCd} {stkNm} …")
        stkResults.append(_runOneStkBt(stratId, stkCd, stkNm, startYmd, endYmd, initCash))

    gateRes = evaluateBtGates(stkResults)
    label = f"관심종목 USE_BT=Y · {len(watchRows)}종"
    return _finishReport(
        stratId,
        stratName,
        static,
        gateRes,
        stkResults,
        startYmd,
        endYmd,
        initCash,
        label,
        "watch",
    )


def _runRank16h(stratId, stratName, rankDt, tradeDt, initCash):
    if not str(stratId).startswith("maTouchScale"):
        print("[오류] rank16h 는 maTouchScale_v1 전용")
        sys.exit(1)

    static = runStaticChecks(stratId)
    print(formatStaticReport(stratId, static))
    print()

    univ = screenUnivFromDb(rankDt)
    if not univ.get("ok"):
        print(f"[중단] 유니버스 없음: {univ.get('errMsg')}")
        sys.exit(1)

    picks = univ.get("picks") or []
    stkResults = []
    for p in picks:
        stkCd = p.get("stkCd") or ""
        stkNm = p.get("stkNm") or stkCd
        if not stkCd:
            continue
        print(f"  BT {stkCd} {stkNm} …")
        stkResults.append(_runOneStkBt(stratId, stkCd, stkNm, tradeDt, tradeDt, initCash))

    gateRes = evaluateBtGates(stkResults)
    label = f"rank16h {rankDt} 선정 {len(picks)}종 → BT {tradeDt}"
    return _finishReport(
        stratId,
        stratName,
        static,
        gateRes,
        stkResults,
        tradeDt,
        tradeDt,
        initCash,
        label,
        "rank16h",
        extra={"rankDt": rankDt, "tradeDt": tradeDt, "univCnt": len(picks)},
    )


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "help", "?"):
        print(_usage())
        return

    if args[0] == "list":
        _cmdList()
        return

    if args[0] == "static":
        if len(args) < 2:
            print(_usage())
            sys.exit(1)
        _cmdStatic(args[1])
        return

    got = _resolve(args[0])
    sid = got["stratId"]
    sname = got.get("name") or sid

    if len(args) >= 2 and args[1] == "watch":
        if len(args) < 4:
            print(_usage())
            sys.exit(1)
        startYmd = _normYmd(args[2])
        endYmd = _normYmd(args[3])
        cash = _parseCash(args[4] if len(args) > 4 else None)
        if not startYmd or not endYmd:
            print("[오류] 날짜 형식 YYYYMMDD")
            sys.exit(1)
        _runWatchBt(sid, sname, startYmd, endYmd, cash)
        return

    if len(args) >= 2 and args[1] == "rank16h":
        if len(args) < 4:
            print(_usage())
            sys.exit(1)
        rankDt = _normYmd(args[2])
        tradeDt = _normYmd(args[3])
        cash = _parseCash(args[4] if len(args) > 4 else None)
        if not rankDt or not tradeDt:
            print("[오류] 날짜 형식 YYYYMMDD")
            sys.exit(1)
        _runRank16h(sid, sname, rankDt, tradeDt, cash)
        return

    print(_usage())


if __name__ == "__main__":
    main()
