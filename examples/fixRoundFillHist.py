"""
과거 청산이력(AI_STRAT_LIVE_TRADE) 체결 기준 보정.

원장 수량은 주문 접수 시점 값이라 부분체결·미체결취소가 빠져 있었다.
그 값으로 손익을 재산정한 라운드가 남아 있어 승패가 뒤집힌 건이 있다.
체결 로그(AI_ALG_EVT)를 진실로 놓고 다시 계산한다.

기본은 **미리보기**다. 실제 반영은 --apply 를 줘야 한다.

사용:
    python examples/fixRoundFillHist.py                  # 전체 미리보기
    python examples/fixRoundFillHist.py --from 20260901  # 기간 미리보기
    python examples/fixRoundFillHist.py --apply          # 반영
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.stratRoundFill import listRoundFills


def _pickRound(rounds, exitDt):
    """
    같은 종목을 여러 번 돌렸으면 라운드가 여러 개다.
    청산일이 일치하는 라운드만 고른다. 없으면 보정 대상에서 뺀다.
    """
    hit = [r for r in rounds if str(r.get("exitDt") or "") == str(exitDt or "")]
    if len(hit) == 1:
        return hit[0], ""
    if not hit:
        return None, "청산일 일치 라운드 없음"
    return None, f"청산일이 같은 라운드 {len(hit)}개 — 자동 매칭 불가"

_UPDATE_SQL = """
UPDATE AI_STRAT_LIVE_TRADE
SET BUY_QTY=%s, SELL_QTY=%s, BUY_AMT=%s, SELL_AMT=%s,
    AVG_BUY_PX=%s, EXIT_PX=%s, PNL_WON=%s, PNL_PCT=%s,
    NOTE=CONCAT(COALESCE(NOTE,''), ' fillFix')
WHERE TRADE_ID=%s
"""

_DELETE_SQL = "DELETE FROM AI_STRAT_LIVE_TRADE WHERE TRADE_ID=%s"


def _wl(pnl):
    if pnl > 0:
        return "승"
    if pnl < 0:
        return "패"
    return "본전"


def _parseArgs(argv):
    opt = {"apply": False, "from": "", "to": ""}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--apply":
            opt["apply"] = True
        elif a == "--dry":
            # 기본값이지만 조회 전용임을 호출부에서 드러내기 위해 받는다
            opt["apply"] = False
        elif a == "--from" and i + 1 < len(argv):
            i += 1
            opt["from"] = argv[i][:8]
        elif a == "--to" and i + 1 < len(argv):
            i += 1
            opt["to"] = argv[i][:8]
        i += 1
    return opt


def _listRounds(cur, dtFrom, dtTo):
    sql = """
        SELECT TRADE_ID, ALG_CD, MODE, STK_CD, STK_NM, ENTRY_DT, EXIT_DT,
               BUY_QTY, SELL_QTY, BUY_AMT, SELL_AMT, PNL_WON
        FROM AI_STRAT_LIVE_TRADE
        WHERE 1=1
    """
    args = []
    if dtFrom:
        sql += " AND EXIT_DT>=%s"
        args.append(dtFrom)
    if dtTo:
        sql += " AND EXIT_DT<=%s"
        args.append(dtTo)
    sql += " ORDER BY EXIT_DT, TRADE_ID LIMIT 2000"
    cur.execute(sql, args)
    return cur.fetchall() or []


def main():
    opt = _parseArgs(sys.argv[1:])
    print("[모드] 반영(--apply)" if opt["apply"] else "[모드] 미리보기 — DB 변경 없음")
    conn = openDbConn()
    fixes = []
    drops = []
    skips = []
    same = 0
    try:
        with conn.cursor() as cur:
            rows = _listRounds(cur, opt["from"], opt["to"])
        if not rows:
            print("대상 라운드 없음")
            return
        print(f"=== 청산이력 {len(rows)}건 체결 대조 ===")
        for r in rows:
            alg = str(r.get("ALG_CD") or "").strip().upper()[:5]
            cd = normStkCd(r.get("STK_CD"))
            nm = r.get("STK_NM") or cd
            exitDt = str(r.get("EXIT_DT") or "")[:8]
            entryDt = str(r.get("ENTRY_DT") or "")[:8] or exitDt
            oldPnl = float(r.get("PNL_WON") or 0)
            rounds = listRoundFills(
                alg, cd, entryDt, exitDt, mode=str(r.get("MODE") or "paper")
            )
            if not rounds:
                # 체결로 만들어지는 라운드가 하나도 없다 = 미체결 취소 유령
                drops.append(
                    (r.get("TRADE_ID"), exitDt, nm, oldPnl, "체결 없음(미체결취소)")
                )
                continue
            fill, why = _pickRound(rounds, exitDt)
            if fill is None:
                skips.append((exitDt, nm, oldPnl, why))
                continue
            if int(fill.get("buyQty") or 0) <= 0:
                # 매도만 있고 매수가 조회 구간 밖 — 원가를 못 구하므로 손대지 않는다
                skips.append((exitDt, nm, oldPnl, "매수 체결이 조회 기간 밖"))
                continue
            if not fill.get("closed"):
                left = int(fill.get("buyQty") or 0) - int(fill.get("sellQty") or 0)
                drops.append(
                    (r.get("TRADE_ID"), exitDt, nm, oldPnl, f"잔량 {left}주 보유")
                )
                continue
            newPnl = float(fill.get("pnlWon") or 0)
            if abs(newPnl - oldPnl) < 1:
                same += 1
                continue
            fixes.append((r.get("TRADE_ID"), exitDt, nm, oldPnl, newPnl, fill))

        for _tid, dt, nm, oldPnl, newPnl, _f in fixes:
            flip = " ★승패뒤집힘" if _wl(oldPnl) != _wl(newPnl) else ""
            print(
                f"  [수정] {dt} {nm:<12} {oldPnl:+,.0f}({_wl(oldPnl)}) → "
                f"{newPnl:+,.0f}({_wl(newPnl)}){flip}"
            )
        for _tid, dt, nm, oldPnl, why in drops:
            print(f"  [삭제] {dt} {nm:<12} {oldPnl:+,.0f}원 — {why}")
        for dt, nm, oldPnl, why in skips:
            print(f"  [보류] {dt} {nm:<12} {oldPnl:+,.0f}원 — {why}")
        flips = sum(
            1 for _t, _d, _n, o, n, _f in fixes if _wl(o) != _wl(n)
        )
        print()
        print(
            f"수정 {len(fixes)}건(승패뒤집힘 {flips}) · 삭제 {len(drops)}건 · "
            f"보류 {len(skips)}건 · 변동없음 {same}건"
        )
        if not opt["apply"]:
            print()
            print("미리보기입니다. 반영하려면 --apply 를 붙여 다시 실행하세요.")
            return

        with conn.cursor() as cur:
            for tid, _dt, _nm, _o, newPnl, f in fixes:
                cur.execute(
                    _UPDATE_SQL,
                    (
                        int(f.get("buyQty") or 0),
                        int(f.get("sellQty") or 0),
                        float(f.get("buyAmt") or 0),
                        float(f.get("sellAmt") or 0),
                        float(f.get("avgBuyPx") or 0) or None,
                        float(f.get("exitPx") or 0) or None,
                        newPnl,
                        float(f.get("pnlPct") or 0),
                        tid,
                    ),
                )
            for tid, _dt, _nm, _o, _why in drops:
                cur.execute(_DELETE_SQL, (tid,))
        conn.commit()
        print(f"반영 완료 — 수정 {len(fixes)}건 / 삭제 {len(drops)}건")

        # 승패 스냅샷 재집계 (계좌탭)
        from utils.algPnlDayStore import rebuildAlgPnlDay

        for dt in sorted({d for _t, d, _n, _o, _p, _f in fixes} | {d for _t, d, _n, _o, _w in drops}):
            got = rebuildAlgPnlDay(mode="paper", tradeDt=dt)
            print(f"  스냅샷 재집계 {dt}: {got.get('ok')}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
