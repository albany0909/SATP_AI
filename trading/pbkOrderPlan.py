"""PBK01 모의 주문 계획. 브로커를 부르지 않는다.

백테스트는 터치 시 이평, 시가가 이평 아래면 시가에 체결한다고 본다.
모의 주문은 그 가격으로 소급하지 않고, 주문 시점의 현재가로만 기록한다.
지정가는 min(매도 1호가, 이평을 호가 단위로 올린 값)이다. 기본 추격 한도는 0이다.
"""

from __future__ import annotations

PBK_CHASE_MAX_PCT = 0.0
BUY_FROM = "090000"
BUY_UNTIL = "151500"


def pbkChaseMaxPct(settings=None):
    """추격 한도(%). 키가 없으면 0. 파일을 쓰지 않는다."""
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    raw = settings.get("pbkChaseMaxPct") if isinstance(settings, dict) else None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return PBK_CHASE_MAX_PCT
    if val < 0:
        return PBK_CHASE_MAX_PCT
    return val


def absPx(raw):
    """키움 등락 부호·콤마를 떼고 절대 가격. 없으면 None."""
    if raw is None or raw == "":
        return None
    try:
        num = abs(float(str(raw).replace(",", "").lstrip("+")))
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def _hm(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) < 4:
        return ""
    return digits.zfill(6)[-6:]


def inBuySession(hm):
    """매수 창. 09:00 포함, 15:15 미만."""
    text = _hm(hm)
    return bool(text) and BUY_FROM <= text < BUY_UNTIL


def buyLimitPx(ask, line, chasePct=0):
    """min(매도 1호가, 이평 상한). 추격 0이면 이평을 호가 단위로 올린 값까지."""
    from utils.krxTick import ceilToKrxTick, floorToKrxTick

    askPx = absPx(ask)
    linePx = absPx(line)
    if askPx is None or linePx is None:
        return None
    try:
        chase = float(chasePct or 0)
    except (TypeError, ValueError):
        chase = 0.0
    if chase > 0:
        cap = floorToKrxTick(linePx * (1.0 + chase / 100.0))
    else:
        cap = ceilToKrxTick(linePx)
    if not cap or cap <= 0:
        return None
    return min(askPx, float(cap))


def gapPct(price, line):
    px = absPx(price)
    base = absPx(line)
    if px is None or base is None:
        return None
    return (px - base) / base * 100.0


def quoteTradable(quote, now=None):
    """주입된 테스트 시세는 fresh 키가 없으면 쓴다. src 가 있으면 오늘 3분 이내만."""
    if not isinstance(quote, dict):
        return False
    if quote.get("fresh") is False:
        return False
    if quote.get("fresh") is True:
        return absPx(quote.get("price") or quote.get("curPrc")) is not None
    if quote.get("src"):
        if now is None:
            return False
        from trading.pbkQuotePlan import quoteIsFresh

        info = dict(quote)
        if not info.get("curPrc"):
            info["curPrc"] = quote.get("price")
        return quoteIsFresh(info, now)
    return absPx(quote.get("price") or quote.get("curPrc")) is not None


def _legBlocked(pos, leg):
    """오늘 이미 낸 단계만 막는다. 마감 확정 뒤 잔량이 있으면 다음 터치는 허용한다."""
    if not isinstance(pos, dict) or pos.get("released"):
        return False
    pending = (pos.get("pending") or {}).get(str(leg))
    if pending:
        return True
    legRow = pos.get(f"leg{leg}")
    if isinstance(legRow, dict) and legRow.get("settled"):
        try:
            filled = int(legRow.get("filledQty") or 0)
            remain = int(legRow.get("remainQty") or 0)
        except (TypeError, ValueError):
            return False
        return filled > 0 and remain <= 0
    if isinstance(legRow, dict):
        try:
            if int(legRow.get("qty") or 0) > 0 or int(legRow.get("rmnQty") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        if str(legRow.get("ordNo") or "").strip():
            return True
        return legRow.get("fillPx") is not None
    return bool(legRow)


def _hit(price, low, line):
    base = absPx(line)
    if base is None:
        return False, ""
    if price is not None and price <= base:
        return True, "현재가"
    if low is not None and low <= base:
        return True, "저가"
    return False, ""


def _posByCode(positions):
    out = {}
    for pos in positions or []:
        if isinstance(pos, dict) and pos.get("stkCd"):
            out[str(pos.get("stkCd"))] = pos
    return out


def sellableQty(pos):
    """매도 수량. brokerQty 가 있으면 키움 실제 잔고만 쓴다."""
    if not isinstance(pos, dict):
        return 0
    if pos.get("brokerQty") is not None:
        try:
            return max(0, int(pos.get("brokerQty") or 0))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(pos.get("qty") or 0))
    except (TypeError, ValueError):
        return 0


def _slotUsed(pos):
    if not isinstance(pos, dict) or pos.get("released") or pos.get("closed"):
        return False
    if _legBlocked(pos, 1) or _legBlocked(pos, 2):
        return True
    raw = pos.get("brokerQty") if pos.get("brokerQty") is not None else pos.get("qty")
    try:
        return int(raw or 0) > 0
    except (TypeError, ValueError):
        return False


_paperCash = None


def notePaperCash(amount):
    """모의 매수가능 금액. 모르면 None 이라 슬롯 한도만 쓴다."""
    global _paperCash
    if amount is None:
        _paperCash = None
        return
    try:
        _paperCash = max(0, int(float(amount)))
    except (TypeError, ValueError):
        _paperCash = None


def paperCash():
    return _paperCash


def buildLiveActions(
    candidates,
    quotes,
    positions,
    hm,
    ymd,
    chaseMaxPct=None,
    slotKrw=6_000_000,
    slotLimit=5,
    now=None,
    requireBroker=False,
    cash=None,
    pauseNew=False,
    blockedNew=None,
    parkRoom=None,
):
    """주문·놓침·건너뜀. 브로커는 호출하지 않고, 과거 가격으로 체결을 만들지 않는다."""
    from strategies.impl.pullbackTwoStep import (
        LEG1_PCT,
        LEG2_PCT,
        cashCappedQty,
        floorPx,
        floorQty,
        legMoney,
        marketName,
        scaledSlot,
        slotScale,
        stopTriggered,
    )

    chase = PBK_CHASE_MAX_PCT if chaseMaxPct is None else float(chaseMaxPct)
    textHm = _hm(hm)
    leftCash = None if cash is None else max(0, int(float(cash)))
    byPos = _posByCode(positions)
    actions = []
    missed = []
    skips = []
    paused = []
    blocked = {str(code) for code in (blockedNew or []) if str(code or "")}
    used = {code for code, pos in byPos.items() if _slotUsed(pos)}

    def _skip(code, reason, leg=None):
        skips.append({"stkCd": code, "reason": reason, "leg": leg})

    def _miss(code, name, leg, lineName, line, basis, touchPx, nowPx, reason="당일 터치 놓침"):
        gap = gapPct(nowPx, line)
        missed.append({
            "ymd": str(ymd or ""),
            "stkCd": code,
            "stkNm": name or code,
            "leg": int(leg),
            "lineName": lineName,
            "line": line,
            "basis": basis,
            "touchPx": touchPx,
            "nowPx": nowPx,
            "gapPct": None if gap is None else round(gap, 4),
            "hm": textHm,
            "reason": reason,
        })

    # 매도를 먼저 낸다. f+10 시가, 15:15 손절.
    for code, pos in byPos.items():
        if pos.get("closed") or (pos.get("pending") or {}).get("sell"):
            _skip(code, "매도대기")
            continue
        if requireBroker and pos.get("brokerQty") is None:
            _skip(code, "실체결대기")
            continue
        qty = sellableQty(pos)
        if qty <= 0:
            _skip(code, "실체결없음")
            continue
        quote = (quotes or {}).get(code) or {}
        if not quoteTradable(quote, now):
            _skip(code, "시세 대기")
            continue
        price = absPx(quote.get("price") or quote.get("curPrc"))
        openPx = absPx(quote.get("open"))
        if str(pos.get("exitDt") or "") == str(ymd or "") and inBuySession(textHm):
            limit = floorPx(openPx or price)
            if limit <= 0:
                _skip(code, "시세 대기", "sell")
                continue
            actions.append({
                "side": "SELL",
                "stkCd": code,
                "leg": "sell",
                "qty": qty,
                "limitPx": limit,
                "fillPx": price,
                "trdeTp": "0",
                "reason": "f+10",
            })
            continue
        if stopTriggered(textHm, price, pos.get("avg")):
            actions.append({
                "side": "SELL",
                "stkCd": code,
                "leg": "sell",
                "qty": qty,
                "limitPx": floorPx(price),
                "fillPx": price,
                "trdeTp": "0",
                "reason": "손절",
            })

    if not inBuySession(textHm):
        for row in candidates or []:
            code = str((row or {}).get("stkCd") or "")
            if code:
                _skip(code, "시간외")
        return {"actions": actions, "missed": missed, "skips": skips, "paused": paused, "exitHint": ""}

    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        if not code:
            continue
        name = str(row.get("stkNm") or row.get("name") or code)
        pos = byPos.get(code) or {}
        if row.get("amtOk") is False and (
            _slotUsed(pos) or int(pos.get("brokerQty") or pos.get("qty") or 0) > 0
        ):
            _skip(code, "대금미달")
            continue
        quote = (quotes or {}).get(code) or {}
        if not quoteTradable(quote, now):
            _skip(code, "시세 대기")
            continue
        price = absPx(quote.get("price") or quote.get("curPrc"))
        low = absPx(quote.get("low"))
        if price is None:
            _skip(code, "시세 대기")
            continue
        if pos.get("buyHold"):
            _skip(code, "잔고불일치")
            continue
        expire = str(row.get("expireDt") or pos.get("expireDt") or "")
        if expire and str(ymd or "") > expire:
            _skip(code, "창종료")
            continue
        owns = code in used
        if (code in blocked or pauseNew) and not owns:
            _skip(code, "상승장쉬기")
            if code not in paused:
                paused.append(code)
            continue
        legacy = pos.get("slotBoost") in (None, "") and (
            bool(pos.get("leg1")) or int(pos.get("qty") or 0) > 0 or int(pos.get("brokerQty") or 0) > 0
        )
        market = str(row.get("market") or row.get("marketName") or pos.get("market") or "")
        if not market:
            market = marketName(code)
        scale = slotScale(
            market, row.get("ma5"), row.get("ma20"), locked=pos.get("slotBoost"), legacy=legacy,
        )
        budget = scaledSlot(slotKrw, scale)
        if abs(scale - 1.5) < 1e-9 and parkRoom is not None:
            budget = min(budget, max(0, int(parkRoom)))
        want = []
        if not _legBlocked(pos, 1):
            hit, basis = _hit(price, low, row.get("ma5"))
            if hit:
                want.append((1, "5이평", row.get("ma5"), basis, LEG1_PCT))
        leg1Ready = _legBlocked(pos, 1) or any(item[0] == 1 for item in want)
        if leg1Ready and not _legBlocked(pos, 2):
            hit, basis = _hit(price, low, row.get("ma20"))
            if hit:
                want.append((2, "20이평", row.get("ma20"), basis, LEG2_PCT))
        if not want:
            continue
        if not owns:
            if len(used) >= int(slotLimit):
                _skip(code, "슬롯한도")
                continue
            used.add(code)
        ask = absPx(quote.get("ask") if quote.get("ask") is not None else quote.get("ask1"))
        for leg, lineName, line, basis, pct in want:
            if ask is None:
                _miss(code, name, leg, lineName, absPx(line), basis, low if basis == "저가" else price, price, "호가없음")
                continue
            limit = buyLimitPx(ask, line, chase)
            if limit is None or limit <= 0:
                _miss(code, name, leg, lineName, absPx(line), basis, ask, ask, "호가없음")
                continue
            qty = floorQty(legMoney(budget, pct), limit)
            qty = cashCappedQty(qty, limit, leftCash)
            legRow = pos.get(f"leg{leg}") if isinstance(pos.get(f"leg{leg}"), dict) else {}
            if legRow.get("settled"):
                try:
                    cap = int(legRow.get("remainQty") or 0)
                except (TypeError, ValueError):
                    cap = 0
                if int(legRow.get("filledQty") or 0) > 0:
                    qty = min(qty, cap)
            if qty < 1 or limit <= 0:
                _skip(code, "현금부족" if leftCash is not None else "수량0", leg)
                continue
            if leftCash is not None:
                spend = int(qty) * int(limit)
                leftCash = max(0, leftCash - spend - spend * 15 // 10000)
            actions.append({
                "side": "BUY",
                "stkCd": code,
                "stkNm": name,
                "leg": leg,
                "qty": qty,
                "limitPx": limit,
                "fillPx": price,
                "ask1": ask,
                "linePx": absPx(line),
                "orderGapPct": None if gapPct(limit, line) is None else round(gapPct(limit, line), 4),
                "trdeTp": "0",
                "reason": lineName,
                "slotBoost": 1.5 if abs(scale - 1.5) < 1e-9 else 1.0,
                "signalDt": row.get("signalDt") or pos.get("signalDt") or "",
                "expireDt": row.get("expireDt") or pos.get("expireDt") or "",
                "hm": textHm,
            })
    return {"actions": actions, "missed": missed, "skips": skips, "paused": paused}


def _knownName(name, code):
    text = str(name or "").strip()
    if not text or text == str(code or ""):
        return ""
    return text


def exitDtFromFirst(firstBuyDt, calendar=None):
    """첫 체결일을 0번째로 센 f+10 거래일. 달력이 없으면 휴장 달력으로 센다."""
    from datetime import datetime, timedelta

    from strategies.impl.pullbackTwoStep import HOLD_DAYS, holidayCalendar, tradingDaysAfter

    day = str(firstBuyDt or "")[:8]
    if len(day) != 8:
        return ""
    if not calendar:
        end = (datetime.strptime(day, "%Y%m%d") + timedelta(days=40)).strftime("%Y%m%d")
        calendar = holidayCalendar(day, end)
    ahead = tradingDaysAfter(day, calendar, HOLD_DAYS)
    if len(ahead) < HOLD_DAYS:
        return ""
    return ahead[-1]


def _firstFillDay(pos):
    day = str((pos or {}).get("firstBuyDt") or "")[:8]
    if len(day) == 8:
        return day
    found = []
    for key in ("leg1", "leg2"):
        leg = (pos or {}).get(key) or {}
        if isinstance(leg, dict):
            text = str(leg.get("ymd") or "")[:8]
            if len(text) == 8:
                found.append(text)
    return min(found) if found else ""


def _normCode(raw):
    from utils.stkCdUtil import normStkCd

    return normStkCd(raw)


def _signalText(row):
    from strategies.impl.pullbackTwoStep import _ymd

    if not isinstance(row, dict):
        return ""
    return _ymd(row.get("signalDt") or row.get("signalDate") or row.get("candleDt"))


def signalFromRecords(code, records):
    """상태·체결 기록에 이미 적힌 장대양봉일. 마지막 값을 쓴다."""
    want = _normCode(code)
    if not want:
        return ""
    found = ""
    for row in records or []:
        if not isinstance(row, dict):
            continue
        buckets = [row]
        for key in ("positions", "candidates"):
            nested = row.get(key)
            if isinstance(nested, list):
                buckets.extend(item for item in nested if isinstance(item, dict))
        for item in buckets:
            if _normCode(item.get("stkCd") or item.get("stk_cd") or item.get("code")) != want:
                continue
            sig = _signalText(item)
            if sig:
                found = sig
    return found


def signalDtForHold(code, candidates=None, disclosures=None, bars=None, firstBuyDt="", asOf="", history=None):
    """후보·이전 기록·공시의 장대양봉일을 쓰고, 없으면 첫 체결 직전 장대양봉을 쓴다."""
    from strategies.impl.pullbackTwoStep import _ymd, findCandleEvents, longCandleBefore

    recorded = signalFromRecords(code, candidates) or signalFromRecords(code, history)
    if recorded:
        return recorded
    want = _normCode(code)
    items = []
    if isinstance(disclosures, dict):
        for key, val in disclosures.items():
            if _normCode(key) == want:
                items = list(val or [])
                break
    elif isinstance(disclosures, list):
        items = list(disclosures)
    picked = ""
    if items and bars:
        events = findCandleEvents(bars, items, want or code, asOf=asOf or None)
        first = _ymd(firstBuyDt)
        for event in events or []:
            sig = _ymd((event or {}).get("signalDt"))
            if not sig:
                continue
            if first and sig > first:
                continue
            if sig >= picked:
                picked = sig
    if picked:
        return picked
    return longCandleBefore(bars, firstBuyDt)


def completeHoldRecords(positions, candidates=None, disclosures=None, barsByCode=None, names=None, asOf="", history=None):
    """비어 있는 signalDt·exitDt·stkNm 만 채운다. 있는 값은 바꾸지 않는다."""
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        if item.get("released") or item.get("closed"):
            out.append(item)
            continue
        code = str(item.get("stkCd") or "")
        first = _firstFillDay(item)
        if first and not str(item.get("firstBuyDt") or ""):
            item["firstBuyDt"] = first
        if code and not _signalText(item):
            sig = signalDtForHold(
                code,
                candidates,
                disclosures,
                (barsByCode or {}).get(code) or (barsByCode or {}).get(_normCode(code)),
                firstBuyDt=item.get("firstBuyDt") or "",
                asOf=asOf,
                history=history,
            )
            if sig:
                item["signalDt"] = sig
        if not str(item.get("exitDt") or "") and item.get("firstBuyDt") and not item.get("released"):
            exitDt = exitDtFromFirst(item.get("firstBuyDt"))
            if exitDt:
                item["exitDt"] = exitDt
        name = _knownName(item.get("stkNm"), code)
        if not name:
            alt = _knownName((names or {}).get(code), code)
            if not alt:
                for row in candidates or []:
                    if isinstance(row, dict) and str(row.get("stkCd") or "") == code:
                        alt = _knownName(row.get("stkNm") or row.get("name"), code)
                        if alt:
                            break
            if alt:
                item["stkNm"] = alt
        out.append(item)
    return out


def applyPaperResult(positions, action, status, ordNo="", ymd="", calendar=None):
    """접수·체결·실패를 포지션 목록에 반영한 새 목록. 실패면 대기를 지운다."""
    from strategies.impl.pullbackTwoStep import HOLD_DAYS, tradingDaysAfter, weightedAvg

    code = str((action or {}).get("stkCd") or "")
    rows = [dict(pos) for pos in (positions or []) if isinstance(pos, dict)]
    found = None
    for pos in rows:
        if str(pos.get("stkCd") or "") == code:
            found = pos
            break
    if found is None:
        found = {"stkCd": code, "qty": 0, "avg": None, "leg1": None, "leg2": None, "pending": {}}
        rows.append(found)
    pending = dict(found.get("pending") or {})
    legKey = str((action or {}).get("leg") or "")
    side = str((action or {}).get("side") or "")
    if status == "pending":
        pending[legKey] = {
            "ordNo": str(ordNo or ""),
            "qty": int(action.get("qty") or 0),
            "limitPx": action.get("limitPx"),
            "fillPx": action.get("fillPx"),
        }
        found["pending"] = pending
        return rows
    if status == "fail":
        pending.pop(legKey, None)
        found["pending"] = pending
        return rows
    pending.pop(legKey, None)
    found["pending"] = pending
    fillPx = absPx(action.get("fillPx"))
    qty = int(action.get("qty") or 0)
    if side == "SELL":
        found["qty"] = 0
        found["closed"] = True
        found["closedYmd"] = str(ymd or "")
        found["closePx"] = fillPx
        found["closeReason"] = action.get("reason") or ""
        return rows
    prev = found.get(f"leg{legKey}") if isinstance(found.get(f"leg{legKey}"), dict) else {}
    keptFilled = int(prev.get("filledQty") or 0) if prev.get("settled") else 0
    if prev.get("settled") and int(prev.get("originalPlan") or 0) > 0:
        original = int(prev.get("originalPlan") or 0)
    else:
        original = qty
    leg = {
        "qty": qty,
        "originalPlan": original,
        "filledQty": keptFilled,
        "fillPx": fillPx,
        "limitPx": action.get("limitPx"),
        "ordNo": str(ordNo or ""),
        "ymd": str(ymd or ""),
        "hm": str(action.get("hm") or ""),
        "ask1": action.get("ask1"),
        "linePx": action.get("linePx"),
        "orderGapPct": action.get("orderGapPct"),
        "settled": False,
    }
    found[f"leg{legKey}"] = leg
    if str(action.get("leg") or "") == "1" and found.get("slotBoost") in (None, ""):
        if action.get("slotBoost") not in (None, ""):
            found["slotBoost"] = action.get("slotBoost")
    if action.get("signalDt") and not found.get("signalDt"):
        found["signalDt"] = action.get("signalDt")
    if action.get("expireDt") and not found.get("expireDt"):
        found["expireDt"] = action.get("expireDt")
    if found.get("released"):
        found["released"] = False
    if keptFilled > 0:
        realName = _knownName(action.get("stkNm"), code)
        if realName and not _knownName(found.get("stkNm"), code):
            found["stkNm"] = realName
        return rows
    if found.get("brokerQty") is None:
        found["brokerQty"] = 0
        found["brokerAvg"] = None
    fills = []
    for key in ("leg1", "leg2"):
        row = found.get(key)
        if isinstance(row, dict) and int(row.get("qty") or 0) > 0:
            fills.append(row)
    avg, total = weightedAvg(fills)
    found["qty"] = total
    found["avg"] = avg
    if not found.get("firstBuyDt"):
        found["firstBuyDt"] = str(ymd or "")
        if not found.get("exitDt"):
            if calendar:
                ahead = tradingDaysAfter(found["firstBuyDt"], calendar, HOLD_DAYS)
                found["exitDt"] = ahead[-1] if len(ahead) >= HOLD_DAYS else ""
            else:
                found["exitDt"] = exitDtFromFirst(found["firstBuyDt"])
    realName = _knownName(action.get("stkNm"), code)
    if realName and not _knownName(found.get("stkNm"), code):
        found["stkNm"] = realName
    return rows


def _ordKey(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    bare = text.lstrip("0")
    return bare or "0"


def _sameOrd(left, right):
    a = _ordKey(left)
    b = _ordKey(right)
    return bool(a) and a == b


def noteBrokerFill(positions, stkCd, side, qty, px, ordNo="", cumQty=None):
    """키움 00 체결. 전략 기록가(fillPx)는 바꾸지 않고 실제 수량·평균만 누적한다.

    같은 주문번호의 누적 체결(cumQty)이 이미 반영된 값보다 크지 않으면 더하지 않는다.
    """
    code = str(stkCd or "")
    sideText = str(side or "")
    buy = "매수" in sideText or sideText.upper() in ("BUY", "B")
    sell = "매도" in sideText or sideText.upper() in ("SELL", "S")
    try:
        q = abs(int(qty or 0))
    except (TypeError, ValueError):
        q = 0
    price = absPx(px)
    if not code or q <= 0 or price is None or not (buy or sell):
        return list(positions or []), False
    rows = [dict(pos) for pos in (positions or []) if isinstance(pos, dict)]
    found = None
    for pos in rows:
        if str(pos.get("stkCd") or "") != code:
            continue
        pending = pos.get("pending") or {}
        legs = [pos.get("leg1"), pos.get("leg2")]
        matched = False
        if str(ordNo or ""):
            for item in list(pending.values()) + [leg for leg in legs if isinstance(leg, dict)]:
                if _sameOrd((item or {}).get("ordNo"), ordNo):
                    matched = True
                    break
            for item in pos.get("openOrders") or []:
                if _sameOrd((item or {}).get("ordNo"), ordNo):
                    matched = True
                    break
        if matched or not str(ordNo or ""):
            found = pos
            break
    if found is None:
        return rows, False
    book = dict(found.get("ordFill") or {})
    key = _ordKey(ordNo) or str(ordNo or "")
    try:
        prev = int(book.get(key) or 0)
    except (TypeError, ValueError):
        prev = 0
    if cumQty not in (None, ""):
        try:
            target = abs(int(cumQty))
        except (TypeError, ValueError):
            target = prev + q
    else:
        target = prev + q
    delta = max(0, target - prev)
    book[key] = max(prev, target)
    found["ordFill"] = book
    try:
        held = int(found.get("brokerQty") or 0)
    except (TypeError, ValueError):
        held = 0
    if buy:
        oldAvg = absPx(found.get("brokerAvg")) or 0
        newQty = held + delta
        if delta and newQty > 0:
            found["brokerAvg"] = ((oldAvg * held) + (price * delta)) / newQty
        found["brokerQty"] = newQty
    else:
        found["brokerQty"] = max(0, held - delta)
    found["brokerSrc"] = "fill"
    from datetime import datetime

    stamp = datetime.now().strftime("%H%M%S")
    if not found.get("firstFillHm"):
        found["firstFillHm"] = stamp
    found["lastFillHm"] = stamp
    for legName in ("leg1", "leg2"):
        leg = found.get(legName)
        if isinstance(leg, dict) and _sameOrd(leg.get("ordNo"), ordNo):
            try:
                plan = int(leg.get("qty") or 0)
            except (TypeError, ValueError):
                plan = 0
            leg = dict(leg)
            leg["rmnQty"] = max(0, plan - int(book.get(key) or 0))
            found[legName] = leg
    return rows, True


def applyAccountQty(positions, accountRows):
    """잔고 조회의 실제 수량. 전략 기록 수량과는 따로 둔다."""
    by = {}
    for row in accountRows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        try:
            qty = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            qty = 0
        if code and qty > 0:
            by[code] = row
    rows = []
    changed = False
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        code = str(item.get("stkCd") or "")
        acct = by.get(code)
        if acct is not None:
            newQty = int(acct.get("qtyNum") or 0)
            avg = acct.get("avgNum")
            newAvg = absPx(avg) if avg not in (None, "") else item.get("brokerAvg")
            if item.get("brokerQty") != newQty or item.get("brokerAvg") != newAvg:
                item["brokerQty"] = newQty
                item["brokerAvg"] = newAvg
                item["brokerSrc"] = "account"
                changed = True
        rows.append(item)
    return rows, changed


def parseOpenOrders(body):
    """ka10075 미체결(oso). 잔량이 있는 행만."""
    from api.getAcntEvlt import parsePadNum
    from utils.stkCdUtil import normStkCd

    out = []
    for item in (body or {}).get("oso") or []:
        if not isinstance(item, dict):
            continue
        rmn = parsePadNum(item.get("oso_qty")) or 0
        if rmn <= 0:
            continue
        sideName = str(item.get("io_tp_nm") or "")
        side = "SELL" if "매도" in sideName else "BUY"
        out.append({
            "stkCd": normStkCd(item.get("stk_cd")),
            "ordNo": str(item.get("ord_no") or "").strip(),
            "side": side,
            "ordQty": parsePadNum(item.get("ord_qty")) or 0,
            "rmnQty": int(rmn),
            "fillQty": parsePadNum(item.get("cntr_qty")) or 0,
            "fillPx": absPx(item.get("cntr_pric")),
        })
    return out


def parseAccountHolds(body):
    """kt00004 보유. 잔량이 있는 종목만."""
    from api.getAcntEvlt import parsePadNum
    from utils.stkCdUtil import normStkCd

    out = []
    raw = (body or {}).get("stk_acnt_evlt_prst") or []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        qty = parsePadNum(item.get("rmnd_qty")) or 0
        if qty <= 0:
            continue
        code = normStkCd(item.get("stk_cd"))
        if not code:
            continue
        out.append({
            "stkCd": code,
            "stkNm": item.get("stk_nm") or code,
            "qtyNum": int(qty),
            "avgNum": parsePadNum(item.get("avg_prc")) or 0,
        })
    return out


def reconcilePositions(positions, accountRows=None, openOrders=None, accountKnown=False):
    """구 상태(주문 시점 전량으로 적힌 leg)를 보유로 유지한다.

    잔고가 있으면 실제 수량·평균만 덮어쓰고, 미체결은 기존 주문번호에 잔량만 붙인다.
    새 주문은 만들지 않는다.
    """
    acct = {}
    for row in accountRows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        try:
            qty = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            qty = 0
        if code and qty > 0:
            acct[code] = row
    orders = [row for row in (openOrders or []) if isinstance(row, dict)]
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        code = str(item.get("stkCd") or "")
        if accountKnown:
            row = acct.get(code)
            if row is None:
                item["brokerQty"] = 0
                item["brokerSrc"] = "account"
            else:
                item["brokerQty"] = int(row.get("qtyNum") or 0)
                avg = row.get("avgNum")
                if avg not in (None, ""):
                    item["brokerAvg"] = absPx(avg)
                item["brokerSrc"] = "account"
        book = dict(item.get("ordFill") or {})
        opens = []
        for order in orders:
            if str(order.get("stkCd") or "") != code:
                continue
            if str(order.get("side") or "BUY") != "BUY":
                continue
            opens.append(dict(order))
            key = _ordKey(order.get("ordNo"))
            filled = int(order.get("fillQty") or 0)
            if not filled:
                filled = max(0, int(order.get("ordQty") or 0) - int(order.get("rmnQty") or 0))
            if key:
                book[key] = max(int(book.get(key) or 0), filled)
        item["ordFill"] = book
        item["openOrders"] = opens
        for legName in ("leg1", "leg2"):
            leg = item.get(legName)
            if not isinstance(leg, dict):
                continue
            leg = dict(leg)
            matched = None
            for order in opens:
                if _sameOrd(leg.get("ordNo"), order.get("ordNo")):
                    matched = order
                    break
            if matched is None and not str(leg.get("ordNo") or "") and len(opens) == 1:
                matched = opens[0]
                leg["ordNo"] = str(matched.get("ordNo") or "")
            if matched is not None:
                leg["rmnQty"] = int(matched.get("rmnQty") or 0)
                if not str(leg.get("ordNo") or ""):
                    leg["ordNo"] = str(matched.get("ordNo") or "")
            item[legName] = leg
        out.append(item)
    return out


def unfilledRemainder(positions):
    """주문 수량과 실제 체결 수량의 차이. 추격 주문은 만들지 않는다."""
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict) or pos.get("closed"):
            continue
        plan = 0
        for key in ("leg1", "leg2"):
            leg = pos.get(key) or {}
            if isinstance(leg, dict):
                try:
                    plan += int(leg.get("qty") or 0)
                except (TypeError, ValueError):
                    pass
        broker = sellableQty(pos) if pos.get("brokerQty") is not None else 0
        left = plan - broker
        if left > 0:
            out.append({
                "stkCd": str(pos.get("stkCd") or ""),
                "planQty": plan,
                "brokerQty": broker,
                "leftQty": left,
            })
    return out


def _knownOrderFill(pos, leg):
    book = (pos or {}).get("ordFill") or {}
    key = _ordKey((leg or {}).get("ordNo"))
    if not key or key not in book:
        return None
    try:
        return max(0, int(book.get(key) or 0))
    except (TypeError, ValueError):
        return None


def _sessionClosed(day, asOf, includeToday):
    text = str(day or "")[:8]
    today = str(asOf or "")[:8]
    if len(text) != 8 or len(today) != 8:
        return False
    if text < today:
        return True
    return bool(includeToday and text == today)


def _dropExpiredOpenOrders(item, asOf, includeToday):
    """장마감으로 끝난 미체결만 상태에서 지운다. 취소 주문은 내지 않는다."""
    legs = []
    unsettled = False
    settledKeys = set()
    dayByKey = {}
    for key in ("leg1", "leg2"):
        leg = item.get(key)
        if not isinstance(leg, dict):
            continue
        legs.append(leg)
        ordKey = _ordKey(leg.get("ordNo"))
        if ordKey:
            dayByKey[ordKey] = str(leg.get("ymd") or "")[:8]
        if leg.get("settled"):
            if ordKey:
                settledKeys.add(ordKey)
        else:
            unsettled = True
    if legs and all(_sessionClosed(leg.get("ymd"), asOf, includeToday) for leg in legs):
        item["openOrders"] = []
        for leg in legs:
            leg["rmnQty"] = 0
        return item
    kept = []
    for order in item.get("openOrders") or []:
        if not isinstance(order, dict):
            continue
        ordKey = _ordKey(order.get("ordNo"))
        day = str(order.get("ymd") or "")[:8]
        if not day and ordKey:
            day = dayByKey.get(ordKey) or ""
        if ordKey and ordKey in settledKeys:
            continue
        if _sessionClosed(day, asOf, includeToday):
            continue
        if not day and not unsettled and legs:
            continue
        kept.append(order)
    item["openOrders"] = kept
    return item


def settlePositions(positions, asOfYmd, includeToday=False, accountKnown=False, orderFills=None):
    """장 마감 후 실체결만 남기고 미체결은 소멸시킨다. 새 주문은 만들지 않는다.

    실체결이 있으면 그 단계의 잔량만 다음 터치 한도가 된다.
    실체결이 0이면 슬롯을 풀고 후보로 되돌린다.
    """
    asOf = str(asOfYmd or "")
    out = []
    logs = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        if item.get("closed"):
            out.append(item)
            continue
        brokerKnown = item.get("brokerQty") is not None or accountKnown
        if not brokerKnown:
            out.append(item)
            continue
        try:
            broker = max(0, int(item.get("brokerQty") or 0))
        except (TypeError, ValueError):
            broker = 0
        pool = broker
        settledNow = []
        for legName, legNo in (("leg1", 1), ("leg2", 2)):
            leg = item.get(legName)
            if not isinstance(leg, dict):
                continue
            leg = dict(leg)
            if leg.get("settled"):
                pool -= max(0, int(leg.get("filledQty") or 0))
                item[legName] = leg
                continue
            orderDay = str(leg.get("ymd") or "")
            if orderDay and orderDay > asOf:
                item[legName] = leg
                continue
            if orderDay == asOf and not includeToday:
                item[legName] = leg
                continue
            if not orderDay and not includeToday:
                item[legName] = leg
                continue
            plan = int(leg.get("originalPlan") or leg.get("qty") or 0)
            orderQty = int(leg.get("qty") or 0)
            prior = int(leg.get("filledQty") or 0) if leg.get("originalPlan") else 0
            # 예전 ordFill·미체결 잔량은 쓰지 않는다. 잔고 수량이 기준이다.
            fresh = None
            if orderFills is not None:
                key = _ordKey(leg.get("ordNo"))
                if key in orderFills:
                    try:
                        fresh = max(0, int(orderFills.get(key) or 0))
                    except (TypeError, ValueError):
                        fresh = 0
            fromBalance = min(max(0, orderQty), max(0, pool))
            if fresh is not None and fresh != fromBalance:
                item["buyHold"] = True
                item["buyHoldReason"] = "잔고불일치"
            thisFill = fromBalance
            pool = max(0, pool - thisFill)
            totalFilled = prior + thisFill
            if plan <= 0:
                plan = orderQty
            remain = max(0, plan - totalFilled)
            expired = max(0, orderQty - thisFill)
            leg.update({
                "originalPlan": plan,
                "filledQty": totalFilled,
                "remainQty": remain,
                "expiredQty": expired,
                "rmnQty": 0,
                "settled": True,
            })
            item[legName] = leg
            settledNow.append(leg)
            logs.append({
                "ymd": orderDay or asOf,
                "stkCd": str(item.get("stkCd") or ""),
                "stkNm": _knownName(item.get("stkNm"), item.get("stkCd")),
                "leg": legNo,
                "legName": "1차" if legNo == 1 else "2차",
                "ordNo": str(leg.get("ordNo") or ""),
                "ordQty": orderQty,
                "limitPx": leg.get("limitPx"),
                "fillPx": leg.get("fillPx"),
                "brokerQty": thisFill,
                "brokerAvg": item.get("brokerAvg"),
                "expiredQty": expired,
                "ordHm": str(leg.get("hm") or ""),
                "firstFillHm": str(item.get("firstFillHm") or ""),
                "lastFillHm": str(item.get("lastFillHm") or ""),
                "ask1": leg.get("ask1"),
                "linePx": leg.get("linePx"),
                "orderGapPct": leg.get("orderGapPct") if leg.get("orderGapPct") is not None else (
                    None if gapPct(leg.get("limitPx"), leg.get("linePx")) is None
                    else round(gapPct(leg.get("limitPx"), leg.get("linePx")), 4)
                ),
                "fillGapPct": None if gapPct(
                    item.get("brokerAvg") if item.get("brokerAvg") not in (None, "") else leg.get("fillPx"),
                    leg.get("linePx"),
                ) is None else round(gapPct(
                    item.get("brokerAvg") if item.get("brokerAvg") not in (None, "") else leg.get("fillPx"),
                    leg.get("linePx"),
                ), 4),
            })
        if not settledNow:
            _dropExpiredOpenOrders(item, asOf, includeToday)
            out.append(item)
            continue
        item["settledYmd"] = asOf
        item["pending"] = {}
        if broker <= 0:
            item["released"] = True
            item["qty"] = 0
            item["brokerQty"] = 0
            item["firstBuyDt"] = ""
            item["exitDt"] = ""
            for legName in ("leg1", "leg2"):
                leg = item.get(legName)
                if isinstance(leg, dict) and leg.get("settled") and int(leg.get("filledQty") or 0) <= 0:
                    leg = dict(leg)
                    leg["remainQty"] = 0
                    item[legName] = leg
        else:
            item["released"] = False
            item["qty"] = broker
            if item.get("brokerAvg") not in (None, ""):
                item["avg"] = item.get("brokerAvg")
        _dropExpiredOpenOrders(item, asOf, includeToday)
        out.append(item)
    return out, logs


def displayOpenQty(pos):
    """아직 장중에 남은 미체결. 마감 소멸분은 보여 주지 않는다."""
    if not isinstance(pos, dict) or pos.get("closed") or pos.get("released"):
        return 0
    rmn = 0
    anyLeg = False
    openLeg = False
    for key in ("leg1", "leg2"):
        leg = pos.get(key)
        if not isinstance(leg, dict):
            continue
        anyLeg = True
        if leg.get("settled"):
            continue
        openLeg = True
        try:
            rmn += int(leg.get("rmnQty") or 0)
        except (TypeError, ValueError):
            pass
    if anyLeg and not openLeg:
        return 0
    if rmn <= 0 and openLeg:
        for order in pos.get("openOrders") or []:
            try:
                rmn += int((order or {}).get("rmnQty") or 0)
            except (TypeError, ValueError):
                pass
    plan = 0
    for key in ("leg1", "leg2"):
        leg = pos.get(key) or {}
        if isinstance(leg, dict) and not leg.get("settled"):
            try:
                plan += int(leg.get("qty") or 0)
            except (TypeError, ValueError):
                pass
    broker = sellableQty(pos) if pos.get("brokerQty") is not None else 0
    if rmn <= 0 and plan > broker:
        rmn = plan - broker
    if rmn <= 0 and not anyLeg:
        try:
            stated = int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            stated = 0
        if stated > broker:
            rmn = stated - broker
    return max(0, rmn)


def parseAsk(body):
    """ka10004 매도 1호가. 없으면 None."""
    return absPx((body or {}).get("sel_fpr_bid"))


def parseFillQty(body):
    """ka10076 체결을 주문번호별 체결 수량으로 합친다."""
    from api.getAcntEvlt import parsePadNum

    out = {}
    for item in (body or {}).get("cntr") or []:
        if not isinstance(item, dict):
            continue
        side = str(item.get("io_tp_nm") or "")
        if side and "매수" not in side:
            continue
        key = _ordKey(item.get("ord_no"))
        if not key:
            continue
        qty = parsePadNum(item.get("cntr_qty")) or 0
        out[key] = int(out.get(key) or 0) + int(qty)
    return out


def fillBookNames(positions, candidates):
    """상태·체결 기록의 종목명이 코드면 후보 이름으로 바꾼다."""
    names = {}
    for item in list(candidates or []) + list(positions or []):
        if not isinstance(item, dict):
            continue
        code = str(item.get("stkCd") or "")
        name = str(item.get("stkNm") or item.get("name") or "").strip()
        if code and name and name != code:
            names[code] = name
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        code = str(item.get("stkCd") or "")
        cur = str(item.get("stkNm") or "").strip()
        if code and (not cur or cur == code) and names.get(code):
            item["stkNm"] = names[code]
        out.append(item)
    return out


def balanceMismatches(positions, accountRows):
    """상태 수량과 계좌 잔고가 다른 종목. 청산·슬롯 해제는 비교하지 않는다."""
    acct = {}
    for row in accountRows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        try:
            qty = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            qty = 0
        if code:
            acct[code] = qty
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict) or pos.get("closed") or pos.get("released"):
            continue
        code = str(pos.get("stkCd") or "")
        if not code:
            continue
        if pos.get("brokerQty") is None:
            stateQty = 0
        else:
            try:
                stateQty = int(pos.get("brokerQty") or 0)
            except (TypeError, ValueError):
                stateQty = 0
        acctQty = int(acct.get(code) or 0)
        if stateQty != acctQty and (stateQty > 0 or acctQty > 0):
            out.append({"stkCd": code, "stateQty": stateQty, "acctQty": acctQty})
    return out


def _displayName(code, *candidates):
    for raw in candidates:
        text = str(raw or "").strip()
        if text and text != str(code or ""):
            return text
    return str(code or "")


def _annotateHold(item, pos=None):
    algs = list(item.get("algCds") or [])
    item["algText"] = str(item.get("algLabel") or (" · ".join(algs) if algs else ""))
    openQty = displayOpenQty(pos) if pos is not None else int(item.get("openQty") or 0)
    item["openQty"] = openQty
    item["openText"] = f"미체결 {openQty}주" if openQty > 0 else ""
    return item


def fillHoldNames(rows, lookup=None):
    """종목명이 비었거나 코드와 같으면 stkNm·마스터에서 채운다."""
    pending = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        name = str(row.get("stkNm") or "").strip()
        if code and (not name or name == code):
            pending.append(code)
    found = {}
    if pending:
        if lookup is not None:
            try:
                found = lookup(pending) or {}
            except Exception:
                found = {}
        else:
            try:
                from utils.pbkWatchScreen import lookupStkNames

                found = lookupStkNames(pending) or {}
            except Exception:
                found = {}
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        code = str(item.get("stkCd") or "")
        name = str(item.get("stkNm") or "").strip()
        if code and (not name or name == code):
            alt = str((found or {}).get(code) or "").strip()
            if alt and alt != code:
                item["stkNm"] = alt
        out.append(item)
    return out


def paperHoldRows(accountRows, pbkPositions):
    """모의 보유 표. 수량·평단·매입금액은 실체결만 쓴다.

    실체결이 0이어도 행은 남기고, 미체결 잔량은 openText 에만 둔다.
    """
    by = {}
    for row in accountRows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        try:
            qty = int(row.get("qtyNum") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not code or qty <= 0:
            continue
        item = dict(row)
        item["algCds"] = list(item.get("algCds") or [])
        item["stkNm"] = _displayName(code, item.get("stkNm"))
        by[code] = item
    for pos in pbkPositions or []:
        if not isinstance(pos, dict) or pos.get("closed") or pos.get("released"):
            continue
        code = str(pos.get("stkCd") or "")
        if not code:
            continue
        name = _displayName(code, pos.get("stkNm"), (by.get(code) or {}).get("stkNm"))
        if code in by:
            algs = list(by[code].get("algCds") or [])
            if "PBK01" not in algs:
                algs.append("PBK01")
            by[code]["algCds"] = algs
            by[code]["algLabel"] = " · ".join(algs)
            by[code]["stkNm"] = name
            _annotateHold(by[code], pos)
            continue
        if pos.get("brokerQty") is None and not (pos.get("leg1") or pos.get("leg2") or pos.get("openOrders")):
            try:
                if int(pos.get("qty") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        show = sellableQty(pos) if pos.get("brokerQty") is not None else 0
        avg = absPx(pos.get("brokerAvg")) if pos.get("brokerAvg") not in (None, "") else 0
        avg = int(avg or 0)
        pur = avg * show if show > 0 and avg > 0 else 0
        by[code] = _annotateHold({
            "stkCd": code,
            "stkNm": name,
            "qtyNum": show,
            "avgNum": avg if show > 0 else 0,
            "curNum": avg if show > 0 else 0,
            "apiCurNum": avg if show > 0 else 0,
            "purNum": pur,
            "evltNum": pur,
            "pnlNum": 0,
            "algCds": ["PBK01"],
            "algLabel": "PBK01",
            "firstBuyDt": pos.get("firstBuyDt") or "",
            "qty": str(show),
            "avgPrc": f"{avg:,}" if show > 0 and avg > 0 else "-",
            "curPrc": f"{avg:,}" if show > 0 and avg > 0 else "-",
            "pnl": "0 (0%)",
        }, pos)
    for code, item in by.items():
        if "openText" not in item:
            _annotateHold(item, None)
    return list(by.values())


def visibleHoldRows(mode, accountRows, pbkPositions, tradeOn):
    """실전은 RAM01 매매 등록만. 모의는 계좌 잔고와 PBK 보유를 보여 준다."""
    if str(mode or "").lower() == "real":
        out = []
        for row in accountRows or []:
            if not isinstance(row, dict):
                continue
            if tradeOn(row.get("stkCd")):
                item = dict(row)
                item["algCds"] = ["RAM01"]
                item["algLabel"] = "실계좌"
                out.append(item)
        return out
    return paperHoldRows(accountRows, pbkPositions)


def fillsPath(folder, ymd):
    from pathlib import Path

    return Path(folder) / f"pbk01_fills_{ymd}.jsonl"


def missedPath(folder, ymd):
    from pathlib import Path

    return Path(folder) / f"pbk01_missed_{ymd}.jsonl"
