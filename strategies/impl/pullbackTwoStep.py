"""PBK01 실적 장대양봉 눌림 2분할. 주문은 하지 않는 순수 함수.

장대양봉 당일에는 사지 않는다. 청산은 첫 매수일 f 를 0번째로 세어 f+10 거래일 시가다.
하락장 비중, 급락 다음날 휴식, KODEX 주차는 넣지 않는다.
"""

from __future__ import annotations

import math
import re

ALG_CD = "PBK01"
STRAT_ID = "pullbackTwoStep_v1"
ALG_VER = 1
NAME_KR = "실적 장대양봉 눌림목"
ENGLISH_NAME = "Earnings Pullback Two Step"

CAPITAL_KRW = 30_000_000
SLOT_COUNT = 5
LEG1_PCT = 60
LEG2_PCT = 40
CANDLE_WINDOW = 10
BUY_WINDOW = 40
HOLD_DAYS = 10
MIN_DAY_PCT = 10.0
VOL_MULT = 3.0
MIN_AMT20 = 300_000_000
STOP_RATIO = 0.80
STOP_HM = "151500"

_KEEP = re.compile(r"영업\(잠정\)실적|영업실적|잠정실적|손익구조")
_DROP = re.compile(r"전망|예고|해지|철회|취소|정정신고|소송|경영권|유상|전환사채|최대주주변경|담보")


class PaperOnlyError(RuntimeError):
    """paperOnly 가 아니거나 모의 호스트가 아니면 주문을 보내지 않는다."""


def disclosureAccepted(title):
    """실적 공시만 남긴다. 전망·예고와 제외 단어가 있으면 버린다."""
    text = str(title or "")
    if not _KEEP.search(text):
        return False
    if _DROP.search(text):
        return False
    return True


def _ymd(raw):
    text = str(raw or "").strip().replace("-", "")[:8]
    return text if len(text) == 8 and text.isdigit() else ""


def _f(bar, *keys):
    if not isinstance(bar, dict):
        return None
    for key in keys:
        if bar.get(key) is None:
            continue
        try:
            return float(bar.get(key))
        except (TypeError, ValueError):
            return None
    return None


def _barDt(bar):
    return _ymd(bar.get("tradeDt") or bar.get("TRADE_DT"))


def _amount(bar):
    amt = _f(bar, "amount", "AMT", "trdePrca")
    if amt is not None:
        return amt
    close = _f(bar, "close", "CLOSE_PRC")
    vol = _f(bar, "volume", "TRDE_QTY")
    if close is None or vol is None:
        return None
    return close * vol


def _halted(bar):
    if not isinstance(bar, dict):
        return True
    flag = bar.get("halted", bar.get("suspended", bar.get("trdeStop")))
    if flag in (True, 1, "Y", "y", "1"):
        return True
    return False


def floorQty(money, price):
    """금액/가격 내림. 0 이하면 0주."""
    try:
        won = float(money)
        px = float(price)
    except (TypeError, ValueError):
        return 0
    if won <= 0 or px <= 0:
        return 0
    return int(won // px)


def floorPx(price):
    try:
        px = float(price)
    except (TypeError, ValueError):
        return 0
    if px <= 0:
        return 0
    return int(math.floor(px))


def slotBudget(capital=CAPITAL_KRW, slots=SLOT_COUNT):
    slots = int(slots or 0)
    if slots <= 0:
        return 0
    return int(int(capital) // slots)


def legMoney(budget, pct):
    return int(int(budget) * int(pct) // 100)


def priorMean(bars, idx, period, field):
    """idx 봉은 빼고 직전 period 개. 부족하면 None."""
    start = idx - int(period)
    if start < 0:
        return None
    vals = []
    for bar in bars[start:idx]:
        if field == "amount":
            num = _amount(bar)
        else:
            num = _f(bar, field, "close" if field == "close" else field)
        if num is None:
            return None
        vals.append(num)
    if len(vals) != int(period):
        return None
    return sum(vals) / float(period)


def amountMeanEnding(bars, idx, period=20):
    """idx 봉을 포함한 최근 period 일 평균 거래대금."""
    start = idx - int(period) + 1
    if start < 0:
        return None
    vals = []
    for bar in bars[start:idx + 1]:
        num = _amount(bar)
        if num is None:
            return None
        vals.append(num)
    if len(vals) != int(period):
        return None
    return sum(vals) / float(period)


def isLongBull(bar, prevClose, avgVol):
    """거래정지 아님, +10% 이상, 양봉, 거래량 ≥ 3 × 직전 20일 평균."""
    if _halted(bar):
        return False
    close = _f(bar, "close", "CLOSE_PRC")
    openPx = _f(bar, "open", "OPEN_PRC")
    vol = _f(bar, "volume", "TRDE_QTY")
    if None in (close, openPx, vol, prevClose, avgVol):
        return False
    if prevClose <= 0 or avgVol <= 0:
        return False
    if (close / prevClose - 1.0) * 100.0 < MIN_DAY_PCT:
        return False
    if not (close > openPx):
        return False
    if vol < VOL_MULT * avgVol:
        return False
    return True


def _sortBars(bars):
    rows = [bar for bar in (bars or []) if _barDt(bar)]
    rows.sort(key=_barDt)
    out = []
    seen = set()
    for bar in rows:
        day = _barDt(bar)
        if day in seen:
            continue
        seen.add(day)
        out.append(bar)
    return out


def findCandleEvent(bars, disclosures, stkCd, asOf=None):
    """종목당 첫 실적 장대양봉. asOf 이후 봉과 공시는 보지 않는다."""
    rows = _sortBars(bars)
    limit = _ymd(asOf)
    if limit:
        rows = [bar for bar in rows if _barDt(bar) <= limit]
    days = [_barDt(bar) for bar in rows]
    if not days:
        return None
    accepted = []
    for item in disclosures or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("reportNm") or ""
        if not disclosureAccepted(title):
            continue
        day = _ymd(item.get("date") or item.get("rceptDt") or item.get("publishedAt"))
        if not day or (limit and day > limit):
            continue
        accepted.append((day, str(title), str(item.get("rceptNo") or "")))
    accepted.sort(key=lambda row: (row[0], row[2], row[1]))
    for receipt, title, rcept in accepted:
        later = [day for day in days if day >= receipt]
        if not later:
            continue
        t0 = later[0]
        i0 = days.index(t0)
        window = days[i0:i0 + CANDLE_WINDOW]
        for day in window:
            idx = days.index(day)
            if idx < 1:
                continue
            avgVol = priorMean(rows, idx, 20, "volume")
            if avgVol is None:
                volKey = priorMean(rows, idx, 20, "TRDE_QTY")
                avgVol = volKey
            if avgVol is None:
                vals = []
                ok = True
                for bar in rows[idx - 20:idx] if idx >= 20 else []:
                    num = _f(bar, "volume", "TRDE_QTY")
                    if num is None:
                        ok = False
                        break
                    vals.append(num)
                avgVol = (sum(vals) / 20.0) if ok and len(vals) == 20 else None
            amt = amountMeanEnding(rows, idx, 20)
            prev = _f(rows[idx - 1], "close", "CLOSE_PRC")
            if avgVol is None or amt is None or amt < MIN_AMT20:
                continue
            if not isLongBull(rows[idx], prev, avgVol):
                continue
            return {
                "stkCd": str(stkCd or ""),
                "signalDt": day,
                "signalIdx": idx,
                "t0": t0,
                "receiptDt": receipt,
                "title": title,
                "rceptNo": rcept,
                "close": _f(rows[idx], "close", "CLOSE_PRC"),
            }
    return None


def inBuyWindow(signalIdx, todayIdx, window=BUY_WINDOW):
    """장대양봉 다음 거래일부터 t+window 까지. 신호 당일은 제외."""
    try:
        sig = int(signalIdx)
        cur = int(todayIdx)
    except (TypeError, ValueError):
        return False
    return sig < cur <= sig + int(window)


def exitIndex(firstIdx, holdDays=HOLD_DAYS):
    """f 를 0번째로 세면 holdDays 번째 뒤 봉. f+10 이지 f+11 이 아니다."""
    return int(firstIdx) + int(holdDays)


def exitOn(days, firstIdx, holdDays=HOLD_DAYS):
    j = exitIndex(firstIdx, holdDays)
    if j < 0 or j >= len(days or []):
        return ""
    return str(days[j])


def priorMa(bars, idx, period):
    return priorMean(bars, idx, period, "close")


def touchFill(openPx, maPx, lowPx):
    """저가가 이평 이하면 min(시가, 이평). 아니면 None."""
    if None in (openPx, maPx, lowPx):
        return None
    if float(lowPx) > float(maPx):
        return None
    return min(float(openPx), float(maPx))


def planTouches(bars, idx, budget=None):
    """그날 저가 기준 1차 MA5 60%, 2차 MA20 40%. 같은 날이면 둘 다."""
    budget = slotBudget() if budget is None else int(budget)
    if idx < 0 or idx >= len(bars or []):
        return {"leg1": None, "leg2": None}
    bar = bars[idx]
    openPx = _f(bar, "open", "OPEN_PRC")
    lowPx = _f(bar, "low", "LOW_PRC")
    ma5 = priorMa(bars, idx, 5)
    ma20 = priorMa(bars, idx, 20)
    out = {"leg1": None, "leg2": None, "ma5": ma5, "ma20": ma20}
    px1 = touchFill(openPx, ma5, lowPx)
    if px1 is not None:
        money = legMoney(budget, LEG1_PCT)
        out["leg1"] = {
            "leg": 1,
            "pct": LEG1_PCT,
            "ma": ma5,
            "fillPx": px1,
            "limitPx": floorPx(px1),
            "qty": floorQty(money, px1),
            "money": money,
        }
    px2 = touchFill(openPx, ma20, lowPx)
    if px2 is not None:
        money = legMoney(budget, LEG2_PCT)
        out["leg2"] = {
            "leg": 2,
            "pct": LEG2_PCT,
            "ma": ma20,
            "fillPx": px2,
            "limitPx": floorPx(px2),
            "qty": floorQty(money, px2),
            "money": money,
        }
    return out


def allocateSlotFills(rows, slotLimit=SLOT_COUNT):
    """같은 날 터치면 장대양봉이 오래된 순, 같으면 코드순. 슬롯이 없으면 신규만 거절."""
    ordered = sorted(
        list(rows or []),
        key=lambda row: (str(row.get("signalDt") or ""), str(row.get("stkCd") or "")),
    )
    held = set()
    for row in ordered:
        if row.get("hasLeg1") or row.get("hasLeg2"):
            held.add(str(row.get("stkCd") or ""))
    free = int(slotLimit) - len(held)
    out = []
    for row in ordered:
        code = str(row.get("stkCd") or "")
        want1 = bool(row.get("hitLeg1")) and not row.get("hasLeg1")
        want2 = bool(row.get("hitLeg2")) and not row.get("hasLeg2")
        if not want1 and not want2:
            continue
        owns = code in held
        if not owns:
            if free <= 0:
                out.append({"stkCd": code, "taken": False, "reason": "NO_SLOT", "leg1": False, "leg2": False})
                continue
            free -= 1
            held.add(code)
        out.append({"stkCd": code, "taken": True, "reason": "SLOT", "leg1": want1, "leg2": want2})
    return out


def weightedAvg(fills):
    cost = 0.0
    qty = 0
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        q = int(row.get("qty") or 0)
        if q <= 0 or row.get("fillPx") is None:
            continue
        cost += q * float(row["fillPx"])
        qty += q
    if qty <= 0:
        return None, 0
    return cost / qty, qty


def stopTriggered(nowHm, price, avg, stopHm=STOP_HM):
    """15:15 에만 보고, 현재가 ≤ 평단×0.80 이면 손절. 장중 저가는 쓰지 않는다."""
    hm = str(nowHm or "").zfill(6)[-6:]
    stop = str(stopHm or STOP_HM).zfill(6)[-6:]
    if hm[:4] != stop[:4]:
        return False
    try:
        px = float(price)
        base = float(avg)
    except (TypeError, ValueError):
        return False
    if base <= 0 or px <= 0:
        return False
    return px <= base * STOP_RATIO


def stopLine(avg):
    try:
        return float(avg) * STOP_RATIO
    except (TypeError, ValueError):
        return None


def candidateRows(events, positions, barsByCode, today):
    """1차 매수 전이고 신호 다음 날부터 t+40 안에 있는 종목."""
    day = _ymd(today)
    held = set()
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        if pos.get("leg1") or int(pos.get("qty") or 0) > 0:
            held.add(str(pos.get("stkCd") or ""))
    out = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        code = str(event.get("stkCd") or "")
        if not code or code in held:
            continue
        bars = _sortBars((barsByCode or {}).get(code) or [])
        days = [_barDt(bar) for bar in bars]
        sig = _ymd(event.get("signalDt"))
        if sig not in days or not day:
            continue
        sigI = days.index(sig)
        if day not in days:
            if day <= sig:
                continue
            future = [item for item in days if item < day]
            if not future:
                continue
            # 오늘 봉이 아직 없어도, 확정 봉 기준으로 창 안이면 후보
            lastI = days.index(future[-1])
            if lastI + 1 > sigI + BUY_WINDOW:
                continue
            todayI = lastI + 1
        else:
            todayI = days.index(day)
            if not inBuyWindow(sigI, todayI):
                continue
        if todayI > sigI + BUY_WINDOW:
            continue
        ma5 = priorMean(bars, len(bars), 5, "close") if day not in days else priorMa(bars, todayI, 5)
        ma20 = priorMean(bars, len(bars), 20, "close") if day not in days else priorMa(bars, todayI, 20)
        if day in days:
            ma5 = priorMa(bars, todayI, 5)
            ma20 = priorMa(bars, todayI, 20)
        expireI = sigI + BUY_WINDOW
        expire = days[expireI] if expireI < len(days) else ""
        out.append({
            "stkCd": code,
            "signalDt": sig,
            "ma5": ma5,
            "ma20": ma20,
            "expireDt": expire,
            "expireIdx": expireI,
        })
    out.sort(key=lambda row: (row["signalDt"], row["stkCd"]))
    return out


def touchesFromCandidates(candidates, quotes, owned=None):
    """장중 터치는 계산된 후보(와 이미 잡은 슬롯)만 본다. 목록 밖 종목은 무시한다."""
    owned = {str(code) for code in (owned or [])}
    wanted = []
    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stkCd") or "")
        if not code or code in owned:
            continue
        quote = (quotes or {}).get(code) or {}
        try:
            px = float(quote.get("price"))
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        hit1 = row.get("ma5") is not None and px <= float(row["ma5"])
        hit2 = row.get("ma20") is not None and px <= float(row["ma20"])
        if not hit1 and not hit2:
            continue
        wanted.append({
            "stkCd": code,
            "signalDt": row.get("signalDt") or "",
            "hitLeg1": hit1,
            "hitLeg2": hit2,
            "hasLeg1": False,
            "hasLeg2": False,
            "price": px,
        })
    allowed = {row["stkCd"] for row in allocateSlotFills(wanted) if row.get("taken")}
    out = []
    for row in wanted:
        if row["stkCd"] not in allowed:
            continue
        out.append({
            "stkCd": row["stkCd"],
            "leg1": row["hitLeg1"],
            "leg2": row["hitLeg2"],
            "price": row["price"],
            "source": "candidate",
        })
    return out


def formatCandidateLog(rows):
    bits = []
    for row in rows or []:
        ma5 = "-" if row.get("ma5") is None else f"{float(row['ma5']):.0f}"
        ma20 = "-" if row.get("ma20") is None else f"{float(row['ma20']):.0f}"
        bits.append(f"{row.get('stkCd')} MA5 {ma5} MA20 {ma20} 만료 {row.get('expireDt') or '-'}")
    body = " · ".join(bits)
    n = len(rows or [])
    if body:
        return f"[PBK01] 후보 {n}건 {body}"
    return f"[PBK01] 후보 {n}건"


def formatSlotLog(positions, slotLimit=SLOT_COUNT):
    lines = []
    used = 0
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        if int(pos.get("qty") or 0) <= 0 and not pos.get("leg1") and not pos.get("leg2"):
            continue
        used += 1
        leg1 = pos.get("leg1") or {}
        leg2 = pos.get("leg2") or {}
        avg = pos.get("avg")
        avgTxt = "-" if avg is None else f"{float(avg):.0f}"
        stop = stopLine(avg)
        stopTxt = "-" if stop is None else f"{stop:.0f}"
        lines.append(
            f"{pos.get('stkCd')} 1차 {int(leg1.get('qty') or 0)}주"
            f" 2차 {int(leg2.get('qty') or 0)}주"
            f" 평단 {avgTxt} f {pos.get('firstBuyDt') or '-'}"
            f" 청산 {pos.get('exitDt') or '-'} 손절 {stopTxt}"
        )
    head = f"[PBK01] 슬롯 {used}/{int(slotLimit)}"
    if not lines:
        return head
    return head + " " + " · ".join(lines)


def submitPbkOrder(action, session, broker, paperOnly=True, log=None):
    """모의가 아니면 예외. 브로커는 호출하지 않는다."""
    def _log(text):
        if log is not None:
            log(text)
        else:
            try:
                from utils.termLog import termLog

                termLog(text)
            except Exception:
                pass

    if paperOnly is not True:
        _log("[PBK01] paperOnly가 아니라 주문을 거부")
        raise PaperOnlyError("PBK01 paperOnly")
    if not isinstance(session, dict) or str(session.get("mode") or "").lower() != "paper":
        _log("[PBK01] 모의 세션이 아니라 주문을 거부")
        raise PaperOnlyError("PBK01 paper session")
    host = str(session.get("host") or "")
    try:
        from config.config import paper_host_url, real_host_url
    except Exception:
        paper_host_url, real_host_url = "", ""
    paperHost = str(paper_host_url or "")
    realHost = str(real_host_url or "")
    if not host or not paperHost or host != paperHost or host == realHost:
        _log("[PBK01] 모의 호스트가 아니라 주문을 거부")
        raise PaperOnlyError("PBK01 paper host")
    if "mockapi.kiwoom.com" not in host:
        _log("[PBK01] 모의 호스트가 아니라 주문을 거부")
        raise PaperOnlyError("PBK01 paper host")
    if broker is None:
        return {"ok": False, "sent": False, "reason": "NO_BROKER"}
    return broker(action, session)


def algVerHistPayload(gitHash="", ymd="2026-11-19"):
    """AI_ALG_VER_HIST 에 넣을 값. 이 함수는 DB 를 열지 않는다."""
    return {
        "algCd": ALG_CD,
        "stratId": STRAT_ID,
        "algVer": ALG_VER,
        "ymd": ymd,
        "note": "v1 실적 장대양봉 눌림 2분할. 모의 전용. 주문 기본 비활성.",
        "gitHash": str(gitHash or "")[:16],
        "src": "manual",
    }


def loadPbkOptions(spec=None):
    if spec is None:
        try:
            from strategies.specLoader import loadStratSpec

            spec = loadStratSpec(STRAT_ID) or {}
        except Exception:
            spec = {}
    params = (spec or {}).get("params") or {}
    capital = int(params.get("capitalKrw") or CAPITAL_KRW)
    slots = int(params.get("slotCount") or SLOT_COUNT)
    return {
        "enabled": (spec or {}).get("enabled") is True and params.get("paperOnly") is True,
        "paperOnly": params.get("paperOnly") is True,
        "capitalKrw": capital,
        "slotCount": slots,
        "slotKrw": int(params.get("slotKrw") or slotBudget(capital, slots)),
        "stopHm": str(params.get("stopHm") or STOP_HM),
        "scanHm": str(params.get("scanHm") or "08:40"),
    }
