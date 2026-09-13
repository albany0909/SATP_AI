"""
전략 검증 코어 — 정적 점검 + 배치 BT 게이트 판정.

텔레그램 val / Cursor Skill 이 공통 사용.
"""

from pathlib import Path

from strategies.specLoader import listStratSpecs, loadStratSpec, resolveStratRef
from utils.termLog import termLog

_ROOT = Path(__file__).resolve().parent.parent
_DOCS_DIR = _ROOT / "strategies" / "docs"
_IMPL_DIR = _ROOT / "strategies" / "impl"

# 배치 게이트 기본 임계 (환경에 맞게 조정 가능)
GATE_MIN_OK_STKS = 3
GATE_MIN_TRADE_SUM = 10
GATE_MAX_AVG_MDD = 25.0
GATE_NEED_POS_RET = True  # 평균 복리>0 또는 플러스 종목≥1

# stratId prefix → impl 파일 (engine registry 와 동일 유지)
_IMPL_BY_PREFIX = {
    "mahaPullback": "mahaPullback.py",
    "alignMaBreak": "alignMaBreak.py",
    "highPullback": "highPullback.py",
    "bullBodyMa2050": "bullBodyMa2050.py",
    "maTouchScale": "maTouchScale.py",
    "maSupportEod": "maSupportEod.py",
}


def _implFileForStrat(stratId):
    """연결된 impl 파일명 또는 None."""
    sid = str(stratId or "")
    for prefix, fn in _IMPL_BY_PREFIX.items():
        if sid.startswith(prefix):
            return fn
    return None


def resolveValidateStrat(raw):
    """전략 참조 → {ok, stratId, name, seq?}."""
    got = resolveStratRef(raw)
    if not got.get("ok"):
        return got
    spec = loadStratSpec(got["stratId"])
    if spec is None:
        return {"ok": False, "errMsg": f"스펙 없음: {got['stratId']}"}
    return {
        "ok": True,
        "stratId": got["stratId"],
        "algCd": got.get("algCd") or spec.get("algCd") or "",
        "name": got.get("name") or spec.get("name") or got["stratId"],
        "seq": got.get("seq"),
        "spec": spec,
    }


def runStaticChecks(stratId):
    """
    명세/스펙/구현 존재·연계 정적 검증.
    반환: {ok, grade, checks:[], miss:[], notes:[]}
    """
    checks = []
    miss = []
    notes = []

    spec = loadStratSpec(stratId)
    if not spec:
        miss.append("specs JSON 없음")
        return {
            "ok": False,
            "grade": "FAIL",
            "checks": checks,
            "miss": miss,
            "notes": notes,
            "spec": None,
        }
    checks.append(f"spec: status={spec.get('status')} enabled={spec.get('enabled')}")
    checks.append(f"timeframe={spec.get('timeframe')}")

    docPath = _DOCS_DIR / f"{stratId}.md"
    if docPath.is_file():
        checks.append(f"docs OK ({docPath.name})")
    else:
        # 버전이 붙은 stem 과 동일 파일
        miss.append(f"docs 없음: strategies/docs/{stratId}.md")

    # impl 연결 — 엔진 registry와 동일 규칙
    implOk = False
    implFn = _implFileForStrat(stratId)
    if implFn:
        implPath = _IMPL_DIR / implFn
        if implPath.is_file():
            implOk = True
            checks.append(f"impl: {implFn}")
        else:
            miss.append(f"impl {implFn} 없음")
    else:
        miss.append(f"runBacktest 미연결 전략: {stratId}")
        notes.append("backtest/engine.py · stratValidate._IMPL_BY_PREFIX 등록 필요")

    # params 비어 있지 않은지
    params = spec.get("params") or {}
    if params:
        checks.append(f"params 키 수={len(params)}")
    else:
        notes.append("params 비어 있음 — 기본값만 쓰는지 확인")

    # 전략별 필수 파라미터 키
    if str(stratId).startswith("alignMaBreak") or str(stratId).startswith(
        "highPullback"
    ):
        for key in ("buyAfterTm", "exitAfterTm", "buyAmtWon", "exitStopMa"):
            if key not in params:
                miss.append(f"params 누락: {key}")
            else:
                checks.append(f"param {key}={params.get(key)}")
    if str(stratId).startswith("highPullback"):
        for key in ("setupMinFluRt", "lookbackHigh", "volMult"):
            if key not in params:
                notes.append(f"params 권장키 없음: {key}")
            else:
                checks.append(f"param {key}={params.get(key)}")
    if str(stratId).startswith("bullBodyMa2050"):
        for key in (
            "buyAmtWon",
            "exitEodTm",
            "smaTouchA",
            "smaTouchB",
            "entryBarMin",
            "exitBarMin",
            "minAmtMn",
        ):
            if key not in params:
                miss.append(f"params 누락: {key}")
            else:
                checks.append(f"param {key}={params.get(key)}")
        notes.append("유니버스=키움조건식·BT일봉근사 / 익절=매수당일 15:18 전량")
    if str(stratId).startswith("maTouchScale"):
        for key in (
            "maSteps",
            "buyAmtByStep",
            "exitAfterTm",
            "buyCutoffTm",
            "stopMa",
            "takeMa",
            "hardStopPct",
            "tp1Pct",
            "tp1QtyRatio",
            "tp2Pct",
            "tp2QtyRatio",
            "halfTpPct",
            "banRebuySameDay",
            "confirmMode",
            "confirmBarMin",
            "buyCutoffIfNextClosed",
            "nextClosedHalfTm",
            "nextClosedHalfMinPct",
            "nextClosedHalfMaxPct",
            "eodFlatIfNoProfit",
            "afterHalfStopPct",
            "breakevenAfterTp1",
            "buyBanUntilTm",
            "reclaimMinPct",
            "banBuyIfOpenBelowSma",
            "halfAddNextDay",
            "halfAddMinPnlPct",
            "halfAddQtyRatio",
            "halfTpFromStat",
            "rsiTpPeriod",
            "rsiTpMin",
            "tp3Pct",
            "tp3QtyRatio",
            "tp4Pct",
            "limitWaitSec",
        ):
            if key not in params:
                miss.append(f"params 누락: {key}")
            else:
                checks.append(f"param {key}={params.get(key)}")
        from utils.stratDeskSummary import buildStratValidateNote

        notes.append(buildStratValidateNote(stratId) or "규칙 요약 없음")
    if str(stratId).startswith("maSupportEod"):
        for key in (
            "smaLen",
            "weakBearMaxPct",
            "dojiMaxPct",
            "buyAmtWon",
            "banSameDayBreakoutBuy",
            "paperOnly",
            "buyStartTm",
            "buyEndTm",
            "sellStartTm",
            "sellEndTm",
        ):
            if key not in params:
                miss.append(f"params 누락: {key}")
            else:
                checks.append(f"param {key}={params.get(key)}")
        from utils.stratDeskSummary import buildStratValidateNote

        notes.append(buildStratValidateNote(stratId) or "규칙 요약 없음")
    if miss:
        grade = "FAIL"
        ok = False
    else:
        grade = "PASS"
        ok = True
    if not implOk:
        grade = "FAIL"
        ok = False

    return {
        "ok": ok,
        "grade": grade,
        "checks": checks,
        "miss": miss,
        "notes": notes,
        "spec": spec,
    }


def formatStaticReport(stratId, staticRes):
    """정적 검증 텍스트."""
    from strategies.specLoader import getAlgCd, resolveAlgRef

    got = resolveAlgRef(stratId)
    title = (got.get("algCd") if got.get("ok") else "") or getAlgCd(stratId) or stratId
    lines = [
        f"[정적검증] {title}",
        f"결과: {staticRes.get('grade')}",
    ]
    for c in staticRes.get("checks") or []:
        lines.append(f"  · {c}")
    for m in staticRes.get("miss") or []:
        lines.append(f"  ✗ {m}")
    for n in staticRes.get("notes") or []:
        lines.append(f"  ! {n}")
    return "\n".join(lines)


def normMktLabel(raw):
    """
    시장 표시 → 코스피 / 코스닥 / 기타 문자열.
    MARKET_NAME·MRKT_TP 값 수용.
    """
    text = str(raw or "").strip()
    if not text:
        return "-"
    low = text.lower()
    tp = text
    if tp in ("0", "001") or "코스피" in text or "kospi" in low:
        return "코스피"
    if tp in ("10", "101") or "코스닥" in text or "kosdaq" in low:
        return "코스닥"
    return text


def enrichStkMktMeta(stkCd, stkNm=None, marketName=None, mrktTp=None):
    """
    종목 메타 보강 (없으면 AI_STK_INFO 조회).
    반환: {stkCd, stkNm, mktLabel}
    """
    code = str(stkCd or "").strip()
    name = str(stkNm or "").strip()
    mktRaw = marketName if marketName is not None else mrktTp
    mktLabel = normMktLabel(mktRaw) if mktRaw else "-"

    if (not name or mktLabel == "-") and code:
        try:
            from utils.stkInfoStore import findStkByCd

            row = findStkByCd(code)
            if row:
                if not name:
                    name = str(row.get("STK_NM") or "").strip()
                if mktLabel == "-":
                    mktLabel = normMktLabel(row.get("MARKET_NAME"))
        except Exception:
            pass

    return {
        "stkCd": code or "-",
        "stkNm": name or code or "-",
        "mktLabel": mktLabel or "-",
    }



def evaluateBtGates(stkResults, gates=None):
    """
    종목별 BT 결과 목록으로 게이트 판정.
    stkResults: [{stkCd, stkNm, ok, errMsg?, stats?, tradeCnt?}, ...]
    반환: {verdict, metrics, reasons, top, bottom}
    """
    g = {
        "minOkStks": GATE_MIN_OK_STKS,
        "minTradeSum": GATE_MIN_TRADE_SUM,
        "maxAvgMdd": GATE_MAX_AVG_MDD,
        "needPosRet": GATE_NEED_POS_RET,
    }
    if isinstance(gates, dict):
        g.update(gates)

    okRows = [r for r in (stkResults or []) if r.get("ok")]
    failRows = [r for r in (stkResults or []) if not r.get("ok")]

    tradeSum = 0
    retList = []
    mddList = []
    winList = []
    for r in okRows:
        s = r.get("stats") or {}
        tc = int(s.get("tradeCnt") or 0)
        tradeSum += tc
        retList.append(float(s.get("finalRetPct") or 0.0))
        mddList.append(float(s.get("maxDdPct") or 0.0))
        winList.append(float(s.get("winRate") or 0.0))

    okCnt = len(okRows)
    avgRet = sum(retList) / len(retList) if retList else 0.0
    avgMdd = sum(mddList) / len(mddList) if mddList else 0.0
    avgWin = sum(winList) / len(winList) if winList else 0.0
    posCnt = sum(1 for x in retList if x > 0)

    reasons = []
    hardFail = False
    softFail = False

    if okCnt < g["minOkStks"]:
        reasons.append(f"유효종목 {okCnt}<{g['minOkStks']}")
        hardFail = True
    if tradeSum < g["minTradeSum"]:
        reasons.append(f"합산거래 {tradeSum}<{g['minTradeSum']}")
        softFail = True
    if mddList and avgMdd > g["maxAvgMdd"]:
        reasons.append(f"평균MDD {avgMdd:.1f}%>{g['maxAvgMdd']}")
        hardFail = True
    if g["needPosRet"] and retList:
        if avgRet <= 0 and posCnt < 1:
            reasons.append("평균복리≤0 이고 플러스 종목 없음")
            hardFail = True

    if hardFail:
        verdict = "FAIL"
    elif softFail or (okCnt > 0 and tradeSum < g["minTradeSum"] * 2):
        verdict = "CONDITIONAL"
    elif okCnt == 0:
        verdict = "FAIL"
        reasons.append("성공 시뮬 0건")
    else:
        verdict = "PASS"
        if not reasons:
            reasons.append("게이트 통과")

    # 상위/하위
    scored = []
    for r in okRows:
        s = r.get("stats") or {}
        scored.append(
            {
                "stkCd": r.get("stkCd"),
                "stkNm": r.get("stkNm") or r.get("stkCd"),
                "finalRetPct": float(s.get("finalRetPct") or 0),
                "maxDdPct": float(s.get("maxDdPct") or 0),
                "tradeCnt": int(s.get("tradeCnt") or 0),
            }
        )
    top = sorted(scored, key=lambda x: -x["finalRetPct"])[:3]
    bottom = sorted(scored, key=lambda x: x["finalRetPct"])[:3]

    return {
        "verdict": verdict,
        "reasons": reasons,
        "metrics": {
            "okCnt": okCnt,
            "failCnt": len(failRows),
            "tradeSum": tradeSum,
            "avgRetPct": avgRet,
            "avgMddPct": avgMdd,
            "avgWinRate": avgWin,
            "posCnt": posCnt,
        },
        "top": top,
        "bottom": bottom,
        "gates": g,
    }


def formatValidateReport(
    stratId,
    stratName,
    staticRes,
    gateRes,
    startDt,
    endDt,
    initCash,
    universeLabel,
    stkResults=None,
):
    """종합 검증 리포트 텍스트."""
    m = (gateRes or {}).get("metrics") or {}
    lines = [
        f"[전략검증] {stratId} / {stratName or '-'}",
        f"Static: {staticRes.get('grade')} "
        + (
            f"— 누락 {len(staticRes.get('miss') or [])}건"
            if staticRes.get("miss")
            else "— 명세·스펙·구현 연계 OK"
        ),
        f"Universe: {universeLabel}",
        f"기간: {startDt} ~ {endDt}",
        f"투자금: {float(initCash or 0) / 10000:,.0f}만원",
        f"BT: 성공 {m.get('okCnt', 0)} / 실패 {m.get('failCnt', 0)} "
        f"· 거래합 {m.get('tradeSum', 0)} "
        f"· 평균승률 {m.get('avgWinRate', 0):.1f}% "
        f"· 평균복리 {m.get('avgRetPct', 0):+.2f}% "
        f"· 평균MDD {m.get('avgMddPct', 0):.2f}%",
        f"Verdict: {(gateRes or {}).get('verdict') or 'N/A'}",
    ]
    # 부분익절을 포함한 수량가중 실현손익 (종목별 BT 합산)
    buyAmtSum = 0.0
    sellAmtSum = 0.0
    pnlSum = 0.0
    scaleOutSum = 0
    openQtySum = 0
    for r in stkResults or []:
        if not r.get("ok"):
            continue
        sp = r.get("scalePnl") or {}
        buyAmtSum += float(sp.get("buyAmt") or 0)
        sellAmtSum += float(sp.get("sellAmt") or 0)
        pnlSum += float(sp.get("pnlWon") or 0)
        scaleOutSum += int(sp.get("scaleOutCnt") or 0)
        openQtySum += int(sp.get("openQty") or 0)
    if buyAmtSum or sellAmtSum:
        lines.append(
            f"수량가중 수익금: {pnlSum:+,.0f}원 "
            f"(매수합 {buyAmtSum:,.0f} / 매도합 {sellAmtSum:,.0f} · "
            f"부분익절 {scaleOutSum}건"
            + (f" · 미청산 {openQtySum}주" if openQtySum else "")
            + ")"
        )
    for r in (gateRes or {}).get("reasons") or []:
        lines.append(f"  · {r}")

    top = (gateRes or {}).get("top") or []
    if top:
        lines.append("Top(복리):")
        for t in top:
            lines.append(
                f"  {t.get('stkNm')}({t.get('stkCd')}) "
                f"{t.get('finalRetPct'):+.2f}% "
                f"MDD{t.get('maxDdPct'):.1f}% T{t.get('tradeCnt')}"
            )
    bottom = (gateRes or {}).get("bottom") or []
    if bottom:
        lines.append("Bottom(복리):")
        for t in bottom:
            lines.append(
                f"  {t.get('stkNm')}({t.get('stkCd')}) "
                f"{t.get('finalRetPct'):+.2f}% "
                f"MDD{t.get('maxDdPct'):.1f}% T{t.get('tradeCnt')}"
            )

    # 거래 상세 — 종목·시장·포착/매수/매도
    lines.append("")
    lines.append("■ 거래상세")
    lines.append(
        "  [성공실패] 종목명 | 종목코드 | 시장 | 포착일 | 매수일 | 매도일 | "
        "매수가 | 매도가 | 수익률"
    )
    tradeLines = []
    for r in stkResults or []:
        if not r.get("ok"):
            continue
        meta = enrichStkMktMeta(
            r.get("stkCd"),
            stkNm=r.get("stkNm"),
            marketName=r.get("marketName"),
            mrktTp=r.get("mrktTp"),
        )
        rounds = r.get("rounds") or []
        if not rounds:
            continue
        for rd in rounds:
            tradeLines.append(
                {
                    "stkNm": meta["stkNm"],
                    "stkCd": meta["stkCd"],
                    "mktLabel": meta["mktLabel"],
                    "catchDt": rd.get("catchDt") or "-",
                    "buyDt": rd.get("buyDt") or "-",
                    "sellDt": rd.get("sellDt") or "-",
                    "buyPrc": float(rd.get("buyPrc") or 0),
                    "sellPrc": float(rd.get("sellPrc") or 0),
                    "retPct": float(rd.get("retPct") or 0),
                    "result": rd.get("result") or "-",
                    "maxPnlPct": rd.get("maxPnlPct"),
                    "sellReason": rd.get("sellReason") or "",
                }
            )
    if not tradeLines:
        lines.append("  (거래 없음)")
    else:
        # 매수일 순
        tradeLines.sort(
            key=lambda x: (x.get("buyDt") or "", x.get("stkCd") or "")
        )
        maxShow = 50
        for i, t in enumerate(tradeLines[:maxShow], 1):
            lines.append(
                f"  {i}. {t['result']} "
                f"{t['stkNm']} | {t['stkCd']} | {t['mktLabel']} | "
                f"{t['catchDt']} | {t['buyDt']} | {t['sellDt']} | "
                f"{t['buyPrc']:,.0f} | {t['sellPrc']:,.0f} | "
                f"{t['retPct']:+.2f}%"
                + (
                    f" | 고점 {t['maxPnlPct']:+.2f}%"
                    if t.get("maxPnlPct") is not None
                    else ""
                )
            )
        if len(tradeLines) > maxShow:
            lines.append(f"  ... 외 {len(tradeLines) - maxShow}건")
        okN = sum(
            1
            for t in tradeLines
            if str(t.get("result") or "").replace(" ", "") in ("[성공]", "성공")
        )
        failN = len(tradeLines) - okN
        lines.append(f"  합계: {len(tradeLines)}건 (성공 {okN} / 실패 {failN})")
        mfeRows = [
            {
                "MAX_PNL_PCT": t.get("maxPnlPct"),
                "EXIT_REASON": t.get("sellReason") or "",
                "AVG_BUY_PX": t.get("buyPrc"),
            }
            for t in tradeLines
            if t.get("maxPnlPct") is not None
        ]
        if mfeRows:
            try:
                from utils.maTouchMfeStats import formatHalfTpStatsLines

                halfNow = 3.0
                try:
                    spec = loadStratSpec(stratId) or {}
                    halfNow = float(
                        (spec.get("params") or {}).get("tp1Pct")
                        or (spec.get("params") or {}).get("halfTpPct")
                        or 1.5
                    )
                except Exception:
                    pass
                for ln in formatHalfTpStatsLines(mfeRows, halfNow):
                    if ln:
                        lines.append("  " + ln if not ln.startswith("[") else ln)
            except Exception:
                pass

    # 실패 종목 일부
    fails = [r for r in (stkResults or []) if not r.get("ok")]
    if fails:
        lines.append(f"실패예: {len(fails)}건")
        for r in fails[:5]:
            lines.append(
                f"  {r.get('stkCd')}: {(r.get('errMsg') or '-')[:60]}"
            )

    # Next
    lines.append("Next:")
    verdict = (gateRes or {}).get("verdict")
    if staticRes.get("grade") == "FAIL":
        lines.append("  1) 정적 누락(docs/specs/impl) 보완")
    if verdict == "FAIL":
        lines.append("  2) 기간·유니버스 확대 또는 파라미터 재검토")
        lines.append("  3) 실패 종목 분봉 적재/신호 희소 원인 확인")
    elif verdict == "CONDITIONAL":
        lines.append("  1) 표본(거래·종목) 늘려 재검증")
        lines.append("  2) 통과 후 status=test 유지·모의 관찰")
    else:
        lines.append("  1) status=test 유지, 모의 관찰 후 live 후보 검토")
        lines.append("  2) USE_TRADE 는 수동 전환만")

    return "\n".join(lines)


def listRegisteredStratsLine():
    """도움말용 전략 목록 한 줄."""
    specs = listStratSpecs()
    if not specs:
        return "(없음)"
    return ", ".join(f"{s['seq']}:{s.get('algCd') or s['stratId']}" for s in specs)


# 모듈 로드 시 로그 소음 방지 — 필요 시 termLog 호출
def _log(msg):
    termLog(msg)
