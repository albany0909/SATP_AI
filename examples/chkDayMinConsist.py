"""
일봉·1분봉 정합성 점검 (조회만).

같은 거래일에 대해 AI_DAY_STK_PRC 종가와 AI_MIN_STK_PRC 마지막 봉 종가를 비교한다.
장중에 적재된 미완성 일봉이 남아 있으면 SMA 가 오염되므로 이걸로 찾아낸다.

사용: python examples/chkDayMinConsist.py [TRADE_DT]
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.dbConn import openDbConn

TOL_PCT = 0.3

tradeDt = sys.argv[1] if len(sys.argv) > 1 else "20260818"

SQL = """
SELECT d.STK_CD, d.CLOSE_PRC dayClose, d.HIGH_PRC dayHi, d.LOW_PRC dayLo,
       m.lastClose, m.loPrc, m.hiPrc, m.barCnt
FROM AI_DAY_STK_PRC d
JOIN (
  SELECT a.STK_CD, a.CLOSE_PRC lastClose, b.loPrc, b.hiPrc, b.barCnt
  FROM AI_MIN_STK_PRC a
  JOIN (
    SELECT STK_CD, MAX(BAR_TM) mx, MIN(LOW_PRC) loPrc,
           MAX(HIGH_PRC) hiPrc, COUNT(*) barCnt
    FROM AI_MIN_STK_PRC
    WHERE TRADE_DT=%s AND TIC_SCOPE='1'
    GROUP BY STK_CD
  ) b ON b.STK_CD=a.STK_CD AND b.mx=a.BAR_TM
  WHERE a.TRADE_DT=%s AND a.TIC_SCOPE='1'
) m ON m.STK_CD=d.STK_CD
WHERE d.TRADE_DT=%s AND d.INDC_TP='0'
ORDER BY d.STK_CD
"""

conn = openDbConn()
try:
    with conn.cursor() as cur:
        cur.execute(SQL, (tradeDt, tradeDt, tradeDt))
        rows = cur.fetchall() or []
finally:
    conn.close()

bad = []
for r in rows:
    dayClose = float(r.get("dayClose") or 0)
    minClose = float(r.get("lastClose") or 0)
    diffPct = ((minClose - dayClose) / dayClose * 100) if dayClose else 0.0
    isBad = abs(diffPct) > TOL_PCT
    if isBad:
        bad.append(r["STK_CD"])
    print(
        f"{r['STK_CD']} 일봉종가 {dayClose:>10,.0f} · 분봉끝 {minClose:>10,.0f} "
        f"({diffPct:+6.2f}%) · 일봉저 {float(r.get('dayLo') or 0):>10,.0f} "
        f"분봉저 {float(r.get('loPrc') or 0):>10,.0f} · bars {r.get('barCnt')} "
        f"{'MISMATCH' if isBad else 'ok'}"
    )

print()
print(f"{tradeDt} 비교 {len(rows)}종 · 불일치 {len(bad)}종 (기준 {TOL_PCT}%)")
if bad:
    print("불일치 종목: " + ", ".join(bad))
