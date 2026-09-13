"""
네이버 페이 증권 주요재무정보 표 파싱.
- 기본: wiseReport ajax cF1001 (연간+분기, (E) 추정 포함 — 화면과 동일 골격)
- 폴백: finance.naver.com item/main.naver 축약 표
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from html import unescape

from config.researchSources import getResearchDefaults
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog


def _ua():
    d = getResearchDefaults()
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


def _fetchHtml(url, referer=None):
    headers = {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        "Referer": referer or "https://finance.naver.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_timeout()) as res:
        raw = res.read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _strip(html):
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = unescape(t)
    t = t.replace("\xa0", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def _parseNumCell(attrs, inner):
    """셀 → {text, num, isEst, neg, src}."""
    txt = _strip(inner).replace(" ", "")
    isEst = (
        "bge" in (attrs or "").lower()
        or "(E)" in txt
        or "&#40;E&#41;" in (inner or "")
    )
    neg = "cUp" in (inner or "") or "negative" in (attrs or "").lower()
    num = None
    clean = txt
    if clean in ("", "-", "—", "N/A", "n/a"):
        clean = ""
        disp = ""
    else:
        disp = txt
        if clean.startswith("(") and clean.endswith(")"):
            neg = True
            clean = clean[1:-1]
        if clean.startswith("-"):
            neg = True
        try:
            num = float(clean.replace(",", ""))
            if neg and num > 0:
                num = -num
        except ValueError:
            num = None
    if num is not None and num < 0:
        neg = True
    return {
        "text": disp if clean != "" else "",
        "num": num,
        "isEst": isEst,
        "neg": neg,
        "src": "NAVER",
    }


def _normPeriodLabel(raw):
    """2026/12(E) (IFRS연결) → 2026/12."""
    s = _strip(raw)
    s = re.sub(r"\s*\(IFRS[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\s+", "", s)
    isEst = bool(re.search(r"\(E\)", s, re.I))
    label = re.sub(r"\(E\)", "", s, flags=re.I).strip()
    # 2023.12 → 2023/12 통일
    label = label.replace(".", "/")
    m = re.match(r"^(\d{4})/(\d{1,2})", label)
    if m:
        label = f"{m.group(1)}/{int(m.group(2)):02d}"
    return label, isEst


def _pickDataTable(html):
    """ajax 응답에서 실제 주요재무 표(연간·분기 헤더 포함) 선택."""
    tables = re.findall(
        r"<table[^>]*class=['\"][^'\"]*gHead01[^'\"]*['\"][^>]*>.*?</table>",
        html,
        re.I | re.S,
    )
    for t in tables:
        if "연간" in t and ("분기" in t or "매출" in t):
            # 차트용 dummy(숫자만) 제외: title 행 필요
            if re.search(r"주요재무정보", t):
                return t
    for t in tables:
        if re.search(r"\d{4}/\d{2}", t) and "매출" in t:
            return t
    # cF1001 aspx 정적 표
    m = re.search(
        r'<table[^>]*id=["\']cTB26["\'][^>]*>.*?</table>',
        html,
        re.I | re.S,
    )
    if m:
        return m.group(0)
    m = re.search(
        r'<table[^>]*class="[^"]*tb_type1_ifrs[^"]*"[^>]*>.*?</table>',
        html,
        re.I | re.S,
    )
    if m:
        return m.group(0)
    return None


def _parseFinTable(tbl, sourceUrl, unitNote=None):
    """HTML table → yearCols/qtrCols/rows."""
    if not tbl:
        return None

    # 연간/분기 colspan
    yearCnt = 4
    qtrCnt = 4
    mY = re.search(
        r'colspan=["\'](\d+)["\'][^>]*>\s*연간',
        tbl,
        re.I,
    ) or re.search(
        r"연간[^<]{0,80}colspan=[\"'](\d+)[\"']",
        tbl,
        re.I,
    )
    mQ = re.search(
        r'colspan=["\'](\d+)["\'][^>]*>\s*분기',
        tbl,
        re.I,
    ) or re.search(
        r"분기[^<]{0,80}colspan=[\"'](\d+)[\"']",
        tbl,
        re.I,
    )
    if mY:
        yearCnt = int(mY.group(1))
    if mQ:
        qtrCnt = int(mQ.group(1))

    # 기간 헤더 th
    thead = re.search(r"<thead[^>]*>(.*?)</thead>", tbl, re.I | re.S)
    head = thead.group(1) if thead else ""
    trsHead = re.findall(r"<tr[^>]*>(.*?)</tr>", head, re.I | re.S)
    periodThs = []
    if trsHead:
        # 마지막 헤더 행 = 기간
        periodThs = re.findall(r"<th[^>]*>(.*?)</th>", trsHead[-1], re.I | re.S)
    periods = []
    for th in periodThs:
        st = _strip(th)
        if not re.search(r"\d{4}", st):
            continue
        label, isEst = _normPeriodLabel(th)
        if not label:
            continue
        periods.append({"label": label, "isEst": isEst, "raw": st})

    # 첫 데이터 행 셀 수로 열 보정
    bodyM = re.search(r"<tbody[^>]*>(.*?)</tbody>", tbl, re.I | re.S)
    body = bodyM.group(1) if bodyM else ""
    firstTds = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.I | re.S):
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.I | re.S)
        if tds:
            firstTds = len(tds)
            break
    if firstTds > 0 and yearCnt + qtrCnt != firstTds:
        if mY and not mQ:
            yearCnt = firstTds
            qtrCnt = 0
        elif firstTds >= 8 and yearCnt == 4:
            qtrCnt = firstTds - yearCnt
        else:
            # 절반씩
            yearCnt = firstTds // 2
            qtrCnt = firstTds - yearCnt

    # periods가 과다한 경우(구버전 cTB26) 데이터 열 수로 자름
    if periods and firstTds and len(periods) > firstTds:
        periods = periods[:firstTds]
    if not periods and firstTds:
        periods = [
            {"label": f"c{i+1}", "isEst": False, "raw": ""} for i in range(firstTds)
        ]

    yearCnt = max(1, min(yearCnt, len(periods) if periods else yearCnt))
    yearCols = periods[:yearCnt]
    qtrCols = periods[yearCnt : yearCnt + qtrCnt] if qtrCnt else periods[yearCnt:]

    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.I | re.S):
        th = re.search(r"<th[^>]*>(.*?)</th>", tr, re.I | re.S)
        if not th:
            continue
        name = _strip(th.group(1)).strip()
        name = re.sub(r"^\s+", "", name)
        if not name or name in ("주요재무정보",):
            continue
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.I | re.S)
        vals = [_parseNumCell(a, i) for a, i in tds]
        # isEst: 컬럼 헤더 우선
        nCols = len(yearCols) + len(qtrCols)
        while len(vals) < nCols:
            vals.append(
                {
                    "text": "",
                    "num": None,
                    "isEst": False,
                    "neg": False,
                    "src": "NAVER",
                }
            )
        vals = vals[:nCols]
        for i, col in enumerate(yearCols + qtrCols):
            if col.get("isEst"):
                vals[i]["isEst"] = True
        rows.append(
            {
                "name": name,
                "yearVals": vals[: len(yearCols)],
                "qtrVals": vals[len(yearCols) :],
            }
        )

    if not rows:
        return None

    return {
        "ok": True,
        "unitNote": unitNote or "단위 : 억원, %, 배, 주",
        "yearCols": yearCols,
        "qtrCols": qtrCols,
        "rows": rows,
        "sourceUrl": sourceUrl,
        "srcLabel": "네이버 페이 증권 주요재무정보",
    }


def _fetchEncParam(stkCd):
    """회사 개요 페이지에서 ajax encparam 추출."""
    url = (
        f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx"
        f"?cmp_cd={stkCd}"
    )
    try:
        html = _fetchHtml(url, referer="https://finance.naver.com/")
    except Exception as err:
        termLog(f"[naverFinSummary] encparam 페이지 실패 {stkCd}: {err}")
        return None, url
    m = re.search(
        r"encparam['\"\s:=]+([A-Za-z0-9%+/=]{8,})",
        html,
        re.I,
    )
    if m:
        return m.group(1), url
    return None, url


def _fetchWiseAjaxSummary(stkCd):
    """전체(A) 주요재무정보 — 이미지와 동일 골격."""
    enc, pageUrl = _fetchEncParam(stkCd)
    if not enc:
        return None
    q = urllib.parse.urlencode(
        {
            "cmp_cd": stkCd,
            "fin_typ": "0",
            "freq_typ": "A",
            "encparam": enc,
        }
    )
    ajaxUrl = (
        "https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?" + q
    )
    try:
        html = _fetchHtml(ajaxUrl, referer=pageUrl)
    except Exception as err:
        termLog(f"[naverFinSummary] ajax 실패 {stkCd}: {err}")
        return None
    tbl = _pickDataTable(html)
    parsed = _parseFinTable(
        tbl,
        sourceUrl=f"https://finance.naver.com/item/coinfo.naver?code={stkCd}",
    )
    if parsed:
        parsed["stkCd"] = stkCd
        parsed["encparam"] = True
    return parsed


def _fetchMainNaverSummary(stkCd):
    """종목 메인 축약 표 폴백."""
    url = f"https://finance.naver.com/item/main.naver?code={stkCd}"
    try:
        html = _fetchHtml(url)
    except Exception as err:
        termLog(f"[naverFinSummary] main 실패 {stkCd}: {err}")
        return {"ok": False, "errMsg": str(err), "stkCd": stkCd, "rows": []}

    tbl = _pickDataTable(html)
    unitNote = "단위 : 억원, %, 배, 주"
    um = re.search(r"단위\s*:\s*[^<\n]+", html)
    if um:
        unitNote = _strip(um.group(0))
    parsed = _parseFinTable(tbl, sourceUrl=url, unitNote=unitNote)
    if not parsed:
        return {
            "ok": False,
            "errMsg": "네이버 주요재무정보 표 없음",
            "stkCd": stkCd,
            "rows": [],
            "sourceUrl": url,
        }
    parsed["stkCd"] = stkCd
    return parsed


def fetchNaverFinSummary(stkCd):
    """
    네이버 종목 주요재무정보 표.
    반환: {
      ok, stkCd, unitNote, yearCols[], qtrCols[], rows[
        {name, yearVals[{text,num,isEst,neg,src}], qtrVals[...]}
      ], sourceUrl, errMsg?
    }
    """
    code = normStkCd(stkCd)
    if not code:
        return {"ok": False, "errMsg": "종목코드 없음", "rows": []}

    wise = _fetchWiseAjaxSummary(code)
    if wise and wise.get("ok") and wise.get("rows"):
        return wise

    return _fetchMainNaverSummary(code)
