"""8월 LIVE_TRADE vs 체결 로그 대조 (조회 전용)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.dbConn import openDbConn
from utils.stratRoundFill import listRoundFills


def main():
    conn = openDbConn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT STK_CD, STK_NM, ENTRY_DT, EXIT_DT, ROUND(PNL_WON) PNL
        FROM AI_STRAT_LIVE_TRADE
        WHERE ALG_CD=%s AND MODE=%s AND EXIT_DT LIKE %s
        ORDER BY EXIT_DT, TRADE_ID
        LIMIT 80
        """,
        ["MTS01", "paper", "202608%"],
    )
    rows = cur.fetchall() or []
    conn.close()

    ok = bad = ghost = multi = 0
    savedTot = fillTot = 0.0
    print("종목 | 청산일 | 저장 | 체결 | 차이")
    for r in rows:
        cd = r["STK_CD"]
        nm = r["STK_NM"] or cd
        frm = str(r["ENTRY_DT"] or r["EXIT_DT"])[:8]
        to = str(r["EXIT_DT"])[:8]
        saved = float(r["PNL"] or 0)
        savedTot += saved
        rounds = listRoundFills("MTS01", cd, frm, to, mode="paper")
        hit = [x for x in rounds if str(x.get("exitDt") or "") == to]
        if not hit:
            ghost += 1
            print(f"[무매칭] {nm} {to} 저장 {saved:+,.0f}")
            continue
        if len(hit) > 1:
            multi += 1
            print(f"[다중] {nm} {to} 저장 {saved:+,.0f} rounds={len(hit)}")
            continue
        h = hit[0]
        if not h.get("closed"):
            ghost += 1
            print(
                f"[미청산] {nm} {to} 저장 {saved:+,.0f} "
                f"buy={h.get('buyQty')} sell={h.get('sellQty')}"
            )
            continue
        p = float(h.get("pnlWon") or 0)
        fillTot += p
        d = saved - p
        if abs(d) < 1:
            ok += 1
        else:
            bad += 1
            print(f"[불일치] {nm} {to} 저장 {saved:+,.0f} 체결 {p:+,.0f} 차 {d:+,.0f}")

    print()
    print(f"일치 {ok} / 불일치 {bad} / 유령·무매칭 {ghost} / 다중보류 {multi}")
    print(f"8월 저장합 {savedTot:+,.0f}")
    print(f"8월 체결매칭합(1:1 closed만) {fillTot:+,.0f} ({ok + bad}건)")
    print(f"차이(저장-체결매칭) {savedTot - fillTot:+,.0f}")


if __name__ == "__main__":
    main()
