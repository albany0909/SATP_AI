"""
알고리즘 당일 거래 복기 — 승률·수익·이벤트·개선 힌트.

저장: config/data/algDayReview/{tradeDt}_{algCd}.json
실주문 없음. AI_STRAT_LIVE_TRADE · AI_ALG_EVT · AI_ALG_PNL_DAY 기반.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from utils.termLog import termLog

_REVIEW_DIR = Path(__file__).resolve().parent.parent / "config" / "data" / "algDayReview"


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _reviewPath(tradeDt, algCd):
    return _REVIEW_DIR / f"{tradeDt}_{algCd}.json"


def _loadJson(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _saveJson(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _skipReasonBuckets(items):
    """BUY_SKIP note/reason 집계."""
    buckets = {}
    for x in items or []:
        if str(x.get("evtTp") or "") != "BUY_SKIP":
            continue
        key = str(x.get("reason") or "OTHER").strip() or "OTHER"
        note = str(x.get("note") or "")
        if "CROSS_ALG" in note or "CROSS_ALG" in key:
            key = "CROSS_ALG_HOLD"
        elif "REBUY" in note or "REBUY" in key:
            key = "REBUY_BANNED"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _flt(val):
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _roundOrNone(val, digits=2):
    num = _flt(val)
    if num is None:
        return None
    return round(num, digits)


def _roundHalfTpStat(stat):
    """JSON 저장용 — 소수 반올림."""
    if not stat:
        return {}
    out = dict(stat)
    for key in ("avgMfe", "medMfe", "avgStopMfe", "medStopMfe", "suggestPct"):
        if out.get(key) is not None:
            out[key] = _roundOrNone(out.get(key), 2)
    hits = []
    for row in out.get("hits") or []:
        hits.append(
            {
                "pct": row.get("pct"),
                "cnt": int(row.get("cnt") or 0),
                "rate": round(float(row.get("rate") or 0), 1),
            }
        )
    out["hits"] = hits
    return out


def _halfTpNow(algCd):
    """실매매 1차 %. 통계치 오버레이가 있으면 그걸, 없으면 spec."""
    code = str(algCd or "").strip().upper()
    if code == "MTS01":
        try:
            from config.settingsStore import getMaTouchLive

            live = getMaTouchLive() or {}
            if live.get("halfTpFromStat") and live.get("halfTpPct") is not None:
                return float(live.get("halfTpPct"))
        except Exception:
            pass
    try:
        from strategies.specLoader import loadStratSpec, resolveAlgRef

        got = resolveAlgRef(algCd)
        if not got.get("ok"):
            return 3.0 if code == "MTS01" else None
        spec = loadStratSpec(got.get("stratId") or "") or {}
        params = spec.get("params") or {}
        if "halfTpPct" not in params:
            return None
        return float(params.get("halfTpPct") or 3)
    except Exception:
        return 3.0 if code == "MTS01" else None


def _closeMfePct(row):
    """청산 행 고점%. DB 값 없으면 분봉 보강."""
    stored = _flt(row.get("MAX_PNL_PCT"))
    if stored is not None:
        return stored
    try:
        from utils.maTouchMfeStats import mfeOfRow

        return mfeOfRow(row)
    except Exception:
        return None


def _buildHalfTp(algCd, todayRows, histRows, fromYmd=None):
    """
    당일·누적 MFE / 1차 익절 도달률.
    누적 표본은 +% 1차 익절이 처음 쓰인 날부터 현재까지.
    권고값은 장마감 복기에서 실매매 1차에 반영한다.
    """
    now = _halfTpNow(algCd)
    if now is None:
        return {"applicable": False}
    from utils.maTouchMfeStats import formatHalfTpStatsLines, summarizeHalfTpFromRows

    startYmd = str(fromYmd or "").replace("-", "")[:8]
    # 고점 미적재 구간은 분봉으로 보강 (1차 익절 도입일~현재만 조회하므로 건수 제한)
    histUseMin = bool(startYmd)
    todayStat = _roundHalfTpStat(
        summarizeHalfTpFromRows(todayRows, useMinBars=True)
    )
    histStat = _roundHalfTpStat(
        summarizeHalfTpFromRows(histRows, useMinBars=histUseMin)
    )
    dayLines = [
        x.strip()
        for x in formatHalfTpStatsLines(
            todayRows, now, title="[당일] 매수~청산 최대수익률(MFE)", useMinBars=True
        )
        if str(x).strip()
    ]
    histTitle = "[10거래일 매수] 1차전 손절 전 고점(MFE)"
    if startYmd:
        histTitle = f"[10거래일 매수] 1차전 -2%손절 전 고점 {startYmd}~"
    histLines = [
        x.strip()
        for x in formatHalfTpStatsLines(
            histRows, now, title=histTitle, useMinBars=histUseMin
        )
        if str(x).strip()
    ]
    dayCloses = []
    for row in todayRows or []:
        mfe = _closeMfePct(row)
        dayCloses.append(
            {
                "stkCd": row.get("STK_CD") or "",
                "stkNm": row.get("STK_NM") or "",
                "exitReason": row.get("EXIT_REASON") or "",
                "maxPnlPct": _roundOrNone(mfe, 2),
                "halfYn": row.get("HALF_YN") or "N",
            }
        )
    return {
        "applicable": True,
        "halfTpNow": now,
        "statFromYmd": startYmd,
        "today": todayStat,
        "hist": histStat,
        "dayLines": dayLines,
        "histLines": histLines,
        "closes": dayCloses,
    }


def _appendHalfTpInsights(insights, halfTp):
    """1차 익절 MFE 기반 문제·개선 (파라미터는 자동 변경하지 않음)."""
    if not halfTp or not halfTp.get("applicable"):
        return insights
    problems = list(insights.get("problems") or [])
    keeps = list(insights.get("keeps") or [])
    improves = list(insights.get("improves") or [])
    now = float(halfTp.get("halfTpNow") or 3)
    today = halfTp.get("today") or {}
    hist = halfTp.get("hist") or {}
    fromYmd = str(halfTp.get("statFromYmd") or "")
    n = int(today.get("n") or 0)
    med = today.get("medMfe")
    medStop = today.get("medStopMfe")
    names = []
    for row in halfTp.get("closes") or []:
        mfe = _flt(row.get("maxPnlPct"))
        nm = row.get("stkNm") or row.get("stkCd") or ""
        if not nm:
            continue
        if mfe is not None:
            names.append(f"{nm} {mfe:+.2f}%")
        else:
            names.append(f"{nm} 고점없음")
    if names:
        keeps.append("당일 종목 고점 " + " · ".join(names))
    missN = sum(1 for row in halfTp.get("closes") or [] if row.get("maxPnlPct") is None)
    if missN:
        problems.append(
            f"고점 미적재 {missN}건 — 청산 당시 MFE 미기록(분봉·MAX_PNL_PCT 없음)"
        )
    if n:
        if med is not None:
            keeps.append(f"당일 고점(MFE) 중앙 {med:+.2f}% · 표본 {n}건")
        hitNow = None
        for h in today.get("hits") or []:
            if abs(float(h.get("pct") or 0) - now) < 1e-9:
                hitNow = h
                break
        if hitNow is not None and int(hitNow.get("cnt") or 0) == 0:
            problems.append(
                f"당일 1차 +{now:g}% 도달 0/{n}건 — 절반익절이 한 번도 안 나감"
            )
        if medStop is not None and medStop + 1e-9 < now:
            improves.insert(
                0,
                f"당일 손절 고점 중앙 {medStop:+.2f}% < 현재 1차 +{now:g}% "
                "— 1차 익절 인하 검토",
            )
    sug = hist.get("suggestPct")
    histN = int(hist.get("stopN") or hist.get("n") or 0)
    if sug is not None and abs(float(sug) - now) > 0.05:
        note = "" if hist.get("sampleOk") else " (누적 표본 부족, 참고)"
        improves.insert(
            0,
            f"10거래일 매수 권고 1차 +{sug:g}% (현재 +{now:g}%, "
            f"1차전손절 {histN}건"
            + (f", {fromYmd}~" if fromYmd else "")
            + f"){note} — 손절 전 고점 평균, 장마감 복기에서 실매매 1차에 반영",
        )
    placeholderP = "(특이 리스크 없음)"
    if any(p != placeholderP for p in problems):
        problems = [p for p in problems if p != placeholderP]
    placeholderK = "(당일 수익 청산 없음 — 보유·EOD 대기 중일 수 있음)"
    if any(p != placeholderK for p in keeps):
        keeps = [p for p in keeps if p != placeholderK]
    return {
        "problems": problems,
        "keeps": keeps,
        "improves": improves,
    }


def _buildInsights(algCd, tradeDt, closes, items, pnlSnap, skipBuckets):
    """규칙 기반 복기 코멘트 (LLM 없이)."""
    problems = []
    keeps = []
    improves = []
    wins = [c for c in closes if float(c.get("pnlWon") or 0) > 0]
    losses = [c for c in closes if float(c.get("pnlWon") or 0) < 0]
    buyCnt = sum(1 for x in items if x.get("evtTp") == "BUY")
    skipCnt = sum(1 for x in items if x.get("evtTp") == "BUY_SKIP")

    if not closes and buyCnt == 0 and skipCnt == 0:
        problems.append("당일 매수·청산·스킵 이벤트 없음 — 신호/유니버스·장 상태 확인")
    if skipCnt > buyCnt and skipCnt >= 3:
        problems.append(f"매수 스킵({skipCnt}) > 매수({buyCnt}) — 진입 조건·복수전략 차단 검토")
    if skipBuckets.get("CROSS_ALG_HOLD"):
        problems.append(
            f"복수전략 보유로 스킵 {skipBuckets['CROSS_ALG_HOLD']}건 — 종목 분산·전략 역할 분리 검토"
        )
    if losses and not wins:
        problems.append(f"당일 청산 전패({len(losses)}건) — 손절·진입 타이밍 점검")
    if wins:
        best = max(wins, key=lambda c: float(c.get("pnlPct") or 0))
        keeps.append(
            f"수익 청산 {len(wins)}건 — 최고 {best.get('stkNm') or best.get('stkCd')} "
            f"{float(best.get('pnlPct') or 0):+.2f}% ({best.get('exitReason') or '-'})"
        )
    if pnlSnap:
        tdy = float(pnlSnap.get("tdyPnl") or 0)
        mon = float(pnlSnap.get("monPnl") or 0)
        if tdy > 0:
            keeps.append(f"당일 실현손익 +{tdy:,.0f}원")
        elif tdy < 0:
            problems.append(f"당일 실현손익 {tdy:,.0f}원")
        if mon < 0 and tdy < 0:
            improves.append("월간·당일 모두 마이너스 — params·게이트 재검증(val) 후 소액/모의 유지")

    improves.append("수정 후: examples/valStrat.py 또는 텔레그램 val 로 BT 게이트 PASS 확인")
    improves.append("PASS·CONDITIONAL 이면 spec status=test 유지 · 실매매는 settings enabled 로 반영")

    if not problems:
        problems.append("(특이 리스크 없음)")
    if not keeps:
        keeps.append("(당일 수익 청산 없음 — 보유·EOD 대기 중일 수 있음)")

    return {
        "problems": problems,
        "keeps": keeps,
        "improves": improves,
    }


def buildAlgDayReview(algCd, tradeDt=None):
    """
    알고리즘 1개 · 1일 복기 JSON.
    AI_ALG_EVT · AI_STRAT_LIVE_TRADE · AI_ALG_PNL_DAY 기반.
    """
    from trading.deskLiveAlgs import algDisplayName, stripAlgTradeSuffix
    from utils.algEvtStore import listAlgEvt, resolveAlgMeta
    from utils.stratLiveTradeStore import listLiveTrades

    ymd = str(tradeDt or _todayYmd()).replace("-", "")[:8]
    code = str(algCd or "MTS01").strip().upper()[:5]
    meta = resolveAlgMeta(code) or {}

    items = []
    for r in listAlgEvt(code, ymd, limit=800) or []:
        items.append(
            {
                "evtTp": r.get("EVT_TP") or "",
                "reason": r.get("REASON") or "",
                "stkCd": r.get("STK_CD") or "",
                "stkNm": r.get("STK_NM") or "",
                "note": r.get("NOTE") or "",
                "evtTm": r.get("EVT_TM") or "",
                "px": float(r.get("PX") or 0) if r.get("PX") is not None else None,
                "sma5": float(r.get("SMA5") or 0) if r.get("SMA5") is not None else None,
                "sma10": float(r.get("SMA10") or 0) if r.get("SMA10") is not None else None,
                "sma15": float(r.get("SMA15") or 0) if r.get("SMA15") is not None else None,
                "sma20": float(r.get("SMA20") or 0) if r.get("SMA20") is not None else None,
                "qty": r.get("QTY"),
            }
        )

    todayRows = listLiveTrades(algCd=code, exitDt=ymd, limit=80) or []
    fromYmd = None
    if code == "MTS01":
        try:
            from utils.maTouchMfeStats import resolveHalfTpBuyFromYmd

            fromYmd = resolveHalfTpBuyFromYmd(ymd)
        except Exception:
            fromYmd = None
    if fromYmd:
        # 최근 10거래일 매수 청산. 1차전 손절 고점 평균용.
        histRows = listLiveTrades(algCd=code, entryDtFrom=fromYmd, limit=500) or []
    else:
        histRows = listLiveTrades(algCd=code, limit=200) or []
    closes = []
    for r in todayRows:
        mfe = _closeMfePct(r)
        closes.append(
            {
                "stkCd": r.get("STK_CD") or "",
                "stkNm": r.get("STK_NM") or "",
                "exitReason": r.get("EXIT_REASON") or "",
                "pnlWon": float(r.get("PNL_WON") or 0),
                "pnlPct": float(r.get("PNL_PCT") or 0),
                "steps": r.get("STEPS") or "",
                "halfYn": r.get("HALF_YN") or "N",
                "avgBuyPx": _flt(r.get("AVG_BUY_PX")),
                "exitPx": _flt(r.get("EXIT_PX")),
                "maxPx": _flt(r.get("MAX_PX")),
                "maxPnlPct": _roundOrNone(mfe, 2),
                "exitTm": r.get("EXIT_TM") or "",
            }
        )

    buyCnt = sum(1 for x in items if x.get("evtTp") == "BUY")
    skipCnt = sum(1 for x in items if x.get("evtTp") == "BUY_SKIP")
    sellCnt = sum(1 for x in items if x.get("evtTp") == "SELL")
    # 승패는 전량청산 라운드의 최종 손익 (분할익절 여부 무관, 본전은 제외)
    winCnt = sum(1 for c in closes if float(c.get("pnlWon") or 0) > 0)
    lossCnt = sum(1 for c in closes if float(c.get("pnlWon") or 0) < 0)
    totPnl = sum(float(c.get("pnlWon") or 0) for c in closes)
    totBuy = sum(
        float(c.get("pnlWon") or 0) / (float(c.get("pnlPct") or 1) / 100.0)
        if float(c.get("pnlPct") or 0)
        else 0
        for c in closes
    )
    wlCnt = winCnt + lossCnt
    winRate = (winCnt / wlCnt * 100.0) if wlCnt else 0.0
    retPct = (totPnl / totBuy * 100.0) if totBuy else 0.0

    pnlSnap = {}
    try:
        from utils.algPnlDayStore import listAlgPnlDay

        rows = listAlgPnlDay(mode="paper", tradeDt=ymd, algCd=code)
        if rows:
            r = rows[0]
            pnlSnap = {
                "tdyPnl": float(r.get("TDY_PNL") or 0),
                "tdyPnlRt": float(r.get("TDY_PNL_RT") or 0),
                "tdyWin": int(r.get("TDY_WIN") or 0),
                "tdyLoss": int(r.get("TDY_LOSS") or 0),
                "monPnl": float(r.get("MON_PNL") or 0),
                "monPnlRt": float(r.get("MON_PNL_RT") or 0),
                "totPnl": float(r.get("TOT_PNL") or 0),
                "totPnlRt": float(r.get("TOT_PNL_RT") or 0),
            }
    except Exception:
        pnlSnap = {}

    skipBuckets = _skipReasonBuckets(items)
    halfTp = _buildHalfTp(code, todayRows, histRows, fromYmd=fromYmd)
    insights = _buildInsights(code, ymd, closes, items, pnlSnap, skipBuckets)
    insights = _appendHalfTpInsights(insights, halfTp)

    name = stripAlgTradeSuffix(meta.get("name") or algDisplayName(code))
    todayMfe = (halfTp.get("today") or {}) if halfTp.get("applicable") else {}
    report = {
        "ok": True,
        "algCd": code,
        "algVer": meta.get("algVer") or "",
        "name": name,
        "tradeDt": ymd,
        "summary": {
            "buyCnt": buyCnt,
            "skipCnt": skipCnt,
            "sellCnt": sellCnt,
            "closeCnt": len(closes),
            "winCnt": winCnt,
            "lossCnt": lossCnt,
            "winRate": round(winRate, 2),
            "dayPnlWon": round(totPnl, 0),
            "dayRetPct": round(retPct, 4),
            "skipBuckets": skipBuckets,
            "medMfe": todayMfe.get("medMfe"),
            "halfTpNow": halfTp.get("halfTpNow") if halfTp.get("applicable") else None,
            "halfTpSuggest": (halfTp.get("hist") or {}).get("suggestPct")
            if halfTp.get("applicable")
            else None,
        },
        "pnlSnap": pnlSnap,
        "items": items,
        "closes": closes,
        "halfTp": halfTp,
        "insights": insights,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return report


def saveAlgDayReview(report, mode="paper"):
    """JSON + DB 저장. 경로 반환."""
    if not report or not report.get("ok"):
        return None
    path = _reviewPath(report.get("tradeDt"), report.get("algCd"))
    _saveJson(path, report)
    try:
        from utils.algDayReviewStore import upsertAlgDayReview

        upsertAlgDayReview(report, mode=mode)
    except Exception as err:
        termLog(f"[algDayReview] DB upsert 실패: {err}")
    return str(path)


def loadAlgDayReview(tradeDt, algCd, mode="paper"):
    """DB 우선, 없으면 JSON 파일."""
    try:
        from utils.algDayReviewStore import getAlgDayReview

        rep = getAlgDayReview(tradeDt, algCd, mode=mode)
        if rep:
            return rep
    except Exception:
        pass
    return _loadJson(_reviewPath(str(tradeDt).replace("-", "")[:8], algCd))


def reviewNeedsRebuild(report):
    """구버전 복기(halfTp 없음)는 매매일지 조회 시 재생성."""
    if not report or not report.get("ok"):
        return True
    return "halfTp" not in report


def formatAlgDayReviewText(report):
    """텔레그램·로그용."""
    if not report:
        return "[복기] 데이터 없음"
    s = report.get("summary") or {}
    ins = report.get("insights") or {}
    pnl = report.get("pnlSnap") or {}
    lines = [
        f"[장마감복기] {report.get('name')} ({report.get('algCd')}) v{report.get('algVer') or '-'}",
        f"일자 {report.get('tradeDt')}",
        f"청산 {s.get('closeCnt', 0)} · 승{s.get('winCnt', 0)}/패{s.get('lossCnt', 0)} "
        f"· 승률 {s.get('winRate', 0):.1f}%",
        f"당일실현 {s.get('dayPnlWon', 0):+,.0f}원 ({s.get('dayRetPct', 0):+.2f}%)",
        f"매수 {s.get('buyCnt', 0)} · 스킵 {s.get('skipCnt', 0)} · 매도이벤트 {s.get('sellCnt', 0)}",
    ]
    if pnl:
        lines.append(
            f"스냅(누적) 월 {pnl.get('monPnl', 0):+,.0f}원 ({pnl.get('monPnlRt', 0):+.2f}%)"
        )
    sk = s.get("skipBuckets") or {}
    if sk:
        lines.append("스킵사유: " + ", ".join(f"{k}={v}" for k, v in sk.items()))
    halfTp = report.get("halfTp") or {}
    if halfTp.get("applicable"):
        lines.append("")
        for block in (halfTp.get("dayLines") or []) + (halfTp.get("histLines") or []):
            if block.startswith("[") and "]" in block:
                title, _, rest = block[1:].partition("]")
                line = title.strip() + ((" " + rest.strip()) if rest.strip() else "")
                lines.append(f"▶ {line}")
            else:
                lines.append(f"  {block}")
    lines.append("")
    lines.append("▶ 문제")
    for x in ins.get("problems") or []:
        lines.append(f"  · {x}")
    lines.append("▶ 유지")
    for x in ins.get("keeps") or []:
        lines.append(f"  · {x}")
    lines.append("▶ 개선·검증")
    for x in ins.get("improves") or []:
        lines.append(f"  · {x}")
    return "\n".join(lines)


def runAllAlgDayReviews(tradeDt=None, mode="paper", logDetail=False):
    """운영 알고리즘 전체 복기 + JSON 저장 + algPnl 스냅샷 갱신.

    logDetail=True 이면 터미널에 전문 출력 (기본 False — 알림은 텔레그램만).
    """
    from trading.deskLiveAlgs import listDeskLiveAlgs

    ymd = str(tradeDt or _todayYmd()).replace("-", "")[:8]
    out = []
    try:
        from utils.algPnlDayStore import rebuildAlgPnlDay

        rebuildAlgPnlDay(mode=mode, tradeDt=ymd)
    except Exception as err:
        termLog(f"[algDayReview] algPnl 스냅샷 실패: {err}")

    for row in listDeskLiveAlgs() or []:
        alg = row.get("algCd") or ""
        if not alg:
            continue
        try:
            rep = buildAlgDayReview(alg, ymd)
            path = saveAlgDayReview(rep, mode=mode)
            out.append({"algCd": alg, "path": path, "report": rep})
            if logDetail:
                termLog(formatAlgDayReviewText(rep))
            else:
                s = rep.get("summary") or {}
                termLog(
                    f"[algDayReview] {alg} 저장 {path} "
                    f"· 청산{s.get('closeCnt', 0)} 승률{s.get('winRate', 0):.0f}%"
                )
            if str(alg).upper() == "MTS01":
                try:
                    from utils.maTouchMfeStats import applySuggestHalfTp

                    ht = (rep.get("halfTp") or {})
                    hist = ht.get("hist") or {}
                    sug = hist.get("suggestPct")
                    if sug is None:
                        sug = (ht.get("today") or {}).get("suggestPct")
                    if sug is not None:
                        applySuggestHalfTp(
                            sug,
                            tradeDt=ymd,
                            sampleN=int(hist.get("stopN") or hist.get("n") or 0),
                            fromYmd=ht.get("statFromYmd"),
                        )
                except Exception as halfErr:
                    termLog(f"[algDayReview] 1차익절 반영 실패: {halfErr}")
        except Exception as err:
            termLog(f"[algDayReview] {alg} 실패: {err}")
    return {"ok": True, "tradeDt": ymd, "reviews": out}


def sendAlgDayReviewTelegram(reviews, tradeDt=None):
    """당일 복기 리포트만 텔레그램 전송 (pushKind=dayReview)."""
    from telegram.telSend import sendTelegramMsg

    ymd = str(tradeDt or _todayYmd()).replace("-", "")[:8]
    items = reviews or []
    sent = 0
    for item in items:
        rep = item.get("report")
        if not rep:
            continue
        body = formatAlgDayReviewText(rep)
        if len(body) > 3500:
            body = body[:3490] + "\n…"
        sendTelegramMsg(body, pushKind="dayReview")
        sent += 1
    if sent == 0:
        sendTelegramMsg(
            f"[장마감복기] {ymd}\n당일 운영 알고리즘 복기 데이터 없음",
            pushKind="dayReview",
        )
    return {"ok": True, "sent": sent, "tradeDt": ymd}
