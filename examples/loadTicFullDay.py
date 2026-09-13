"""20260731 삼성 1틱 전량 적재 (장 시작까지)"""

from api.getTicStkPrc import fetchTicStkPrc
from api.getToken import issueAccToken
from config.config import real_app_key, real_app_secret, real_host_url
from utils.ticStkPrcStore import upsertTicStkPrcRows

issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
print("token", issued.get("ok"))
if not issued.get("ok"):
    raise SystemExit(1)

# 당일 전체 — startDt 필터로 09:00 이전에서 중단
fetched = fetchTicStkPrc(
    real_host_url,
    issued["token"],
    "005930",
    ticScope="1",
    startDt="20260731",
    endDt="20260731",
    tokenType=issued.get("tokenType") or "bearer",
    isPaper=False,
    maxPages=1000,
)
rows = fetched.get("rows") or []
print(
    "ok",
    fetched.get("ok"),
    "cnt",
    len(rows),
    "page",
    fetched.get("pageUsed"),
    "err",
    fetched.get("errMsg"),
)
if rows:
    print("oldest", rows[0]["CNTR_TM"], "newest", rows[-1]["CNTR_TM"])
    print(upsertTicStkPrcRows(rows))
