"""테마그룹별(ka90001) 등락률 Top N + 테마구성종목(ka90002) Top 5 출력 예제"""

from __future__ import annotations

import argparse
import json
import sys
import time
from io import TextIOWrapper
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.getThemaCompStk import fetchTopStkInThema
from api.getThemaGrp import fetchTopThemaByFluRt
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


def _fmtQty(v):
    if v is None:
        return "-"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def main(argv=None):
    parser = argparse.ArgumentParser(description="테마 등락률 Top + 테마내 종목 Top5")
    parser.add_argument("--top-thema", type=int, default=5, help="테마 출력 수 (기본 5)")
    parser.add_argument("--top-stk", type=int, default=5, help="테마당 종목 출력 수 (기본 5)")
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
        "--date-tp",
        type=str,
        default="1",
        help="날짜구분 n일전 (1~99, 기본 1; 기간수익률 기준)",
    )
    parser.add_argument(
        "--by-prft",
        action="store_true",
        help="테마/종목을 기간수익률 우선 정렬 (기본은 등락률)",
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
    modeLabel = "모의투자" if isPaper else "실전투자"
    stexTp = args.stex
    stexLabel = {"1": "KRX", "2": "NXT", "3": "통합"}.get(stexTp, stexTp)
    sortLabel = "기간수익률" if args.by_prft else "등락률"
    # flu_pl_amt_tp: 3=상위등락률, 1=상위기간수익률
    fluPlAmtTp = "1" if args.by_prft else "3"

    print(f"=== 테마 {sortLabel} Top {args.top_thema} + 테마내 종목 Top {args.top_stk} ===")
    print(
        f"세션: {modeLabel} | 거래소: {stexLabel} | "
        f"정렬: {sortLabel} | date_tp={args.date_tp}일전 | flu_pl_amt_tp={fluPlAmtTp}"
    )
    print()

    topThema = fetchTopThemaByFluRt(
        hostUrl,
        token,
        topN=args.top_thema,
        dateTp=args.date_tp,
        stexTp=stexTp,
        fluPlAmtTp=fluPlAmtTp,
        tokenType=tokenType,
        isPaper=isPaper,
        sortByDtPrft=args.by_prft,
    )
    if not topThema.get("ok"):
        print(f"[실패] 테마그룹별(ka90001) 오류: {topThema.get('errMsg')}")
        if args.dump_err_body and topThema.get("body") is not None:
            print(json.dumps(topThema.get("body"), ensure_ascii=False, indent=2))
        return 1

    themaRows = topThema.get("rows") or []
    print(
        f"조회 테마 {topThema.get('allCnt')}개 중 Top {len(themaRows)} "
        f"(정렬키: {sortLabel} desc)"
    )
    print()

    if not themaRows:
        print("출력할 테마가 없습니다.")
        return 0

    waitSec = 1.05 if isPaper else 0.25

    for i, thema in enumerate(themaRows, 1):
        themaCd = thema.get("themaCd")
        themaNm = thema.get("themaNm") or "-"
        stkNum = thema.get("stkNum")
        rising = thema.get("risingStkNum")
        fall = thema.get("fallStkNum")
        mainStk = thema.get("mainStk") or "-"
        print(
            f"[{i}] {themaNm}({themaCd}) | "
            f"등락 {_fmtFlu(thema.get('fluRt'))} | "
            f"기간수익 {_fmtFlu(thema.get('dtPrftRt'))} | "
            f"종목수 {stkNum if stkNum is not None else '-'} "
            f"(↑{rising if rising is not None else '-'} "
            f"↓{fall if fall is not None else '-'}) | "
            f"주요 {mainStk}"
        )

        time.sleep(waitSec)

        stkRes = fetchTopStkInThema(
            hostUrl,
            token,
            themaGrpCd=themaCd,
            dateTp=args.date_tp,
            stexTp=stexTp,
            topN=args.top_stk,
            tokenType=tokenType,
            isPaper=isPaper,
            sortByDtPrft=args.by_prft,
        )
        if not stkRes.get("ok"):
            print(f"    └ 종목 조회 실패: {stkRes.get('errMsg')}")
            if args.dump_err_body and stkRes.get("body") is not None:
                print(json.dumps(stkRes.get("body"), ensure_ascii=False, indent=2))
            print()
            continue

        stkRows = stkRes.get("rows") or []
        if not stkRows:
            print(f"    └ 종목 없음 (전체 {stkRes.get('allCnt') or 0}개)")
            print()
            continue

        for j, stk in enumerate(stkRows, 1):
            print(
                f"    {j}. {stk.get('stkCd')} {stk.get('stkNm') or '-'} | "
                f"{_fmtFlu(stk.get('fluRt'))} | "
                f"현재가 {_fmtPrc(stk.get('curPrc'))} | "
                f"누적거래량 {_fmtQty(stk.get('accTrdeQty'))}"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
