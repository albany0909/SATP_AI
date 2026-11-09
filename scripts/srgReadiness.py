# -*- coding: utf-8 -*-
"""SRG01 입력 준비 상태. 주문을 내지 않는다.

python scripts/srgReadiness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from trading.surgeTradeInputs import dartKeyConfigured, loadMembership, readinessChecks

    path = Path(__file__).resolve().parents[1] / "config" / "data" / "indexMembership.json"
    if "--fetch" in sys.argv:
        from scripts.srgMembershipRefresh import refreshIfDue

        refreshIfDue()
    book = loadMembership(path)
    mode = "paper"
    try:
        from config.settingsStore import getDeskTradeMode

        mode = getDeskTradeMode()
    except Exception:
        mode = ""
    report = readinessChecks(
        membership=book,
        issueCount=0,
        deskMode=mode,
        quoteSeen=False,
        dartConfigured=dartKeyConfigured(),
    )
    for row in report["checks"]:
        print(f"{row['status']} {row['name']} {row['detail']}")
    print("ORDERS 0")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
