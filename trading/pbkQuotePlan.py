"""PBK01 장중 시세 구독 계획. 주문과 매매 조건은 바꾸지 않는다.

키움 REST 실시간 REG (realtime/wsClient.py, trading/watchRtMonitor.py):
- 이 프로세스의 0B 그룹은 grp_no=2, 종목 180, item 200.
- 한 번에 보내는 묶음은 40 item.
- refresh "1" 은 그 그룹의 기존 등록을 지우고 이번 목록만 남긴다.
- refresh "0" 은 기존 등록을 유지한 채 더한다.
추가 종목은 항상 refresh "0" 이다. 그룹 전체를 다시 깔 때만 첫 묶음이 "1" 이고 나머지는 "0" 이다.
"""

from __future__ import annotations

MAX_STK = 180
MAX_ITEM = 200
REG_CHUNK = 40
POLL_PER_CYCLE = 4
STALE_MIN = 3
HOLD_POLL_SEC = 60


def maDistancePct(price, ma):
    """현재가가 이평보다 얼마나 위·아래인지. 퍼센트. 없으면 None."""
    try:
        px = float(str(price).replace(",", ""))
        line = float(ma)
    except (TypeError, ValueError):
        return None
    if px <= 0 or line <= 0:
        return None
    return (px - line) / line * 100.0


def rankForSubscription(rows):
    """보유가 먼저, 그 다음 MA5 에 가까운 후보. 현재가가 없으면 맨 뒤."""
    holds = []
    known = []
    unknown = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "").strip()
        if not code:
            continue
        if row.get("holding"):
            holds.append(row)
            continue
        dist = maDistancePct(row.get("price"), row.get("ma5"))
        if dist is None:
            unknown.append(row)
        else:
            known.append((abs(dist), code, row))
    holds.sort(key=lambda row: str(row.get("stkCd") or ""))
    known.sort(key=lambda item: (item[0], item[1]))
    unknown.sort(key=lambda row: str(row.get("stkCd") or ""))
    return holds + [item[2] for item in known] + unknown


def planPbkQuotes(
    rows,
    keptCodes,
    itemSize,
    usedItems=0,
    usedStocks=0,
    maxItem=MAX_ITEM,
    maxStk=MAX_STK,
):
    """한도 안에 넣을 종목과 REST 폴링으로 돌릴 종목.

    keptCodes 는 관심·차트처럼 이미 구독 중이라 빼면 안 되는 종목이다.
    그 종목의 item 수는 usedItems 에 이미 들어 있다.
    """
    kept = []
    seenKept = set()
    for raw in keptCodes or []:
        code = str(raw or "").strip()
        if code and code not in seenKept:
            seenKept.add(code)
            kept.append(code)
    items = int(usedItems or 0)
    stocks = int(usedStocks or 0)
    subscribe = list(kept)
    poll = []
    for row in rankForSubscription(rows):
        code = str(row.get("stkCd") or "").strip()
        if not code or code in seenKept:
            continue
        try:
            need = int(itemSize(code) or 1)
        except (TypeError, ValueError):
            need = 1
        if need < 1:
            need = 1
        if stocks + 1 > int(maxStk) or items + need > int(maxItem):
            poll.append(code)
            continue
        subscribe.append(code)
        seenKept.add(code)
        stocks += 1
        items += need
    return {
        "subscribe": subscribe,
        "poll": poll,
        "kept": kept,
        "items": items,
        "stocks": stocks,
    }


def regChunks(items, chunk=REG_CHUNK, replace=False):
    """REG 묶음. replace 가 아니면 전부 refresh 0 이라 기존 구독이 남지 않고 지워지지 않는다."""
    out = []
    size = int(chunk or REG_CHUNK)
    if size < 1:
        size = REG_CHUNK
    flat = [item for item in (items or []) if item]
    for index in range(0, len(flat), size):
        refresh = "1" if replace and index == 0 else "0"
        out.append({"items": flat[index : index + size], "refresh": refresh})
    return out


def takePollBatch(codes, cursor, limit=POLL_PER_CYCLE):
    """폴링 대상만 순환한다. 비면 빈 목록."""
    rows = [str(code) for code in (codes or []) if str(code or "").strip()]
    if not rows or int(limit or 0) <= 0:
        return [], int(cursor or 0)
    start = int(cursor or 0) % len(rows)
    out = []
    pos = start
    for _ in range(min(int(limit), len(rows))):
        out.append(rows[pos])
        pos = (pos + 1) % len(rows)
    return out, pos


def shouldBeat(lastAt, now, everySec=60):
    if lastAt is None:
        return True
    try:
        return float(now) - float(lastAt) >= float(everySec)
    except (TypeError, ValueError):
        return True


def _hhmmss(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    digits = digits.zfill(6)[:6]
    hour, minute, sec = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    if hour > 23 or minute > 59 or sec > 59:
        return None
    return hour * 3600 + minute * 60 + sec


def quoteAgeSec(info, now):
    """마지막 틱이 얼마나 오래됐는지. 전일은 오늘 틱보다 크게, 시각이 없으면 None."""
    if now is None:
        return None
    tick = _hhmmss((info or {}).get("tm"))
    today = now.strftime("%Y%m%d")
    ymd = str((info or {}).get("tickYmd") or "")
    if ymd and ymd != today:
        return 200000 + (86400 - (tick or 0))
    if tick is None:
        return None
    nowSec = now.hour * 3600 + now.minute * 60 + now.second
    if tick > nowSec:
        return 200000 + (86400 - tick)
    return nowSec - tick


def quoteIsFresh(info, now, staleSec=None):
    """오늘 0B·폴링이고 마지막 틱이 기준 분 안이면 신선하다."""
    if staleSec is None:
        staleSec = STALE_MIN * 60
    src = str((info or {}).get("src") or "")
    if src not in ("0B", "poll") or not (info or {}).get("curPrc"):
        return False
    ymd = str((info or {}).get("tickYmd") or "")
    today = now.strftime("%Y%m%d") if now is not None else ""
    if ymd and today and ymd != today:
        return False
    if not ymd:
        return False
    age = quoteAgeSec(info, now)
    if age is None:
        return False
    return age <= float(staleSec)


def classifyQuote(info, now, registered=True, staleSec=None):
    """캐시가 비거나 낡은 이유. 신선하면 빈 문자열."""
    if not registered:
        return "미등록"
    if not info or not info.get("curPrc"):
        return "없음"
    src = str(info.get("src") or "")
    if src not in ("0B", "poll"):
        return "전일"
    ymd = str(info.get("tickYmd") or "")
    today = now.strftime("%Y%m%d") if now is not None else ""
    if not ymd or (today and ymd != today):
        return "전일"
    if quoteIsFresh(info, now, staleSec):
        return ""
    return "오래됨"


def stalePollEntries(rows, ticks, now, registered=None, staleSec=None):
    """구독 여부와 상관없이 낡거나 없는 종목. 없는 것이 먼저, 그 다음 오래된 순."""
    reg = None if registered is None else {str(code) for code in registered}
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "").strip()
        if not code:
            continue
        info = (ticks or {}).get(code) or {}
        onBook = True if reg is None else code in reg
        reason = classifyQuote(info, now, registered=onBook, staleSec=staleSec)
        if not reason:
            continue
        age = quoteAgeSec(info, now)
        out.append({"stkCd": code, "reason": reason, "ageSec": age})

    def _key(item):
        age = item.get("ageSec")
        missing = 0 if age is None else 1
        return (missing, -(age or 0), item["stkCd"])

    out.sort(key=_key)
    return out


def takeOldest(entries, limit):
    """오래된 순으로 limit 종만."""
    if int(limit or 0) <= 0:
        return []
    return [item["stkCd"] for item in (entries or [])[: int(limit)]]


def holdPollForce(now):
    """15:13~15:15 손절 직전에는 보유를 전부 다시 받는다."""
    if now is None:
        return False
    hm = now.strftime("%H%M%S")
    return "151300" <= hm <= "151559"


def _holdNeedsPoll(info, now, force=False):
    """0B 가 60초 안이면 폴링하지 않는다. 그 외 보유는 60초를 넘기면 다시 받는다."""
    if force:
        return True
    src = str((info or {}).get("src") or "")
    age = quoteAgeSec(info, now)
    if src == "0B" and age is not None and age <= HOLD_POLL_SEC:
        return False
    if not info or not info.get("curPrc"):
        return True
    if age is None:
        return True
    return age > HOLD_POLL_SEC


def pollBatch(rows, ticks, now, registered=None, limit=POLL_PER_CYCLE, staleSec=None, priorityCodes=None):
    """보유는 60초를 넘기면 먼저 받고, 남는 칸만 후보에 쓴다.

    15:13~15:15 에는 보유를 시세가 있어도 모두 넣는다.
    """
    force = holdPollForce(now)
    holds = []
    holding = set()
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("holding"):
            continue
        code = str(row.get("stkCd") or "").strip()
        if not code or code in holding:
            continue
        holding.add(code)
        info = (ticks or {}).get(code) or {}
        if _holdNeedsPoll(info, now, force=force):
            holds.append((quoteAgeSec(info, now), code))
    holds.sort(key=lambda item: (-1 if item[0] is None else -item[0], item[1]))
    holdCodes = [code for _age, code in holds]
    stale = stalePollEntries(rows, ticks, now, registered=registered, staleSec=staleSec)
    rest = [item["stkCd"] for item in stale if item["stkCd"] not in holding]
    pri = []
    seen = set(holdCodes)
    for raw in priorityCodes or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        info = (ticks or {}).get(code) or {}
        if quoteIsFresh(info, now, staleSec):
            continue
        seen.add(code)
        pri.append(code)
    priTake = pri[: max(0, int(limit or 0))]
    room = max(0, int(limit or 0) - len(holdCodes) - len(priTake))
    restTake = []
    for code in rest:
        if code in seen or len(restTake) >= room:
            continue
        restTake.append(code)
    return holdCodes + priTake + restTake


def _px(raw):
    """부호·콤마를 뗀 양수 가격. 없으면 None."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        num = abs(float(str(raw).replace(",", "").lstrip("+")))
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _pct(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ymd(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def krxTmInSession(tm):
    """정규장 시각(09:00~15:30). 전일 15:30 도 시각만 보면 참이다. 날짜는 따로 본다."""
    sec = _hhmmss(tm)
    if sec is None:
        return False
    return 9 * 3600 <= sec <= 15 * 3600 + 30 * 60


def krxQuoteDay(info):
    """krxPrc 가 어느 날 값인지. 폴링이 현재가만 고친 전일 종가는 dayCloseDt."""
    info = info or {}
    if _px(info.get("krxPrc")) is None:
        return ""
    explicit = _ymd(info.get("krxYmd"))
    if explicit:
        return explicit
    closeDt = _ymd(info.get("dayCloseDt"))
    tick = _ymd(info.get("tickYmd"))
    krxSec = _hhmmss(info.get("krxTm"))
    curSec = _hhmmss(info.get("tm"))
    src = str(info.get("src") or "")
    if tick and krxSec is not None and curSec is not None and krxSec == curSec and src in ("0B", "poll"):
        return tick
    if closeDt:
        return closeDt
    if tick and src == "0B":
        return tick
    return ""


def curNewerThanKrx(info, today=""):
    """curPrc 시각이 krxPrc 보다 뒤면 True."""
    info = info or {}
    if _px(info.get("curPrc")) is None:
        return False
    kday = krxQuoteDay(info)
    cday = _ymd(info.get("tickYmd"))
    if not kday:
        return bool(cday)
    if cday and cday > kday:
        return True
    if cday and cday < kday:
        return False
    curSec = _hhmmss(info.get("tm"))
    krxSec = _hhmmss(info.get("krxTm"))
    if curSec is None or krxSec is None:
        return False
    if not cday or cday == kday:
        return curSec > krxSec
    return False


def _prevForDisplay(px, info, today, usedKrx):
    """표시 현재가의 전일 종가. 없으면 None."""
    info = info or {}
    prev = _px(info.get("prevClose"))
    if prev is None:
        prev = _px(info.get("basePrc"))
    if prev:
        return prev
    kpx = _px(info.get("krxPrc"))
    kday = krxQuoteDay(info)
    if kpx is None or not kday or abs(kpx - px) <= 0.5:
        return None
    if today and kday < str(today):
        return kpx
    if kday == str(today or ""):
        kflu = _pct(info.get("krxFlu"))
        if kflu is None:
            return None
        denom = 1.0 + kflu / 100.0
        if denom <= 0:
            return None
        return kpx / denom
    return None


def holdChangePct(displayPx, info, today="", usedKrx=False):
    """표시 현재가의 전일대비(%). 가격과 안 맞는 잔여 등락률은 쓰지 않는다."""
    px = _px(displayPx)
    if px is None:
        return None
    prev = _prevForDisplay(px, info, today, usedKrx)
    if prev:
        return (px - prev) / prev * 100.0
    if usedKrx or (_px((info or {}).get("krxPrc")) is not None and abs(_px(info.get("krxPrc")) - px) <= 0.5):
        return _pct((info or {}).get("krxFlu"))
    return None


def selectHoldQuote(info, today="", inKrx=False):
    """보유 그리드 현재가·등락률.

    krxPrc 는 당일 날짜이고 장중 krxTm 일 때만 쓴다.
    전일이거나 curPrc 가 더 새것이면 curPrc 를 쓴다.
    """
    info = info or {}
    today = str(today or "")
    krxOk = (
        _px(info.get("krxPrc")) is not None
        and krxQuoteDay(info) == today
        and krxTmInSession(info.get("krxTm"))
    )
    newer = curNewerThanKrx(info, today)
    usedKrx = False
    prc = None
    if not inKrx and _px(info.get("nxtPrc")) is not None:
        prc = info.get("nxtPrc")
    elif inKrx and krxOk and not newer:
        prc = info.get("krxPrc")
        usedKrx = True
    elif _px(info.get("curPrc")) is not None and (newer or not krxOk):
        prc = info.get("curPrc")
    elif krxOk:
        prc = info.get("krxPrc")
        usedKrx = True
    elif _px(info.get("curPrc")) is not None:
        prc = info.get("curPrc")
    elif inKrx and _px(info.get("nxtPrc")) is not None:
        prc = info.get("nxtPrc")
    elif not inKrx and _px(info.get("krxPrc")) is not None:
        prc = info.get("krxPrc")
    flu = holdChangePct(prc, info, today, usedKrx)
    return prc, flu


def stopInputPx(info, today=""):
    """15:15 손절에 넣는 현재가. 전일 krx 종가는 넣지 않는다. 모르면 None."""
    info = info or {}
    today = _ymd(today)
    cur = _px(info.get("curPrc"))
    if cur is None:
        cur = _px(info.get("price"))
    krx = _px(info.get("krxPrc"))
    kday = krxQuoteDay(info)
    krxOk = krx is not None and bool(today) and kday == today and krxTmInSession(info.get("krxTm"))
    cday = _ymd(info.get("tickYmd"))
    src = str(info.get("src") or "")
    curLive = cur is not None and src in ("0B", "poll") and (not today or cday == today)
    if curLive and krx is not None and kday and today and kday < today and abs(cur - krx) <= 0.5:
        curLive = False
    if curLive and krxOk and not curNewerThanKrx(info, today):
        curSec = _hhmmss(info.get("tm"))
        krxSec = _hhmmss(info.get("krxTm"))
        if curSec is not None and krxSec is not None and krxSec > curSec:
            return krx
        return cur
    if curLive:
        return cur
    if krxOk:
        return krx
    return None


def applyPollPrice(info, price, tm, low=None, now=None, staleSec=None):
    """더 새로운 오늘 0B 가 있으면 폴링 값으로 덮지 않는다.

    장중 시각이면 KRX 현재가로 보고 krxPrc·krxTm 도 같이 고친다.
    전일 종가는 prevClose 로 남겨 등락률을 다시 계산한다.
    """
    from datetime import datetime

    current = dict(info or {})
    moment = now or datetime.now()
    src = str(current.get("src") or "")
    if src == "0B" and quoteIsFresh(current, moment, staleSec):
        return current, False
    try:
        px = abs(float(str(price).replace(",", "").lstrip("+")))
    except (TypeError, ValueError):
        return current, False
    if px <= 0:
        return current, False
    today = moment.strftime("%Y%m%d")
    tmTxt = str(tm or moment.strftime("%H%M%S"))
    oldDay = krxQuoteDay(current)
    if _px(current.get("prevClose")) is None and oldDay and oldDay < today:
        current["prevClose"] = current.get("krxPrc") or current.get("curPrc")
    elif _px(current.get("prevClose")) is None and oldDay == today:
        oldFlu = _pct(current.get("krxFlu"))
        oldKrx = _px(current.get("krxPrc"))
        if oldFlu is not None and oldKrx:
            denom = 1.0 + oldFlu / 100.0
            if denom > 0:
                current["prevClose"] = oldKrx / denom
    current["curPrc"] = px
    current["src"] = "poll"
    current["tm"] = tmTxt
    current["tickYmd"] = today
    if low not in (None, ""):
        current["lowPrc"] = low
    if krxTmInSession(tmTxt):
        current["krxPrc"] = px
        current["krxTm"] = tmTxt
        current["krxYmd"] = today
        flu = holdChangePct(px, current, today, usedKrx=False)
        if flu is None:
            current["krxFlu"] = ""
            current["fluRt"] = ""
        else:
            text = f"{flu:+.2f}"
            current["krxFlu"] = text
            current["fluRt"] = text
    return current, True


def summarizeQuotes(rows, ticks, topN=5):
    """틱이 있는 종목 수와 MA5·MA20 에 가까운 순."""
    ranked = []
    fresh = 0
    total = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "").strip()
        if not code:
            continue
        total += 1
        tick = (ticks or {}).get(code) or {}
        src = str(tick.get("src") or "")
        tm = str(tick.get("tm") or "")
        price = tick.get("price")
        if price is None:
            price = tick.get("curPrc")
        live = src in ("0B", "poll") and (tm or price)
        if live:
            fresh += 1
        d5 = maDistancePct(price, row.get("ma5"))
        d20 = maDistancePct(price, row.get("ma20"))
        dists = [abs(val) for val in (d5, d20) if val is not None]
        nearest = min(dists) if dists else None
        ranked.append({
            "stkCd": code,
            "tm": tm,
            "src": src,
            "ma5Pct": d5,
            "ma20Pct": d20,
            "near": nearest,
            "live": bool(live),
        })
    known = [row for row in ranked if row["near"] is not None]
    known.sort(key=lambda row: (row["near"], row["stkCd"]))
    return {"fresh": fresh, "total": total, "top": known[: int(topN or 0)]}


def formatSummary(summary):
    """5분 요약 한 줄. 토큰은 넣지 않는다."""
    top = []
    for row in (summary or {}).get("top") or []:
        bits = [str(row.get("stkCd") or "")]
        if row.get("ma5Pct") is not None:
            bits.append(f"5이평 {float(row['ma5Pct']):+.1f}%")
        if row.get("ma20Pct") is not None:
            bits.append(f"20이평 {float(row['ma20Pct']):+.1f}%")
        if row.get("tm"):
            bits.append(str(row["tm"]))
        top.append(" ".join(bits))
    tail = " · ".join(top) if top else "거리 없음"
    fresh = int((summary or {}).get("fresh") or 0)
    total = int((summary or {}).get("total") or 0)
    stale = int((summary or {}).get("stale") or 0)
    return f"[PBK01] 시세 {fresh}/{total} 틱 · stale 폴링 {stale}종 · {tail}"
