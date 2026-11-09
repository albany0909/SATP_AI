# -*- coding: utf-8 -*-
"""지수 명부 조회는 하지 않는다.

급등주 후보는 ka00198 당일누적 순위만 쓴다.
이 스크립트는 파일을 읽거나 쓰지 않고, ka20002 와 KRX 를 호출하지 않는다.
"""
from __future__ import annotations


def fetchKospiPaperPages():
    return {"ok": False, "reason": "MEMBERSHIP_DISABLED", "pages": []}


def fetchKosdaq150Krx(tradeYmd):
    return {"ok": False, "reason": "MEMBERSHIP_DISABLED", "output": [], "indexCodes": None}


def refreshIfDue(now=None, path=None):
    return {"fetched": False, "reason": "MEMBERSHIP_DISABLED", "orders": 0}


def main():
    result = refreshIfDue()
    print(f"orders={result['orders']} fetched={result['fetched']} reason={result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
