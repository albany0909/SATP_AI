"""20260731 1분봉 vs 1틱 마하세븐 비교 (데이터 재사용)"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from datetime import datetime, timedelta

from api.getMinStkPrc import fetchMinStkPrcRange
from api.getToken import issueAccToken
from backtest.engine import formatBtResult, runBacktest
from config.config import real_app_key, real_app_secret, real_host_url
from utils.dbConn import openDbConn
from utils.minStkPrcStore import upsertMinStkPrcRows

STK = "005930"
DAY = "20260731"


def main():
    print("NOTE: ka10079 cannot reach 20260601 — comparing on", DAY)
    warm = (datetime.strptime(DAY, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM AI_MIN_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE=%s AND TRADE_DT BETWEEN %s AND %s
                """,
                (STK, "1", warm, DAY),
            )
            minCnt = cur.fetchone()["cnt"]
            cur.execute(
                """
                SELECT COUNT(*) AS cnt, MIN(CNTR_TM) AS mn, MAX(CNTR_TM) AS mx
                FROM AI_TIC_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE=%s AND TRADE_DT=%s
                """,
                (STK, "1", DAY),
            )
            ticInfo = cur.fetchone()
    finally:
        conn.close()

    print("minCnt", minCnt, "ticInfo", dict(ticInfo))

    if minCnt < 200:
        issued = _token()
        fetched = fetchMinStkPrcRange(
            real_host_url,
            issued["token"],
            STK,
            warm,
            DAY,
            ticScope="1",
            tokenType=issued.get("tokenType") or "bearer",
            isPaper=False,
        )
        if not fetched.get("ok"):
            print("min fail", fetched.get("errMsg"))
            return 1
        upsertMinStkPrcRows(fetched.get("rows") or [])

    # 틱이 장초반까지 없으면 추가 적재
    needMore = True
    if ticInfo["cnt"] and ticInfo["mn"] and str(ticInfo["mn"]) <= DAY + "091000":
        needMore = False

    if needMore:
        issued = _token()
        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM AI_TIC_STK_PRC WHERE STK_CD=%s AND TIC_SCOPE=%s AND TRADE_DT=%s",
                    (STK, "1", DAY),
                )
            conn.commit()
        finally:
            conn.close()

        fetched = fetchTicStkPrc(
            real_host_url,
            issued["token"],
            STK,
            ticScope="1",
            startDt=DAY,
            endDt=DAY,
            tokenType=issued.get("tokenType") or "bearer",
            isPaper=False,
            maxPages=1000,
        )
        rows = fetched.get("rows") or []
        print("tic refetch cnt", len(rows), "page", fetched.get("pageUsed"))
        if not rows:
            print("틱 당일 데이터 부족")
            return 1
        print("oldest", rows[0]["CNTR_TM"], "newest", rows[-1]["CNTR_TM"])
        print(upsertTicStkPrcRows(rows))

    rMin = runBacktest(
        "mahaPullback_v1", STK, DAY, DAY, warmupStartDt=warm, ticScope="1", barSource="min"
    )
    print("\n===== 1분봉 =====")
    print(formatBtResult(rMin))

    rTic = runBacktest(
        "mahaPullback_v1", STK, DAY, DAY, warmupStartDt=DAY, ticScope="1", barSource="tic"
    )
    print("\n===== 1틱 =====")
    print(formatBtResult(rTic))

    if rMin.get("ok") and rTic.get("ok"):
        sm, st = rMin["stats"], rTic["stats"]
        print("\n===== 비교 =====")
        print(
            f"거래 {sm['tradeCnt']}회 → {st['tradeCnt']}회 | "
            f"복리 {sm['finalRetPct']:+.3f}% → {st['finalRetPct']:+.3f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
