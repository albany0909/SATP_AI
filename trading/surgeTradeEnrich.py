"""급등주 탭 칸을 채우는 조회. 주문은 내지 않는다.

ka00198 에는 거래대금이 없다. ka10001 의 trde_pre 는 등락률이라 대금이 아니다.
대금과 기관·외국인은 ka10059 한 번으로 받는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from trading.surgeTradeData import (
    loadFirstSurgeBook,
    parseKa10059Flow,
    rsi14,
    selectTabDisplay,
    turnoverEok,
)
from trading.surgeTradeInputs import tagFromTitle

# 52주(거래일 250일) 고가. 당일 봉은 빼고 그 이전만 본다.
NEW_HIGH_LOOKBACK = 250
# 당일 1봉 + 이전 250봉. RSI 15봉과 같은 ka10081 을 이 길이까지 읽는다.
NEW_HIGH_FETCH_BARS = NEW_HIGH_LOOKBACK + 1
# 최근 20거래일 안에만 첫 급등이 있으면 뉴.
NEW_SURGE_WINDOW = 20
SURGE_DAY_PCT = 10.0

# 주도주 탭과 같은 짧은 이슈 단어. URL 이 있는 제목만 확인으로 본다.
_LEADER_TAGS = (
    ("지분분쟁", "지분분쟁"),
    ("경영권분쟁", "지분분쟁"),
    ("경영권", "지분분쟁"),
    ("어닝서프라이즈", "어닝서프라이즈"),
    ("깜짝실적", "어닝서프라이즈"),
    ("깜짝 실적", "어닝서프라이즈"),
    ("관세", "관세"),
    ("전쟁", "전쟁"),
    ("코로나", "코로나"),
    ("팬데믹", "코로나"),
)


def dailyNewHigh(bars, todayPx, asof="", lookback=NEW_HIGH_LOOKBACK):
    """
    오늘 고가(또는 현재가)가 이전 lookback 거래일 고가의 최대 이상이면 True.
    봉이 더 적으면 있는 봉만 쓴다. 이전 봉이 없으면 배지를 달지 않는다.
    반환: (newHigh, lookbackUsed, priorHigh)
    """
    try:
        px = float(todayPx)
    except (TypeError, ValueError):
        return False, 0, None
    if px <= 0:
        return False, 0, None
    day = str(asof or "")[:8]
    prior = []
    for bar in bars or []:
        if isinstance(bar, dict):
            dt = str(bar.get("dt") or "")[:8]
            raw = bar.get("high", bar.get("highPric", bar.get("close", bar.get("curPrc"))))
            if day and dt and dt >= day:
                continue
        else:
            dt = ""
            raw = bar
        try:
            high = float(raw)
        except (TypeError, ValueError):
            continue
        if high <= 0:
            continue
        prior.append((dt, high))
    if any(dt for dt, _high in prior):
        prior.sort(key=lambda item: item[0])
    window = prior[-int(lookback) :] if len(prior) > int(lookback) else prior
    if not window:
        return False, 0, None
    priorHigh = max(high for _dt, high in window)
    return px + 1e-9 >= priorHigh, len(window), priorHigh


def firstNewSurge(bars, asof="", todayPct=None, bookYmd="", window=NEW_SURGE_WINDOW):
    """
    최근 window 거래일에 급등(+10%)이 있고, 그보다 앞선 봉에는 급등이 없으면 True.
    오늘 급등은 todayPct 로 센다. firstSurge.json 날짜가 그 창 안이면 급등일이다.
    봉이 적어도 같은 규칙이다.
    반환: (newSurge, firstSurgeYmd)
    """
    day = str(asof or "")[:8]
    series = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        dt = str(bar.get("dt") or "")[:8]
        raw = bar.get("close", bar.get("curPrc"))
        if len(dt) != 8:
            continue
        if day and dt > day:
            continue
        try:
            close = float(raw)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        series.append((dt, close))
    series.sort(key=lambda item: item[0])
    collapsed = []
    for dt, close in series:
        if collapsed and collapsed[-1][0] == dt:
            collapsed[-1] = (dt, close)
        else:
            collapsed.append((dt, close))
    surge = set()
    for idx in range(1, len(collapsed)):
        prev = collapsed[idx - 1][1]
        cur = collapsed[idx][1]
        if prev > 0 and (cur / prev - 1.0) * 100.0 + 1e-9 >= SURGE_DAY_PCT:
            surge.add(collapsed[idx][0])
    dates = [dt for dt, _close in collapsed]
    if day and day not in dates:
        dates.append(day)
    try:
        if day and float(todayPct) + 1e-9 >= SURGE_DAY_PCT:
            surge.add(day)
            if day not in dates:
                dates.append(day)
    except (TypeError, ValueError):
        pass
    dates = sorted(set(dates))
    recent = dates[-int(window) :]
    earlier = dates[: len(dates) - len(recent)]
    recentSet = set(recent)
    book = str(bookYmd or "")[:8]
    if len(book) == 8:
        if book in recentSet:
            surge.add(book)
        elif not recent or book < recent[0]:
            surge.add(book)
            if book not in earlier:
                earlier.append(book)
    earlierSet = set(earlier)
    inWindow = any(item in recentSet for item in surge)
    inEarlier = any(item in earlierSet for item in surge)
    found = sorted(item for item in surge if item)
    return bool(inWindow and not inEarlier), (found[0] if found else "")


def marketLabel(name):
    text = str(name or "").strip()
    upper = text.upper()
    if "코스닥" in text or upper in ("KOSDAQ", "KQ"):
        return "코스닥"
    if "코스피" in text or "거래소" in text or upper in ("KOSPI", "KP"):
        return "코스피"
    return ""


def newsFailSummary(count):
    """한 번의 조회에서 실패한 종목 수만 적는다. 종목코드는 넣지 않는다."""
    return f"[surgeNews] 헤드라인 실패 {int(count)}종목"


def _splitNews(raw):
    if isinstance(raw, dict):
        return list(raw.get("items") or []), raw.get("ok") is False
    if isinstance(raw, list):
        return raw, False
    return [], True


def issueFromHeadlines(items):
    """제목과 URL 이 있을 때만 태그. 없으면 빈 칸과 이유를 남긴다."""
    sawTitle = False
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("reportNm") or "")
        url = str(item.get("url") or item.get("newsUrl") or item.get("sourceUrl") or "")
        if title:
            sawTitle = True
        if not url.startswith("http"):
            continue
        tag = tagFromTitle(title)
        if not tag:
            low = title.lower()
            for key, lab in _LEADER_TAGS:
                if key.lower() in low:
                    tag = lab
                    break
        if tag:
            return {
                "tag": tag,
                "confirmed": True,
                "sourceUrl": url,
                "firstSurgeDate": str(item.get("publishedAt") or "")[:8],
                "note": "headline+url",
            }
    if sawTitle:
        return {"tag": "", "confirmed": False, "note": "제목에 확인 태그가 없다"}
    return {"tag": "", "confirmed": False, "note": "확인된 제목·URL 이 없다"}


def _cachePath(directory, ymd):
    return Path(directory) / f"columns_{str(ymd)[:8]}.json"


def loadColumnCache(directory, ymd):
    path = _cachePath(directory, ymd)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def saveColumnCache(directory, ymd, cache):
    path = _cachePath(directory, ymd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache


def displayExtras(directory, asof):
    """화면이 읽는 값. 네트워크를 열지 않는다."""
    book = loadFirstSurgeBook(directory)
    cache = loadColumnCache(directory, asof)
    byCode = {}
    for code, row in cache.items():
        if not isinstance(row, dict):
            continue
        issue = {
            "tag": row.get("issues") or "",
            "confirmed": row.get("issueConfirmed") is True,
            "sourceUrl": row.get("issueUrl") or "",
            "firstSurgeDate": row.get("issueDate") or "",
            "note": row.get("issueNote") or "",
        }
        byCode[str(code)] = {
            "market": row.get("market") or "",
            "rsi14": row.get("rsi14"),
            "issue": issue,
            "flow": {
                "turnoverEok": row.get("turnoverEok"),
                "turnoverNote": row.get("turnoverNote") or "",
                "orgnNetMillionKrw": row.get("orgnNetMillionKrw"),
                "frgnNetMillionKrw": row.get("frgnNetMillionKrw"),
                "unit": row.get("flowUnit") or "",
                "asof": row.get("flowAsOf") or "",
                "flowStatus": row.get("flowStatus") or "",
            },
            "dataState": row.get("dataState") or "PARTIAL",
            "newHigh": row.get("newHigh") is True,
            "newHighLookback": int(row.get("newHighLookback") or 0),
            "newSurge": row.get("newSurge") is True,
            "firstSurgeYmd": row.get("firstSurgeYmd") or "",
        }
    return {"firstSurge": book, "byCode": byCode}


def _parseWhen(text):
    raw = str(text or "").replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _need(row, now, field, minutes):
    if not isinstance(row, dict) or row.get(field + "At") in (None, ""):
        return True
    seen = _parseWhen(row.get(field + "At"))
    if seen is None:
        return True
    if minutes is None:
        return seen.strftime("%Y%m%d") != now.strftime("%Y%m%d")
    return now - seen >= timedelta(minutes=minutes)


def enrichDisplayed(now, store, fetchers=None):
    """
    화면에 나온 종목만 캐시에 채운다.
    fetchers 가 없으면 모의 조회로 받는다. 실패해도 칸만 비운다.
    반환 orders 는 항상 0.
    """
    picked = selectTabDisplay(store, now)
    snap = picked.get("snap") or {}
    asof = picked.get("asof") or now.strftime("%Y%m%d")
    codes = []
    for row in snap.get("rows") or []:
        code = str((row or {}).get("stkCd") or "")
        if len(code) == 6 and code.isdigit() and code not in codes:
            from trading.surgeTradeData import ka00198ChangePct, TAB_MIN_DAILY_PCT

            change = ka00198ChangePct(row)
            if change is not None and change + 1e-9 >= TAB_MIN_DAILY_PCT:
                codes.append(code)
    from trading.surgeTradeData import ka00198ChangePct

    prices = {}
    pcts = {}
    for raw in snap.get("rows") or []:
        code = str((raw or {}).get("stkCd") or "")
        if code:
            prices[code] = (raw or {}).get("price")
            pcts[code] = ka00198ChangePct(raw)
    book = loadFirstSurgeBook(store.dir)
    cache = loadColumnCache(store.dir, asof)
    calls = {"ka10059": 0, "ka10081": 0, "ka10100": 0, "news": 0}
    newsFailures = 0
    for code in codes:
        row = dict(cache.get(code) or {})
        try:
            bookYmd = ""
            booked = book.get(code) if isinstance(book, dict) else None
            if isinstance(booked, dict):
                bookYmd = booked.get("ymd") or ""
            if _fillCode(
                row,
                code,
                asof,
                now,
                fetchers,
                calls,
                prices.get(code),
                pcts.get(code),
                bookYmd,
            ):
                newsFailures += 1
        except Exception:
            row["dataState"] = "PARTIAL"
            newsFailures += 1
        filled = any(
            row.get(key) not in (None, "")
            for key in ("market", "turnoverEok", "rsi14", "issues")
        )
        row["dataState"] = "OK" if filled else "PARTIAL"
        cache[code] = row
    saveColumnCache(store.dir, asof, cache)
    if newsFailures:
        try:
            from utils.termLog import termLog

            termLog(newsFailSummary(newsFailures))
        except Exception:
            pass
    return {
        "orders": 0,
        "codes": len(codes),
        "calls": calls,
        "asof": asof,
        "newsFailures": newsFailures,
    }


def _fillCode(row, code, asof, now, fetchers, calls, price=None, todayPct=None, bookYmd=""):
    getters = fetchers if isinstance(fetchers, dict) else {}
    if _need(row, now, "market", None):
        market = _call(getters, "market", code, calls, "ka10100")
        label = marketLabel(market if isinstance(market, str) else (market or {}).get("marketName"))
        if label:
            row["market"] = label
        row["marketAt"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    if _need(row, now, "flow", 15):
        body = _call(getters, "flow", code, calls, "ka10059")
        parsed = parseKa10059Flow(
            body if isinstance(body, dict) else {},
            {"amtQtyTp": "1", "trdeTp": "0"},
        )
        eok, note = turnoverEok(
            (body or {}).get("acc_trde_prica") if isinstance(body, dict) else None,
            "백만원",
        )
        if eok is not None:
            row["turnoverEok"] = round(float(eok), 2)
            row["turnoverNote"] = note
        if parsed.get("state") == "OK":
            row["orgnNetMillionKrw"] = parsed.get("orgnNetMillionKrw")
            row["frgnNetMillionKrw"] = parsed.get("frgnNetMillionKrw")
            row["flowUnit"] = parsed.get("unit") or ""
            row["flowAsOf"] = parsed.get("asof") or asof
            row["flowStatus"] = parsed.get("flowStatus") or ""
        row["flowAt"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    if _need(row, now, "rsi", None) or "dayCloses" not in row:
        bars = _call(getters, "bars", code, calls, "ka10081")
        barList = bars if isinstance(bars, list) else []
        closes = _closesOf(barList)
        value = rsi14(closes, adjusted=True)
        if value is not None:
            row["rsi14"] = round(float(value), 2)
        flag, used, prior = dailyNewHigh(barList, price, asof)
        row["newHigh"] = flag
        row["newHighLookback"] = used
        if prior is not None:
            row["priorHigh"] = prior
        row["dayCloses"] = _dayCloses(barList)
        row["rsiAt"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    elif row.get("priorHigh") not in (None, ""):
        flag, _used, _prior = dailyNewHigh(
            [{"dt": "19000101", "high": row.get("priorHigh")}],
            price,
            asof,
        )
        row["newHigh"] = flag
    newsFailed = False
    if _need(row, now, "issue", None):
        raw = _call(getters, "news", code, calls, "news")
        items, newsFailed = _splitNews(raw)
        found = issueFromHeadlines(items)
        row["issues"] = found.get("tag") or ""
        row["issueConfirmed"] = found.get("confirmed") is True
        row["issueUrl"] = found.get("sourceUrl") or ""
        row["issueDate"] = found.get("firstSurgeDate") or ""
        row["issueNote"] = found.get("note") or ""
        row["issueAt"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    surgeBars = [{"dt": dt, "close": close} for dt, close in (row.get("dayCloses") or [])]
    fresh, found = firstNewSurge(surgeBars, asof, todayPct, bookYmd)
    row["newSurge"] = fresh
    row["firstSurgeYmd"] = found
    return newsFailed


def _dayCloses(bars):
    out = []
    if not isinstance(bars, list):
        return out
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        dt = str(bar.get("dt") or "")[:8]
        raw = bar.get("close", bar.get("curPrc"))
        if len(dt) != 8:
            continue
        try:
            close = float(raw)
        except (TypeError, ValueError):
            continue
        if close > 0:
            out.append([dt, close])
    return out


def _closesOf(bars):
    if not isinstance(bars, list):
        return []
    out = []
    for bar in bars:
        raw = bar.get("close", bar.get("curPrc")) if isinstance(bar, dict) else bar
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            continue
    return out


def _call(getters, name, code, calls, counter):
    fn = getters.get(name) if getters else None
    if fn is None:
        fn = _liveFetch(name)
    calls[counter] = calls.get(counter, 0) + 1
    try:
        return fn(code)
    except Exception:
        return None


def _liveFetch(name):
    def _run(code):
        from trading.surgeTradeLive import ensurePaperReadSession
        from config import runtimeState
        from utils.trRateLimit import acquireTrSlot

        got = ensurePaperReadSession()
        if not got.get("ok"):
            return None
        token = runtimeState.getToken("paper")
        host = (got.get("session") or {}).get("host")
        if not token or not host:
            return None
        if name == "flow":
            acquireTrSlot("ka10059", "paper")
            from api.getInvSrOrgn import _parseBody, getInvSrOrgn

            res = getInvSrOrgn(
                host,
                token,
                code,
                datetime.now().strftime("%Y%m%d"),
                amtQtyTp="1",
                trdeTp="0",
                tokenType=runtimeState.paperTokenType or "bearer",
            )
            ok, body = _parseBody(res)
            if not ok:
                return None
            rows = body.get("stk_invsr_orgn") or []
            return rows[0] if rows else None
        if name == "bars":
            acquireTrSlot("ka10081", "paper")
            from api.getDayChart import fetchDayBars

            gotBars = fetchDayBars(
                host,
                token,
                code,
                datetime.now().strftime("%Y%m%d"),
                updStkpcTp="1",
                minBars=NEW_HIGH_FETCH_BARS,
                tokenType=runtimeState.paperTokenType or "bearer",
                isPaper=True,
            )
            if not gotBars.get("ok"):
                return None
            bars = list(gotBars.get("bars") or [])
            bars.reverse()
            return bars
        if name == "market":
            acquireTrSlot("ka10100", "paper")
            from api.getStkInfoByCd import fetchStkInfoByCd

            info = fetchStkInfoByCd(
                host, token, code, tokenType=runtimeState.paperTokenType or "bearer"
            )
            if not info.get("ok"):
                return ""
            return str((info.get("body") or {}).get("marketName") or "")
        if name == "news":
            from research.naverStkNews import fetchMobileStkHeadlines

            return fetchMobileStkHeadlines(code)
        return None

    return _run
