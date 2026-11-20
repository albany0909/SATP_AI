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


def applyPollPrice(info, price, tm):
    """0B 가 이미 있으면 폴링 값으로 덮지 않는다."""
    current = dict(info or {})
    if str(current.get("src") or "") == "0B" and current.get("curPrc"):
        return current, False
    try:
        px = abs(float(str(price).replace(",", "").lstrip("+")))
    except (TypeError, ValueError):
        return current, False
    if px <= 0:
        return current, False
    current["curPrc"] = px
    current["src"] = "poll"
    current["tm"] = str(tm or "")
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
    return f"[PBK01] 시세 {fresh}/{total} 틱 · {tail}"
