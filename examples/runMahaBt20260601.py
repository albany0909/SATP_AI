"""005930 1분봉 적재 후 mahaPullback 20260601 백테스트"""

from datetime import datetime, timedelta

from api.getMinStkPrc import fetchMinStkPrcRange
from api.getToken import issueAccToken
from backtest.engine import formatBtResult, runBacktest
from config.config import real_app_key, real_app_secret, real_host_url
from utils.minStkPrcStore import upsertMinStkPrcRows

STK = "005930"
START = "20260601"
END = "20260601"
WARM = (datetime.strptime(START, "%Y%m%d") - timedelta(days=10)).strftime("%Y%m%d")


def main():
    issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
    print("token", issued.get("ok"), issued.get("errMsg"))
    if not issued.get("ok"):
        return 1

    print(f"load minprc {STK} 1 {WARM}~{END}")
    fetched = fetchMinStkPrcRange(
        real_host_url,
        issued["token"],
        STK,
        WARM,
        END,
        ticScope="1",
        tokenType=issued.get("tokenType") or "bearer",
        isPaper=False,
    )
    print(
        "fetch",
        fetched.get("ok"),
        fetched.get("errMsg"),
        "cnt",
        fetched.get("rowCnt"),
        "page",
        fetched.get("pageUsed"),
    )
    if not fetched.get("ok"):
        return 1
    rows = fetched.get("rows") or []
    if rows:
        saved = upsertMinStkPrcRows(rows)
        print("upsert", saved)

    result = runBacktest(
        "mahaPullback_v1",
        STK,
        START,
        END,
        warmupStartDt=WARM,
        ticScope="1",
    )
    print(formatBtResult(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
