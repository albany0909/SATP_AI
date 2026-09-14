"""
매매창 거래(검증) 로그.
워커 스레드에서 Signal 로 GUI 스레드에 한 줄을 넘긴다.
"""

from datetime import datetime

from PySide6.QtCore import QObject, Signal

from utils.termLog import termLog

_bus = None

_EVT_LABEL = {
    "BUY": "매수",
    "SELL": "매도",
    "BUY_ORD": "매수주문",
    "SELL_ORD": "매도주문",
    "BUY_SKIP": "매수스킵",
    "BUY_CNCL": "매수취소",
    "BUY_ORD_CNCL": "매수주문취소",
    "BUY_FAIL": "매수실패",
    "SELL_FAIL": "매도실패",
    "RANK_IN": "관심추가",
    "WATCH_OUT": "관심해제",
}

# 거래탭에 넣을 주문·체결·실패 이벤트만
DAY_TRADE_EVT_TPS = (
    "BUY",
    "SELL",
    "BUY_ORD",
    "SELL_ORD",
    "BUY_FAIL",
    "SELL_FAIL",
    "BUY_CNCL",
    "BUY_ORD_CNCL",
)

_REASON_LABEL = {
    "AFTER_1400": "14시이후",
    "BELOW_SMA20": "SMA20이하",
    "WAIT_SMA20": "20선3분대기",
    "WAIT_BAR3": "20선3분돌파대기",
    "WAIT_BULL": "20선3분양봉대기",
    "LIMIT_UNFILL_BELOW_MA": "지정가미체결·이평아래",
    "LIMIT_UNFILL_5M": "5분미체결취소",
    "LIMIT_UNFILL_3M": "3분미체결→시장가",
    "LIMIT_UNFILL_10M": "10분미체결취소",
    "LIMIT_CNCL_FAIL": "지정가취소실패",
    "QTY0": "수량0",
    "FILLED": "이미매수",
    "rank_15m": "15분편입",
    "rank_16h": "16시순위편입",
    "univ_replace": "대상교체",
    "SMA5": "5일선",
    "SMA10": "10일선",
    "SMA15": "15일선",
    "STOP": "전량청산",
    "TAKE": "전량청산",
    "FLAT": "당일미수익",
    "eod_flat_no_profit": "당일미수익15:18",
    "flat_1515_0_3": "15:15 0~3%전량",
    "flat_1515_le3": "15:15 0~3%전량",
    "flat_next_closed_0_3": "15:15 0~3%전량",
    "15:15 0~3% 전량": "15:15 0~3%전량",
    "15:15 3%이하 전량": "15:15 0~3%전량",
    "rsi_tp_70": "익절 RSI70",
    "tp1_1.5pct": "1차익절 +1.5%",
    "tp1_1.2pct": "1차익절 +1.2%",
    "tp2_2pct": "2차익절 +2%",
    "tp3_3pct": "3차익절 +3%",
    "tp3_4pct": "3차익절 +4%",
    "tp3_5pct": "3차익절 +5%",
    "tp4_7pct": "4차익절 +7%",
    "tp4_10pct": "4차익절 +10%",
    "tp3_15pct": "3차익절 +15%",
    "tp3_rsi_65": "3차 RSI65",
    "after_half_tp_20pct": "3차익절 +20%",
    "after_half_tp_15pct": "3차익절 +15%",
    "after_half_rsi_65": "3차 RSI65",
    "절반후익절 +20%": "3차익절 +20%",
    "절반후익절 +15%": "3차익절 +15%",
    "절반후 RSI65": "3차 RSI65",
    "익절후손절 0%": "익절후손절 0%",
    "2차후손절 0%": "익절후손절 0%",
    "1차익절 +1.2%": "1차익절 +1.2%",
    "3차익절 +3%": "3차익절 +3%",
}


def _pick(row, *keys, default=""):
    for k in keys:
        if row is None:
            break
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return default


def _fmtTm(raw):
    text = str(raw or "").replace(":", "").strip()
    if len(text) >= 6 and text[:6].isdigit():
        t = text[:6]
        return f"{t[0:2]}:{t[2:4]}:{t[4:6]}"
    return datetime.now().strftime("%H:%M:%S")


def _fmtPx(raw):
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        return f"{abs(int(float(raw))):,}원"
    except (TypeError, ValueError):
        return ""


def _fmtQty(raw):
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n}주"


def _reasonTxt(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _REASON_LABEL:
        return _REASON_LABEL[text]
    if text.startswith("rsi_tp_"):
        return f"익절 RSI{text[7:]}"
    if text.startswith("tp1_"):
        return f"1차익절 +{text[4:].replace('pct', '')}%"
    if text.startswith("tp2_"):
        return f"2차익절 +{text[4:].replace('pct', '')}%"
    if text.startswith("tp4_"):
        return f"4차익절 +{text[4:].replace('pct', '')}%"
    if text.startswith("tp3_rsi_"):
        return f"3차 RSI{text[8:]}"
    if text.startswith("tp3_"):
        return f"3차익절 +{text[4:].replace('pct', '')}%"
    if text.startswith("after_half_tp_"):
        return f"3차익절 +{text[14:].replace('pct', '')}%"
    if text.startswith("after_half_rsi_"):
        return f"3차 RSI{text[15:]}"
    if text.startswith("after_half_stop_"):
        pct = text[16:].replace("pct", "")
        return f"익절후손절 {pct}%" if pct != "0" else "익절후손절 0%"
    if text.startswith("trail_peak"):
        return "잔량트레일"
    if text.upper().startswith("SMA") and text[3:].isdigit():
        return f"{text[3:]}일선"
    return text


def formatAlgEvtLine(row):
    """이벤트 dict → 거래로그 한 줄. DB 대문자키·코드 카멜키 모두 허용."""
    row = row or {}
    tm = _fmtTm(_pick(row, "evtTm", "EVT_TM"))
    alg = str(_pick(row, "algCd", "ALG_CD", default="MTS01")).strip() or "MTS01"
    evtTp = str(_pick(row, "evtTp", "EVT_TP")).strip().upper()
    label = _EVT_LABEL.get(evtTp, evtTp or "이벤트")
    nm = str(_pick(row, "stkNm", "STK_NM")).strip()
    cd = str(_pick(row, "stkCd", "STK_CD")).strip()
    name = f"{nm}({cd})" if nm and cd else (nm or cd or "-")
    reason = _reasonTxt(_pick(row, "reason", "REASON"))
    px = _fmtPx(_pick(row, "px", "PX", default=None))
    qty = _fmtQty(_pick(row, "qty", "QTY", default=None))
    note = str(_pick(row, "note", "NOTE")).strip()
    parts = [tm, f"[{alg}]", label, name]
    if reason:
        parts.append(reason)
    if px:
        parts.append(px)
    if qty:
        parts.append(qty)
    if note and note not in (reason, name):
        parts.append(note)
    return " ".join(parts)


def _fmtPxNum(raw):
    """그리드용 가격. 원 접미사 없이 천단위."""
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        return f"{abs(int(float(raw))):,}"
    except (TypeError, ValueError):
        return str(raw)


def _fmtPnlPct(raw):
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        return f"{float(raw):+.2f}%"
    except (TypeError, ValueError):
        return ""


def _fmtPnlPctDual(realPct, evalPct=None):
    """
    당일매도 수익률 칸 — 실현(비용후) 위주, 평가(비용전) 병기.
    예: 실-0.12%(평+0.11)
    """
    realTxt = _fmtPnlPct(realPct)
    if not realTxt:
        return ""
    try:
        ev = float(evalPct) if evalPct is not None else None
    except (TypeError, ValueError):
        ev = None
    if ev is None:
        return f"실{realTxt}"
    return f"실{realTxt}(평{ev:+.2f}%)"


def _fmtPnlAmt(raw):
    """수익금. 부호+천단위."""
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        return f"{int(round(float(raw))):+,}"
    except (TypeError, ValueError):
        return ""


def rowToDayTrade(row, pnlPct=None, pnlAmt=None, evalPct=None):
    """
    당일 거래 그리드 1행.
    주문·체결·실패만: BUY/SELL/BUY_ORD/SELL_ORD/BUY_FAIL/SELL_FAIL/BUY_CNCL…
    """
    row = row or {}
    evtTp = str(_pick(row, "evtTp", "EVT_TP")).strip().upper()
    if evtTp not in DAY_TRADE_EVT_TPS:
        return None
    # 표시용 구분 (매수/매도/실패 등)
    side = _EVT_LABEL.get(evtTp) or evtTp
    # 평단·손익 집계용 쪽 (매수 계열 / 매도 계열)
    if evtTp in ("BUY", "BUY_ORD", "BUY_FAIL", "BUY_CNCL", "BUY_ORD_CNCL"):
        sideKind = "BUY"
    else:
        sideKind = "SELL"
    reason = _reasonTxt(_pick(row, "reason", "REASON"))
    note = str(_pick(row, "note", "NOTE")).strip()
    if note and note not in (reason,) and not reason:
        reason = note
    elif note and reason and note not in (reason,) and not note.startswith("SMA"):
        reason = f"{reason} {note}".strip()
    pnl = pnlPct
    if pnl is None:
        pnl = _pick(row, "pnlPct", "PNL_PCT", default=None)
    amt = pnlAmt
    if amt is None:
        amt = _pick(row, "pnlAmt", "PNL_AMT", default=None)
    ev = evalPct
    if ev is None:
        ev = _pick(row, "evalPct", "EVAL_PCT", default=None)
    # 손익은 실제 매도체결(SELL)만
    if evtTp == "SELL":
        pnlTxt = _fmtPnlPctDual(pnl, ev) if (pnl is not None or ev is not None) else ""
        amtTxt = _fmtPnlAmt(amt)
    else:
        pnlTxt = ""
        amtTxt = ""
    return {
        "tm": _fmtTm(_pick(row, "evtTm", "EVT_TM")),
        "stkNm": str(_pick(row, "stkNm", "STK_NM")).strip(),
        "stkCd": str(_pick(row, "stkCd", "STK_CD")).strip(),
        "algCd": str(_pick(row, "algCd", "ALG_CD", default="")).strip().upper(),
        "side": side,
        "sideKind": sideKind,
        "evtTp": evtTp,
        "px": _fmtPxNum(_pick(row, "px", "PX", default=None)),
        "pnl": pnlTxt,
        "pnlAmt": amtTxt,
        "reason": reason,
        "qty": _pick(row, "qty", "QTY", default=None),
        "pxRaw": _pick(row, "px", "PX", default=None),
    }


def evtKindOf(row):
    """색 구분용 이벤트 타입."""
    return str(_pick(row or {}, "evtTp", "EVT_TP")).strip().upper()


class TradeLogBus(QObject):
    """kind, line — GUI 스레드에서 목록에 붙인다. 체결 후 계좌 갱신."""

    logged = Signal(str, str)
    tradeRow = Signal(object)
    acntRefresh = Signal()


def initTradeLogBus(parent=None):
    """매매창 생성 시 1회. parent=창 이어야 GUI 스레드에 붙는다."""
    global _bus
    if _bus is None:
        _bus = TradeLogBus(parent)
    return _bus


def getTradeLogBus():
    return _bus


def pushAlgEvtToDesk(row):
    """콘솔 + 매매창. 창이 없으면 콘솔만. deskTradeMode 와 다른 MODE 는 화면에만 안 넣는다."""
    line = formatAlgEvtLine(row)
    kind = evtKindOf(row)
    termLog(f"[거래로그] {line}")
    try:
        from utils.tradeSession import getDeskTradeMode

        raw = str(
            (row or {}).get("mode") or (row or {}).get("MODE") or ""
        ).strip().lower()
        rowMode = "real" if raw in ("real", "live", "실전") else "paper"
        if getDeskTradeMode() != rowMode:
            return
    except Exception:
        pass
    bus = _bus
    if bus is not None:
        bus.logged.emit(kind, line)
        if kind in DAY_TRADE_EVT_TPS:
            bus.tradeRow.emit(row)


def pushFillToDesk(sideLabel, stkCd, stkNm, qty, px, ordNo=""):
    """00 체결 한 줄 + 계좌/보유 갱신 요청. 일별 적재는 emitFillLog."""
    from utils.deskLogStore import fmtFillLine

    line, kind, _hms = fmtFillLine(sideLabel, stkCd, stkNm, qty, px, ordNo)
    termLog(f"[거래로그] {line}")
    bus = _bus
    if bus is not None:
        bus.logged.emit(kind, line)
        bus.acntRefresh.emit()


def clearTradeLogBus():
    global _bus
    _bus = None
