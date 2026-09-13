"""
라운드 집계 대조 (조회 전용).

원장(AI_STRAT_ORD_LEDGER)·청산이력(AI_STRAT_LIVE_TRADE)이 실제 체결 로그
(AI_ALG_EVT)와 얼마나 어긋나는지 본다. 원장 수량은 주문 접수 시점 값이라
부분체결·미체결취소가 빠지므로, 체결 기준을 진실로 놓고 비교한다.

사용:
    python examples/chkRoundFillDiff.py 20260903
    python examples/chkRoundFillDiff.py 20260903 MTS01
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.stratRoundFill import calcRoundFill


def _wl(pnl):
    """승패 판정 — 전량청산 최종 손익 기준."""
    if pnl > 0:
        return "승"
    if pnl < 0:
        return "패"
    return "본전"


def _listClosedLedger(cur, closeDt, algCd=None):
    sql = """
        SELECT ALG_CD, STK_CD, STK_NM, FIRST_BUY_DT, CLOSE_DT,
               BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, MODE
        FROM AI_STRAT_ORD_LEDGER
        WHERE CLOSE_DT=%s AND STK_STS_CD='9'
    """
    args = [closeDt]
    if algCd:
        sql += " AND ALG_CD=%s"
        args.append(algCd)
    sql += " ORDER BY ALG_CD, STK_CD LIMIT 100"
    cur.execute(sql, args)
    return cur.fetchall() or []


def _liveTradePnl(cur, alg, cd, exitDt):
    cur.execute(
        """
        SELECT PNL_WON FROM AI_STRAT_LIVE_TRADE
        WHERE ALG_CD=%s AND STK_CD=%s AND EXIT_DT=%s
        LIMIT 1
        """,
        (alg, cd, exitDt),
    )
    row = cur.fetchone()
    return float((row or {}).get("PNL_WON") or 0) if row else None


def main():
    closeDt = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    algCd = (sys.argv[2] if len(sys.argv) > 2 else "").strip().upper() or None
    if len(closeDt) != 8 or not closeDt.isdigit():
        print("사용: python examples/chkRoundFillDiff.py YYYYMMDD [ALGCD]")
        return
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            rows = _listClosedLedger(cur, closeDt, algCd)
            if not rows:
                print(f"{closeDt} 청산 원장 없음")
                return
            print(f"=== {closeDt} 청산 원장 {len(rows)}건 — 체결 기준 대조 ===")
            keep = []
            drop = []
            for r in rows:
                alg = str(r.get("ALG_CD") or "").strip().upper()[:5]
                cd = normStkCd(r.get("STK_CD"))
                nm = r.get("STK_NM") or cd
                mode = str(r.get("MODE") or "paper").lower()
                fill = calcRoundFill(
                    alg, cd, r.get("FIRST_BUY_DT") or closeDt, closeDt, mode=mode
                )
                livePnl = _liveTradePnl(cur, alg, cd, closeDt)
                ledBuy = int(r.get("BUY_QTY") or 0)
                ledSell = int(r.get("SELL_QTY") or 0)
                fBuy = int(fill.get("buyQty") or 0)
                fSell = int(fill.get("sellQty") or 0)
                if not fill.get("closed"):
                    why = "매수체결 0(미체결취소)" if fBuy <= 0 else f"잔량 {fBuy - fSell}주 보유"
                    drop.append((nm, why))
                    print(
                        f"  [제외] {nm:<12} 원장 {ledBuy}/{ledSell}주 · "
                        f"체결 {fBuy}/{fSell}주 → {why}"
                        + (f" · 현재 기록 {livePnl:+,.0f}원" if livePnl is not None else "")
                    )
                    continue
                pnl = float(fill.get("pnlWon") or 0)
                keep.append((nm, pnl))
                mark = ""
                if livePnl is not None and abs(livePnl - pnl) >= 1:
                    mark = f"  ← 현재 기록 {livePnl:+,.0f}원 ({_wl(livePnl)})"
                print(
                    f"  [라운드] {nm:<12} 체결 {fBuy}주 왕복 · "
                    f"{pnl:+,.0f}원 ({_wl(pnl)}){mark}"
                )
            win = sum(1 for _n, p in keep if p > 0)
            loss = sum(1 for _n, p in keep if p < 0)
            print()
            print(
                f"체결 기준 청산 {len(keep)}건 — {win}승 {loss}패 · "
                f"합계 {sum(p for _n, p in keep):+,.0f}원 / 제외 {len(drop)}건"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
