"""
당일 이평터치 거래로그 분석 (조회만).

이벤트마다 그 시점의 이평·가격을 꺼내 어떤 조건이 성립했는지 대조한다.
  매수: 직전틱 > SMA(step) · 현재틱 <= SMA(step) · 현재가 > SMA20 · 시각 < 14:00
  매도: 절반익절 / SMA20 손절 / -5% 하드스탑 / 15:18 SMA10 잔량익절
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.algEvtStore import listAlgEvt
from utils.dbConn import openDbConn

ymd = sys.argv[1] if len(sys.argv) > 1 else "20260819"


def fnum(val):
    if val is None:
        return None
    return float(val)


rows = listAlgEvt("MTS01", ymd, limit=800) or []
print(f"=== {ymd} MTS01 이벤트 {len(rows)}건 (원본) ===")
for r in rows:
    px = fnum(r.get("PX"))
    s5, s10, s15, s20 = (
        fnum(r.get("SMA5")),
        fnum(r.get("SMA10")),
        fnum(r.get("SMA15")),
        fnum(r.get("SMA20")),
    )
    print(
        f"{r.get('EVT_TM')} {r.get('EVT_TP'):<9} "
        f"{(r.get('STK_NM') or '-'):<10}({r.get('STK_CD')}) "
        f"px={px or 0:>10,.0f} qty={r.get('QTY')} "
        f"사유={r.get('REASON')} "
        f"trade={r.get('TRADE_CD') or '-'}"
    )
    print(
        f"            SMA5 {s5 or 0:>10,.0f} SMA10 {s10 or 0:>10,.0f} "
        f"SMA15 {s15 or 0:>10,.0f} SMA20 {s20 or 0:>10,.0f}"
        + (f" · note={r.get('NOTE')}" if r.get("NOTE") else "")
    )

print()
print("=== 원장 (당일 관련 라운드) ===")
conn = openDbConn()
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT TRADE_CD, STK_CD, STK_NM, STK_STS_CD, BUY_PX, SELL_PX,
                   BUY_QTY, SELL_QTY, RMND_QTY, BUY_AMT, SELL_AMT,
                   FILLED_STEPS, HALF_YN, CLOSE_PNL_PCT,
                   FIRST_BUY_DT, LAST_BUY_DT, CLOSE_DT,
                   BUY_1, BUY_2, BUY_3, TP_1, SL_1
            FROM AI_STRAT_ORD_LEDGER
            WHERE ALG_CD='MTS01'
              AND (FIRST_BUY_DT=%s OR LAST_BUY_DT=%s OR CLOSE_DT=%s)
            ORDER BY TRADE_CD
            """,
            (ymd, ymd, ymd),
        )
        for r in cur.fetchall() or []:
            print(
                f"{r['TRADE_CD']} {r['STK_CD']} {r.get('STK_NM') or '-'} "
                f"상태 {r.get('STK_STS_CD')} 평단 {r.get('BUY_PX')} "
                f"매수 {r.get('BUY_QTY')}주/{r.get('BUY_AMT')} "
                f"매도 {r.get('SELL_QTY')}주/{r.get('SELL_AMT')} "
                f"잔량 {r.get('RMND_QTY')} steps={r.get('FILLED_STEPS')} "
                f"half={r.get('HALF_YN')} 청산일 {r.get('CLOSE_DT') or '-'} "
                f"수익률 {r.get('CLOSE_PNL_PCT')}"
            )
            print(
                f"    매수단계가 B1={r.get('BUY_1')} B2={r.get('BUY_2')} "
                f"B3={r.get('BUY_3')} · TP1={r.get('TP_1')} SL1={r.get('SL_1')}"
            )
finally:
    conn.close()
