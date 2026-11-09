# -*- coding: utf-8 -*-
"""SRG01 입력 준비 상태. 주문을 내지 않는다.

python scripts/srgReadiness.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from trading.surgeTradeInputs import loadMembership, readinessChecks

    path = Path(__file__).resolve().parents[1] / "config" / "data" / "indexMembership.json"
    book = loadMembership(path)
    mode = "paper"
    try:
        from config.settingsStore import getDeskTradeMode

        mode = getDeskTradeMode()
    except Exception:
        mode = ""
    report = readinessChecks(
        env={"DART_API_KEY": os.environ.get("DART_API_KEY", "")},
        membership=book,
        issueCount=0,
        deskMode=mode,
        quoteSeen=False,
    )
    for row in report["checks"]:
        print(f"{row['status']} {row['name']} {row['detail']}")
    print("ORDERS 0")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
