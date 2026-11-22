"""알고리즘 시그널·매수·매도 이력.

저장은 config/data/algEvents.jsonl 한 줄에 JSON 하나다. DB 스키마는 바꾸지 않는다.
PBK01 은 이 파일과 기존 모의 상태·체결 jsonl 을 같이 읽어 보여 준다.
읽는 경로는 파일을 고치지 않는다.

한 줄 형식:
{"algCd","stkCd","stkNm","kind":"signal|buy|sell","dt":"YYYYMMDD",
 "px","qty","leg","retPct","reason","signalDt"}
"""

from __future__ import annotations

import json
from pathlib import Path

EVENT_NAME = "algEvents.jsonl"


def eventsPath(folder):
    return Path(folder) / EVENT_NAME


def _ymd(raw):
    text = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return text[:8] if len(text) >= 8 else ""


def _num(raw):
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def _int(raw):
    num = _num(raw)
    if num is None:
        return 0
    return int(round(num))


def _code(raw):
    from utils.stkCdUtil import normStkCd

    return normStkCd(raw) or ""


def _readJsonl(path):
    filePath = Path(path)
    if not filePath.is_file():
        return []
    out = []
    try:
        text = filePath.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def eventKey(row):
    """같은 체결을 파일과 원장에서 두 번 세지 않는 키."""
    px = _num((row or {}).get("px"))
    pxText = "" if px is None else f"{px:.4f}"
    return "|".join([
        str((row or {}).get("algCd") or ""),
        _code((row or {}).get("stkCd")),
        str((row or {}).get("kind") or ""),
        _ymd((row or {}).get("dt")),
        str((row or {}).get("leg") or ""),
        pxText,
        str(_int((row or {}).get("qty"))),
    ])


def normalizeEvent(row, algCd="PBK01"):
    if not isinstance(row, dict):
        return None
    kind = str(row.get("kind") or "").strip()
    if kind not in ("signal", "buy", "sell"):
        return None
    code = _code(row.get("stkCd"))
    day = _ymd(row.get("dt") or row.get("signalDt"))
    if not code or not day:
        return None
    px = _num(row.get("px") if row.get("px") is not None else row.get("fillPx"))
    qty = _int(row.get("qty"))
    if kind == "signal":
        px = None
        qty = 0
    return {
        "algCd": str(row.get("algCd") or algCd or ""),
        "stkCd": code,
        "stkNm": str(row.get("stkNm") or ""),
        "kind": kind,
        "dt": day,
        "px": px,
        "qty": qty,
        "leg": row.get("leg") if kind == "buy" else None,
        "retPct": _num(row.get("retPct")),
        "reason": str(row.get("reason") or ""),
        "signalDt": _ymd(row.get("signalDt")),
    }


def mergeEvents(*groups):
    out = []
    seen = set()
    for group in groups:
        for row in group or []:
            item = row if row and row.get("kind") in ("signal", "buy", "sell") and row.get("stkCd") else normalizeEvent(row)
            if not item:
                continue
            key = eventKey(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _legQty(leg):
    if not isinstance(leg, dict):
        return 0
    if leg.get("filledQty") not in (None, ""):
        return max(0, _int(leg.get("filledQty")))
    if leg.get("brokerQty") not in (None, ""):
        return max(0, _int(leg.get("brokerQty")))
    if leg.get("fillPx") not in (None, ""):
        return max(0, _int(leg.get("qty")))
    return 0


def _isStop(reason):
    text = str(reason or "").lower()
    return "손절" in str(reason or "") or "stop" in text


def eventsFromPbkLedger(state, fills=None):
    """모의 상태와 체결 jsonl 에서 시그널·매수·매도를 만든다. 파일은 쓰지 않는다."""
    state = state or {}
    found = []
    for item in list(state.get("candidates") or []) + list(state.get("positions") or []):
        if not isinstance(item, dict):
            continue
        code = _code(item.get("stkCd"))
        if not code:
            continue
        name = str(item.get("stkNm") or "")
        signal = _ymd(item.get("signalDt"))
        if signal:
            found.append({
                "algCd": "PBK01",
                "stkCd": code,
                "stkNm": name,
                "kind": "signal",
                "dt": signal,
                "px": None,
                "qty": 0,
                "leg": None,
                "retPct": None,
                "reason": "signal",
                "signalDt": signal,
            })
        for legNo, key in ((1, "leg1"), (2, "leg2")):
            leg = item.get(key)
            qty = _legQty(leg)
            px = _num((leg or {}).get("fillPx"))
            if px is None:
                px = _num(item.get("avg"))
            day = _ymd((leg or {}).get("ymd") or item.get("firstBuyDt"))
            if qty <= 0 or px is None or not day:
                continue
            found.append({
                "algCd": "PBK01",
                "stkCd": code,
                "stkNm": name,
                "kind": "buy",
                "dt": day,
                "px": px,
                "qty": qty,
                "leg": legNo,
                "retPct": None,
                "reason": "1차" if legNo == 1 else "2차",
                "signalDt": signal,
            })
        sellPx = _num(item.get("sellPx") if item.get("sellPx") is not None else item.get("exitPx"))
        sellDay = _ymd(item.get("sellDt") or item.get("exitDt"))
        closed = bool(item.get("closed") or item.get("released")) and _int(item.get("qty")) <= 0
        if closed and sellPx and sellDay:
            avg = _num(item.get("avg"))
            ret = None if not avg else (sellPx / avg - 1.0) * 100.0
            found.append({
                "algCd": "PBK01",
                "stkCd": code,
                "stkNm": name,
                "kind": "sell",
                "dt": sellDay,
                "px": sellPx,
                "qty": _int(item.get("soldQty") or item.get("qty") or 0) or sum(
                    row["qty"] for row in found if row["stkCd"] == code and row["kind"] == "buy"
                ),
                "leg": None,
                "retPct": ret,
                "reason": str(item.get("exitReason") or item.get("reason") or ""),
                "signalDt": signal,
            })
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "").upper()
        kind = "sell" if side == "SELL" or str(row.get("kind") or "") == "sell" else "buy"
        if kind == "buy" and row.get("leg") in (None, "") and side != "BUY":
            kind = "buy"
        day = _ymd(row.get("dt") or row.get("ymd"))
        code = _code(row.get("stkCd"))
        px = _num(row.get("px") if row.get("px") is not None else row.get("fillPx"))
        qty = _int(row.get("qty") if row.get("qty") not in (None, "") else row.get("brokerQty"))
        if not code or not day or px is None or qty <= 0:
            continue
        if kind == "buy" and row.get("leg") in (None, "") and side not in ("BUY", ""):
            continue
        if kind == "buy" and side == "SELL":
            kind = "sell"
        found.append({
            "algCd": str(row.get("algCd") or "PBK01"),
            "stkCd": code,
            "stkNm": str(row.get("stkNm") or ""),
            "kind": kind,
            "dt": day,
            "px": px,
            "qty": qty,
            "leg": _int(row.get("leg")) or None if kind == "buy" else None,
            "retPct": _num(row.get("retPct")),
            "reason": str(row.get("reason") or row.get("legName") or ""),
            "signalDt": _ymd(row.get("signalDt")),
        })
    return mergeEvents(found)


def loadDeskEvents(folder=None):
    """저장 이벤트와 PBK 모의 원장을 합친다. 쓰기는 하지 않는다."""
    from utils.appPaths import configDataDir
    from utils.pbk01Store import latestByKind

    base = Path(folder) if folder else Path(configDataDir())
    stored = [normalizeEvent(row) for row in _readJsonl(eventsPath(base))]
    state = {}
    statePath = base / "pbk01_state.jsonl"
    if statePath.is_file():
        state = latestByKind(statePath, "state") or {}
    fills = []
    for path in sorted(base.glob("pbk01_fills_*.jsonl")):
        fills.extend(_readJsonl(path))
    return mergeEvents(stored, eventsFromPbkLedger(state, fills))


def eventsForChart(code, folder=None):
    cd = _code(code)
    if not cd:
        return []
    return [row for row in loadDeskEvents(folder) if row.get("stkCd") == cd]


def _barDay(bar):
    for key in ("tradeDt", "dt", "ymd", "date"):
        day = _ymd((bar or {}).get(key))
        if day:
            return day
    return ""


def _barPx(bar, *keys):
    for key in keys:
        num = _num((bar or {}).get(key))
        if num is not None and num > 0:
            return num
    return None


def _sma(closes, idx, period):
    if idx + 1 < period:
        return None
    window = closes[idx + 1 - period:idx + 1]
    if len(window) != period or any(val is None or val <= 0 for val in window):
        return None
    return sum(window) / float(period)


def firstMaTouches(bars, signalDt, exitDt=""):
    """기준봉 다음 날부터 5이평·20이평을 처음 터치한 봉. 화면용이며 저장하지 않는다."""
    signal = _ymd(signalDt)
    exitDay = _ymd(exitDt)
    closes = []
    lows = []
    days = []
    for bar in bars or []:
        days.append(_barDay(bar))
        closes.append(_barPx(bar, "close"))
        low = _barPx(bar, "low", "close")
        lows.append(low)
    found = {5: None, 20: None}
    for idx, day in enumerate(days):
        if not day or not signal or day <= signal:
            continue
        if exitDay and day > exitDay:
            break
        low = lows[idx]
        if low is None:
            continue
        for period in (5, 20):
            if found[period] is not None:
                continue
            line = _sma(closes, idx, period)
            if line is None or low > line:
                continue
            found[period] = {"line": period, "dt": day, "px": line}
    return [found[5], found[20]]


def _fmtPx(px):
    num = _num(px)
    if num is None:
        return "-"
    return f"{int(round(num)):,}"


def _dashDay(day):
    text = _ymd(day)
    if len(text) != 8:
        return "-"
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def exitLabel(reason, retPct):
    """매도 +4.3% 와 손절 -20.0% 를 구분한다."""
    ret = _num(retPct)
    if _isStop(reason):
        if ret is None:
            return "손절"
        return f"손절 {ret:.1f}%"
    if ret is None:
        return "매도"
    return f"매도 {ret:+.1f}%"


def reasonText(reason):
    text = str(reason or "").strip()
    if _isStop(text):
        return "손절"
    if text in ("f+10", "f+10 시가") or text.startswith("f+10"):
        return "f+10 시가"
    return text or "-"


def _weightedAvg(buys):
    cost = 0.0
    qty = 0
    for row in buys or []:
        px = _num(row.get("px"))
        q = _int(row.get("qty"))
        if px is None or q <= 0:
            continue
        cost += px * q
        qty += q
    if qty <= 0:
        return None, 0
    return cost / qty, qty


def _holdDays(buyDt, sellDt):
    from datetime import datetime

    a = _ymd(buyDt)
    b = _ymd(sellDt)
    if len(a) != 8 or len(b) != 8:
        return None
    try:
        return (datetime.strptime(b, "%Y%m%d") - datetime.strptime(a, "%Y%m%d")).days
    except ValueError:
        return None


def closedRows(events):
    """매도가 있는 종목만. 관심종목에서 빠져도 이력으로 남긴다."""
    groups = {}
    for row in events or []:
        if not isinstance(row, dict) or not row.get("stkCd"):
            continue
        groups.setdefault((str(row.get("algCd") or ""), row["stkCd"]), []).append(row)
    out = []
    for (alg, code), items in groups.items():
        sells = [row for row in items if row.get("kind") == "sell"]
        if not sells:
            continue
        buys = [row for row in items if row.get("kind") == "buy"]
        signals = [row for row in items if row.get("kind") == "signal"]
        sell = max(sells, key=lambda row: row.get("dt") or "")
        buyDay = min((row.get("dt") or "99999999") for row in buys) if buys else ""
        avg, buyQty = _weightedAvg(buys)
        sellPx = _num(sell.get("px"))
        sellQty = _int(sell.get("qty")) or buyQty
        ret = _num(sell.get("retPct"))
        if ret is None and avg and sellPx:
            ret = (sellPx / avg - 1.0) * 100.0
        pnl = None
        if avg and sellPx and sellQty:
            pnl = (sellPx - avg) * sellQty
        name = ""
        for row in items:
            if row.get("stkNm"):
                name = row["stkNm"]
                break
        signal = signals[0]["dt"] if signals else (sell.get("signalDt") or "")
        days = _holdDays(buyDay, sell.get("dt"))
        retText = "-" if ret is None else f"{ret:+.1f}%"
        pnlText = "-" if pnl is None else f"{int(round(pnl)):+,}"
        out.append({
            "algCd": alg,
            "stkCd": code,
            "stkNm": name or code,
            "signalDt": signal,
            "signalText": _dashDay(signal),
            "buyDt": buyDay,
            "avg": avg,
            "buyText": f"{_dashDay(buyDay)} · {_fmtPx(avg)}",
            "sellDt": sell.get("dt") or "",
            "sellPx": sellPx,
            "sellText": f"{_dashDay(sell.get('dt'))} · {_fmtPx(sellPx)}",
            "holdDays": "" if days is None else str(days),
            "retPct": ret,
            "pnl": pnl,
            "pnlText": f"{retText} / {pnlText}",
            "reason": reasonText(sell.get("reason")),
        })
    out.sort(key=lambda row: (row.get("sellDt") or "", row.get("stkCd") or ""), reverse=True)
    return out


def legendLine(events):
    algs = []
    for row in events or []:
        alg = str((row or {}).get("algCd") or "")
        if alg and alg not in algs:
            algs.append(alg)
    if not algs:
        return ""
    return "   ".join(f"{alg} 기준봉·눌림·매수·매도" for alg in algs)


def _mark(idx, kind, px, label, color, algCd):
    return {
        "i": idx,
        "kind": kind,
        "px": px,
        "label": label,
        "color": color,
        "algCd": algCd,
    }


def chartMarks(events, bars, visStart=0, visCount=None):
    """보이는 일봉 인덱스 기준 마커. 주·월 봉에는 날짜가 없으면 빠진다."""
    rows = list(bars or [])
    if visCount is None:
        visCount = len(rows)
    start = max(0, int(visStart))
    end = start + max(0, int(visCount))
    index = {}
    for i, bar in enumerate(rows):
        day = _barDay(bar)
        if day and day not in index:
            index[day] = i
    out = []
    exitDay = ""
    for row in events or []:
        if row.get("kind") == "sell" and row.get("dt"):
            exitDay = max(exitDay, row["dt"])
    for row in events or []:
        if row.get("kind") != "signal":
            continue
        for touch in firstMaTouches(rows, row.get("dt"), exitDay):
            if not touch:
                continue
            idx = index.get(touch["dt"])
            if idx is None or idx < start or idx >= end:
                continue
            line = touch["line"]
            color = "#00897B" if line == 5 else "#1565C0"
            out.append(_mark(idx - start, "algTouch", touch["px"], f"{line}이평", color, row.get("algCd")))
    for row in events or []:
        kind = row.get("kind")
        if kind not in ("signal", "buy", "sell"):
            continue
        idx = index.get(row.get("dt") or "")
        if idx is None or idx < start or idx >= end:
            continue
        alg = row.get("algCd") or ""
        if kind == "signal":
            bar = rows[idx]
            px = _barPx(bar, "high", "close") or 0
            out.append(_mark(idx - start, "algSignal", px, f"{alg} 기준", "#6A1B9A", alg))
        elif kind == "buy":
            leg = _int(row.get("leg")) or 1
            label = f"매수{leg} {_fmtPx(row.get('px'))}×{_int(row.get('qty'))}"
            out.append(_mark(idx - start, "algBuy", row.get("px") or 0, label, "#C62828", alg))
        else:
            label = exitLabel(row.get("reason"), row.get("retPct"))
            color = "#E65100" if _isStop(row.get("reason")) else "#1565C0"
            out.append(_mark(idx - start, "algSell", row.get("px") or 0, label, color, alg))
    return out
