"""분봉/틱 DB 로드 + 백테스트 엔진"""

from datetime import datetime, timedelta

from utils.dbConn import openDbConn
from strategies.impl.alignMaBreak import AlignMaBreakStrategy
from strategies.impl.bullBodyMa2050 import BullBodyMa2050Strategy
from strategies.impl.highPullback import HighPullbackStrategy
from strategies.impl.mahaPullback import MahaPullbackStrategy
from strategies.impl.maSupportEod import MaSupportEodStrategy
from strategies.impl.maTouchScale import MaTouchScaleStrategy, stampMinBarsWithDaySma
from strategies.specLoader import loadStratSpec

# 이평터치 일봉 SMA 워밍업 (거래일 20봉 ≈ 달력 여유)
_MA_TOUCH_SMA_WARM_DAYS = 80


def _isMaTouchStrat(stratId):
    return str(stratId or "").startswith("maTouchScale")


def _isMaSupportEodStrat(stratId):
    return str(stratId or "").startswith("maSupportEod")


def loadMinBars(stkCd, ticScope, startDt, endDt):
    """
    AI_MIN_STK_PRC → bar list (시간 오름차순).
    bar: open/high/low/close/qty/cntrTm/tradeDt
    """
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CNTR_TM, TRADE_DT, BAR_TM,
                       OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_MIN_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE=%s
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY CNTR_TM ASC
                """,
                (str(stkCd), str(ticScope), str(startDt), str(endDt)),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    bars = []
    for r in rows:
        if r["CLOSE_PRC"] is None:
            continue
        bars.append(
            {
                "cntrTm": r["CNTR_TM"],
                "tradeDt": r["TRADE_DT"],
                "barTm": r["BAR_TM"],
                "open": float(r["OPEN_PRC"] or 0),
                "high": float(r["HIGH_PRC"] or 0),
                "low": float(r["LOW_PRC"] or 0),
                "close": float(r["CLOSE_PRC"]),
                "qty": int(r["TRDE_QTY"] or 0),
            }
        )
    return bars


def loadDayBars(stkCd, startDt, endDt):
    """AI_DAY_STK_PRC 일봉 → bar list (일자 오름차순)."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0'
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    bars = []
    for r in rows:
        if r["CLOSE_PRC"] is None:
            continue
        dt = str(r["TRADE_DT"])
        bars.append(
            {
                "cntrTm": dt + "151800",
                "tradeDt": dt,
                "barTm": "151800",
                "open": float(r["OPEN_PRC"] or 0),
                "high": float(r["HIGH_PRC"] or 0),
                "low": float(r["LOW_PRC"] or 0),
                "close": float(r["CLOSE_PRC"]),
                "qty": int(r["TRDE_QTY"] or 0),
            }
        )
    return bars


def loadTicBars(stkCd, ticScope, startDt, endDt):
    """
    AI_TIC_STK_PRC → bar list (시간·SEQ 오름차순).
    1틱 차트 행을 봉처럼 사용 (OHLC가 동일할 수 있음).
    """
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CNTR_TM, TIC_SEQ, TRADE_DT, BAR_TM,
                       OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC, TRDE_QTY
                FROM AI_TIC_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE=%s
                  AND TRADE_DT BETWEEN %s AND %s
                ORDER BY CNTR_TM ASC, TIC_SEQ ASC
                """,
                (str(stkCd), str(ticScope), str(startDt), str(endDt)),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    bars = []
    for r in rows:
        if r["CLOSE_PRC"] is None:
            continue
        cntr = r["CNTR_TM"]
        seq = int(r["TIC_SEQ"] or 0)
        bars.append(
            {
                "cntrTm": f"{cntr}#{seq}",
                "tradeDt": r["TRADE_DT"],
                "barTm": r["BAR_TM"],
                "open": float(r["OPEN_PRC"] or r["CLOSE_PRC"] or 0),
                "high": float(r["HIGH_PRC"] or r["CLOSE_PRC"] or 0),
                "low": float(r["LOW_PRC"] or r["CLOSE_PRC"] or 0),
                "close": float(r["CLOSE_PRC"]),
                "qty": int(r["TRDE_QTY"] or 0),
            }
        )
    return bars


def _calcStats(trades, initCash=100_000_000.0):
    """
    라운드(매수→전량청산) 단위 통계.
    승패 기준은 라운드를 전부 청산했을 때의 최종 손익 —
    분할익절이 있으면 그 이익까지 합쳐서 판정하므로 buildTradeRounds 와 항상 같다.
    """
    if not trades:
        return {
            "tradeCnt": 0,
            "winCnt": 0,
            "lossCnt": 0,
            "winRate": 0.0,
            "sumRetPct": 0.0,
            "avgRetPct": 0.0,
            "maxDdPct": 0.0,
            "finalRetPct": 0.0,
            "equityEnd": initCash,
        }

    equity = initCash
    peak = equity
    maxDd = 0.0
    rets = []
    winCnt = 0
    lossCnt = 0
    for rnd in buildTradeRounds(trades):
        retPct = float(rnd.get("retPct") or 0.0)
        rets.append(retPct)
        if retPct >= 0:
            winCnt += 1
        else:
            lossCnt += 1
        equity *= 1.0 + retPct / 100.0
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if dd > maxDd:
            maxDd = dd

    tradeCnt = winCnt + lossCnt
    sumRet = sum(rets) if rets else 0.0
    return {
        "tradeCnt": tradeCnt,
        "winCnt": winCnt,
        "lossCnt": lossCnt,
        "winRate": (winCnt / tradeCnt * 100.0) if tradeCnt else 0.0,
        "sumRetPct": sumRet,
        "avgRetPct": (sumRet / tradeCnt) if tradeCnt else 0.0,
        "maxDdPct": maxDd,
        "finalRetPct": (equity / initCash - 1.0) * 100.0,
        "equityEnd": equity,
        "rets": rets,
    }


def calcScalePnl(trades):
    """
    수량가중 실현손익. BUY/ADD 매수금액 − SELL/SELL_HALF 매도금액.
    부분익절(SELL_HALF)을 포함한 실제 수익금. qty 없는 이벤트는 건너뛴다.
    """
    buyAmt = 0.0
    sellAmt = 0.0
    buyQty = 0
    sellQty = 0
    scaleOutCnt = 0
    fullExitCnt = 0
    for t in trades or []:
        tp = str(t.get("type") or "")
        try:
            px = float(t.get("price") or 0)
            qty = int(t.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0 or qty <= 0:
            continue
        amt = px * qty
        if tp in ("BUY", "ADD"):
            buyAmt += amt
            buyQty += qty
        elif tp in ("SELL", "SELL_HALF"):
            sellAmt += amt
            sellQty += qty
            if tp == "SELL_HALF":
                scaleOutCnt += 1
            else:
                fullExitCnt += 1
    pnlWon = sellAmt - buyAmt
    return {
        "buyAmt": buyAmt,
        "sellAmt": sellAmt,
        "buyQty": buyQty,
        "sellQty": sellQty,
        "pnlWon": pnlWon,
        "scaleOutCnt": scaleOutCnt,
        "fullExitCnt": fullExitCnt,
        "openQty": buyQty - sellQty,
    }


def _roundAmts(evts):
    """
    라운드 이벤트 → 수량가중 매수·매도 금액.
    분할매수(ADD)·분할익절(SELL_HALF)까지 합산해야 전량청산 시점의
    최종 손익을 알 수 있다. qty 가 없는 전략은 (0,0)을 돌려주고
    호출부에서 단가 기준으로 폴백한다.
    """
    buyAmt = 0.0
    sellAmt = 0.0
    buyQty = 0
    sellQty = 0
    for t in evts or []:
        tp = str(t.get("type") or "")
        try:
            px = float(t.get("price") or 0)
            qty = int(t.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0 or qty <= 0:
            continue
        if tp in ("BUY", "ADD"):
            buyAmt += px * qty
            buyQty += qty
        elif tp in ("SELL", "SELL_HALF"):
            sellAmt += px * qty
            sellQty += qty
    # 매수수량을 다 팔지 못한(=라운드가 안 닫힌) 경우는 가중 계산을 쓰지 않는다
    if buyQty <= 0 or buyAmt <= 0 or sellQty < buyQty:
        return 0.0, 0.0
    return buyAmt, sellAmt


def buildTradeRounds(trades):
    """
    매수 → 전량청산 = 1라운드.
    승패·수익률은 라운드를 전부 청산했을 때의 최종 손익 기준이다.
    분할매수(ADD)·분할익절(SELL_HALF)을 수량가중으로 합산하므로,
    절반 익절해도 잔량에서 더 크게 잃으면 그 라운드는 [실패]가 된다.
    qty 를 안 싣는 전략은 매수단가 대비 최종 청산단가로 폴백한다.
    각 항목: catchDt(포착), buyDt, sellDt, buyPrc, sellPrc, retPct,
             result([성공]/[실패]), reason, buyAmt, sellAmt, pnlWon
    포착일: buy.setupDt / buy.catchDt 우선, 없으면 매수일.
    """
    rounds = []
    if not trades:
        return rounds
    i = 0
    while i < len(trades):
        if trades[i].get("type") != "BUY":
            i += 1
            continue
        startIdx = i
        buy = trades[startIdx]
        sell = None
        endIdx = -1
        for j in range(startIdx + 1, len(trades)):
            if trades[j].get("type") == "SELL":
                sell = trades[j]
                endIdx = j
                i = j + 1
                break
        else:
            break
        try:
            buyPrc = float(buy.get("price") or 0)
            sellPrc = float(sell.get("price") or 0)
        except (TypeError, ValueError):
            buyPrc, sellPrc = 0.0, 0.0
        if buyPrc <= 0:
            continue
        # 라운드 구간(BUY ~ 전량 SELL)의 모든 체결을 모아 최종 손익을 낸다
        buyAmt, sellAmt = _roundAmts(trades[startIdx : endIdx + 1])
        if buyAmt > 0:
            retPct = (sellAmt / buyAmt - 1.0) * 100.0
            pnlWon = sellAmt - buyAmt
        else:
            retPct = (sellPrc / buyPrc - 1.0) * 100.0
            pnlWon = None
        buyDt = str(buy.get("tradeDt") or "").strip()
        catchDt = (
            str(buy.get("setupDt") or buy.get("catchDt") or buyDt).strip()
            or buyDt
        )
        sellDt = str(sell.get("tradeDt") or "").strip()
        rounds.append(
            {
                "catchDt": catchDt,
                "buyDt": buyDt,
                "sellDt": sellDt,
                "buyPrc": buyPrc,
                "sellPrc": sellPrc,
                "retPct": retPct,
                "result": "[성공]" if retPct >= 0 else "[실패]",
                "buyReason": buy.get("reason") or "",
                "sellReason": sell.get("reason") or "",
                "maxPnlPct": sell.get("maxPnlPct"),
                "buyAmt": buyAmt or None,
                "sellAmt": sellAmt or None,
                "pnlWon": pnlWon,
            }
        )
    return rounds



def runBacktest(
    stratId,
    stkCd,
    startDt,
    endDt,
    warmupStartDt=None,
    ticScope="1",
    initCash=100_000_000.0,
    barSource="min",
):
    """
    스펙 로드 → 분봉/틱 로드 → 전략 시뮬.
    barSource: 'min' | 'tic'
    """
    spec = loadStratSpec(stratId)
    if spec is None:
        return {"ok": False, "errMsg": f"스펙 없음: {stratId}"}

    warm = warmupStartDt or startDt
    src = (barSource or "min").lower()
    # 이평선지지: 일봉 종가 전략 — 분봉 요청이어도 일봉 사용
    if _isMaSupportEodStrat(stratId):
        try:
            smaStart = (
                datetime.strptime(str(startDt), "%Y%m%d")
                - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
            ).strftime("%Y%m%d")
        except ValueError:
            smaStart = warm
        bars = loadDayBars(stkCd, min(str(warm), smaStart), endDt)
        srcLabel = "일봉"
        src = "day"
    # 이평선터치: 일봉 SMA + 1분봉 시각(14:00/15:18). 분봉 없으면 일봉 근사
    elif _isMaTouchStrat(stratId) and src != "day" and src != "tic":
        try:
            smaStart = (
                datetime.strptime(str(startDt), "%Y%m%d")
                - timedelta(days=_MA_TOUCH_SMA_WARM_DAYS)
            ).strftime("%Y%m%d")
        except ValueError:
            smaStart = warm
        dayBars = loadDayBars(stkCd, min(str(warm), smaStart), endDt)
        minBars = loadMinBars(stkCd, ticScope or "1", startDt, endDt)
        if minBars and not dayBars:
            return {
                "ok": False,
                "errMsg": f"일봉 SMA 없음: {stkCd} (1분봉은 있음)",
            }
        tmpStrat = MaTouchScaleStrategy(spec.get("params") or {})
        if minBars:
            bars = stampMinBarsWithDaySma(
                minBars, dayBars, tmpStrat._needPeriods()
            )
            srcLabel = "1분+일SMA"
            src = "min"
        else:
            bars = dayBars
            srcLabel = "일봉"
            src = "day"
    elif _isMaTouchStrat(stratId) or src == "day":
        bars = loadDayBars(stkCd, warm, endDt)
        srcLabel = "일봉"
        src = "day"
    elif src == "tic":
        bars = loadTicBars(stkCd, ticScope or "1", warm, endDt)
        srcLabel = f"{ticScope}틱"
    else:
        bars = loadMinBars(
            stkCd, ticScope or spec.get("timeframe") or "1", warm, endDt
        )
        srcLabel = f"{ticScope}분"

    if not bars:
        return {
            "ok": False,
            "errMsg": f"바 데이터 없음: {stkCd} {warm}~{endDt} ({src}/{ticScope})",
        }

    params = spec.get("params") or {}
    # stratId prefix → 구현 클래스 (신규 전략 시 여기 + stratValidate 동기화)
    if stratId.startswith("mahaPullback"):
        strat = MahaPullbackStrategy(params)
    elif stratId.startswith("alignMaBreak"):
        strat = AlignMaBreakStrategy(params)
    elif stratId.startswith("highPullback"):
        strat = HighPullbackStrategy(params)
    elif stratId.startswith("bullBodyMa2050"):
        strat = BullBodyMa2050Strategy(params)
    elif stratId.startswith("maTouchScale"):
        strat = MaTouchScaleStrategy(params)
    elif stratId.startswith("maSupportEod"):
        strat = MaSupportEodStrategy(params)
    else:
        return {"ok": False, "errMsg": f"미구현 전략: {stratId}"}

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
        if t["type"] == "BUY" and startDt <= t.get("tradeDt", "") <= endDt:
            j = i + 1
            # 라운드 끝은 전량 SELL. 중간 분할매수(ADD)·분할익절(SELL_HALF)도
            # 그대로 남겨야 승패를 전량청산 최종 손익으로 판정할 수 있다.
            while j < len(trades) and trades[j]["type"] not in ("BUY", "SELL"):
                j += 1
            if j < len(trades) and trades[j]["type"] == "SELL":
                filtered.extend(trades[i : j + 1])
                i = j + 1
                continue
            filtered.append(t)
        i += 1

    stats = _calcStats(filtered, initCash=initCash)
    rounds = buildTradeRounds(filtered)
    scalePnl = calcScalePnl(trades)
    return {
        "ok": True,
        "stratId": stratId,
        "name": spec.get("name"),
        "stkCd": stkCd,
        "ticScope": str(ticScope),
        "barSource": src,
        "srcLabel": srcLabel,
        "warmupStartDt": warm,
        "startDt": startDt,
        "endDt": endDt,
        "barCnt": len(bars),
        "trades": filtered,
        "rounds": rounds,
        "allTradeCnt": len(trades),
        "stats": stats,
        "scalePnl": scalePnl,
        "params": params,
    }


def formatBtResult(result):
    """텔레그램/터미널용 요약 텍스트."""
    if not result.get("ok"):
        return f"[백테스트실패] {result.get('errMsg')}"

    s = result["stats"]
    srcLabel = result.get("srcLabel") or result.get("ticScope")
    lines = [
        f"[백테스트] {result.get('name') or result.get('stratId')}",
        f"종목: {result.get('stkCd')} / 데이터: {srcLabel}",
        f"기간: {result.get('startDt')} ~ {result.get('endDt')}",
        f"워밍업시작: {result.get('warmupStartDt')} (봉/틱수 {result.get('barCnt')})",
        f"거래: {s['tradeCnt']}회 (승 {s['winCnt']} / 패 {s['lossCnt']})",
        f"승률: {s['winRate']:.1f}%",
        f"평균수익률: {s['avgRetPct']:+.3f}%",
        f"복리수익률: {s['finalRetPct']:+.3f}%",
        f"MDD: {s['maxDdPct']:.3f}%",
    ]
    scale = result.get("scalePnl") or {}
    if scale.get("buyAmt") or scale.get("sellAmt"):
        lines.append(
            f"수량가중 수익금: {float(scale.get('pnlWon') or 0):+,.0f}원 "
            f"(매수 {float(scale.get('buyAmt') or 0):,.0f} / "
            f"매도 {float(scale.get('sellAmt') or 0):,.0f} · "
            f"부분익절 {int(scale.get('scaleOutCnt') or 0)}건)"
        )
    trades = result.get("trades") or []
    rounds = result.get("rounds") or []
    if rounds:
        from utils.stratValidate import enrichStkMktMeta

        meta = enrichStkMktMeta(
            result.get("stkCd"),
            stkNm=result.get("stkNm"),
            marketName=result.get("marketName"),
            mrktTp=result.get("mrktTp"),
        )
        lines.append("")
        lines.append("■ 거래상세")
        lines.append(
            "  [성공실패] 종목명 | 종목코드 | 시장 | 포착일 | 매수일 | 매도일 | "
            "매수가 | 매도가 | 수익률"
        )
        for i, r in enumerate(rounds[:40], 1):
            lines.append(
                f"  {i}. {r.get('result') or '-'} "
                f"{meta['stkNm']} | {meta['stkCd']} | {meta['mktLabel']} | "
                f"{r.get('catchDt') or '-'} | {r.get('buyDt') or '-'} | "
                f"{r.get('sellDt') or '-'} | "
                f"{float(r.get('buyPrc') or 0):,.0f} | "
                f"{float(r.get('sellPrc') or 0):,.0f} | "
                f"{float(r.get('retPct') or 0):+.2f}%"
            )
        if len(rounds) > 40:
            lines.append(f"  ... 외 {len(rounds) - 40}건")
    elif trades:
        lines.append("")
        lines.append("■ 체결")
        for t in trades[:40]:
            lines.append(
                f"  {t['type']} {t.get('cntrTm')} {t['price']:.0f} ({t.get('reason')})"
            )
        if len(trades) > 40:
            lines.append(f"  ... 외 {len(trades) - 40}건")
    return "\n".join(lines)
