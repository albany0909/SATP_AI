"""
이평 기준 비교 (조회만).

HTS 차트 방식: 마지막 점이 당일 봉이라 **당일 현재가를 종가로 넣어** 계산 →
  SMA(n) = (직전 n-1 거래일 종가 + 현재가) / n  · 장중 실시간으로 움직인다
전략(maTouchScale) 방식: **전일까지 확정 종가 n개** → 장중 고정

당일 매매 이벤트의 체결가를 현재가로 넣어 두 값을 나란히 본다.
8/18 장중값 일봉은 1분봉 실제 종가로 정정해 계산한다.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from utils.dbConn import openDbConn

FIX_DT = "20260818"

# (종목코드, 종목명, 매매 시점 현재가, 전략이 본 이평기간)
CASES = [
    ("005930", "삼성전자", 249750.0, 10),
    ("005380", "현대차", 413500.0, 10),
    ("011070", "LG이노텍", 591000.0, 10),
    ("034020", "두산에너빌리티", 73700.0, 15),
    ("036930", "주성엔지니어링", 176400.0, 5),
    ("011200", "HMM", 21550.0, 5),
    ("403870", "HPSP", 40250.0, 5),
]


def loadCloses(cur, stkCd, upToDt, limit=40):
    cur.execute(
        """
        SELECT TRADE_DT, CLOSE_PRC
        FROM AI_DAY_STK_PRC
        WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT<=%s
        ORDER BY TRADE_DT DESC LIMIT %s
        """,
        (stkCd, upToDt, limit),
    )
    return [(r["TRADE_DT"], float(r["CLOSE_PRC"] or 0)) for r in (cur.fetchall() or [])]


def lastMinClose(cur, stkCd, ymd):
    cur.execute(
        """
        SELECT CLOSE_PRC FROM AI_MIN_STK_PRC
        WHERE STK_CD=%s AND TIC_SCOPE='1' AND TRADE_DT=%s
        ORDER BY BAR_TM DESC LIMIT 1
        """,
        (stkCd, ymd),
    )
    row = cur.fetchone()
    return float(row["CLOSE_PRC"]) if row and row.get("CLOSE_PRC") else None


conn = openDbConn()
try:
    with conn.cursor() as cur:
        for cd, nm, curPx, per in CASES:
            closes = loadCloses(cur, cd, FIX_DT)
            if len(closes) < per:
                print(f"{nm}({cd}): 일봉 부족")
                continue
            real = lastMinClose(cur, cd, FIX_DT)
            if real and closes[0][0] == FIX_DT:
                if abs(real - closes[0][1]) / closes[0][1] * 100 > 0.3:
                    closes[0] = (FIX_DT, real)
            stratSma = sum(c for _, c in closes[:per]) / per
            chartSma = (sum(c for _, c in closes[: per - 1]) + curPx) / per
            gap = chartSma - stratSma
            touchStrat = "터치" if curPx <= stratSma else "미터치"
            touchChart = "터치" if curPx <= chartSma else "미터치"
            print(f"[{nm}({cd})] 현재가 {curPx:,.0f} · SMA{per}")
            print(
                f"   전략(전일확정) {stratSma:>11,.0f} → {touchStrat}\n"
                f"   차트(당일포함) {chartSma:>11,.0f} → {touchChart}  "
                f"(차이 {gap:+,.0f})"
            )
finally:
    conn.close()
