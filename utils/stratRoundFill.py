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


def hasSellFill(algCd, stkCd, fromDt, toDt=None, mode=None):
    """실제 매도 체결이 하나라도 있었는지. 미체결 취소 유령 라운드를 거른다."""
    got = calcRoundFill(algCd, stkCd, fromDt, toDt=toDt, mode=mode)
    if not got.get("ok"):
        # 집계 실패 시엔 기존 동작을 막지 않는다
        return True
    return int(got.get("sellQty") or 0) > 0
