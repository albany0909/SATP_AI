"""ka10016 신고저가 조회 예제 (모의 토큰)"""

import json
import sys
from io import TextIOWrapper
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.getNtlPric import DT_250, MRKT_ALL, NTL_HIGH, fetchNtlPric
from config.config import paper_app_key, paper_app_secret, paper_host_url

if isinstance(sys.stdout, TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


def _issueToken():
    r = requests.post(
        paper_host_url + "/oauth2/token",
        headers={"Content-Type": "application/json;charset=UTF-8"},
        json={
            "grant_type": "client_credentials",
            "appkey": paper_app_key,
            "secretkey": paper_app_secret,
        },
        timeout=30,
    )
    body = r.json()
    return body.get("token") or body.get("access_token"), body


if __name__ == "__main__":
    token, body = _issueToken()
    if not token:
        print("토큰 실패", json.dumps(body, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print("token OK")

    # 52주 신고 + 등락 10% 이상
    got = fetchNtlPric(
        paper_host_url,
        token,
        mrktTp=MRKT_ALL,
        ntlTp=NTL_HIGH,
        dt=DT_250,
        isPaper=True,
        minFluRt=10.0,
        bullOnly=True,
        maxRows=30,
    )
    print("ok", got.get("ok"), "cnt", got.get("rowCnt"), "err", got.get("errMsg"))
    for r in got.get("rows") or []:
        print(r["stkCd"], r["stkNm"], r.get("curPrc"), r.get("fluRt"))
