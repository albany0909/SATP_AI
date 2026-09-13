"""이평터치 검증 데이터 점검 — 유니버스 종목의 검증일 1분봉·일봉 보유 현황 (조회만)."""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from trading.maTouchUnivLoad import screenUnivFromDb
from utils.btEnsureMinData import countMinBarsByDay
from utils.dbConn import openDbConn

rankDt = sys.argv[1] if len(sys.argv) > 1 else "20260814"
tradeDt = sys.argv[2] if len(sys.argv) > 2 else "20260818"

univ = screenUnivFromDb(rankDt)
picks = univ.get("picks") or []
conn = openDbConn()
try:
    with conn.cursor() as cur:
        for p in picks:
            cd = p["stkCd"]
            minCnt = int((countMinBarsByDay(cd, "1", tradeDt, tradeDt) or {}).get(tradeDt) or 0)
            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT=%s
                """,
                (cd, tradeDt),
            )
            dayCnt = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT COUNT(*) AS c
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT<=%s
                """,
                (cd, tradeDt),
            )
            histCnt = int((cur.fetchone() or {}).get("c") or 0)
            print(
                f"{cd} {p.get('stkNm'):<12} 1분봉 {minCnt:>4} · "
                f"{tradeDt}일봉 {dayCnt} · 누적일봉 {histCnt}"
            )
finally:
    conn.close()
