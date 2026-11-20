"""현재 PBK 후보의 거래대금을 백테스트 기준으로 출력한다.

기준: 장대양봉 당일을 뺀 직전 20거래일 평균. 일 거래대금은 저장 amount 또는 종가×거래량(원).
3억 원 이상이면 통과. AI_DAY_STK_PRC 는 SELECT 만 한다.

  python3 tools/pbkAmtCheck.py
  python3 tools/pbkAmtCheck.py --state config/data/pbk01_state.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _won(num):
    if num is None:
        return "-"
    return f"{num / 100000000:.2f}억"


def main(argv=None):
    parser = argparse.ArgumentParser(description="PBK 후보 20일 거래대금 점검 (읽기 전용)")
    parser.add_argument("--state", default="")
    args = parser.parse_args(argv)
    from strategies.impl.pullbackTwoStep import MIN_AMT20, signalAmt20
    from utils.chartBarLoad import loadStoredDayBars
    from utils.pbk01Store import latestByKind, pbkPath

    path = Path(args.state) if args.state else pbkPath("pbk01_state.jsonl")
    saved = latestByKind(path, "state") or {}
    rows = list(saved.get("candidates") or [])
    print(f"기준 {MIN_AMT20:.0f}원 (3억). 신호일 제외 직전 20일. 파일 {path}")
    print(f"{'코드':<8} {'이름':<12} {'신호일':<10} {'직전20일':>10} {'신호일포함':>10} 결과")
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        name = str(row.get("stkNm") or "")
        day = str(row.get("signalDt") or "")
        bars = loadStoredDayBars(code) or []
        got = signalAmt20(bars, day) if day else None
        if not got:
            print(f"{code:<8} {name:<12} {day:<10} {'-':>10} {'-':>10} 계산불가")
            continue
        mark = "통과" if got["ok"] else "탈락"
        print(f"{code:<8} {name:<12} {day:<10} {_won(got['prior']):>10} {_won(got['ending']):>10} {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
