"""BT trades with entry/exit datetime for catch list."""
import sys

sys.stdout.reconfigure(encoding="utf-8")
from backtest.engine import runBacktest

stks = [
    ("045100", "한영이엔지"),
    ("119850", "지엔씨에너지"),
    ("017000", "신원종합개발"),
    ("095500", "미래나노텍"),
    ("037070", "파세코"),
    ("252990", "샘씨엔에스"),
    ("004540", "깨끗한나라"),
    ("006660", "삼성공조"),
    ("012690", "모나리자"),
]
start, end, warm = "20260702", "20260803", "20260304"


def fmtTm(t):
    c = str(t.get("cntrTm") or "")
    if len(c) >= 14 and c[:8].isdigit():
        return f"{c[0:4]}-{c[4:6]}-{c[6:8]} {c[8:10]}:{c[10:12]}:{c[12:14]}"
    # barTm + tradeDt
    dt = str(t.get("tradeDt") or "")
    bt = str(t.get("barTm") or "")
    if len(dt) == 8 and len(bt) >= 4:
        bt = bt.zfill(6)[-6:]
        return f"{dt[0:4]}-{dt[4:6]}-{dt[6:8]} {bt[0:2]}:{bt[2:4]}:{bt[4:6]}"
    return dt or "-"


print("ok|nm|cd|entry|exit|buy|sell|ret|reason")
for cd, nm in stks:
    r = runBacktest(
        "bullBodyMa2050_v1",
        cd,
        start,
        end,
        warmupStartDt=warm,
        ticScope="1",
        initCash=10_000_000,
        barSource="min",
    )
    if not r.get("ok"):
        print(f"ERR|{nm}|{cd}|{r.get('errMsg')}")
        continue
    trades = r.get("trades") or []
    i = 0
    n = 0
    while i < len(trades):
        if trades[i].get("type") != "BUY":
            i += 1
            continue
        b = trades[i]
        if i + 1 < len(trades) and trades[i + 1].get("type") == "SELL":
            s = trades[i + 1]
            i += 2
        else:
            i += 1
            continue
        n += 1
        ret = (float(s["price"]) / float(b["price"]) - 1) * 100
        ok = "성공" if ret >= 0 else "실패"
        print(
            f"{ok}|{nm}|{cd}|{fmtTm(b)}|{fmtTm(s)}|"
            f"{int(b['price'])}|{int(s['price'])}|{ret:+.2f}%|{s.get('reason')}"
        )
    if n == 0:
        print(f"무거래|{nm}|{cd}|-|-|-|-|-|-")
