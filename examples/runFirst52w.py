"""당일 최초 52주 신고가 진입 종목 조회 실행 스크립트"""

import sys
from io import TextIOWrapper
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.getToken import issueAccToken
from config.config import (
    paper_app_key,
    paper_app_secret,
    paper_host_url,
    real_app_key,
    real_app_secret,
    real_host_url,
)
from utils.ntlFirstEntry import fetchFirst52wHighList, formatFirst52wReport

if isinstance(sys.stdout, TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    # 모의 우선
    issued = issueAccToken(paper_host_url, paper_app_key, paper_app_secret)
    host, isPaper, mode = paper_host_url, True, "paper"
    if not issued.get("ok"):
        issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
        host, isPaper, mode = real_host_url, False, "real"
    if not issued.get("ok"):
        print("토큰 실패:", issued.get("errMsg"))
        return 1

    token = issued["token"]
    tokenType = issued.get("tokenType") or "bearer"
    print(f"로그인 OK ({mode})")

    got = fetchFirst52wHighList(
        host,
        token,
        tokenType=tokenType,
        isPaper=isPaper,
        minFluRt=None,
        useClose=False,
        maxCand=60,
        maxOut=40,
    )
    print(formatFirst52wReport(got, modeLabel=mode))
    return 0 if got.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
