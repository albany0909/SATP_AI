"""SRG01 가 매수 전에 요구하는 구성·이슈·시세.

하나라도 없거나 오래되면 신규·추가는 하지 않는다.
보유 청산은 이 모듈이 막지 않고, 시세가 있을 때만 엔진이 판단한다.
네트워크는 주입된 transport 가 있을 때만 연다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from trading.surgeTradeData import (
    FLOW_STATUS_PROVISIONAL,
    membershipOk,
    parseDartList,
    parseKa10059Flow,
    turnoverEok,
)

# ka20002 명세: inds_cd 201 = KOSPI200, mrkt_tp 2 = 코스피200.
KOSPI200_SPEC = {
    "index": "KOSPI200",
    "api": "ka20002",
    "mrktTp": "2",
    "indsCd": "201",
    "stexTp": "1",
    "limit": "연속조회가 남아 있으면 전수라고 보지 않는다. 잘린 명부는 매수에 쓰지 않는다.",
}
# 같은 명세의 업종코드 목록에 KOSDAQ150 은 없다.
KOSDAQ150_GAP = (
    "ka20002 inds_cd 설명은 001/002/003/004/101/201/701 만 적는다. "
    "KOSDAQ150 코드는 이 명세에 없다. 코드를 추정해 종목을 만들지 않는다."
)

_TAG_RULES = (
    ("지분분쟁", ("지분분쟁", "경영권분쟁", "경영권 분쟁")),
    ("어닝서프라이즈", ("어닝서프라이즈", "어닝 서프라이즈", "실적서프라이즈")),
    ("전쟁", ("전쟁", "무력충돌")),
    ("코로나", ("코로나", "코로나19")),
    ("관세", ("관세",)),
)

_QUOTE_CODES = set()


def rebalanceAnchor(ymd):
    """오늘 이전에 시작된 6월·12월 리밸런스 기준일 YYYYMMDD."""
    text = str(ymd or "")[:8]
    year = int(text[:4])
    month = int(text[4:6])
    if month >= 12:
        return f"{year}1201"
    if month >= 6:
        return f"{year}0601"
    return f"{year - 1}1201"


def membershipRefreshDue(book, ymd):
    """명부가 없거나 이번 6월·12월 기준보다 오래되면 True."""
    if not isinstance(book, dict) or not book.get("indexes"):
        return True
    anchor = rebalanceAnchor(ymd)
    fetched = str(book.get("asOf") or book.get("fetchedAt") or "")[:8]
    return (not fetched) or fetched < anchor


def emptyMembership(as_of=""):
    return {
        "source": "ka20002",
        "asOf": str(as_of or "")[:8],
        "indexes": {
            "KOSPI200": {
                **KOSPI200_SPEC,
                "complete": False,
                "truncated": None,
                "codes": [],
                "effectiveFrom": "",
                "effectiveTo": "",
                "reason": "NOT_FETCHED",
            },
            "KOSDAQ150": {
                "index": "KOSDAQ150",
                "api": None,
                "complete": False,
                "truncated": None,
                "codes": [],
                "reason": KOSDAQ150_GAP,
            },
        },
    }


def membershipFromKa20002(body, as_of, effectiveFrom, effectiveTo, truncated=False):
    """ka20002 응답을 KOSPI200 명부로 만든다. 종목을 지어내지 않는다."""
    book = emptyMembership(as_of)
    rows = []
    if isinstance(body, dict):
        rows = body.get("inds_stkpc") or body.get("rows") or []
    codes = []
    for row in rows:
        if isinstance(row, dict):
            raw = str(row.get("stk_cd") or row.get("stkCd") or "")
        else:
            raw = str(row or "")
        code = raw.split("_")[0].strip()
        if len(code) == 6 and code.isdigit() and code not in codes:
            codes.append(code)
    slot = book["indexes"]["KOSPI200"]
    slot["codes"] = codes
    slot["truncated"] = bool(truncated)
    slot["complete"] = bool(codes) and not truncated
    slot["effectiveFrom"] = str(effectiveFrom or "")[:8]
    slot["effectiveTo"] = str(effectiveTo or "99991231")[:8]
    slot["asOf"] = str(as_of or "")[:8]
    slot["reason"] = "" if slot["complete"] else "INCOMPLETE"
    return book


def membershipLookupBook(book):
    """membershipOk 가 읽는 코드→기간 맵. 불완전한 지수는 넣지 않는다."""
    out = {}
    indexes = (book or {}).get("indexes") or {}
    for name, slot in indexes.items():
        if not isinstance(slot, dict) or slot.get("complete") is not True:
            continue
        start = str(slot.get("effectiveFrom") or "")
        end = str(slot.get("effectiveTo") or "")
        if len(start) != 8 or len(end) != 8:
            continue
        for code in slot.get("codes") or []:
            out[str(code)] = {
                "index": name,
                "effectiveFrom": start,
                "effectiveTo": end,
            }
    return out


def saveMembership(path, book):
    filePath = Path(path)
    filePath.parent.mkdir(parents=True, exist_ok=True)
    filePath.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(filePath)


def loadMembership(path):
    filePath = Path(path)
    if not filePath.is_file():
        return emptyMembership()
    try:
        data = json.loads(filePath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return emptyMembership()
    return data if isinstance(data, dict) else emptyMembership()


def tagFromTitle(title):
    """제목에 적힌 문구만 태그로 쓴다. 없으면 빈 문자열."""
    text = str(title or "")
    for tag, words in _TAG_RULES:
        if any(word in text for word in words):
            return tag
    return ""


def issueFromDartBody(body, stkCd):
    """Open DART list.json. 키가 없거나 태그가 없으면 확인 이슈가 아니다."""
    rows = parseDartList(body, stkCd)
    out = []
    for row in rows:
        tag = tagFromTitle(row.get("title"))
        item = dict(row)
        item["tag"] = tag
        item["confirmed"] = bool(tag and row.get("sourceUrl") and row.get("publishedAt"))
        item["eventId"] = row.get("rceptNo") or ""
        item["eventDate"] = str(row.get("publishedAt") or "")[:8]
        out.append(item)
    return out


def issueFromNews(item):
    """기존 뉴스 행(제목·URL·시각·종목). 종목에 연결된 제목만 본다."""
    if not isinstance(item, dict):
        return None
    code = str(item.get("stkCd") or item.get("STK_CD") or "").strip()
    title = str(item.get("title") or item.get("TITLE") or "")
    url = str(item.get("url") or item.get("newsUrl") or item.get("NEWS_URL") or "")
    published = str(item.get("publishedAt") or item.get("PUB_DT") or "")
    tag = tagFromTitle(title)
    if len(code) != 6 or not tag or not url.startswith("http") or len(published) < 8:
        return {
            "stkCd": code,
            "tag": tag,
            "confirmed": False,
            "title": title,
            "sourceUrl": url,
            "publishedAt": published,
            "provenance": "news",
        }
    return {
        "stkCd": code,
        "tag": tag,
        "confirmed": True,
        "eventId": str(item.get("newsId") or item.get("NEWS_ID") or ""),
        "eventDate": published[:8],
        "title": title,
        "sourceUrl": url,
        "publishedAt": published[:8],
        "provenance": "news",
    }


def firstSurgeRecord(records, stkCd, tradeYmd, dailyPct):
    """
    확인된 이슈의 최초 급등일만 매수 후보.
    같은 이벤트가 더 이른 날 있으면 오늘은 최초가 아니다.
    """
    try:
        daily = float(dailyPct)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "DAILY_MISSING"}
    if daily < 10:
        return {"ok": False, "reason": "DAILY_BELOW_10"}
    code = str(stkCd or "")
    day = str(tradeYmd or "")[:8]
    confirmed = [
        r
        for r in records or []
        if isinstance(r, dict)
        and str(r.get("stkCd") or "") == code
        and r.get("confirmed") is True
        and r.get("tag")
        and r.get("sourceUrl")
        and str(r.get("eventDate") or "")[:8]
    ]
    if not confirmed:
        return {"ok": False, "reason": "ISSUE_UNCONFIRMED"}
    earliest = min(str(r.get("eventDate"))[:8] for r in confirmed)
    if earliest != day:
        return {"ok": False, "reason": "NOT_FIRST_SURGE_DAY", "firstYmd": earliest}
    today = [r for r in confirmed if str(r.get("eventDate"))[:8] == day]
    row = today[0]
    return {
        "ok": True,
        "reason": "",
        "eventId": row.get("eventId") or "",
        "eventDate": day,
        "tag": row.get("tag"),
        "sourceUrl": row.get("sourceUrl"),
        "publishedAt": row.get("publishedAt"),
    }


def quoteFromSources(code, now, wsInfo=None, rest=None, maxAgeSec=20):
    """
    0B 가 신선하면 그 가격. 아니면 ka10001 현재가.
    둘 다 없거나 오래되면 stale. 가격을 만들지 않는다.
    """
    nowDt = now if isinstance(now, datetime) else datetime.strptime(str(now)[:14], "%Y%m%d%H%M%S")
    ws = wsInfo if isinstance(wsInfo, dict) else {}
    if str(ws.get("src") or "") == "0B":
        built = _quoteFromPx(ws.get("curPrc"), ws.get("asof") or ws.get("tm"), nowDt, maxAgeSec, "0B")
        if built.get("ok"):
            built["open"] = ws.get("open")
            built["prevClose"] = ws.get("prevClose")
            return built
    restRow = rest if isinstance(rest, dict) else {}
    built = _quoteFromPx(
        restRow.get("cur_prc") or restRow.get("curPrc"),
        restRow.get("asof"),
        nowDt,
        maxAgeSec,
        "ka10001",
    )
    if built.get("ok"):
        built["open"] = restRow.get("open_pric") or restRow.get("open")
        built["prevClose"] = restRow.get("base_pric") or restRow.get("prevClose")
    return built


def _quoteFromPx(px, asof, nowDt, maxAgeSec, source):
    try:
        price = abs(int(float(str(px).replace(",", "").replace("+", "").replace("--", "-"))))
    except (TypeError, ValueError):
        return {"ok": False, "stale": True, "reason": "NO_PRICE", "source": source}
    if price <= 0:
        return {"ok": False, "stale": True, "reason": "NO_PRICE", "source": source}
    stamp = _coerceAsOf(asof, nowDt)
    if stamp is None:
        return {"ok": False, "stale": True, "reason": "QUOTE_ASOF_MISSING", "source": source, "px": price}
    age = (nowDt - stamp).total_seconds()
    if age < 0 or age > float(maxAgeSec):
        return {
            "ok": False,
            "stale": True,
            "reason": "STALE_QUOTE",
            "source": source,
            "px": price,
            "asof": stamp,
        }
    return {"ok": True, "stale": False, "reason": "", "source": source, "px": price, "asof": stamp}


def _coerceAsOf(asof, nowDt):
    if isinstance(asof, datetime):
        return asof
    text = str(asof or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        return datetime.strptime(text[:14], "%Y%m%d%H%M%S")
    if len(text) == 6 and text.isdigit():
        return nowDt.replace(
            hour=int(text[0:2]), minute=int(text[2:4]), second=int(text[4:6]), microsecond=0
        )
    return None


def registerSrgQuoteCodes(codes):
    """보유·후보만 0B 대상에 올린다. 설정 파일은 쓰지 않는다."""
    for raw in codes or []:
        code = str(raw or "").strip()
        if len(code) == 6 and code.isdigit():
            _QUOTE_CODES.add(code)
    return sorted(_QUOTE_CODES)


def srgQuoteCodes():
    return sorted(_QUOTE_CODES)


def clearSrgQuoteCodes():
    _QUOTE_CODES.clear()


def entryGate(code, tradeYmd, dailyPct, book, records, quoteOk):
    """매수 입력. 하나라도 실패하면 ok False."""
    reasons = []
    lookup = membershipLookupBook(book)
    member, index = membershipOk(code, tradeYmd, lookup)
    if not member:
        reasons.append("MEMBERSHIP")
    issue = firstSurgeRecord(records, code, tradeYmd, dailyPct)
    if not issue.get("ok"):
        reasons.append(issue.get("reason") or "ISSUE")
    if quoteOk is not True:
        reasons.append("QUOTE")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "index": index if member else "",
        "issue": issue,
    }


def readinessChecks(env=None, membership=None, issueCount=0, deskMode="paper", quoteSeen=False):
    """주문 없이 입력 상태만 적는다. 비밀값은 출력하지 않는다."""
    env = env or {}
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    book = membership if isinstance(membership, dict) else emptyMembership()
    kospi = (book.get("indexes") or {}).get("KOSPI200") or {}
    add(
        "kospi200Membership",
        kospi.get("complete") is True and bool(kospi.get("codes")),
        "ka20002 mrkt_tp=2 inds_cd=201"
        if kospi.get("complete")
        else (kospi.get("reason") or "명부 없음"),
    )
    add("kosdaq150Membership", False, KOSDAQ150_GAP)
    dartSet = bool(str(env.get("DART_API_KEY") or "").strip())
    add("dartApiKey", dartSet, "present" if dartSet else "absent")
    add(
        "issueRecords",
        int(issueCount or 0) > 0,
        f"confirmedRecords={int(issueCount or 0)}",
    )
    add(
        "quoteFeed",
        bool(quoteSeen),
        "0B or fresh ka10001" if quoteSeen else "no fresh quote observed",
    )
    add("flowUnit", True, "ka10059 acc_trde_prica 백만원; flow provisional before 20:00")
    add("deskTradeModePaper", str(deskMode or "") == "paper", str(deskMode or ""))
    add("srg01TradingEnabled", False, "spec stays disabled until a separate decision")
    required = [c for c in checks if c["name"] != "srg01TradingEnabled"]
    return {"ok": all(c["status"] == "PASS" for c in required), "checks": checks}


def flowColumn(row, requested, capturedHm):
    """수급 칸. 20:00 전에는 확정으로 적지 않는다."""
    parsed = parseKa10059Flow(row, requested)
    eok, note = turnoverEok(
        (row or {}).get("acc_trde_prica") if isinstance(row, dict) else None,
        "백만원" if str((requested or {}).get("amtQtyTp")) == "1" else "",
    )
    parsed["turnoverEok"] = eok
    parsed["turnoverNote"] = note
    parsed["flowStatus"] = FLOW_STATUS_PROVISIONAL
    hm = str(capturedHm or "")
    if hm and hm < "200000":
        parsed["flowStatusNote"] = "16:00 포함, 20:00 전 값은 잠정. 키움이 확정을 보장하지 않는다."
    return parsed
