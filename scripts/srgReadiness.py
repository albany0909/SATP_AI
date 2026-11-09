# -*- coding: utf-8 -*-
"""SRG01 입력 준비 상태. 주문을 내지 않는다.

python scripts/srgReadiness.py
python scripts/srgReadiness.py --fetch
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fetchRank():
    """모의 세션을 직접 만들고 ka00198 만 저장한다. 토큰은 출력하지 않는다."""
    from trading.surgeTradeLive import (
        _rankStore,
        ensurePaperReadSession,
        paperRankTransport,
        runManualRankSave,
    )

    got = ensurePaperReadSession()
    reason = str(got.get("reason") or "")
    print(f"FETCH {reason}")
    if reason == "NO_PAPER_CREDENTIALS":
        print("FETCH 매매창 모의 로그인 안에서 실행하거나 .env 모의 앱키를 둔다.")
    if not got.get("ok"):
        print("FETCH orders=0")
        return
    result = runManualRankSave(
        got.get("session"),
        _rankStore(),
        datetime.now(),
        paperRankTransport(got.get("session")),
    )
    print(f"FETCH state={result.get('state')} orders={result.get('orders', 0)}")


def main():
    from trading.surgeTradeInputs import dartKeyConfigured, readinessChecks

    if "--fetch" in sys.argv:
        try:
            _fetchRank()
        except Exception:
            print("FETCH PAPER_LOGIN_FAIL")
            print("FETCH orders=0")
    mode = "paper"
    try:
        from config.settingsStore import getDeskTradeMode

        mode = getDeskTradeMode()
    except Exception:
        mode = ""
    report = readinessChecks(
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
