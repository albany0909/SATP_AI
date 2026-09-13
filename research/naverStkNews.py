"""
네이버 페이 증권 종목 뉴스·공시알림 목록 메타 수집 (본문 미저장).
"""

from __future__ import annotations

import re
import time
import urllib.request
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

from config.researchSources import getResearchDefaults
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 이슈성 키워드 (제목 매칭 — 단순 태그)
_ISSUE_WORDS = (
    "급등",
    "급락",
    "상한가",
    "하한가",
    "거래정지",
    "횡령",
    "배임",
    "소송",
    "제재",
    "리콜",
    "실적",
    "흑자",
    "적자",
    "인수",
    "합병",
    "유상증자",
    "무상",
    "자사주",
    "전환사채",
    "CB",
    "BW",
    "유상",
    "공모",
    "상장폐지",
    "관리종목",
    "주의",
    "테마",
    "공급계약",
    "수주",
    "FDA",
    "임상",
    "특허",
    "배당",
    "액면",
    "분할",
    "스팩",
)


def _ua():
    d = getResearchDefaults()
    # 뉴스 목록은 브라우저 UA 없으면 빈 결과 페이지를 돌려주는 경우 있음
    return d.get("browserUserAgent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )


def _timeout():
    d = getResearchDefaults()
    try:
        return float(d.get("requestTimeoutSec") or 20)
    except (TypeError, ValueError):
        return 20.0


def _intervalSec():
    d = getResearchDefaults()
    try:
        return max(0.3, float(d.get("minIntervalMs") or 800) / 1000.0)
    except (TypeError, ValueError):
        return 0.8


def _fetchHtml(url, referer=None):
    headers = {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_timeout()) as res:
        raw = res.read()
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _stripTags(html):
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def isIssueTitle(title):
    t = title or ""
    return any(w in t for w in _ISSUE_WORDS)


def _newsIdFromUrl(url):
    """article_id + office_id 조합 키."""
    try:
        q = parse_qs(urlparse(url).query)
        aid = (q.get("article_id") or q.get("articleId") or [""])[0]
        oid = (q.get("office_id") or q.get("officeId") or [""])[0]
        if aid and oid:
            return f"{oid}_{aid}"
        if aid:
            return f"a_{aid}"
    except Exception:
        pass
    path = urlparse(url).path + "?" + urlparse(url).query
    return "u_" + str(abs(hash(path)) % (10**12))


def _parseNewsDate(text):
    """'2026.08.09' 또는 '08.09' / '08.09 14:30' 등 → (YYYYMMDD, rawTm)"""
    s = (text or "").strip()
    m = re.search(
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}:\d{2}))?",
        s,
    )
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        tm = m.group(4) or ""
        return f"{y}{mo:02d}{d:02d}", tm or s
    m2 = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}:\d{2}))?", s)
    if m2:
        from datetime import datetime

        y = datetime.now().year
        mo, d = int(m2.group(1)), int(m2.group(2))
        tm = m2.group(3) or ""
        return f"{y}{mo:02d}{d:02d}", tm or s
    return "", s[:20]


def _parseNewsPage(html, stkCd, baseUrl):
    items = []
    seen = set()
    # 1) 제목 셀 패턴 (td.title > a.tit)
    patterns = [
        r'<td[^>]*class=["\']title["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r'<a[^>]+href=["\']([^"\']*news_read[^"\']*)["\'][^>]*class=["\']tit["\'][^>]*>(.*?)</a>',
        r'<a[^>]+href=["\']([^"\']*(?:news_read|article_id)[^"\']*)["\'][^>]*>(.*?)</a>',
    ]
    matches = []
    for pat in patterns:
        matches = list(re.finditer(pat, html or "", re.I | re.S))
        if matches:
            break

    for m in matches:
        href = unescape(m.group(1).strip())
        title = _stripTags(m.group(2))
        title = title.strip('"').strip("'").strip()
        if not title or len(title) < 4:
            continue
        if "javascript" in href.lower():
            continue
        full = urljoin("https://finance.naver.com", href)
        if "code=" not in full and stkCd:
            sep = "&" if "?" in full else "?"
            full = f"{full}{sep}code={stkCd}"
        nid = _newsIdFromUrl(full)
        if nid in seen:
            continue
        seen.add(nid)

        start = max(0, m.start() - 40)
        end = min(len(html), m.end() + 280)
        near = _stripTags((html or "")[start:end])
        pubDt, pubTm = _parseNewsDate(near)
        office = ""
        # info 셀 — 언론사
        om = re.search(
            r"(머니투데이|이데일리|한국경제|매일경제|서울경제|연합뉴스|"
            r"조선비즈|헤럴드경제|아시아경제|파이낸셜뉴스|뉴시스|뉴스1|"
            r"한경|매경|이투데이|비즈니스워치|조선일보|중앙일보|한겨레|"
            r"전자신문|디지털타임스|블로터|ZDNet Korea|ZDNet|"
            r"서울신문|세계일보|문화일보|KBS|MBC|SBS|YTN|"
            r"더구루|인베스트조선|딜사이트|아이뉴스24)",
            near,
        )
        if om:
            office = om.group(0).strip()

        items.append(
            {
                "newsId": nid,
                "title": title[:400],
                "officeNm": office[:80],
                "pubDt": pubDt,
                "pubTm": (pubTm or "")[:20],
                "newsUrl": full[:500],
                "issueYn": "Y" if isIssueTitle(title) else "N",
            }
        )
        if len(items) >= 40:
            break
    return items


def fetchNaverStkNews(stkCd, pages=1):
    """
    종목 뉴스 목록 메타.
    반환: {ok, stkCd, items, errMsg?}
    """
    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드 없음", "items": []}

    parent = f"https://finance.naver.com/item/news.naver?code={code}"
    # iframe 실목록 URL (sm 파라미터 필요 — 없으면 빈 테이블)
    listBase = (
        "https://finance.naver.com/item/news_news.naver"
        f"?code={code}&sm=title_entity_id.basic&page={{page}}"
    )
    allItems = []
    seen = set()
    try:
        for page in range(1, max(1, int(pages)) + 1):
            pageUrl = listBase.format(page=page)
            if page > 1:
                time.sleep(_intervalSec())
            html = _fetchHtml(pageUrl, referer=parent)
            for it in _parseNewsPage(html, code, pageUrl):
                if it["newsId"] in seen:
                    continue
                seen.add(it["newsId"])
                allItems.append(it)

            # 공시·알림 탭 메타도 함께 (이슈성)
            noticeUrl = (
                "https://finance.naver.com/item/news_notice.naver"
                f"?code={code}&page={page}"
            )
            time.sleep(_intervalSec() * 0.5)
            nhtml = _fetchHtml(noticeUrl, referer=parent)
            for it in _parseNewsPage(nhtml, code, noticeUrl):
                # 공시 탭 기사도 뉴스로 적재, 이슈 우선
                it["issueYn"] = "Y"
                if it["newsId"] in seen:
                    continue
                seen.add(it["newsId"])
                allItems.append(it)
    except Exception as err:
        termLog(f"[naverStkNews] {code} 실패: {err}")
        return {
            "ok": False,
            "errMsg": str(err),
            "stkCd": code,
            "items": allItems,
        }

    return {"ok": True, "stkCd": code, "items": allItems, "src": "NAVER"}
