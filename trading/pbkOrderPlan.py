"""PBK01 모의 주문 계획. 브로커를 부르지 않는다.

백테스트는 터치 시 이평, 시가가 이평 아래면 시가에 체결한다고 본다.
모의 주문은 그 가격으로 소급하지 않고, 주문 시점의 현재가로만 기록한다.
현재가가 이평보다 pbkChaseMaxPct(기본 2%) 를 넘으면 당일 터치를 놓친 것으로 둔다.
"""

from __future__ import annotations

PBK_CHASE_MAX_PCT = 2.0
BUY_FROM = "090000"
BUY_UNTIL = "151500"


def pbkChaseMaxPct(settings=None):
    """추격 한도(%). 키가 없으면 2. 파일을 쓰지 않는다."""
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
    if not isinstance(pos, dict):
        return False
    pending = (pos.get("pending") or {}).get(str(leg))
    if pending:
        return True
    legRow = pos.get(f"leg{leg}")
    if isinstance(legRow, dict):
        try:
            if int(legRow.get("qty") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
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


def _slotUsed(pos):
    if not isinstance(pos, dict):
        return False
    if _legBlocked(pos, 1) or _legBlocked(pos, 2):
        return True
    try:
        return int(pos.get("qty") or 0) > 0
    except (TypeError, ValueError):
        return False


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
):
    """주문·놓침·건너뜀. 브로커는 호출하지 않고, 과거 가격으로 체결을 만들지 않는다."""
    from strategies.impl.pullbackTwoStep import (
        LEG1_PCT,
        LEG2_PCT,
        floorPx,
        floorQty,
        legMoney,
        stopTriggered,
    )

    chase = PBK_CHASE_MAX_PCT if chaseMaxPct is None else float(chaseMaxPct)
    textHm = _hm(hm)
    byPos = _posByCode(positions)
    actions = []
    missed = []
    skips = []
    used = {code for code, pos in byPos.items() if _slotUsed(pos)}

    def _skip(code, reason, leg=None):
        skips.append({"stkCd": code, "reason": reason, "leg": leg})

    def _miss(code, name, leg, lineName, line, basis, touchPx, nowPx):
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
            "reason": "당일 터치 놓침",
        })

    # 매도를 먼저 낸다. f+10 시가, 15:15 손절.
    for code, pos in byPos.items():
        if pos.get("closed") or (pos.get("pending") or {}).get("sell"):
            _skip(code, "매도대기")
            continue
        try:
            qty = int(pos.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
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
        return {"actions": actions, "missed": missed, "skips": skips, "exitHint": ""}

    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        if not code:
            continue
        name = str(row.get("stkNm") or row.get("name") or code)
        pos = byPos.get(code) or {}
        quote = (quotes or {}).get(code) or {}
        if not quoteTradable(quote, now):
            _skip(code, "시세 대기")
            continue
        price = absPx(quote.get("price") or quote.get("curPrc"))
        low = absPx(quote.get("low"))
        if price is None:
            _skip(code, "시세 대기")
            continue
        owns = code in used
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
        for leg, lineName, line, basis, pct in want:
            gap = gapPct(price, line)
            if gap is not None and gap > chase:
                touchPx = low if basis == "저가" else price
                _miss(code, name, leg, lineName, absPx(line), basis, touchPx, price)
                continue
            limit = floorPx(price)
            qty = floorQty(legMoney(int(slotKrw), pct), price)
            if qty < 1 or limit <= 0:
                _skip(code, "수량0", leg)
                continue
            actions.append({
                "side": "BUY",
                "stkCd": code,
                "stkNm": name,
                "leg": leg,
                "qty": qty,
                "limitPx": limit,
                "fillPx": price,
                "trdeTp": "0",
                "reason": lineName,
                "signalDt": row.get("signalDt") or "",
            })
    return {"actions": actions, "missed": missed, "skips": skips}


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
    leg = {
        "qty": qty,
        "fillPx": fillPx,
        "limitPx": action.get("limitPx"),
        "ordNo": str(ordNo or ""),
        "ymd": str(ymd or ""),
    }
    found[f"leg{legKey}"] = leg
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
        ahead = tradingDaysAfter(found["firstBuyDt"], calendar, HOLD_DAYS) if calendar else []
        found["exitDt"] = ahead[-1] if len(ahead) >= HOLD_DAYS else ""
    if not found.get("stkNm"):
        found["stkNm"] = action.get("stkNm") or code
    return rows


def missedPath(folder, ymd):
    from pathlib import Path

    return Path(folder) / f"pbk01_missed_{ymd}.jsonl"
