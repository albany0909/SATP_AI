"""틱 연속조회로 가장 오래된 시각이 어디인지 확인"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from api.getTicStkPrc import fetchTicStkPrc
from api.getToken import issueAccToken
from config.config import real_app_key, real_app_secret, real_host_url
from utils.ticStkPrcStore import upsertTicStkPrcRows

issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
print("token", issued.get("ok"))
if not issued.get("ok"):
    raise SystemExit(1)

fetched = fetchTicStkPrc(
    real_host_url,
    issued["token"],
    "005930",
    ticScope="1",
    tokenType=issued.get("tokenType") or "bearer",
    isPaper=False,
    maxPages=150,
)
rows = fetched.get("rows") or []
print("cnt", len(rows), "page", fetched.get("pageUsed"), "ok", fetched.get("ok"), fetched.get("errMsg"))
if rows:
    print("newest", rows[-1]["CNTR_TM"], "oldest", rows[0]["CNTR_TM"])
    byDt = {}
    for r in rows:
        byDt[r["TRADE_DT"]] = byDt.get(r["TRADE_DT"], 0) + 1
    for dt in sorted(byDt):
        print(dt, byDt[dt])
    print(upsertTicStkPrcRows(rows))
