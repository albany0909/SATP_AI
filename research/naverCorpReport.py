"""
네이버 페이 증권 기업리포트(종목 리서치) 목록 수집.

목록: https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}
상세: company_read.naver?nid=...
PDF 본문 전문 추출·재배포는 하지 않음 (메타·제목·요약 필드만).
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

from config.researchSources import getResearchDefaults, naverFinanceUrls
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_BASE = "https://finance.naver.com/research/"
_LIST = (
    "https://finance.naver.com/research/company_list.naver"
    "?searchType=itemCode&itemCode={stkCd}&page={page}"
)
_READ = (
    "https://finance.naver.com/research/company_read.naver"
    "?nid={nid}&page=1&searchType=itemCode&itemCode={stkCd}"
)


def _ua():
    d = getResearchDefaults()
    return d.get("userAgent") or (
        "Mozilla/5.0 (compatible; SATP_AI-Research/0.1; +local)"
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
        return max(0.2, float(d.get("minIntervalMs") or 800) / 1000.0)
    except (TypeError, ValueError):
        return 0.8


def _fetchHtml(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _ua(),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as res:
        raw = res.read()
    # 네이버 금융 목록은 주로 EUC-KR
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
    return " ".join(text.split())


def _parseYmdFromShort(raw):
    """'26.08.05' → '20260805'."""
    text = str(raw or "").strip()
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2})$", text)
    if not m:
        return ""
    yy, mm, dd = m.groups()
    # 20xx 가정 (리포트 연도)
    return f"20{yy}{mm}{dd}"


def parseCompanyListHtml(html, stkCd=""):
    """type_1 테이블 → 리포트 리스트."""
    items = []
    m = re.search(
        r'<table[^>]*class="type_1"[^>]*>(.*?)</table>',
        html or "",
        re.S | re.I,
    )
    if not m:
        return items
    table = m.group(1)
    for tr in re.findall(r"<tr>(.*?)</tr>", table, re.S | re.I):
        if "company_read.naver" not in tr:
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(tds) < 4:
            continue
        # 0 종목, 1 제목(+링크), 2 증권사, 3 PDF, 4 날짜, 5 조회
        titleHtml = tds[1]
        title = _stripTags(titleHtml)
        nidM = re.search(r"company_read\.naver\?nid=(\d+)", titleHtml)
        nid = nidM.group(1) if nidM else ""
        broker = _stripTags(tds[2]) if len(tds) > 2 else ""
        pdfM = re.search(
            r'href="(https?://[^"]+\.pdf)"',
            tds[3] if len(tds) > 3 else "",
            re.I,
        )
        pdfUrl = pdfM.group(1) if pdfM else ""
        dateRaw = _stripTags(tds[4]) if len(tds) > 4 else ""
        viewsRaw = _stripTags(tds[5]) if len(tds) > 5 else ""
        try:
            views = int(re.sub(r"[^\d]", "", viewsRaw) or "0")
        except ValueError:
            views = 0
        stkNm = _stripTags(tds[0]) if tds else ""
        codeFromLink = ""
        cm = re.search(r"code=(\d{6})", tr)
        if cm:
            codeFromLink = cm.group(1)
        detailPath = ""
        if nid:
            detailPath = _READ.format(
                nid=nid, stkCd=codeFromLink or stkCd or "000000"
            )
        items.append(
            {
                "nid": nid,
                "title": title,
                "broker": broker,
                "pubDt": _parseYmdFromShort(dateRaw),
                "pubDtRaw": dateRaw,
                "views": views,
                "pdfUrl": pdfUrl,
                "detailUrl": detailPath,
                "stkNm": stkNm,
                "stkCd": codeFromLink or stkCd,
                "source": "naverFinance",
            }
        )
    return items


def parseCompanyReadHtml(html):
    """
    상세 페이지에서 투자의견·목표가 등 메타 추출 (있을 때만).
    """
    text = html or ""
    out = {
        "opinion": "",
        "targetPrc": None,
        "targetPrcRaw": "",
        "summary": "",
        "author": "",
    }
    # 테이블 셀 기반 휴리스틱
    flat = _stripTags(text)
    # 목표가 123,000원 / 목표가 120000
    tm = re.search(
        r"목표가\s*[:\s]?\s*([0-9][0-9,]{2,})\s*원?",
        flat,
    )
    if tm:
        out["targetPrcRaw"] = tm.group(1)
        try:
            out["targetPrc"] = int(tm.group(1).replace(",", ""))
        except ValueError:
            pass
    # 투자의견
    om = re.search(
        r"투자의견\s*[:\s]?\s*([가-힣A-Za-z+/]{1,20})",
        flat,
    )
    if om:
        out["opinion"] = om.group(1).strip()
    else:
        for kw in (
            "강력매수",
            "매수",
            "중립",
            "보유",
            "비중확대",
            "비중축소",
            "매도",
            "Overweight",
            "Underweight",
            "Buy",
            "Hold",
            "Sell",
            "Trading Buy",
            "Outperform",
            "Marketperform",
        ):
            if re.search(rf"\b{re.escape(kw)}\b", flat) or kw in flat[:800]:
                # 제목 근처가 아닐 때만 — 너무 광범위하면 스킵
                if flat.find(kw) < 1500:
                    out["opinion"] = kw
                    break
    # 본문 요약: source_info 또는 본문 앞부분
    bodyM = re.search(
        r'(?is)<div[^>]*class="[^"]*view_cnt[^"]*"[^>]*>(.*?)</div>',
        text,
    )
    if bodyM:
        summary = _stripTags(bodyM.group(1))
        out["summary"] = summary[:1200]
    elif not out["summary"]:
        # 폴백: 본문 후보 텍스트 중 긴 문단
        chunks = re.findall(r"(?is)<p[^>]*>(.*?)</p>", text)
        merged = _stripTags(" ".join(chunks[:12]))
        if len(merged) > 40:
            out["summary"] = merged[:1200]
    return out


def fetchCorpReportList(stkCd, maxPages=2, maxItems=30):
    """
    종목 기업리포트 목록.
    반환: {ok, stkCd, items, listUrl, errMsg?}
    """
    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드 없음", "items": []}
    pages = max(1, min(int(maxPages or 1), 5))
    limit = max(1, min(int(maxItems or 30), 80))
    items = []
    lastErr = ""
    for page in range(1, pages + 1):
        if page > 1:
            time.sleep(_intervalSec())
        url = _LIST.format(stkCd=code, page=page)
        try:
            html = _fetchHtml(url)
        except urllib.error.HTTPError as err:
            lastErr = f"HTTP {err.code}"
            termLog(f"[naverCorpReport] list fail {code} p{page}: {lastErr}")
            break
        except Exception as err:
            lastErr = str(err)
            termLog(f"[naverCorpReport] list fail {code} p{page}: {err}")
            break
        batch = parseCompanyListHtml(html, stkCd=code)
        if not batch:
            break
        items.extend(batch)
        if len(items) >= limit:
            break
    items = items[:limit]
    return {
        "ok": True if items or not lastErr else False,
        "stkCd": code,
        "items": items,
        "listUrl": _LIST.format(stkCd=code, page=1),
        "cnt": len(items),
        "errMsg": lastErr if not items and lastErr else "",
        "sourceHome": (naverFinanceUrls(code) or {}).get("home") or "",
    }


def enrichReportDetail(item, stkCd):
    """목록 1건에 상세 메타 보강 (요약·목표가·의견)."""
    if not item or not item.get("nid"):
        return item
    code = normStkCd(stkCd or item.get("stkCd"))
    url = item.get("detailUrl") or _READ.format(nid=item["nid"], stkCd=code)
    time.sleep(_intervalSec())
    try:
        html = _fetchHtml(url)
        meta = parseCompanyReadHtml(html)
        item = dict(item)
        item.update(meta)
        item["detailUrl"] = url
        item["detailFetched"] = True
    except Exception as err:
        item = dict(item)
        item["detailFetched"] = False
        item["detailErr"] = str(err)
    return item
