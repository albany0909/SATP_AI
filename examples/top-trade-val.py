"""모의투자 토큰 발급 후 거래대금상위(ka10032) 조회 예제"""

import json
import sys
from io import TextIOWrapper
from pathlib import Path

import requests

# examples/ 에서 실행해도 config 패키지를 찾을 수 있도록 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.config import paper_host_url, paper_app_key, paper_app_secret

# Windows 콘솔 한글 깨짐 방지 (TextIO 스텁에 reconfigure가 없어 isinstance로 좁힘)
if isinstance(sys.stdout, TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")
if isinstance(sys.stderr, TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8")


def getAccToken(hostUrl, appKey, appSecret):
    """접근 토큰 발급 API 호출 후 응답 반환."""
    url = hostUrl + "/oauth2/token"
    headers = {"Content-Type": "application/json;charset=UTF-8"}
    # client_credentials 방식으로 앱키/시크릿 인증
    data = {
        "grant_type": "client_credentials",
        "appkey": appKey,
        "secretkey": appSecret,
    }
    return requests.post(url, headers=headers, json=data)


def getTopTradeVal(hostUrl, token, data, contYn="N", nextKey=""):
    """거래대금상위(ka10032) 조회 API 호출 후 응답 반환."""
    # 순위정보 TR — 거래대금 상위 종목 조회
    url = hostUrl + "/api/dostk/rkinfo"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "cont-yn": contYn,
        "next-key": nextKey,
        "api-id": "ka10032",
    }
    return requests.post(url, headers=headers, json=data)


if __name__ == "__main__":
    # 1. 모의투자용 토큰 발급 (한글 플레이스홀더 사용 금지 — HTTP 헤더는 ASCII만 허용)
    paperRes = getAccToken(paper_host_url, paper_app_key, paper_app_secret)
    paperBody = paperRes.json()
    paperToken = paperBody.get("token", "")
    if not paperToken:
        print("토큰 발급 실패:", json.dumps(paperBody, indent=4, ensure_ascii=False))
        raise SystemExit(1)
    print("모의투자용 토큰 발급: OK")

    # 2. 요청 데이터 (코스피, 관리종목 포함, 통합거래소)
    params = {
        "mrkt_tp": "001",
        "mang_stk_incls": "1",
        "stex_tp": "3",
    }

    # 3. 거래대금상위 조회
    topRes = getTopTradeVal(paper_host_url, paperToken, params)
    print("Code:", topRes.status_code)
    print(
        "Header:",
        json.dumps(
            {key: topRes.headers.get(key) for key in ["next-key", "cont-yn", "api-id"]},
            indent=4,
            ensure_ascii=False,
        ),
    )
    print("Body:", json.dumps(topRes.json(), indent=4, ensure_ascii=False))
