"""
외국인 일별 순매수 수집.

KRX 정보데이터시스템은 세션(LOGOUT)이 필요해
네이버 금융 일자별 순매수(단위 억원, 코스피)를 쓴다.
선물 일별은 같은 페이지에 없어 현물만 적재한다.
"""

import re
import time
from datetime import datetime

import requests

from utils.termLog import termLog

_NAVER_URL = "https://finance.naver.com/sise/investorDealTrendDay.naver"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SLEEP_SEC = 0.25
_MAX_PAGES = 120
_EOK_TO_WON = 100_000_000

_ROW_RE = re.compile(
    r'<td class="date2">(\d{2}\.\d{2}\.\d{2})</td>\s*'
    r"<td[^>]*>([^<]+)</td>\s*"
    r"<td[^>]*>([^<]+)</td>",
    re.IGNORECASE,
)


def _normYmd(raw):
    text = str(raw or "").replace("-", "").replace("/", "").replace(".", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _yyMmDdToYmd(raw):
    """26.08.28 → 20260828"""
    parts = str(raw or "").strip().split(".")
    if len(parts) != 3:
        return None
    yy, mm, dd = parts
    if len(yy) != 2 or not (yy.isdigit() and mm.isdigit() and dd.isdigit()):
        return None
    year = 2000 + int(yy)
    return _normYmd(f"{year:04d}{int(mm):02d}{int(dd):02d}")


def _parseEok(raw):
    text = str(raw or "").replace(",", "").replace("&nbsp;", "").strip()
    if text in ("", "-", "—"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _fetchPage(sess, bizdate, page):
    res = sess.get(
        _NAVER_URL,
        params={"bizdate": bizdate, "sosok": "01", "page": str(page)},
        timeout=20,
    )
    res.raise_for_status()
    # 네이버 표는 euc-kr 이지만 class/숫자는 ASCII
    return res.content.decode("euc-kr", errors="ignore")


def fetchFrgnSpotFut(startDt, endDt):
    """
    코스피 외국인 일자별 순매수.
    반환 rows 의 spotNet 은 원 (억원×1억).
    """
    start = _normYmd(startDt)
    end = _normYmd(endDt)
    if not start or not end:
        return {"ok": False, "errMsg": "일자 형식 YYYYMMDD", "rows": []}
    if start > end:
        start, end = end, start

    sess = requests.Session()
    sess.headers.update({"User-Agent": _UA})
    spotMap = {}
    try:
        for page in range(1, _MAX_PAGES + 1):
            if page > 1:
                time.sleep(_SLEEP_SEC)
            html = _fetchPage(sess, end, page)
            found = 0
            older = False
            for m in _ROW_RE.finditer(html):
                ymd = _yyMmDdToYmd(m.group(1))
                eok = _parseEok(m.group(3))
                if not ymd or eok is None:
                    continue
                found += 1
                if ymd > end:
                    continue
                if ymd < start:
                    older = True
                    continue
                spotMap[ymd] = int(round(eok * _EOK_TO_WON))
            if found == 0 or older:
                break
    except Exception as err:
        termLog(f"[frgnSf] 네이버 조회 실패: {err}")
        return {"ok": False, "errMsg": f"네이버 수급 실패: {err}", "rows": []}

    rows = [
        {"tradeDt": ymd, "spotNet": spotMap[ymd], "futNet": None, "note": "naver_kospi"}
        for ymd in sorted(spotMap.keys())
    ]
    if not rows:
        return {"ok": False, "errMsg": "네이버 수급 행 없음", "rows": [], "futOk": False}
    termLog(f"[frgnSf] 네이버 현물 {len(rows)}일 ({start}~{end})")
    return {
        "ok": True,
        "rows": rows,
        "futOk": False,
        "spotCnt": len(rows),
        "futCnt": 0,
    }
