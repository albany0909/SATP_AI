"""
재무 에이전트 CLI 진입점.

매매 메인(main.py)과 별도 프로세스/세션으로 재무 조회·진단을 수행한다.
  python -m financeAgent.agentMain scope
  python -m financeAgent.agentMain fin 005930
  python -m financeAgent.agentMain fin 삼성전자 --load-rpt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 프로젝트 루트를 path 에 추가 (모듈 직접 실행 시)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _cmdScope(_args):
    from financeAgent.serviceApi import agentScope

    print(json.dumps(agentScope(), ensure_ascii=False, indent=2))
    return 0


def _cmdFin(args):
    from financeAgent.serviceApi import getStkFinanceBundle

    data = getStkFinanceBundle(
        args.stk,
        loadRpt=bool(args.load_rpt),
        finYear=args.year,
        rptLimit=args.limit,
    )
    # 터미널 요약 (전체 JSON 은 --json)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0 if data.get("ok") else 1

    if not data.get("ok"):
        print(f"[financeAgent] FAIL: {data.get('errMsg')}")
        return 1

    fin = data.get("fin") or {}
    print(
        f"[financeAgent] {data.get('stkNm')} ({data.get('stkCd')})\n"
        f"  DART: ok={fin.get('ok')} {fin.get('bsnsYear')} {fin.get('reprtLabel') or ''}\n"
        f"  핵심계정={len(fin.get('highlights') or [])} "
        f"리포트={len(data.get('reports') or [])} "
        f"목표가={len(data.get('cns') or [])} "
        f"가이던스={len(data.get('guides') or [])}"
    )
    if not fin.get("ok"):
        print(f"  DART 메모: {fin.get('errMsg')}")
    for h in (fin.get("highlights") or [])[:8]:
        print(
            f"  - {h.get('fsNm') or h.get('fsDiv')} "
            f"{h.get('accountNm')}: {h.get('thstrmAmount')}"
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SATP_AI Finance Agent — 재무·리포트 (매매와 분리)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pScope = sub.add_parser("scope", help="담당 범위 출력")
    pScope.set_defaults(func=_cmdScope)

    pFin = sub.add_parser("fin", help="종목 재무·리포트 번들 조회")
    pFin.add_argument("stk", help="종목코드 또는 종목명")
    pFin.add_argument(
        "--load-rpt",
        action="store_true",
        help="리포트 DB 적재 후 조회",
    )
    pFin.add_argument("--year", default=None, help="사업연도 YYYY")
    pFin.add_argument("--limit", type=int, default=15, help="리포트 건수")
    pFin.add_argument("--json", action="store_true", help="전체 JSON 출력")
    pFin.set_defaults(func=_cmdFin)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
