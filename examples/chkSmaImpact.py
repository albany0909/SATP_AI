"""
장중값 일봉이 SMA 에 준 영향 점검 (조회만).

현재 DB 일봉으로 계산한 SMA vs 1분봉 실제 종가로 8/18 을 고쳤을 때의 SMA 를
나란히 보여, 오늘 매수·손절 판정이 달라졌는지 확인한다.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.dbConn import openDbConn

fixDt = "20260818"
periods = (5, 10, 15, 20)
codes = sys.argv[1:] or ["034020", "005930", "005380", "011070", "011200", "403870"]


def loadCloses(cur, stkCd, upToDt, limit=40):
    cur.execute(
        """
        SELECT TRADE_DT, CLOSE_PRC
        FROM AI_DAY_STK_PRC
        WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT<=%s
        ORDER BY TRADE_DT DESC
        LIMIT %s
        """,
        (stkCd, upToDt, limit),
    )
    rows = cur.fetchall() or []
    return [(r["TRADE_DT"], float(r["CLOSE_PRC"] or 0)) for r in rows]


def lastMinClose(cur, stkCd, ymd):
    cur.execute(
        """
        SELECT CLOSE_PRC
        FROM AI_MIN_STK_PRC
        WHERE STK_CD=%s AND TIC_SCOPE='1' AND TRADE_DT=%s
        ORDER BY BAR_TM DESC LIMIT 1
        """,
        (stkCd, ymd),
    )
    row = cur.fetchone()
    return float(row["CLOSE_PRC"]) if row and row.get("CLOSE_PRC") else None


def sma(closes, per):
    vals = [c for _, c in closes[:per]]
    if len(vals) < per:
        return None
    return sum(vals) / per


conn = openDbConn()
try:
    with conn.cursor() as cur:
        for cd in codes:
            closes = loadCloses(cur, cd, fixDt)
            if not closes:
                print(f"{cd}: 일봉 없음")
                continue
            realClose = lastMinClose(cur, cd, fixDt)
            fixed = list(closes)
            note = ""
            if realClose and closes[0][0] == fixDt:
                if abs(realClose - closes[0][1]) / closes[0][1] * 100 > 0.3:
                    fixed[0] = (fixDt, realClose)
                    note = f"8/18 {closes[0][1]:,.0f} → 실제 {realClose:,.0f}"
                else:
                    note = "8/18 정상"
            print(f"[{cd}] {note}")
            for per in periods:
                cur0 = sma(closes, per)
                cur1 = sma(fixed, per)
                if cur0 is None or cur1 is None:
                    continue
                diff = cur1 - cur0
                mark = " <-- 차이" if abs(diff) >= 1 else ""
                print(
                    f"   SMA{per:<3} 현재 {cur0:>11,.0f} · 정정 {cur1:>11,.0f} "
                    f"({diff:+,.0f}){mark}"
                )
            print()
finally:
    conn.close()
