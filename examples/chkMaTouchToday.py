"""이평터치 당일 현황 점검 — 청산기록·미청산·일봉 정합 (조회만)."""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.dbConn import openDbConn
from utils.stratLiveTradeStore import listLiveTrades
from utils.stratOrdLedgerStore import listOpenLedgerRows

stkCd = sys.argv[1] if len(sys.argv) > 1 else "034020"

print("=== 청산기록 AI_STRAT_LIVE_TRADE ===")
for r in listLiveTrades(mode="paper", limit=20) or []:
    print(
        f"{r.get('EXIT_DT')} {r.get('EXIT_TM')} {r.get('STK_NM')}({r.get('STK_CD')}) "
        f"{r.get('EXIT_REASON')} 매수 {r.get('BUY_AMT')} 매도 {r.get('SELL_AMT')} "
        f"손익 {r.get('PNL_WON')} ({r.get('PNL_PCT')}%) "
        f"half={r.get('HALF_YN')} steps={r.get('STEPS')}"
    )

print()
print("=== 미청산 원장 ===")
for r in listOpenLedgerRows("maTouchScale_v1") or []:
    print(
        f"{r.get('STK_CD')} {r.get('STK_NM')} 평단 {r.get('BUY_PX')} "
        f"잔량 {r.get('RMND_QTY')} steps={r.get('FILLED_STEPS')} "
        f"첫매수 {r.get('FIRST_BUY_DT')} 최근매수 {r.get('LAST_BUY_DT')}"
    )

print()
print(f"=== {stkCd} 최근 일봉 ===")
conn = openDbConn()
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT TRADE_DT, OPEN_PRC, HIGH_PRC, LOW_PRC, CLOSE_PRC
            FROM AI_DAY_STK_PRC
            WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT>=%s
            ORDER BY TRADE_DT
            """,
            (stkCd, "20260810"),
        )
        for r in cur.fetchall() or []:
            print(
                f"{r['TRADE_DT']} O {r['OPEN_PRC']:>9,} H {r['HIGH_PRC']:>9,} "
                f"L {r['LOW_PRC']:>9,} C {r['CLOSE_PRC']:>9,}"
            )
finally:
    conn.close()
