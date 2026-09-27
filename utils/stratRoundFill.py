"""
알고리즘 라운드 실체결 집계 (AI_ALG_EVT 기준).

원장(AI_STRAT_ORD_LEDGER)의 수량·금액은 **주문 접수 시점** 값이라
부분체결·미체결취소가 반영되지 않는다. 실제로 원장만 믿으면
 - 지정가가 전량 미체결 취소된 주문이 라운드로 남고
 - 잔량이 남은 종목이 전량청산으로 닫히고
 - 틀린 매수수량에 단가를 곱해 손익이 뒤집힌다.
그래서 승패·손익은 체결 로그를 진실로 삼는다.

집계 규칙
 - 매수 체결수량 = BUY − BUY_CNCL (지정가라 주문 시점에 BUY 가 먼저 남는다)
 - 매수 평단     = BUY 주문가의 수량가중 평균 (모의 지정가는 주문가로 체결)
 - 매도          = SELL (시장가라 주문 즉시 체결로 본다)
 - 청산 완료     = 매수체결수량 > 0 이고 매도수량 ≥ 매수체결수량
"""

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 매수 주문 / 매수 취소 / 매도 — 그 외(BUY_SKIP·BUY_FAIL·RANK_IN)는 체결이 아니다
_BUY_TP = "BUY"
_BUY_CNCL_TP = "BUY_CNCL"
_SELL_TP = "SELL"


def _flt(val):
    try:
        return abs(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return 0.0


def _int(val):
    try:
        return int(float(str(val).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def emptyRoundFill():
    """이벤트를 못 읽었을 때 쓰는 빈 집계. ok=False 면 호출부가 기존 값을 유지한다."""
    return {
        "ok": False,
        "buyQty": 0,
        "buyAmt": 0.0,
        "avgBuyPx": 0.0,
        "sellQty": 0,
        "sellAmt": 0.0,
        "exitPx": 0.0,
        "closed": False,
        "pnlWon": 0.0,
        "pnlPct": 0.0,
    }


def _buildRound(acc, closed):
    """누적기 → 라운드 dict."""
    ordQty = acc["ordQty"]
    # 주문가 수량가중 평단. 취소분은 수량만 빼고 평단은 그대로 쓴다
    # (어느 주문이 취소됐는지 로그로 구분되지 않기 때문)
    avgBuyPx = (acc["ordAmt"] / ordQty) if ordQty > 0 else 0.0
    buyQty = max(0, ordQty - acc["cnclQty"])
    sellQty = acc["sellQty"]
    sellAmt = acc["sellAmt"]
    costOfSold = avgBuyPx * min(sellQty, buyQty) if buyQty > 0 else 0.0
    pnlWon = sellAmt - costOfSold
    return {
        "ok": True,
        "buyQty": buyQty,
        "buyAmt": avgBuyPx * buyQty,
        "avgBuyPx": avgBuyPx,
        "sellQty": sellQty,
        "sellAmt": sellAmt,
        "exitPx": acc["exitPx"],
        "closed": bool(closed),
        "pnlWon": pnlWon,
        "pnlPct": (pnlWon / costOfSold * 100.0) if costOfSold > 0 else 0.0,
        "entryDt": acc["entryDt"],
        "exitDt": acc["exitDt"],
    }


def _newAcc():
    return {
        "ordQty": 0,
        "ordAmt": 0.0,
        "cnclQty": 0,
        "sellQty": 0,
        "sellAmt": 0.0,
        "exitPx": 0.0,
        "entryDt": "",
        "exitDt": "",
    }


def splitRoundFills(rows):
    """
    체결 이벤트 스트림 → 라운드 목록 (시간순).

    보유수량이 0으로 돌아오면 한 라운드가 끝난 것으로 보고 끊는다.
    같은 종목을 여러 번 사고팔았으면 각각 별개 라운드다. 기간으로만 묶어
    한꺼번에 더하면 여러 라운드 손익이 뭉쳐 손익이 몇 배로 부풀려진다.
    """
    rounds = []
    acc = _newAcc()
    for r in rows or []:
        tp = str((r or {}).get("EVT_TP") or "").strip().upper()
        px = _flt((r or {}).get("PX"))
        qty = _int((r or {}).get("QTY"))
        dt = str((r or {}).get("TRADE_DT") or "")[:8]
        if qty <= 0:
            continue
        if tp == _BUY_TP:
            acc["ordQty"] += qty
            acc["ordAmt"] += px * qty
            if not acc["entryDt"]:
                acc["entryDt"] = dt
        elif tp == _BUY_CNCL_TP:
            acc["cnclQty"] += qty
            netNow = acc["ordQty"] - acc["cnclQty"]
            if netNow <= 0 and acc["sellQty"] <= 0:
                # 전량 미체결 취소 — 포지션이 생긴 적이 없으니 라운드가 아니다
                acc = _newAcc()
            continue
        elif tp == _SELL_TP:
            if px <= 0:
                continue
            acc["sellQty"] += qty
            acc["sellAmt"] += px * qty
            acc["exitPx"] = px
            acc["exitDt"] = dt
        else:
            continue
        netBuy = acc["ordQty"] - acc["cnclQty"]
        if netBuy > 0 and acc["sellQty"] >= netBuy:
            rounds.append(_buildRound(acc, True))
            acc = _newAcc()
    # 아직 안 닫힌 잔량이 남아 있으면 미청산 라운드로 끝에 붙인다
    if acc["ordQty"] > 0 or acc["sellQty"] > 0:
        rounds.append(_buildRound(acc, False))
    return rounds


def sumRoundFillRows(rows):
    """
    체결 이벤트 행 목록 → **마지막** 라운드.
    미청산 잔량이 남아 있으면 그 미청산 라운드(closed=False)를 돌려준다.
    """
    rounds = splitRoundFills(rows)
    if not rounds:
        out = emptyRoundFill()
        out["ok"] = True
        return out
    return rounds[-1]


def calcRoundFill(algCd, stkCd, fromDt, toDt=None, mode=None):
    """
    한 라운드(최초매수일~청산일)의 실체결 집계.
    fromDt 를 최초매수일로 주면 전일 이월 라운드도 매수분까지 포함된다.
    """
    alg = str(algCd or "").strip().upper()[:5]
    cd = normStkCd(stkCd)
    frm = str(fromDt or "")[:8]
    to = str(toDt or frm)[:8]
    if not alg or not cd or len(frm) != 8:
        return emptyRoundFill()
    sql = """
        SELECT TRADE_DT, EVT_TP, PX, QTY
        FROM AI_ALG_EVT
        WHERE ALG_CD=%s AND STK_CD=%s
          AND TRADE_DT BETWEEN %s AND %s
          AND EVT_TP IN (%s, %s, %s)
    """
    args = [alg, cd, frm, to, _BUY_TP, _BUY_CNCL_TP, _SELL_TP]
    modeKey = str(mode or "").strip().lower()
    if modeKey in ("paper", "real"):
        sql += " AND (MODE=%s OR MODE IS NULL OR MODE='')"
        args.append(modeKey)
    sql += " ORDER BY TRADE_DT, EVT_TM"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return sumRoundFillRows(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[roundFill] 체결 집계 실패 {alg} {cd}: {err}")
        return emptyRoundFill()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listRoundFills(algCd, stkCd, fromDt, toDt=None, mode=None):
    """기간 안의 라운드 전부. 같은 종목을 여러 번 돌린 경우를 나눠 본다."""
    alg = str(algCd or "").strip().upper()[:5]
    cd = normStkCd(stkCd)
    frm = str(fromDt or "")[:8]
    to = str(toDt or frm)[:8]
    if not alg or not cd or len(frm) != 8:
        return []
    sql = """
        SELECT TRADE_DT, EVT_TP, PX, QTY
        FROM AI_ALG_EVT
        WHERE ALG_CD=%s AND STK_CD=%s
          AND TRADE_DT BETWEEN %s AND %s
          AND EVT_TP IN (%s, %s, %s)
    """
    args = [alg, cd, frm, to, _BUY_TP, _BUY_CNCL_TP, _SELL_TP]
    modeKey = str(mode or "").strip().lower()
    if modeKey in ("paper", "real"):
        sql += " AND (MODE=%s OR MODE IS NULL OR MODE='')"
        args.append(modeKey)
    sql += " ORDER BY TRADE_DT, EVT_TM"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return splitRoundFills(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[roundFill] 라운드 분리 실패 {alg} {cd}: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── 00 실체결 로그(AI_DESK_LOG SRC=FILL) 기준 라운드 ─────────────────────────
# EVT 는 엔진 판단 기록이라 부분체결·수동매도·누락이 섞인다. 실제 계좌 손익은
# 키움 00 체결(체결가·수수료 938·세금 939)이 진실이므로 청산 손익은 이것을 우선한다.


def normDeskFillRows(rows):
    """
    AI_DESK_LOG FILL 행 → 단위 체결 목록.

    예전 코드는 FID 911(주문 누계 체결량)을 QTY 로 저장했다. 같은 주문번호에
    행이 여러 개이고 표식(deskLogStore.FILL_CUM_MARK)이 하나도 없으면 누계 행으로
    보고 직전 누계와의 차이를 단위 수량으로 쓴다. 표식이 있으면 이미 단위 수량이다.
    반환: [{dt, tm, side, qty, px, cmsn, tax, feeKnown, ordNo}]
    """
    try:
        from utils.deskLogStore import FILL_CUM_MARK
    except Exception:
        FILL_CUM_MARK = "누계"
    groups = {}
    order = []
    for r in rows or []:
        side = str((r or {}).get("KIND") or "").strip().upper()
        if side not in ("BUY", "SELL"):
            continue
        dt = str(r.get("TRADE_DT") or "")[:8]
        ordNo = str(r.get("ORD_NO") or "").strip()
        key = (dt, ordNo, side) if ordNo else (dt, f"#{r.get('LOG_ID')}", side)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out = []
    for key in order:
        grp = groups[key]
        marked = any(FILL_CUM_MARK in str(g.get("LINE") or "") for g in grp)
        legacyCum = (not marked) and len(grp) > 1
        prevCum = 0
        feeKnown = any(
            (g.get("CMSN") is not None) or (g.get("TAX") is not None) for g in grp
        )
        for g in grp:
            qty = _int(g.get("QTY"))
            px = _flt(g.get("PX"))
            if legacyCum:
                inc = qty - prevCum
                prevCum = max(prevCum, qty)
            else:
                inc = qty
            if inc <= 0 or px <= 0:
                continue
            out.append(
                {
                    "dt": key[0],
                    "tm": str(g.get("LOG_TM") or "").zfill(6)[-6:],
                    "side": key[2],
                    "qty": inc,
                    "px": px,
                    "cmsn": _int(g.get("CMSN")),
                    "tax": _int(g.get("TAX")),
                    "feeKnown": feeKnown,
                    "ordNo": key[1],
                }
            )
    out.sort(key=lambda x: (x["dt"], x["tm"]))
    return out


def _estFee(amt, isSell, mode):
    """수수료·세금 추정 (00 에 938/939 가 없을 때 — 주로 모의)."""
    try:
        from utils.heroPnl import estimateCmsn, estimateSellTax
    except Exception:
        return 0, 0
    cm = estimateCmsn(amt, mode=mode or "real")
    tx = estimateSellTax(amt) if isSell else 0
    return int(cm or 0), int(tx or 0)


def splitDeskFillRounds(fills, mode=None):
    """
    단위 체결 목록 → 라운드 (평균단가법, 체결마다 실현손익 누적).

    매도 1건 실현손익 = (체결가 − 평단) × 수량 − 매수수수료배분 − 매도수수료 − 세금
    창 시작 전에 산 물량을 파는 매도(보유 0에서 매도)는 orphanSellQty 로만 센다.
    """
    rounds = []
    acc = None
    posQty = 0
    posCost = 0.0
    buyFee = 0.0
    orphan = 0

    def _newRound(dt):
        return {
            "ok": True,
            "buyQty": 0,
            "buyAmt": 0.0,
            "avgBuyPx": 0.0,
            "sellQty": 0,
            "sellAmt": 0.0,
            "exitPx": 0.0,
            "grossPnl": 0.0,
            "feeTot": 0.0,
            "pnlWon": 0.0,
            "pnlPct": 0.0,
            "evalPct": 0.0,
            "costOfSold": 0.0,
            "closed": False,
            "entryDt": dt,
            "exitDt": "",
            "orphanSellQty": 0,
            "src": "deskFill",
        }

    for f in fills or []:
        qty = int(f.get("qty") or 0)
        px = float(f.get("px") or 0)
        if qty <= 0 or px <= 0:
            continue
        amt = px * qty
        cmsn = int(f.get("cmsn") or 0)
        tax = int(f.get("tax") or 0)
        if f["side"] == "BUY":
            if not f.get("feeKnown") and cmsn <= 0:
                cmsn, _t = _estFee(amt, False, mode)
            if posQty <= 0:
                acc = _newRound(f["dt"])
                posQty, posCost, buyFee = 0, 0.0, 0.0
            posQty += qty
            posCost += amt
            buyFee += cmsn
            acc["buyQty"] += qty
            acc["buyAmt"] += amt
            continue
        if posQty <= 0 or acc is None:
            orphan += qty
            continue
        if not f.get("feeKnown") and cmsn <= 0 and tax <= 0:
            cmsn, tax = _estFee(amt, True, mode)
        cut = min(qty, posQty)
        if cut < qty:
            orphan += qty - cut
        avg = posCost / posQty
        bFee = buyFee * cut / posQty
        gross = (px - avg) * cut
        fee = bFee + cmsn + tax
        posCost -= avg * cut
        buyFee -= bFee
        posQty -= cut
        acc["sellQty"] += cut
        acc["sellAmt"] += px * cut
        acc["exitPx"] = px
        acc["exitDt"] = f["dt"]
        acc["grossPnl"] += gross
        acc["feeTot"] += fee
        acc["costOfSold"] += avg * cut
        if posQty <= 0:
            acc["closed"] = True
            rounds.append(acc)
            acc = None
            posQty, posCost, buyFee = 0, 0.0, 0.0
    if acc is not None:
        rounds.append(acc)
    for r in rounds:
        r["avgBuyPx"] = (r["buyAmt"] / r["buyQty"]) if r["buyQty"] > 0 else 0.0
        r["pnlWon"] = round(r["grossPnl"] - r["feeTot"])
        cost = r["costOfSold"]
        r["pnlPct"] = (r["pnlWon"] / cost * 100.0) if cost > 0 else 0.0
        r["evalPct"] = (r["grossPnl"] / cost * 100.0) if cost > 0 else 0.0
    if rounds:
        rounds[-1]["orphanSellQty"] = orphan
    return rounds


def calcRoundFromDeskFills(stkCd, fromDt, toDt=None, mode=None):
    """
    00 실체결 로그 기준 마지막 라운드. 실패·없음이면 ok=False.
    계좌 단위 로그라 같은 종목을 다른 알고리즘·수동으로 동시에 매매하면 섞인다.
    """
    cd = normStkCd(stkCd)
    frm = str(fromDt or "")[:8]
    to = str(toDt or frm)[:8]
    if not cd or len(frm) != 8:
        return emptyRoundFill()
    sql = """
        SELECT LOG_ID, TRADE_DT, LOG_TM, KIND, QTY, PX, ORD_NO, CMSN, TAX, LINE
        FROM AI_DESK_LOG
        WHERE SRC='FILL' AND STK_CD=%s AND TRADE_DT BETWEEN %s AND %s
    """
    args = [cd, frm, to]
    modeKey = str(mode or "").strip().lower()
    if modeKey in ("paper", "real"):
        sql += " AND MODE=%s"
        args.append(modeKey)
    sql += " ORDER BY TRADE_DT, LOG_TM, LOG_ID"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[roundFill] 00체결 집계 실패 {cd}: {err}")
        return emptyRoundFill()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    rounds = splitDeskFillRounds(normDeskFillRows(rows), mode=modeKey or None)
    if not rounds:
        return emptyRoundFill()
    return rounds[-1]


def hasSellFill(algCd, stkCd, fromDt, toDt=None, mode=None):
    """실제 매도 체결이 하나라도 있었는지. 미체결 취소 유령 라운드를 거른다."""
    got = calcRoundFill(algCd, stkCd, fromDt, toDt=toDt, mode=mode)
    if not got.get("ok"):
        # 집계 실패 시엔 기존 동작을 막지 않는다
        return True
    return int(got.get("sellQty") or 0) > 0
