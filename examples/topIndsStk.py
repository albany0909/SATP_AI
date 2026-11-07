"""전업종지수(ka20003) 등락률 Top N + 업종별주가(ka20002) Top 5 출력 예제"""

from __future__ import annotations

import argparse
import json
import sys
import time
from io import TextIOWrapper
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.getAllIndsIdex import fetchTopIndsByFluRt
from api.getIndsStkPrc import fetchTopStkInInds
from utils.dayPrcLoadService import acquireKiwoomSession

if isinstance(sys.stdout, TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")
if isinstance(sys.stderr, TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8")


def _fmtFlu(v):
    if v is None:
        return "-"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmtPrc(v):
    if v is None:
        return "-"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        try:
            return f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)


def _fmtAmt(v):
    if v is None:
        return "-"
    try:
        return f"{float(v):,.0f}백만"
    except (TypeError, ValueError):
        return str(v)


def main(argv=None):
    parser = argparse.ArgumentParser(description="업종 등락률 Top + 업종별 종목 Top5")
    parser.add_argument("--top-inds", type=int, default=5, help="상위 업종 수 (기본 5)")
    parser.add_argument("--top-stk", type=int, default=5, help="업종당 상위 종목 수 (기본 5)")
    parser.add_argument(
        "--mode",
        choices=["paper", "real", "auto"],
        default="auto",
        help="키움 세션 모드 (기본 auto)",
    )
    parser.add_argument(
        "--stex",
        choices=["1", "2", "3"],
        default="3",
        help="거래소구분 1=KRX 2=NXT 3=통합 (기본 3)",
    )
    parser.add_argument(
        "--by-amt",
        action="store_true",
        help="업종/종목을 거래대금(없으면 거래량) 우선 정렬",
    )
    parser.add_argument(
        "--include-agg",
        action="store_true",
        help="종합/대형주 등 집계 업종도 포함",
    )
    parser.add_argument(
        "--dump-err-body",
        action="store_true",
        help="API 실패 시 body JSON 출력",
    )
    args = parser.parse_args(argv)

    prefer = None if args.mode == "auto" else args.mode
    session = acquireKiwoomSession(preferMode=prefer)
    if not session:
        print("키움 세션 확보 실패 (.env 앱키 / 런타임 로그인 확인)")
        return 1

    mode, hostUrl, token, tokenType = session
    isPaper = mode == "paper"
    modeLabel = "모의투자" if isPaper else "실투자"
    stexTp = args.stex
    stexLabel = {"1": "KRX", "2": "NXT", "3": "통합"}.get(stexTp, stexTp)

    print(f"=== 전업종 등락률 Top {args.top_inds} + 업종별 종목 Top {args.top_stk} ===")
    print(f"세션: {modeLabel} | 거래소: {stexLabel} | 정렬: {'거래대금' if args.by_amt else '등락률'}")
    print()

    topInds = fetchTopIndsByFluRt(
        hostUrl,
        token,
        topN=args.top_inds,
        excludeAgg=not args.include_agg,
        tokenType=tokenType,
        isPaper=isPaper,
        sortByTrdePrica=args.by_amt,
    )
    if not topInds.get("ok"):
        print(f"[오류] 전업종지수(ka20003) 실패: {topInds.get('errMsg')}")
        if args.dump_err_body and topInds.get("body") is not None:
            print(json.dumps(topInds.get("body"), ensure_ascii=False, indent=2))
        return 1

    indsRows = topInds.get("rows") or []
    if topInds.get("errors"):
        print(f"(일부 루트 경고: {'; '.join(topInds['errors'])})")
    print(
        f"조회 업종 {topInds.get('allCnt')}개 중 "
        f"필터 후 {topInds.get('filteredCnt')}개 → Top {len(indsRows)}"
    )
    print()

    if not indsRows:
        print("상위 업종이 비어 있습니다. (장 마감/데이터 없음 가능)")
        return 0

    waitSec = 1.05 if isPaper else 0.25

    for i, inds in enumerate(indsRows, 1):
        indsCd = inds.get("indsCd")
        indsNm = inds.get("indsNm") or "-"
        mrktTp = inds.get("mrktTp") or "0"
        mktLabel = {"0": "KOSPI", "1": "KOSDAQ", "2": "KOSPI200"}.get(
            str(mrktTp), str(mrktTp)
        )
        print(
            f"[{i}] {indsNm}({indsCd}) [{mktLabel}] "
            f"등락 {_fmtFlu(inds.get('fluRt'))} | "
            f"지수 {_fmtPrc(inds.get('curPrc'))} | "
            f"대금 {_fmtAmt(inds.get('trdePrica'))}"
        )

        time.sleep(waitSec)

        stkRes = fetchTopStkInInds(
            hostUrl,
            token,
            indsCd=indsCd,
            mrktTp=mrktTp,
            stexTp=stexTp,
            topN=args.top_stk,
            tokenType=tokenType,
            isPaper=isPaper,
            sortByTrdePrica=args.by_amt,
        )
        if not stkRes.get("ok"):
            print(f"    → 종목 조회 실패: {stkRes.get('errMsg')}")
            if args.dump_err_body and stkRes.get("body") is not None:
                print(json.dumps(stkRes.get("body"), ensure_ascii=False, indent=2))
            print()
            continue

        stkRows = stkRes.get("rows") or []
        if not stkRows:
            print(f"    → 종목 없음 (전체 {stkRes.get('allCnt') or 0}건)")
            print()
            continue

        for j, stk in enumerate(stkRows, 1):
            print(
                f"    {j}. {stk.get('stkCd')} {stk.get('stkNm') or '-'} | "
                f"{_fmtFlu(stk.get('fluRt'))} | "
                f"현재가 {_fmtPrc(stk.get('curPrc'))}"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
