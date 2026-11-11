"""
실시간 매매 데스크 메인 창.
계좌·보유는 kt00004, 매매대상 현재가는 0B, 상단 지수는 0J, 거래로그는 00 체결+알고리즘 이벤트.
"""

import html
import os
import threading
from datetime import datetime

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from config import runtimeState


def _logAlgTag(line):
    """거래로그 앞머리 'HH:MM:SS [ALG]' 태그. 없으면 빈 문자열."""
    text = str(line or "").strip()
    if len(text) < 11 or text[2] != ":" or text[5] != ":":
        return ""
    rest = text[8:].lstrip()
    if not rest.startswith("["):
        return ""
    end = rest.find("]")
    if end <= 1:
        return ""
    return rest[1:end].strip().upper()


def _tradeLogFgHex(line):
    """알고리즘 로그는 해당 색, 00 체결 등 공통은 검정."""
    tag = _logAlgTag(line)
    if not tag or tag == "00":
        return "#000000"
    try:
        from trading.deskLiveAlgs import algFgColor, isKnownAlgCd

        if isKnownAlgCd(tag):
            return algFgColor(tag) or "#000000"
    except Exception:
        pass
    return "#000000"


# 당일 거래 그리드 — 매수/매도 칸 배경 (매수=연한빨강, 매도=연한파랑, 미체결취소=연한회색)
_DAY_BUY_BG = QColor("#f8d0d6")
_DAY_SELL_BG = QColor("#d0e4f8")
_DAY_BUY_FG = QColor("#8b1538")
_DAY_SELL_FG = QColor("#155a8b")
_DAY_CNCL_BG = QColor("#e8e8e8")
_DAY_CNCL_FG = QColor("#6a6a6a")
# 보유 종목명: 미체결 주문 중 연노랑
_HOLD_ORD_BG = QColor("#FFF59D")
_HOLD_ORD_BG_CLEAR = QColor("#FFFFFF")
# 매수/매도 칸 UserRole — 미체결 취소 여부 (1=전량 미체결 취소)
_DAY_SIDE_CNCL_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_DAY_SIDE_STK_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DAY_SIDE_KIND_ROLE = int(Qt.ItemDataRole.UserRole)
_DAY_SIDE_EVT_ROLE = int(Qt.ItemDataRole.UserRole) + 4
_DAY_SIDE_QTY_ROLE = int(Qt.ItemDataRole.UserRole) + 3


def _fmtPx(raw):
    """키움 현재가(+/- 부호)를 절대값 천단위 표시."""
    if raw is None or str(raw).strip() == "":
        return ""
    s = str(raw).replace(",", "").strip()
    if s[:1] in "+-":
        s = s[1:]
    try:
        return f"{abs(int(float(s))):,}"
    except Exception:
        return str(raw)


def _fmtFlu(raw):
    """등락률 표시. 장외·미수신이면 빈칸."""
    if raw is None or str(raw).strip() == "":
        return ""
    s = str(raw).replace("%", "").strip()
    try:
        return f"{float(s):+.2f}%"
    except Exception:
        return str(raw)


def _absPxNum(raw):
    """현재가 숫자. 키움 +/- 부호·콤마를 떼고 절대값."""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).replace(",", "").strip()
    if s[:1] in "+-":
        s = s[1:]
    try:
        n = abs(int(float(s)))
        return n if n > 0 else None
    except Exception:
        return None


def _hasQuote(raw):
    return raw is not None and str(raw).strip() != ""


def _holdLivePx(tick, apiCur):
    """
    보유 평가용 현재가.
    0B(실시간·복원) 우선, dayClose(일봉 시드)면 계좌 kt00004 cur_prc 를 쓴다.
    재시작 후 낡은 일봉으로 수익률이 뒤집히는 것을 막는다.
    """
    info = tick or {}
    prcRaw, _flu = _dispQuote(info)
    live = _absPxNum(prcRaw)
    try:
        api = abs(int(apiCur or 0))
    except (TypeError, ValueError):
        api = 0
    if api <= 0:
        api = 0
    src = str(info.get("src") or "")
    if src == "dayClose" and api > 0:
        return api
    if live:
        return live
    return api


def _dispQuote(tick):
    """
    그리드 현재가·등락.
    KRX 정규장 안에는 KRX, 장전·장마감·휴장에는 NXT(없으면 마지막 KRX).
    등락률은 선택한 거래소 값이 비면 다른 거래소·캐시 값을 쓴다.
    """
    info = tick or {}
    krxPrc, nxtPrc = info.get("krxPrc"), info.get("nxtPrc")
    krxFlu, nxtFlu = info.get("krxFlu"), info.get("nxtFlu")
    fluAny = (
        krxFlu
        if _hasQuote(krxFlu)
        else (
            nxtFlu
            if _hasQuote(nxtFlu)
            else (info.get("fluRt") if _hasQuote(info.get("fluRt")) else None)
        )
    )
    inKrx = False
    try:
        from datetime import datetime

        from config.krHoliday import isTradingDay
        from config.settingsStore import isWithinMktHours

        now = datetime.now()
        inKrx = isTradingDay(now) and isWithinMktHours()
    except Exception:
        inKrx = True
    if inKrx:
        if _hasQuote(krxPrc):
            return krxPrc, krxFlu if _hasQuote(krxFlu) else fluAny
        if _hasQuote(nxtPrc):
            return nxtPrc, nxtFlu if _hasQuote(nxtFlu) else fluAny
    else:
        if _hasQuote(nxtPrc):
            return nxtPrc, nxtFlu if _hasQuote(nxtFlu) else fluAny
        if _hasQuote(krxPrc):
            return krxPrc, krxFlu if _hasQuote(krxFlu) else fluAny
    return info.get("curPrc"), fluAny


def _fmtManWon(raw):
    """금액 → 만원 단위. 나머지가 있으면 원."""
    try:
        won = int(float(raw))
    except (TypeError, ValueError):
        return str(raw or "-")
    if won >= 10000 and won % 10000 == 0:
        return f"{won // 10000:,}만원"
    return f"{won:,}원"


def _fmtSignedNet(num):
    """순매수 주수 부호 표시."""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return "-"
    if n > 0:
        return f"+{n:,}"
    return f"{n:,}"


def _fmtSignedAmt(raw):
    """순매수 대금 증감(원). 억·만원 단위."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return "-"
    absn = abs(n)
    if absn >= 100000000:
        return f"{n / 100000000.0:+.1f}억"
    if absn >= 10000:
        return f"{n / 10000.0:+,.0f}만원"
    if n > 0:
        return f"+{n:,}원"
    return f"{n:,}원"


def _netOrderText(ind, frgn, orgn):
    """
    최근 순매수 큰 순서. 예: 외인 > 기관 > 개인
    값이 같으면 = .
    """
    items = [
        ("개인", int(ind or 0), 0),
        ("외인", int(frgn or 0), 1),
        ("기관", int(orgn or 0), 2),
    ]
    items.sort(key=lambda x: (-x[1], x[2]))
    parts = [items[0][0]]
    for i in range(1, len(items)):
        op = ">" if items[i - 1][1] > items[i][1] else "="
        parts.append(op)
        parts.append(items[i][0])
    return " ".join(parts)


def _fmtNetChgPct(rate):
    """증가율 표시. 직전 0·급등은 상한을 둔다."""
    try:
        pct = float(rate) * 100.0
    except (TypeError, ValueError):
        return "-"
    if pct > 999:
        return "+999%"
    if pct < -999:
        return "-999%"
    return f"{pct:+.0f}%"


def _tipSignedColor(num):
    """증가 빨강, 감소 파랑, 0 검정."""
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "#000000"
    if n > 0:
        return "#FF0000"
    if n < 0:
        return "#0000FF"
    return "#000000"


def _tipSignedSpan(text, num):
    """툴팁용 부호 색 글자."""
    c = _tipSignedColor(num)
    return f'<span style="color:{c};font-weight:700">{text}</span>'


def _netStepLabel(step):
    try:
        n = int(step)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return "증가"
    if n < 0:
        return "감소"
    return "보합"


def _fmtEokAmt(raw):
    """억원 → 조·억. 부호 유지."""
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if n < 0 else ""
    absn = abs(n)
    if absn >= 10000:
        return f"{sign}{absn / 10000.0:.1f}조"
    if absn >= 1:
        return f"{sign}{absn:,.0f}억"
    if n == 0:
        return "0"
    return f"{n:+.1f}억"


def _fmtRoePct(raw):
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return "-"
    return f"{n:+.1f}%"


def _fmtOpMargin(raw):
    """영업이익률. 예: 10% / -3.2%"""
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return "-"
    if abs(n - round(n)) < 0.05:
        return f"{n:.0f}%"
    return f"{n:.1f}%"


def _watchSnapTipHead(snap):
    """시총·업종·영업이익(영익률)·ROE."""
    snap = snap or {}
    up = html.escape(str(snap.get("upName") or "").strip() or "-")
    macTxt = _fmtEokAmt(snap.get("mac")) if snap.get("mac") is not None else "-"
    bus = snap.get("busPro")
    busRt = snap.get("busRt")
    roe = snap.get("roe")
    if bus is None:
        busHtml = "-"
    else:
        busHtml = _tipSignedSpan(_fmtEokAmt(bus), bus)
    if busRt is None:
        rtHtml = html.escape("(영익:-)")
    else:
        rtHtml = f"(영익:{_tipSignedSpan(_fmtOpMargin(busRt), busRt)})"
    if roe is None:
        roeHtml = "-"
    else:
        roeHtml = _tipSignedSpan(_fmtRoePct(roe), roe)
    return (
        f"시총  {html.escape(macTxt)}<br/>"
        f"업종  {up}<br/>"
        f"영업이익  {busHtml} {rtHtml}<br/>"
        f"ROE  {roeHtml}"
    )


def _watchNetCellParts(netRec, loaded=False, snap=None):
    """순매수 칸 글자·툴팁·히트맵. 수급 없으면 안내."""
    head = _watchSnapTipHead(snap)
    if not netRec:
        if loaded:
            netBody = "수급 적재 없음"
            netTxt = "-"
        else:
            netBody = "수급 불러오는 중…"
            netTxt = "-"
        netTip = (
            "<html><body style='color:#111111'>"
            f"{head}<br/>{html.escape(netBody)}"
            "</body></html>"
        )
        return netTxt, netTip, None
    netTxt = _netOrderText(
        netRec.get("ind"), netRec.get("frgn"), netRec.get("orgn")
    )
    dayRows = list(netRec.get("days") or [])
    netMeta = {
        "frgn": [d.get("frgn") for d in dayRows],
        "orgn": [d.get("orgn") for d in dayRows],
        "ind": [d.get("ind") for d in dayRows],
    }

    def _netLine(label, prefix):
        chg = netRec.get(f"{prefix}AmtChg")
        diff = netRec.get(f"{prefix}AmtDiff")
        return (
            f"{label}  "
            f"{_tipSignedSpan(_fmtNetChgPct(chg), chg)}  "
            f"{_tipSignedSpan(_fmtSignedAmt(diff), diff)}"
        )

    netTip = (
        "<html><body style='color:#111111'>"
        f"{head}<br/>"
        + _netLine("외인", "frgn")
        + "<br/>"
        + _netLine("기관", "orgn")
        + "<br/>"
        + _netLine("-연기금", "pen")
        + "<br/>"
        + _netLine("-투신", "invt")
        + "<br/>"
        + _netLine("-사모", "samo")
        + "<br/>"
        + _netLine("-기타", "orgnEtc")
        + "<br/>"
        + _netLine("개인", "ind")
        + "</body></html>"
    )
    return netTxt, netTip, netMeta


_PNL_PLUS = QColor("#FF0000")
_PNL_MINUS = QColor("#0000FF")
_PNL_ZERO = QColor("#000000")
_SEL_BG = QColor("#d6ebff")


def _signedColor(text):
    """숫자 문자열 → 플러스 빨강 / 마이너스 파랑 / 그 외 검정."""
    raw = str(text or "").replace(",", "").replace("%", "").replace("원", "").strip()
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return _PNL_ZERO
    if n > 0:
        return _PNL_PLUS
    if n < 0:
        return _PNL_MINUS
    return _PNL_ZERO


class SignedPnlDelegate(QStyledItemDelegate):
    """
    평가손익·수익률·등락률: 플러스 빨강, 마이너스 파랑.
    선택 배경은 연한 파랑으로 두어 빨간 글자가 가려지지 않게 한다.
    아이템 BackgroundRole(알고리즘 색 등)이 있으면 그대로 깐다.
    """

    def paint(self, painter, option, index):
        opt = option
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        text = str(index.data() or "")
        color = _signedColor(text)
        painter.save()
        if selected:
            painter.fillRect(opt.rect, _SEL_BG)
        else:
            bgRole = index.data(Qt.ItemDataRole.BackgroundRole)
            if isinstance(bgRole, QBrush) and bgRole.style() != Qt.BrushStyle.NoBrush:
                painter.fillRect(opt.rect, bgRole)
            elif isinstance(bgRole, QColor):
                painter.fillRect(opt.rect, bgRole)
            elif opt.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
                painter.fillRect(opt.rect, opt.backgroundBrush)
        painter.setPen(color)
        painter.drawText(
            opt.rect.adjusted(4, 0, -6, 0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()

    def sizeHint(self, option, index):
        """칸 너비가 금액보다 좁아 잘리지 않게 글자폭+여백을 알려준다."""
        text = str(index.data() or "")
        w = option.fontMetrics.horizontalAdvance(text) + 24
        h = super().sizeHint(option, index).height()
        return QSize(w, h)


def _fmtRsi(raw):
    """RSI 표시. 소수점 이하는 버림. 값 없으면 빈칸."""
    if raw is None or raw == "":
        return ""
    try:
        return str(int(float(raw)))
    except (TypeError, ValueError):
        return ""


def _rsiColor(text):
    """RSI 70 이상 빨강, 30 이하 파랑."""
    raw = str(text or "").strip()
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return _PNL_ZERO
    if n >= 70:
        return _PNL_PLUS
    if n <= 30:
        return _PNL_MINUS
    return _PNL_ZERO


class RsiDelegate(QStyledItemDelegate):
    """관심종목 RSI: 과매수 빨강, 과매도 파랑."""

    def paint(self, painter, option, index):
        opt = option
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        text = str(index.data() or "")
        color = _rsiColor(text)
        painter.save()
        if selected:
            painter.fillRect(opt.rect, _SEL_BG)
        else:
            bgRole = index.data(Qt.ItemDataRole.BackgroundRole)
            if isinstance(bgRole, QBrush) and bgRole.style() != Qt.BrushStyle.NoBrush:
                painter.fillRect(opt.rect, bgRole)
            elif isinstance(bgRole, QColor):
                painter.fillRect(opt.rect, bgRole)
            elif opt.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
                painter.fillRect(opt.rect, opt.backgroundBrush)
        painter.setPen(color)
        painter.drawText(
            opt.rect.adjusted(4, 0, -6, 0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()


class SurgeSortItem(QTableWidgetItem):
    """표시는 콤마 문자열, 정렬은 UserRole+1 숫자."""

    def __lt__(self, other):
        av = self.data(Qt.ItemDataRole.UserRole + 1)
        bv = other.data(Qt.ItemDataRole.UserRole + 1) if isinstance(other, QTableWidgetItem) else None
        if av is None or bv is None:
            return QTableWidgetItem.__lt__(self, other)
        try:
            return float(av) < float(bv)
        except (TypeError, ValueError):
            return QTableWidgetItem.__lt__(self, other)


class SurgeNameDelegate(QStyledItemDelegate):
    """종목명 앞에 신·뉴 배지. 셀 글자는 종목명만 남겨 복사·정렬이 배지를 포함하지 않는다."""

    def paint(self, painter, option, index):
        marks = index.data(Qt.ItemDataRole.UserRole)
        if marks is True:
            marks = "신"
        if not isinstance(marks, str) or not marks:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        from trading.surgeTradeData import badgeFillColor

        painter.save()
        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(opt.rect, opt.palette.highlight())
        else:
            painter.fillRect(opt.rect, opt.palette.base())
        left = opt.rect.left() + 3
        for ch in marks:
            box = QRect(left, opt.rect.center().y() - 7, 14, 14)
            fill = badgeFillColor(ch)
            painter.setPen(QPen(QColor("#212121")))
            painter.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
            painter.drawRect(box)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), ch)
            left += 16
        painter.setPen(QColor("#212121"))
        painter.drawText(
            opt.rect.adjusted(left - opt.rect.left(), 0, -4, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(opt.text or ""),
        )
        painter.restore()


class SideBgDelegate(QStyledItemDelegate):
    """매수/매도 칸만 연한빨강·연한파랑 배경. 주문취소·원주문은 연한회색."""

    def paint(self, painter, option, index):
        text = str(index.data() or "")
        cancelled = bool(index.data(_DAY_SIDE_CNCL_ROLE))
        if cancelled and ("매수" in text or "매도" in text):
            bg, fg = _DAY_CNCL_BG, _DAY_CNCL_FG
        elif "매수" in text:
            bg, fg = _DAY_BUY_BG, _DAY_BUY_FG
        elif "매도" in text:
            bg, fg = _DAY_SELL_BG, _DAY_SELL_FG
        else:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.fillRect(option.rect, bg)
        painter.setPen(fg)
        painter.drawText(
            option.rect.adjusted(4, 0, -4, 0),
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        painter.restore()


class PriceOrderDelegate(QStyledItemDelegate):
    """관심 상태열 — '현재' 글자만 빨강, 나머지는 기본색."""

    _CUR_RED = QColor("#c41e3a")

    def paint(self, painter, option, index):
        text = str(index.data() or "")
        opt = option
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            painter.fillRect(opt.rect, _SEL_BG)
        elif opt.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
            painter.fillRect(opt.rect, opt.backgroundBrush)
        fm = opt.fontMetrics
        x = opt.rect.left() + 4
        basePen = opt.palette.color(QPalette.ColorRole.Text)
        tokens = text.split(" ") if text else []
        for i, tok in enumerate(tokens):
            if not tok:
                continue
            painter.setPen(self._CUR_RED if tok == "현재" else basePen)
            painter.drawText(
                x,
                opt.rect.top(),
                fm.horizontalAdvance(tok) + 2,
                opt.rect.height(),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                tok,
            )
            x += fm.horizontalAdvance(tok)
            if i < len(tokens) - 1:
                x += fm.horizontalAdvance(" ")
        painter.restore()

    def sizeHint(self, option, index):
        text = str(index.data() or "")
        w = option.fontMetrics.horizontalAdvance(text) + 24
        h = super().sizeHint(option, index).height()
        return QSize(w, h)


class NetOrderDelegate(QStyledItemDelegate):
    """
    관심 순매수열.
    바탕: 세로 2행 외인 / 기관. 각 행은 좌측=최근 → 우측=과거.
    색은 연한 파스텔 — 글자가 묻히지 않게 농도를 낮춘다.
    글자: 우선순위 가운데, 외인 빨강, 기관 파랑.
    """

    _FRGN_FG = QColor("#C62828")
    _ORGN_FG = QColor("#1565C0")
    _HEAT_DARK = {
        "frgn": QColor("#FFCDD2"),
        "orgn": QColor("#BBDEFB"),
    }
    _HALO = QColor(255, 255, 255, 220)

    def paint(self, painter, option, index):
        text = str(index.data() or "")
        opt = option
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.fillRect(opt.rect, QColor("#FFFFFF"))
        meta = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(meta, dict) and text and text != "-":
            self._paintNetHeat(painter, opt.rect, meta)
        if selected:
            painter.fillRect(opt.rect, QColor(214, 235, 255, 70))
        fm = opt.fontMetrics
        tokens = [t for t in (text.split(" ") if text else []) if t]
        bits = []
        totalW = 0
        spW = fm.horizontalAdvance(" ")
        for i, tok in enumerate(tokens):
            tw = fm.horizontalAdvance(tok)
            bits.append((tok, tw))
            totalW += tw
            if i < len(tokens) - 1:
                bits.append((" ", spW))
                totalW += spW
        x = opt.rect.left() + max(0, (opt.rect.width() - totalW) // 2)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for tok, tw in bits:
            if tok == " ":
                x += tw
                continue
            if tok == "외인":
                fg = self._FRGN_FG
            elif tok == "기관":
                fg = self._ORGN_FG
            else:
                fg = QColor("#212121")
            self._drawHaloText(
                painter, x, opt.rect.top(), tw + 2, opt.rect.height(), flags, tok, fg
            )
            x += tw
        painter.restore()

    def _drawHaloText(self, painter, x, y, w, h, flags, tok, fg):
        """연한 히트맵 위에서도 글자가 보이게 흰 테두리."""
        painter.setPen(self._HALO)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(x + dx, y + dy, w, h, flags, tok)
        painter.setPen(fg)
        painter.drawText(x, y, w, h, flags, tok)

    def _heatColor(self, key, hold, maxPos):
        """누적보유 0 이하는 흰색. 최대여도 파스텔(약 55%)까지만."""
        try:
            n = int(hold)
        except (TypeError, ValueError):
            return QColor("#FFFFFF")
        if n <= 0 or maxPos <= 0:
            return QColor("#FFFFFF")
        t = min(0.55, max(0.0, n / float(maxPos)))
        dark = self._HEAT_DARK.get(key) or QColor("#EEEEEE")
        return QColor(
            int(255 + (dark.red() - 255) * t),
            int(255 + (dark.green() - 255) * t),
            int(255 + (dark.blue() - 255) * t),
        )

    def _holdPath(self, vals):
        """
        일별 순매수(최근→과거) → 구간 내 누적보유(최근→과거).
        과거부터 더한다. 순매수 0이면 보유(색) 유지, 음수면 줄고, 0 미만은 0으로 바닥.
        """
        nums = []
        for v in vals or []:
            try:
                nums.append(int(v or 0))
            except (TypeError, ValueError):
                nums.append(0)
        n = len(nums)
        hold = [0] * n
        acc = 0
        for i in range(n - 1, -1, -1):
            acc += nums[i]
            if acc < 0:
                acc = 0
            hold[i] = acc
        return hold

    def _paintNetHeat(self, painter, rect, meta):
        """
        세로 2행 외인/기관. 좌=최근, 우=과거.
        늘어난 만큼 진해지고, 안 빠지면 그 색을 유지, 빠지면 연해지고, 0이면 흰색.
        """
        keys = ("frgn", "orgn")
        x0 = float(rect.left())
        y0 = rect.top()
        h = max(rect.height(), 2)
        w = max(float(rect.width()), 2.0)
        regionH = h / 2.0
        nDays = 0
        for key in keys:
            nDays = max(nDays, len(meta.get(key) or []))
        if nDays <= 0:
            nDays = 20
        painter.setPen(Qt.PenStyle.NoPen)
        for ri, key in enumerate(keys):
            vals = list(meta.get(key) or [])
            while len(vals) < nDays:
                vals.append(None)
            vals = vals[:nDays]
            hold = self._holdPath(vals)
            maxPos = 0
            for n in hold:
                if n > maxPos:
                    maxPos = n
            sy = int(y0 + regionH * ri)
            ey = int(y0 + regionH * (ri + 1))
            bandH = max(ey - sy, 1)
            self._fillHoldGradient(
                painter, x0, sy, w, bandH, key, hold, maxPos
            )

    def _fillHoldGradient(self, painter, x0, sy, w, bandH, key, hold, maxPos):
        """
        날짜 사이를 이어 칠한다.
        보유가 같으면 단색(유지), 늘/줄면 그 구간만 그라데이션.
        """
        n = len(hold)
        if n <= 0:
            painter.fillRect(
                QRect(int(x0), sy, int(w), bandH), QColor("#FFFFFF")
            )
            return
        if n == 1:
            painter.fillRect(
                QRect(int(x0), sy, int(w), bandH),
                self._heatColor(key, hold[0], maxPos),
            )
            return
        lastI = float(n - 1)
        for i in range(n - 1):
            xL = x0 + w * (i / lastI)
            xR = x0 + w * ((i + 1) / lastI)
            cL = self._heatColor(key, hold[i], maxPos)
            cR = self._heatColor(key, hold[i + 1], maxPos)
            seg = QRect(int(xL), sy, max(int(round(xR - xL)), 1), bandH)
            if hold[i] == hold[i + 1] or cL == cR:
                # 순매수 0(안 빠짐) — 진하기 유지
                painter.fillRect(seg, cL)
            else:
                grad = QLinearGradient(xL, float(sy), xR, float(sy))
                grad.setColorAt(0.0, cL)
                grad.setColorAt(1.0, cR)
                painter.fillRect(seg, QBrush(grad))

    def sizeHint(self, option, index):
        text = str(index.data() or "")
        w = option.fontMetrics.horizontalAdvance(text) + 16
        h = super().sizeHint(option, index).height()
        return QSize(w, h)


class HoldAlgRowHeader(QHeaderView):
    """보유 표 행번호 — 알고리즘별 바탕색 + 툴팁(알고리즘명)."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self._rowBg = {}
        self._rowTip = {}
        self.setSectionsClickable(False)
        self.setHighlightSections(False)
        self.setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )

    def setRowMeta(self, row, bgHex, tip):
        self._rowBg[int(row)] = QColor(bgHex or "#E0E0E0")
        self._rowTip[int(row)] = str(tip or "")

    def clearRowMeta(self):
        self._rowBg.clear()
        self._rowTip.clear()

    def paintSection(self, painter, rect, logicalIndex):
        if not rect.isValid():
            return
        painter.save()
        bg = self._rowBg.get(int(logicalIndex), QColor("#E0E0E0"))
        painter.fillRect(rect, bg)
        painter.setPen(QColor("#BDBDBD"))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(QColor("#000000"))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            str(int(logicalIndex) + 1),
        )
        painter.restore()

    def event(self, ev):
        if ev.type() == QEvent.Type.ToolTip:
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            # 세로 헤더는 Y 좌표로 행 인덱스
            idx = self.logicalIndexAt(pos.y())
            tip = self._rowTip.get(int(idx), "") if idx >= 0 else ""
            if tip:
                QToolTip.showText(ev.globalPos(), tip, self)
                return True
            QToolTip.hideText()
            return True
        return super().event(ev)


class AlgColorTabBar(QTabBar):
    """알고리즘별 바탕색 탭. 선택 탭은 본색, 비선택은 밝게."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabBg = {}
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setDrawBase(False)
        self.setTabsClosable(False)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setUsesScrollButtons(True)

    def setTabBg(self, index, bgHex):
        self._tabBg[int(index)] = QColor(bgHex or "#ECEFF1")

    def clearTabBg(self):
        self._tabBg.clear()

    def tabSizeHint(self, index):
        sz = super().tabSizeHint(index)
        return QSize(max(int(sz.width()) + 8, 96), max(int(sz.height()), 28))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(self.count()):
            rect = self.tabRect(i)
            if not rect.isValid():
                continue
            bg = self._tabBg.get(i, QColor("#ECEFF1"))
            selected = i == self.currentIndex()
            fill = QColor(bg)
            if not selected:
                fill = bg.lighter(128)
            r = rect.adjusted(1, 3, -2, 0)
            painter.setPen(QPen(bg.darker(118), 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(r, 4, 4)
            if selected:
                painter.setPen(QPen(bg.darker(135), 2))
                painter.drawLine(
                    r.left() + 6, r.bottom() - 2, r.right() - 6, r.bottom() - 2
                )
            font = QFont(self.font())
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(QColor("#000000"))
            painter.drawText(
                r,
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
                self.tabText(i),
            )


class TradeDeskWin(QMainWindow):
    """계좌·보유·매매대상·거래로그. 매매대상은 USE_TRADE=Y."""

    # 워커 스레드 kt00004(+계좌번호) 결과를 GUI 스레드로 넘긴다.
    # body, modeLabel, acctNo
    acntOk = Signal(object, str, str)
    acntFail = Signal(str)
    # 관심종목 수급(AI_DAY_INV_SR) 백그라운드 조회 완료
    watchNetOk = Signal(str, object)
    watchSnapOk = Signal()
    # 차트 일봉 백그라운드 조회 완료 (종목, 봉목록, 종목명)
    chartBarsOk = Signal(str, object, str)
    # 차트 일봉 수동 최신화 완료 (종목, 결과dict)
    chartRefreshDone = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("천둥이")
        try:
            from utils.appIcon import applyWindowIcon

            applyWindowIcon(self, kind="trade")
        except Exception:
            pass
        self.resize(1400, 860)
        self._sideKey = ""
        self._holdRows = []
        self._stratAlgCd = "MTS01"
        self._stratAlgTabCds = []
        self._stratAlgTabsBusy = False
        self._watchAlgCd = "surge"
        self._watchAlgTabCds = []
        self._watchAlgTabsBusy = False
        self._watchNetCache = {}
        self._watchRowCache = {}
        self._watchNetLoading = set()
        self._watchNetWantCds = {}
        self._watchPaintCds = []
        self._watchPrefetchQueued = False
        self._watchTipMeta = {}
        self._watchSnapLoading = False
        self._watchSnapPending = []
        self._watchLastNetMap = {}
        self._watchLastNetLoaded = False
        self._holdSkipCds = set()
        self._holdOpenAlgMap = {}
        self._acntReady = False
        self._acntBusy = False
        self._deskAcctNo = ""
        self._dayCost = {}
        self._dayLedger = {}
        self._deskLogYmd = ""
        self.acntOk.connect(self._applyAcnt)
        self.acntFail.connect(self._applyAcntFail)
        self.watchNetOk.connect(self._onWatchNetOk)
        self.watchSnapOk.connect(self._onWatchSnapOk)
        self.chartBarsOk.connect(self._onChartBarsOk)
        self.chartRefreshDone.connect(self._onChartRefreshDone)
        self._chartStkCd = ""
        self._chartStkNm = ""
        self._chartLoadTok = 0
        self._chartSelBusy = False
        self._buildUi()
        self._bindTradeLog()
        self._bindDeskErr()
        try:
            from utils.deskFileLog import markDeskFileLogRestart

            markDeskFileLogRestart()
        except Exception:
            pass
        self.refreshSessionLabel()
        self.refreshTradeWatch()
        self._loadTodayTradeLog()
        self._loadTodayDayTrades()
        # DB 목록 + 0B 현재가. 장외에도 종목 목록은 보이게 둔다.
        # STP ALG 조회 부담을 줄이기 위해 20초 (목록 캐시와 맞춤)
        self._watchTimer = QTimer(self)
        self._watchTimer.timeout.connect(self.refreshTradeWatch)
        self._watchTimer.start(20000)
        # 실계좌 탭 현재가/손익% 0B 캐시 반영 (2초 스로틀)
        self._acntWatchPxTimer = QTimer(self)
        self._acntWatchPxTimer.timeout.connect(self._tickRealAcntWatchPx)
        self._acntWatchPxTimer.start(2000)
        # 거래일 08:00 에 전날 로그/거래를 비우고 당일 분으로 바꾼다.
        self._deskDayTimer = QTimer(self)
        self._deskDayTimer.timeout.connect(self._rollDeskIfNewDay)
        self._deskDayTimer.start(15000)
        # 계좌 TR 은 모의 1초 1회라 30초 간격. 체결 직후는 2초 뒤 1회.
        self._acntTimer = QTimer(self)
        self._acntTimer.timeout.connect(self.refreshAcnt)
        self._acntTimer.start(30000)
        self._acntSoon = QTimer(self)
        self._acntSoon.setSingleShot(True)
        self._acntSoon.timeout.connect(self.refreshAcnt)
        QTimer.singleShot(800, self.refreshAcnt)
        try:
            from utils.riseRankBoard import startRiseRankBoard

            startRiseRankBoard()
        except Exception as err:
            try:
                from utils.termLog import termLog

                termLog(f"[매매창] 주도주 보드 시작 실패: {err}")
            except Exception:
                pass
        # 상단 지수 0J 캐시 — 1초마다 글자만 갱신
        self._indsTimer = QTimer(self)
        self._indsTimer.timeout.connect(self._paintIndsBar)
        self._indsTimer.start(1000)
        # 차트 탭이 열려 있을 때만 0B 캐시로 당일봉을 덧그림 (주문 경로와 분리)
        self._chartLiveTimer = QTimer(self)
        self._chartLiveTimer.timeout.connect(self._tickDeskChart)

    def _buildUi(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)

        sessRow = QWidget()
        sessLay = QHBoxLayout(sessRow)
        sessLay.setContentsMargins(0, 0, 0, 0)
        sessLay.setSpacing(10)
        self.sessionLbl = QLabel()
        self.sessionLbl.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.sessionLbl.setToolTip("투자모드·매매방식은 오른쪽 설정 탭에서 변경")
        sessLay.addWidget(self.sessionLbl)
        # 토큰 유효여부만 표시 (만료시각은 내부 관리, UI에 안 냄)
        self.tokenAliveLbl = QLabel("")
        self.tokenAliveLbl.setStyleSheet("font-weight: 600; font-size: 13px; padding: 2px 6px;")
        self.tokenAliveLbl.setToolTip("계좌조회·주문 결과로 자동 갱신")
        sessLay.addWidget(self.tokenAliveLbl)
        # 코스피·코스닥·선물(코스피200) — 0J 실시간
        self.indsLbl = QLabel("코스피 -  ·  코스닥 -  ·  선물 -")
        self.indsLbl.setTextFormat(Qt.TextFormat.RichText)
        self.indsLbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.indsLbl.setToolTip(
            "코스피(001)·코스닥(101)·선물=코스피200(201) 업종지수. "
            "장중은 실시간(0J), 없으면 일봉 마지막 종가. "
            "선물은 근월물이 아니라 코스피200 지수다."
        )
        sessLay.addWidget(self.indsLbl, 1)
        outer.addWidget(sessRow)
        self._paintIndsBar()
        self._opsAlgTimer = QTimer(self)
        self._opsAlgTimer.timeout.connect(self.refreshOpsAlgBar)
        self._opsAlgTimer.start(5000)

        body = QWidget()
        bodyLay = QHBoxLayout(body)
        bodyLay.setContentsMargins(0, 0, 0, 0)
        bodyLay.setSpacing(6)
        outer.addWidget(body, 1)

        mainSplit = QSplitter(Qt.Orientation.Horizontal)

        holdPane = QWidget()
        holdLay = QVBoxLayout(holdPane)
        holdLay.setContentsMargins(0, 0, 0, 0)
        holdHead = QWidget()
        holdHeadLay = QHBoxLayout(holdHead)
        holdHeadLay.setContentsMargins(0, 0, 0, 4)
        holdHeadLay.setSpacing(12)
        self.holdTitle = self._sectionTitle("보유종목")
        holdHeadLay.addWidget(self.holdTitle)
        self.holdSumLbl = QLabel("총매입 -  ·  총평가 -  ·  0종  ·  평가수익률 -  ·  평가금 -")
        self.holdSumLbl.setTextFormat(Qt.TextFormat.RichText)
        self.holdSumLbl.setStyleSheet("color: #000; font-weight: 600;")
        holdHeadLay.addWidget(self.holdSumLbl, 1)
        holdLay.addWidget(holdHead)
        self.holdTbl = QTableWidget(0, 11)
        self.holdTbl.setHorizontalHeaderLabels(
            [
                "종목명",
                "평단가",
                "평가손익",
                "평가수익률",
                "수량",
                "등락률",
                "보유비중",
                "매입금액",
                "평가금액",
                "현재가",
                "최초매수일",
            ]
        )
        self._tuneGrid(self.holdTbl, [])
        # 글자가 칸보다 길면 ... 으로 자르지 않고, 컬럼을 늘린다
        self.holdTbl.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.holdTbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.holdTbl.setWordWrap(False)
        # 행번호에 알고리즘 색 — 커스텀 세로헤더
        self._holdAlgHdr = HoldAlgRowHeader(self.holdTbl)
        self.holdTbl.setVerticalHeader(self._holdAlgHdr)
        self._holdAlgHdr.setVisible(True)
        self._holdAlgHdr.setDefaultSectionSize(24)
        self._holdAlgHdr.setMinimumWidth(28)
        self._holdAlgHdr.setFixedWidth(32)
        holdPnlDel = SignedPnlDelegate(self.holdTbl)
        self.holdTbl.setItemDelegateForColumn(2, holdPnlDel)
        self.holdTbl.setItemDelegateForColumn(3, holdPnlDel)
        self.holdTbl.setItemDelegateForColumn(5, holdPnlDel)
        holdLay.addWidget(self.holdTbl, 1)
        mainSplit.addWidget(holdPane)

        watchPane = QWidget()
        watchLay = QVBoxLayout(watchPane)
        watchLay.setContentsMargins(0, 0, 0, 0)
        self.tradeWatchTitle = self._sectionTitle("관심종목")
        watchLay.addWidget(self.tradeWatchTitle)
        # 알고리즘별 관리종목 탭 (deskAlgTabs 순서 · 탭색=algBgColor)
        self.watchAlgTabBar = AlgColorTabBar()
        self.watchAlgTabBar.currentChanged.connect(self._onWatchAlgTabChanged)
        watchLay.addWidget(self.watchAlgTabBar)
        # 주도주 탭 전용: 최종 업데이트 일시 + 수동 갱신 (다른 탭에서는 숨김)
        self._leaderRankTools = QWidget()
        _lrLay = QHBoxLayout(self._leaderRankTools)
        _lrLay.setContentsMargins(4, 0, 4, 2)
        _lrLay.setSpacing(8)
        self._leaderRankUpdatedLbl = QLabel("최종 업데이트: -")
        self._leaderRankUpdatedLbl.setStyleSheet(
            "font-size: 12px; color: #424242; padding: 2px 4px;"
        )
        self._leaderRankRefreshBtn = QPushButton("수동 업데이트")
        self._leaderRankRefreshBtn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._leaderRankRefreshBtn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 2px 10px; "
            "background: #FFCC80; color: #3E2723; border: 1px solid #FFB74D; }"
            "QPushButton:hover { background: #FFB74D; }"
            "QPushButton:disabled { background: #EEEEEE; color: #9E9E9E; border: 1px solid #E0E0E0; }"
        )
        self._leaderRankRefreshBtn.clicked.connect(self._onLeaderRankRefresh)
        _lrLay.addWidget(self._leaderRankUpdatedLbl)
        _lrLay.addWidget(self._leaderRankRefreshBtn)
        _lrLay.addStretch(1)
        self._leaderRankTools.setVisible(False)
        watchLay.addWidget(self._leaderRankTools)
        self._surgeTools = QWidget()
        _sgLay = QHBoxLayout(self._surgeTools)
        _sgLay.setContentsMargins(4, 0, 4, 2)
        _sgLay.setSpacing(8)
        self._surgeStateLbl = QLabel("급등주: 대기")
        self._surgeStateLbl.setStyleSheet("font-size: 12px; color: #004D40; padding: 2px 4px;")
        self._surgeRefreshBtn = QPushButton("수동 갱신")
        self._surgeRefreshBtn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._surgeRefreshBtn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 2px 10px; "
            "background: #80DEEA; color: #004D40; border: 1px solid #4DD0E1; }"
            "QPushButton:disabled { background: #EEEEEE; color: #9E9E9E; }"
        )
        self._surgeRefreshBtn.clicked.connect(self._onSurgeRefresh)
        _sgLay.addWidget(self._surgeStateLbl)
        _sgLay.addWidget(self._surgeRefreshBtn)
        _sgLay.addStretch(1)
        self._surgeTools.setVisible(False)
        watchLay.addWidget(self._surgeTools)
        from trading.surgeTradeData import TAB_COLUMNS

        self.surgeRankTbl = QTableWidget(0, len(TAB_COLUMNS))
        self.surgeRankTbl.setHorizontalHeaderLabels([label for _key, label in TAB_COLUMNS])
        self._tuneGrid(self.surgeRankTbl, [])
        self.surgeRankTbl.setVisible(False)
        self.surgeRankTbl.itemSelectionChanged.connect(self._onSurgeRowPicked)
        watchLay.addWidget(self.surgeRankTbl, 1)
        self._surgePoll = QTimer(self)
        self._surgePoll.setInterval(400)
        self._surgePoll.timeout.connect(self._pollSurgeRefresh)
        self._surgeSnapTimer = QTimer(self)
        self._surgeSnapTimer.setInterval(45000)
        self._surgeSnapTimer.timeout.connect(self._fillSurgeWatch)
        self._surgeStatusWord = ""
        self._leaderRankPoll = QTimer(self)
        self._leaderRankPoll.setInterval(2000)
        self._leaderRankPoll.timeout.connect(self._pollLeaderRankScan)
        # 종목·등락률·RSI·순매수·배열 틀고정 + 나머지 가로 스크롤
        # 선택 알고리즘은 이 프레임 테두리색으로 표시 (행번호 전체 칠하기 대신)
        watchSplit = QWidget()
        watchSplit.setObjectName("watchGridFrame")
        self.watchGridFrame = watchSplit
        watchSplitLay = QHBoxLayout(watchSplit)
        watchSplitLay.setContentsMargins(3, 3, 3, 3)
        watchSplitLay.setSpacing(0)

        self.tradeWatchFrozen = QTableWidget(0, 5)
        self.tradeWatchFrozen.setHorizontalHeaderLabels(
            ["종목", "등락률", "RSI", "순매수", "배열"]
        )
        rsiHdr = self.tradeWatchFrozen.horizontalHeaderItem(2)
        if rsiHdr is not None:
            rsiHdr.setToolTip("RSI14 Wilder · 전체 일봉 + 실시간 현재가")
        netHdr = self.tradeWatchFrozen.horizontalHeaderItem(3)
        if netHdr is not None:
            netHdr.setToolTip(
                "최근 20일 순매수 큰 순서\n"
                "툴팁: 시총·업종·영업이익·ROE + 외인/기관(연기금·투신·사모·기타)/개인 증감\n"
                "바탕 위 외인 / 아래 기관 · 왼쪽=최근 오른쪽=과거"
            )
        self._tuneGrid(self.tradeWatchFrozen, [])
        frzHdr = self.tradeWatchFrozen.horizontalHeader()
        # 글자 길이에 따라 열이 접히지 않게 고정 (3초 새로고침마다 배열이 숨는 것 방지)
        # 배열: 가장 긴 문구(20 > 10 > 현재 > 5)가 보일 만큼만
        # RSI: 헤더·세 자리 정수(100)가 보일 만큼만
        # 순매수: 외인 > 기관 > 개인 이 보일 만큼만
        fmFrz = self.tradeWatchFrozen.fontMetrics()
        arrW = fmFrz.horizontalAdvance("20 > 10 > 현재 > 5") + 12
        hdrArrW = fmFrz.horizontalAdvance("배열") + 16
        if arrW < hdrArrW:
            arrW = hdrArrW
        rsiW = max(fmFrz.horizontalAdvance("100"), fmFrz.horizontalAdvance("RSI")) + 14
        netW = fmFrz.horizontalAdvance("외인 > 기관 > 개인") + 14
        hdrNetW = fmFrz.horizontalAdvance("순매수") + 16
        if netW < hdrNetW:
            netW = hdrNetW
        self._WATCH_FRZ_W = (72, 64, int(rsiW), int(netW), int(arrW))
        frzHdr.setMinimumSectionSize(24)
        for i, colW in enumerate(self._WATCH_FRZ_W):
            frzHdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.tradeWatchFrozen.setColumnWidth(i, colW)
        self.tradeWatchFrozen.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tradeWatchFrozen.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # 긴 종목명이 줄바꿈되면 틀고정 행만 높아져 스크롤 표와 어긋난다
        self.tradeWatchFrozen.setWordWrap(False)
        self.tradeWatchFrozen.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tradeWatchFrozen.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._watchAlgHdr = HoldAlgRowHeader(self.tradeWatchFrozen)
        self.tradeWatchFrozen.setVerticalHeader(self._watchAlgHdr)
        self.tradeWatchFrozen.verticalHeader().setVisible(True)
        self.tradeWatchFrozen.verticalHeader().setDefaultSectionSize(22)
        self.tradeWatchFrozen.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        self._watchAlgHdr.setFixedWidth(32)
        self.tradeWatchFrozen.setItemDelegateForColumn(
            1, SignedPnlDelegate(self.tradeWatchFrozen)
        )
        self.tradeWatchFrozen.setItemDelegateForColumn(
            2, RsiDelegate(self.tradeWatchFrozen)
        )
        self.tradeWatchFrozen.setItemDelegateForColumn(
            3, NetOrderDelegate(self.tradeWatchFrozen)
        )
        self.tradeWatchFrozen.setItemDelegateForColumn(
            4, PriceOrderDelegate(self.tradeWatchFrozen)
        )
        # 틀고정 폭 = 행번호 + 종목 + 등락률 + RSI + 순매수 + 배열 (고정폭)
        self._fitWatchFrozenWidth()

        self.tradeWatchTbl = QTableWidget(0, 4)
        self.tradeWatchTbl.setHorizontalHeaderLabels(
            ["현재가", "5일", "10일", "20일"]
        )
        self._tuneGrid(self.tradeWatchTbl, [])
        scrollHdr = self.tradeWatchTbl.horizontalHeader()
        # 글자 폭에 맞춰 자동 축소·확장 (잘림 방지). 넘치면 호버 시 가로 스크롤
        scrollHdr.setMinimumSectionSize(36)
        scrollHdr.setDefaultSectionSize(48)
        for col in range(4):
            scrollHdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tradeWatchTbl.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tradeWatchTbl.setWordWrap(False)
        self.tradeWatchTbl.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.tradeWatchTbl.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        # 가로·세로 스크롤바: 해당 영역 마우스 올리면 표시, 벗어나면 숨김
        self._watchHScrollHover = False
        self._watchVScrollHover = False
        self._watchAlignBusy = False
        self._watchAlignPending = False
        self.tradeWatchTbl.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tradeWatchTbl.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tradeWatchTbl.verticalHeader().setVisible(False)
        self.tradeWatchTbl.verticalHeader().setDefaultSectionSize(22)
        self.tradeWatchTbl.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        # 세로 스크롤 동기 (틀고정 ↔ 스크롤 영역)
        self.tradeWatchTbl.verticalScrollBar().valueChanged.connect(
            self.tradeWatchFrozen.verticalScrollBar().setValue
        )
        self.tradeWatchFrozen.verticalScrollBar().valueChanged.connect(
            self.tradeWatchTbl.verticalScrollBar().setValue
        )
        # 스크롤 필요 여부·호버 표시 동기
        self.tradeWatchTbl.horizontalScrollBar().rangeChanged.connect(
            self._syncWatchScrollBars
        )
        self.tradeWatchTbl.verticalScrollBar().rangeChanged.connect(
            self._syncWatchScrollBars
        )
        for w in (
            self.tradeWatchFrozen,
            self.tradeWatchFrozen.viewport(),
            self.tradeWatchTbl,
            self.tradeWatchTbl.viewport(),
            self.tradeWatchTbl.horizontalScrollBar(),
            self.tradeWatchTbl.verticalScrollBar(),
        ):
            w.installEventFilter(self)
            w.setMouseTracking(True)

        watchSplitLay.addWidget(self.tradeWatchFrozen, 0)
        watchSplitLay.addWidget(self.tradeWatchTbl, 1)
        self.tradeWatchFrozen.itemSelectionChanged.connect(self._onWatchRowPicked)
        self.tradeWatchTbl.itemSelectionChanged.connect(self._onWatchRowPicked)
        watchLay.addWidget(watchSplit, 1)
        mainSplit.addWidget(watchPane)
        self._rebuildWatchAlgTabs()

        mainSplit.setStretchFactor(0, 1)
        mainSplit.setStretchFactor(1, 1)
        mainSplit.setSizes([700, 700])
        mainSplit.setMinimumWidth(360)
        self.mainSplit = mainSplit

        # 오른쪽 탭: 기본은 접힘. 계좌·로그는 눌렀을 때만 탭 왼쪽에 펼친다.
        self.sidePanel = QWidget()
        self.sidePanel.setMinimumWidth(300)
        self.sidePanel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        sideLay = QVBoxLayout(self.sidePanel)
        sideLay.setContentsMargins(0, 0, 0, 0)
        self.sideTitle = self._sectionTitle("계좌")
        sideLay.addWidget(self.sideTitle)
        self.sideStack = QStackedWidget()
        sideLay.addWidget(self.sideStack, 1)

        acctPane = QWidget()
        acctLay = QVBoxLayout(acctPane)
        acctLay.setContentsMargins(0, 0, 0, 0)
        self.acctTbl = QTableWidget(0, 2)
        self.acctTbl.setHorizontalHeaderLabels(["항목", "내용"])
        self._tuneGrid(self.acctTbl, [1])
        self.acctTbl.verticalHeader().setVisible(False)
        acctHdr = self.acctTbl.horizontalHeader()
        acctHdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.acctTbl.setColumnWidth(0, 96)
        self.acctTbl.setTextElideMode(Qt.TextElideMode.ElideNone)
        acctLay.addWidget(self.acctTbl, 1)
        # 알고리즘별 당일·월간·누적 손익
        self.algPnlTitle = self._sectionTitle("알고리즘 손익")
        acctLay.addWidget(self.algPnlTitle)
        self.algPnlTbl = QTableWidget(0, 4)
        self.algPnlTbl.setHorizontalHeaderLabels(
            ["알고리즘", "당일", "월간", "누적"]
        )
        self._tuneGrid(self.algPnlTbl, [])
        self.algPnlTbl.verticalHeader().setVisible(False)
        self.algPnlTbl.setWordWrap(True)
        self.algPnlTbl.setTextElideMode(Qt.TextElideMode.ElideNone)
        acctLay.addWidget(self.algPnlTbl, 1)
        self.sideStack.addWidget(acctPane)

        stratPane = QWidget()
        stratLay = QVBoxLayout(stratPane)
        stratLay.setContentsMargins(0, 0, 0, 0)
        # 알고리즘 탭 — 관심종목과 동일 색(algBgColor)
        self.stratAlgTabBar = AlgColorTabBar()
        self.stratAlgTabBar.currentChanged.connect(self._onStratAlgTabChanged)
        self._stratAlgCd = "MTS01"
        self._stratAlgTabCds = []
        self._stratAlgTabsBusy = False
        stratLay.addWidget(self.stratAlgTabBar)
        self.stratBox = QTextEdit()
        self.stratBox.setReadOnly(True)
        self.stratBox.setPlaceholderText("알고리즘 정보…")
        stratLay.addWidget(self.stratBox, 1)
        self.sideStack.addWidget(stratPane)
        self._rebuildStratAlgTabs()

        dayPane = QWidget()
        dayLay = QVBoxLayout(dayPane)
        dayLay.setContentsMargins(0, 0, 0, 0)
        self.dayTbl = QTableWidget(0, 7)
        self.dayTbl.setHorizontalHeaderLabels(
            ["시간", "종목명", "매수/매도", "수익금", "수익률", "거래가", "거래사유"]
        )
        self._tuneGrid(self.dayTbl, [6])
        # 종목명은 내용만큼 늘리지 않고 짧게. 긴 이름은 ... 처리
        dayHdr = self.dayTbl.horizontalHeader()
        dayHdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.dayTbl.setColumnWidth(1, 72)
        self.dayTbl.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.dayTbl.setAlternatingRowColors(False)
        self.dayTbl.setItemDelegateForColumn(2, SideBgDelegate(self.dayTbl))
        self.dayTbl.setItemDelegateForColumn(3, SignedPnlDelegate(self.dayTbl))
        self.dayTbl.setItemDelegateForColumn(4, SignedPnlDelegate(self.dayTbl))
        # 행번호 바탕색 = 알고리즘 (보유·관심과 동일)
        self._dayAlgHdr = HoldAlgRowHeader(self.dayTbl)
        self.dayTbl.setVerticalHeader(self._dayAlgHdr)
        self._dayAlgHdr.setVisible(True)
        self._dayAlgHdr.setDefaultSectionSize(24)
        self._dayAlgHdr.setMinimumWidth(28)
        self._dayAlgHdr.setFixedWidth(32)
        dayLay.addWidget(self.dayTbl, 1)
        self.sideStack.addWidget(dayPane)

        logPane = QWidget()
        logLay = QVBoxLayout(logPane)
        logLay.setContentsMargins(0, 0, 0, 0)
        self.fillList = QListWidget()
        self.fillList.setWordWrap(True)
        logLay.addWidget(self.fillList, 1)
        self.sideStack.addWidget(logPane)

        errPane = QWidget()
        errLay = QVBoxLayout(errPane)
        errLay.setContentsMargins(0, 0, 0, 0)
        self.errList = QListWidget()
        self.errList.setWordWrap(True)
        if hasattr(self.errList, "setPlaceholderText"):
            self.errList.setPlaceholderText("오류 없음")
        errLay.addWidget(self.errList, 1)
        self.sideStack.addWidget(errPane)

        from ui.deskStkChart import DeskStkChartPane

        chartPane = DeskStkChartPane()
        self.deskChart = chartPane
        self.deskChart.btnRefresh.clicked.connect(self._onChartRefreshClick)
        self.deskChart.barsMissing.connect(self._onChartBarsMissing)
        self._chartFetchTried = set()
        self.sideStack.addWidget(chartPane)

        # 설정 — 투자모드·매매방식 (상단바에서 이동)
        settPane = QWidget()
        settLay = QVBoxLayout(settPane)
        settLay.setContentsMargins(8, 8, 8, 8)
        settLay.setSpacing(12)
        modeRow = QHBoxLayout()
        modeRow.addWidget(QLabel("투자모드"))
        self.tradeModeCmb = QComboBox()
        self.tradeModeCmb.addItem("모의투자", "paper")
        self.tradeModeCmb.addItem("실전투자", "real")
        self.tradeModeCmb.setToolTip(
            "모의/실전 API·주문 전환. 바꾸면 로그인·WS를 다시 연결한다."
        )
        self.tradeModeCmb.currentIndexChanged.connect(self._onTradeModeChanged)
        modeRow.addWidget(self.tradeModeCmb, 1)
        settLay.addLayout(modeRow)
        self.tradeModeStatusLbl = QLabel("")
        self.tradeModeStatusLbl.setWordWrap(True)
        self.tradeModeStatusLbl.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #1565c0; padding: 2px 0;"
        )
        settLay.addWidget(self.tradeModeStatusLbl)
        exchRow = QHBoxLayout()
        exchRow.addWidget(QLabel("매매방식"))
        self.tradeExchCmb = QComboBox()
        self.tradeExchCmb.addItem("KRX", "KRX")
        self.tradeExchCmb.addItem("통합(NXT 포함)", "NXT")
        self.tradeExchCmb.setToolTip(
            "자동매매 방식. KRX=정규장 주문. "
            "통합(NXT 포함)=NXT가능 종목은 NXT 주문. "
            "NXT 시세는 KRX 선택 시에도 받는다."
        )
        self.tradeExchCmb.currentIndexChanged.connect(self._onTradeExchChanged)
        exchRow.addWidget(self.tradeExchCmb, 1)
        settLay.addLayout(exchRow)
        settHint = QLabel(
            "오른쪽 「설정」에서 모의/실전을 바꿀 수 있습니다.\n"
            "전환 시 확인창 → 설정 저장 → 로그인·WS 재연결.\n"
            "실전은 paperOnly도 함께 해제되어 자동매매 주문이 실계좌로 나갑니다."
        )
        settHint.setStyleSheet("color: #666; font-size: 12px;")
        settHint.setWordWrap(True)
        settLay.addWidget(settHint)
        try:
            from ui.mmgSettRow import addMmgSettRow

            addMmgSettRow(self, settLay)
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[desk] MMG01 설정행 실패: {err}")
        settLay.addStretch(1)
        self.sideStack.addWidget(settPane)
        self._loadTradeModeCmb()
        self._loadTradeExchCmb()

        self.sidePanel.hide()
        self.deskBodySplit = QSplitter(Qt.Orientation.Horizontal)
        self.deskBodySplit.setChildrenCollapsible(False)
        self.deskBodySplit.setHandleWidth(8)
        self.deskBodySplit.setStyleSheet(
            "QSplitter::handle { background: #d0d0d0; }"
            "QSplitter::handle:hover { background: #1565c0; }"
        )
        self.deskBodySplit.addWidget(mainSplit)
        self.deskBodySplit.addWidget(self.sidePanel)
        self.deskBodySplit.setStretchFactor(0, 1)
        self.deskBodySplit.setStretchFactor(1, 2)
        self.deskBodySplit.splitterMoved.connect(self._onDeskBodySplitMoved)
        self._deskBodySizes = None
        bodyLay.addWidget(self.deskBodySplit, 1)

        rail = QFrame()
        rail.setObjectName("sideRail")
        rail.setFixedWidth(44)
        rail.setStyleSheet(
            "#sideRail { background: #f3f3f3; border: 1px solid #ddd; }"
            "QToolButton { border: none; padding: 8px 2px; }"
            "QToolButton:checked { background: #fff; font-weight: 600; }"
        )
        railLay = QVBoxLayout(rail)
        railLay.setContentsMargins(2, 8, 2, 8)
        railLay.setSpacing(6)
        self.acctTab = self._makeSideTab("계좌")
        self.stratTab = self._makeSideTab("전략")
        self.dayTab = self._makeSideTab("거래")
        self.logTab = self._makeSideTab("로그")
        self.errTab = self._makeSideTab("오류")
        self.chartTab = self._makeSideTab("차트")
        self.settTab = self._makeSideTab("설정")
        self.acctTab.clicked.connect(lambda c: self._onSideTab("acct", c))
        self.stratTab.clicked.connect(lambda c: self._onSideTab("strat", c))
        self.dayTab.clicked.connect(lambda c: self._onSideTab("day", c))
        self.logTab.clicked.connect(lambda c: self._onSideTab("log", c))
        self.errTab.clicked.connect(lambda c: self._onSideTab("err", c))
        self.chartTab.clicked.connect(lambda c: self._onSideTab("chart", c))
        self.settTab.clicked.connect(lambda c: self._onSideTab("sett", c))
        railLay.addWidget(self.acctTab)
        railLay.addWidget(self.stratTab)
        railLay.addWidget(self.dayTab)
        railLay.addWidget(self.logTab)
        railLay.addWidget(self.errTab)
        railLay.addWidget(self.chartTab)
        railLay.addWidget(self.settTab)
        railLay.addStretch(1)
        bodyLay.addWidget(rail)

        # 운영 중 알고리즘 — 창 맨 아래 (보유 행번호와 같은 색 칩)
        self.opsAlgLbl = QLabel("운영알고리즘: -")
        self.opsAlgLbl.setTextFormat(Qt.TextFormat.RichText)
        self.opsAlgLbl.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #212121; padding: 4px 0 0 0;"
        )
        self.opsAlgLbl.setWordWrap(True)
        outer.addWidget(self.opsAlgLbl)
        self.refreshOpsAlgBar()

    def _makeSideTab(self, text):
        """세로 탭 버튼. 같은 탭을 다시 누르면 접는다."""
        btn = QToolButton()
        btn.setText("\n".join(list(text)))
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setMinimumHeight(64)
        return btn

    def eventFilter(self, watched, event):
        """관심종목 — 가로/세로 스크롤바 호버 표시."""
        frz = getattr(self, "tradeWatchFrozen", None)
        tbl = getattr(self, "tradeWatchTbl", None)
        if frz is None or tbl is None:
            return super().eventFilter(watched, event)
        hbar = tbl.horizontalScrollBar()
        vbar = tbl.verticalScrollBar()
        scrollSide = (tbl, tbl.viewport(), hbar, vbar)
        frozenSide = (frz, frz.viewport())
        watchTargets = scrollSide + frozenSide
        if watched in watchTargets:
            et = event.type()
            if et == QEvent.Type.Enter:
                if watched in scrollSide:
                    self._watchHScrollHover = True
                self._watchVScrollHover = True
                self._syncWatchScrollBars()
            elif et == QEvent.Type.Leave:
                QTimer.singleShot(0, self._maybeHideWatchScrollBars)
        return super().eventFilter(watched, event)

    def _maybeHideWatchScrollBars(self):
        """커서 위치에 따라 가로/세로 스크롤바 유지 여부 결정."""
        frz = getattr(self, "tradeWatchFrozen", None)
        tbl = getattr(self, "tradeWatchTbl", None)
        if frz is None or tbl is None:
            return
        hbar = tbl.horizontalScrollBar()
        vbar = tbl.verticalScrollBar()
        under = QApplication.widgetAt(QCursor.pos())
        inScroll = False
        inFrozen = False
        w = under
        while w is not None:
            if w is tbl or w is hbar or w is vbar or w is tbl.viewport():
                inScroll = True
                break
            if w is frz or w is frz.viewport():
                inFrozen = True
                break
            w = w.parentWidget() if hasattr(w, "parentWidget") else None
        self._watchHScrollHover = inScroll
        self._watchVScrollHover = inScroll or inFrozen
        self._syncWatchScrollBars()

    def _syncWatchScrollBars(self, *_args):
        """
        가로: 현재가 쪽 호버 + 내용 넘침 시 표시.
        세로: 관심종목(틀고정 포함) 호버 + 내용 넘침 시 표시.
        틀고정에는 스크롤바를 두지 않음.
        """
        frz = getattr(self, "tradeWatchFrozen", None)
        tbl = getattr(self, "tradeWatchTbl", None)
        if frz is None or tbl is None:
            return
        if getattr(self, "_watchAlignBusy", False):
            return
        frz.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        frz.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        hbar = tbl.horizontalScrollBar()
        vbar = tbl.verticalScrollBar()
        hNeed = hbar is not None and hbar.maximum() > 0
        vNeed = vbar is not None and vbar.maximum() > 0
        hHover = bool(getattr(self, "_watchHScrollHover", False))
        vHover = bool(getattr(self, "_watchVScrollHover", False))

        if hNeed and hHover:
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        else:
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        if vNeed and vHover:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        else:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 스크롤바 레이아웃이 끝난 뒤 행·뷰포트 높이를 맞춘다
        if not getattr(self, "_watchAlignPending", False):
            self._watchAlignPending = True
            QTimer.singleShot(0, self._alignWatchFrozenViewport)

    def _alignWatchFrozenViewport(self):
        """
        틀고정/스크롤 표의 헤더·행·뷰포트 높이를 같게 한다.
        세로바가 생기면 오른쪽 표만 뷰포트가 줄어 행이 밀리는 것을 막는다.
        """
        self._watchAlignPending = False
        frz = getattr(self, "tradeWatchFrozen", None)
        tbl = getattr(self, "tradeWatchTbl", None)
        if frz is None or tbl is None:
            return
        if getattr(self, "_watchAlignBusy", False):
            return
        self._watchAlignBusy = True
        try:
            hdrH = max(
                int(frz.horizontalHeader().sizeHint().height() or 0),
                int(tbl.horizontalHeader().sizeHint().height() or 0),
                int(frz.horizontalHeader().height() or 0),
                int(tbl.horizontalHeader().height() or 0),
                22,
            )
            frz.horizontalHeader().setFixedHeight(hdrH)
            tbl.horizontalHeader().setFixedHeight(hdrH)

            n = min(frz.rowCount(), tbl.rowCount())
            rowH = max(
                int(frz.verticalHeader().defaultSectionSize() or 0),
                int(tbl.verticalHeader().defaultSectionSize() or 0),
                22,
            )
            for i in range(n):
                rh = max(int(frz.rowHeight(i) or 0), int(tbl.rowHeight(i) or 0), rowH)
                if frz.rowHeight(i) != rh:
                    frz.setRowHeight(i, rh)
                if tbl.rowHeight(i) != rh:
                    tbl.setRowHeight(i, rh)

            # 여백을 먼저 지운 뒤, 실제 뷰포트 차이만큼 틀고정 아래를 패딩
            frz.setViewportMargins(0, 0, 0, 0)
            tbl.setViewportMargins(0, 0, 0, 0)
            diff = int(frz.viewport().height()) - int(tbl.viewport().height())
            if diff > 0:
                frz.setViewportMargins(0, 0, 0, diff)
            elif diff < 0:
                tbl.setViewportMargins(0, 0, 0, -diff)

            # 스크롤 범위·위치를 스크롤 표 기준으로 맞춤
            srcBar = tbl.verticalScrollBar()
            dstBar = frz.verticalScrollBar()
            dstBar.blockSignals(True)
            dstBar.setRange(srcBar.minimum(), srcBar.maximum())
            dstBar.setPageStep(srcBar.pageStep())
            dstBar.setSingleStep(srcBar.singleStep())
            dstBar.setValue(srcBar.value())
            dstBar.blockSignals(False)
        finally:
            self._watchAlignBusy = False

    def _lockWatchFrozenCols(self):
        """틀고정 5열을 고정폭으로 유지. ResizeToContents 가 배열을 접지 않게."""
        frz = getattr(self, "tradeWatchFrozen", None)
        if frz is None:
            return
        widths = getattr(self, "_WATCH_FRZ_W", None) or (72, 64, 40, 96, 108)
        hdr = frz.horizontalHeader()
        for i, colW in enumerate(widths):
            if i >= frz.columnCount():
                break
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            if int(frz.columnWidth(i) or 0) != int(colW):
                frz.setColumnWidth(i, int(colW))

    def _fitWatchFrozenWidth(self):
        """틀고정 표 폭 = 행번호 + 고정 5열 + 테두리. 매 틱 줄였다 늘리지 않음."""
        frz = getattr(self, "tradeWatchFrozen", None)
        if frz is None:
            return
        self._lockWatchFrozenCols()
        widths = getattr(self, "_WATCH_FRZ_W", None) or (72, 64, 40, 96, 108)
        vh = int(frz.verticalHeader().width() or 32)
        fw = int(frz.frameWidth() or 0) * 2
        w = vh + int(sum(widths)) + fw + 4
        if int(frz.minimumWidth() or 0) != w or int(frz.maximumWidth() or 0) != w:
            frz.setFixedWidth(w)

    def _applyWatchGridBorder(self, bgHex):
        """관심 그리드 바깥 테두리 = 선택 알고리즘색."""
        frame = getattr(self, "watchGridFrame", None)
        if frame is None:
            return
        c = QColor(bgHex or "#E0E0E0")
        if not c.isValid():
            c = QColor("#E0E0E0")
        hexCol = c.name()
        frame.setStyleSheet(
            f"#watchGridFrame {{ border: 3px solid {hexCol}; border-radius: 3px; }}"
        )

    def _tuneGrid(self, tbl, stretchCols):
        """종목·마지막 숫자열을 넓게. 편집 불가."""
        hdr = tbl.horizontalHeader()
        hdr.setStretchLastSection(False)
        for i in range(tbl.columnCount()):
            if i in stretchCols:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        # 선택 배경을 연한 파랑으로 — 빨간 손익 글자가 안 가려지게
        pal = tbl.palette()
        pal.setColor(QPalette.ColorRole.Highlight, _SEL_BG)
        pal.setColor(QPalette.ColorRole.HighlightedText, _PNL_ZERO)
        tbl.setPalette(pal)
        tbl.setStyleSheet(
            "QTableWidget::item:selected { background-color: #d6ebff; }"
        )

    def _onSideTab(self, key, checked):
        """오른쪽 계좌/전략/거래/로그/오류/차트/설정 중 하나만 펼친다. 다시 누르면 숨긴다."""
        tabs = {
            "acct": self.acctTab,
            "log": self.logTab,
            "err": self.errTab,
            "strat": self.stratTab,
            "day": self.dayTab,
            "chart": self.chartTab,
            "sett": self.settTab,
        }
        if not checked:
            if not any(b.isChecked() for b in tabs.values()):
                self._rememberDeskBodySplit()
                self._sideKey = ""
                self.sidePanel.hide()
                self._stopDeskChart()
            return
        for k, btn in tabs.items():
            if k != key:
                btn.setChecked(False)
        titles = {
            "acct": "계좌",
            "log": "거래 로그",
            "err": "오류",
            "strat": "전략",
            "day": "거래",
            "chart": "차트",
            "sett": "설정",
        }
        pages = {
            "acct": 0,
            "strat": 1,
            "day": 2,
            "log": 3,
            "err": 4,
            "chart": 5,
            "sett": 6,
        }
        self.sideTitle.setText(self._titleText(titles.get(key) or key))
        self.sideStack.setCurrentIndex(pages.get(key, 0))
        self._sideKey = key
        if key == "chart":
            self.sidePanel.setMinimumWidth(380)
        else:
            self.sidePanel.setMinimumWidth(300)
        self.sidePanel.show()
        QTimer.singleShot(0, self._applyDeskBodySplit)
        if key == "strat":
            self._rebuildStratAlgTabs()
            self.refreshStratInfo()
        if key == "acct":
            self.refreshAlgPnl()
        if key == "sett":
            self._loadTradeModeCmb()
            self._loadTradeExchCmb()
            self._refreshTradeModeStatus()
        if key == "chart":
            self._openDeskChart()
        else:
            self._stopDeskChart()

    def _rememberDeskBodySplit(self):
        split = getattr(self, "deskBodySplit", None)
        panel = getattr(self, "sidePanel", None)
        if split is None or panel is None or not panel.isVisible():
            return
        sizes = split.sizes()
        if len(sizes) == 2 and sizes[0] > 0 and sizes[1] > 0:
            self._deskBodySizes = [int(sizes[0]), int(sizes[1])]

    def _onDeskBodySplitMoved(self, _pos, _index):
        self._rememberDeskBodySplit()

    def _applyDeskBodySplit(self):
        """관심종목과 차트 사이 핸들. 한 번 조절한 너비는 이 창이 열려 있는 동안 유지한다."""
        from utils.chartBarPlan import deskSideSplitSizes

        split = getattr(self, "deskBodySplit", None)
        panel = getattr(self, "sidePanel", None)
        if split is None or panel is None or not panel.isVisible():
            return
        total = sum(int(n) for n in split.sizes())
        if total < 200:
            total = max(800, int(self.width() or 0) - 60)
        sizes = deskSideSplitSizes(
            total,
            getattr(self, "_deskBodySizes", None),
            chartOpen=getattr(self, "_sideKey", "") == "chart",
        )
        split.setSizes(sizes)

    def _selectedWatchStk(self):
        """관심종목 선택 행 → (stkCd, stkNm). 없으면 빈 값."""
        from utils.stkCdUtil import normStkCd

        frz = getattr(self, "tradeWatchFrozen", None)
        if frz is None:
            return "", ""
        row = frz.currentRow()
        cds = list(getattr(self, "_watchPaintCds", None) or [])
        if row < 0 or row >= len(cds):
            return "", ""
        cd = normStkCd(cds[row]) or ""
        nm = ""
        _nc = 0
        try:
            if str(frz.horizontalHeaderItem(0).text()) == "변동":
                _nc = 1
        except Exception:
            pass
        cell = frz.item(row, _nc)
        if cell is not None:
            nm = str(cell.text() or "").strip()
        return cd, nm

    def _onWatchRowPicked(self):
        """관심종목 고정 칸·가로 칸에서 사용자가 고르면 차트 탭을 연다."""
        if getattr(self, "_watchRefreshBusy", False):
            return
        if getattr(self, "_chartSelBusy", False):
            return
        self._chartSelBusy = True
        try:
            src = self.sender()
            frz = self.tradeWatchFrozen
            tbl = self.tradeWatchTbl
            row = -1
            if src is frz:
                row = frz.currentRow()
                if row >= 0 and tbl.currentRow() != row:
                    tbl.selectRow(row)
            elif src is tbl:
                row = tbl.currentRow()
                if row >= 0 and frz.currentRow() != row:
                    frz.selectRow(row)
            else:
                row = frz.currentRow()
        finally:
            self._chartSelBusy = False
        cd, nm = self._selectedWatchStk()
        self._applyWatchChartPick(cd, nm)

    def _onSurgeRowPicked(self):
        """급등주 표에서 사용자가 고르면 같은 차트 탭을 연다."""
        if getattr(self, "_watchRefreshBusy", False) or getattr(self, "_chartSelBusy", False):
            return
        cd, nm = self._selectedSurgeStk()
        self._applyWatchChartPick(cd, nm)

    def _selectedSurgeStk(self):
        from utils.stkCdUtil import normStkCd

        table = getattr(self, "surgeRankTbl", None)
        if table is None:
            return "", ""
        row = table.currentRow()
        if row < 0:
            return "", ""
        codeCell = table.item(row, 0)
        nameCell = table.item(row, 1)
        cd = normStkCd(codeCell.text() if codeCell is not None else "") or ""
        nm = str(nameCell.text() or "").strip() if nameCell is not None else ""
        return cd, nm

    def _applyWatchChartPick(self, cd, nm):
        """실제 행 선택만 차트 탭을 연다. 갱신 중이면 열지 않는다."""
        from utils.chartBarPlan import chartActionForWatchPick

        action = chartActionForWatchPick(
            True,
            cd,
            currentCode=getattr(self, "_chartStkCd", ""),
            refreshBusy=getattr(self, "_watchRefreshBusy", False),
            syncBusy=getattr(self, "_chartSelBusy", False),
            chartOpen=getattr(self, "_sideKey", "") == "chart",
        )
        if action == "ignore":
            return
        self._chartStkCd = cd
        self._chartStkNm = nm
        self._chartFetchTried = set()
        if action == "reload":
            self._reloadDeskChart()
            return
        btn = getattr(self, "chartTab", None)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._onSideTab("chart", True)

    def _openDeskChart(self):
        """차트 탭을 열 때: 선택 종목 일·주·월봉 로드 + 1초 라이브만."""
        if self._isSurgeWatchTab():
            cd, nm = self._selectedSurgeStk()
        else:
            cd, nm = self._selectedWatchStk()
        if not cd and self._isSurgeWatchTab():
            self._chartStkCd = ""
            self._chartStkNm = ""
            self.deskChart.clearChart("관심종목에서 종목을 선택하세요")
            if not self._chartLiveTimer.isActive():
                self._chartLiveTimer.start(1000)
            return
        if not cd:
            cds = list(getattr(self, "_watchPaintCds", None) or [])
            if cds:
                self._chartSelBusy = True
                try:
                    self.tradeWatchFrozen.selectRow(0)
                    self.tradeWatchTbl.selectRow(0)
                finally:
                    self._chartSelBusy = False
                cd, nm = self._selectedWatchStk()
        self._chartStkCd = cd
        self._chartStkNm = nm
        self._chartFetchTried = set()
        self._reloadDeskChart()
        if not self._chartLiveTimer.isActive():
            self._chartLiveTimer.start(1000)

    def _stopDeskChart(self):
        """탭을 접으면 그리기·타이머를 끈다. 장중 주문/0B 와 겹치지 않게."""
        if getattr(self, "_chartLiveTimer", None) is not None:
            self._chartLiveTimer.stop()

    def _reloadDeskChart(self, allowFetch=True):
        """일·주·월봉은 백그라운드에서만. 차트 탭이 아니면 조회하지 않는다."""
        if getattr(self, "_sideKey", "") != "chart":
            return
        cd = str(getattr(self, "_chartStkCd", "") or "")
        nm = str(getattr(self, "_chartStkNm", "") or "")
        if not cd:
            self.deskChart.clearChart()
            return
        self._chartLoadTok = int(getattr(self, "_chartLoadTok", 0) or 0) + 1
        tok = self._chartLoadTok
        period = str(getattr(self.deskChart, "_barTp", "") or "day")
        self.deskChart.headLbl.setText(f"{nm}({cd}) 차트 조회…")
        from utils.chartBarPlan import CHART_FETCH_TIMEOUT_SEC, chartFetchStartLine
        from utils.termLog import termLog

        termLog(chartFetchStartLine(cd, "조회"))
        QTimer.singleShot(
            int(CHART_FETCH_TIMEOUT_SEC * 1000),
            lambda tok=tok, code=cd, name=nm: self._expireDeskChart(tok, code, name, "조회"),
        )

        def _job():
            from utils.chartBarLoad import ensureChartBars, loadDeskChartBundle
            from utils.chartBarPlan import needsAutoFetch
            from utils.termLog import termLog

            try:
                bundle = loadDeskChartBundle(cd)
            except Exception as err:
                termLog(f"[deskChart] 차트 조회 실패 {cd}: {err}")
                bundle = {"day": [], "week": [], "month": [], "note": str(err)}
            key = (cd, period)
            tried = getattr(self, "_chartFetchTried", set())
            if allowFetch and needsAutoFetch(bundle, period) and key not in tried:
                tried.add(key)
                self._chartFetchTried = tried
                try:
                    got = ensureChartBars(cd) or {}
                except Exception as err:
                    termLog(f"[deskChart] 자동 조회 실패 {cd}: {err}")
                    got = {"ok": False, "errMsg": str(err)}
                try:
                    bundle = loadDeskChartBundle(cd)
                except Exception as err:
                    termLog(f"[deskChart] 재조회 실패 {cd}: {err}")
                    bundle = {"day": [], "week": [], "month": []}
                if needsAutoFetch(bundle, period):
                    note = str(got.get("errMsg") or "일·주·월봉을 받지 못했습니다")
                    bundle["note"] = note
                    termLog(f"[deskChart] {cd} {period} 없음 · {note}")
            if not any(bundle.get(key) for key in ("day", "week", "month")) and not bundle.get("note"):
                errs = bundle.get("errors") or []
                bundle["note"] = str(errs[0]) if errs else "데이터 없음"
            if tok != getattr(self, "_chartLoadTok", 0):
                return
            self.chartBarsOk.emit(cd, bundle, nm)

        threading.Thread(target=_job, daemon=True).start()

    def _onChartBarsMissing(self, period):
        """주·월 전환인데 봉이 없으면 일·주·월을 함께 받는다."""
        if getattr(self, "_sideKey", "") != "chart":
            return
        self._reloadDeskChart(allowFetch=True)

    def _expireDeskChart(self, tok, code, name, kind):
        """조회가 끝나지 않으면 헤더를 되돌린다. 늦은 결과는 버린다."""
        if int(tok or 0) != int(getattr(self, "_chartLoadTok", 0) or 0):
            return
        self._chartLoadTok = int(tok or 0) + 1
        from utils.termLog import termLog

        termLog(f"[deskChart] {kind} 시간 초과 {code}")
        title = f"{name}({code})" if name else str(code)
        self.deskChart.headLbl.setText(f"{title} {kind} 시간 초과")
        btn = getattr(self.deskChart, "btnRefresh", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("차트 최신화")

    def _onChartRefreshClick(self):
        """차트 최신화: 선택 종목 일봉 증분+마지막날 재조회 → DB 적재 → 차트 다시 읽기."""
        cd = str(getattr(self, "_chartStkCd", "") or "")
        nm = str(getattr(self, "_chartStkNm", "") or "")
        if not cd:
            self.deskChart.headLbl.setText("관심종목에서 종목을 먼저 선택하세요")
            return
        btn = self.deskChart.btnRefresh
        btn.setEnabled(False)
        btn.setText("갱신 중…")
        self.deskChart.headLbl.setText(f"{nm}({cd}) 일·주·월봉 최신화 중…")
        self._chartLoadTok = int(getattr(self, "_chartLoadTok", 0) or 0) + 1
        tok = self._chartLoadTok
        from utils.chartBarPlan import CHART_FETCH_TIMEOUT_SEC, chartFetchStartLine
        from utils.termLog import termLog

        termLog(chartFetchStartLine(cd, "최신화"))
        QTimer.singleShot(
            int(CHART_FETCH_TIMEOUT_SEC * 1000),
            lambda tok=tok, code=cd, name=nm: self._expireDeskChart(tok, code, name, "최신화"),
        )

        def _job():
            try:
                from utils.chartBarLoad import ensureChartBars

                res = ensureChartBars(cd, refetch=True) or {}
            except Exception as e:
                from utils.termLog import termLog

                termLog(f"[deskChart] 최신화 실패 {cd}: {e}")
                res = {"ok": False, "errMsg": str(e), "failCnt": 1}
            if tok != getattr(self, "_chartLoadTok", 0):
                return
            self.chartRefreshDone.emit(cd, res)

        threading.Thread(target=_job, daemon=True).start()

    def _onChartRefreshDone(self, stkCd, res):
        btn = self.deskChart.btnRefresh
        btn.setEnabled(True)
        btn.setText("차트 최신화")
        res = res or {}
        nm = str(getattr(self, "_chartStkNm", "") or "")
        if not res.get("ok", False) or int(res.get("failCnt", 0) or 0) > 0:
            err = res.get("errMsg") or res.get("fails") or "실패"
            self.deskChart.headLbl.setText(f"{nm}({stkCd}) 최신화 실패: {str(err)[:120]}")
            return
        msg = (
            f"{nm}({stkCd}) 최신화 완료 · {int(res.get('upsertCnt', 0) or 0)}건 "
            f"({res.get('startDt', '')}~{res.get('endDt', '')})"
        )
        try:
            from PySide6.QtWidgets import QToolTip  # noqa
        except Exception:
            pass
        if str(stkCd) == str(getattr(self, "_chartStkCd", "") or ""):
            self._reloadDeskChart()
        try:
            self.statusBar().showMessage(msg, 6000)
        except Exception:
            pass

    def _onChartBarsOk(self, stkCd, bars, stkNm):
        if getattr(self, "_sideKey", "") != "chart":
            return
        if str(stkCd or "") != str(getattr(self, "_chartStkCd", "") or ""):
            return
        ymd = datetime.now().strftime("%Y%m%d")
        bundle = bars if isinstance(bars, dict) else {"day": bars or []}
        self.deskChart.setChartBundle(stkCd, stkNm, bundle, liveYmd=ymd)
        self._tickDeskChart()

    def _tickDeskChart(self):
        """0B 캐시만 읽어 당일 종가·고저. getWatchLastPxMap(DB시드) 는 쓰지 않는다."""
        if getattr(self, "_sideKey", "") != "chart":
            return
        cd = str(getattr(self, "_chartStkCd", "") or "")
        if not cd:
            return
        try:
            from trading.watchRtMonitor import peekWatchLastPx

            info = peekWatchLastPx(cd) or {}
        except Exception:
            return
        px = info.get("curPrc") or info.get("krxPrc") or info.get("nxtPrc")
        if px is None:
            return
        self.deskChart.applyLivePx(px)

    def _titleText(self, text):
        """섹션 제목 표시. 표 헤더와 구분되도록 감싼다."""
        return f"【{text}】"

    def _sectionTitle(self, text):
        lbl = QLabel(self._titleText(text))
        lbl.setStyleSheet(
            "font-weight: 700; font-size: 15px; color: #111111;"
            " padding: 3px 10px; background-color: #e8eef5;"
            " border-left: 4px solid #1565c0;"
        )
        return lbl

    def _bindTradeLog(self):
        """워커 스레드 이벤트를 GUI 목록으로 받는다."""
        from ui.tradeLogBus import (
            initTradeLogBus,
            pushAlgEvtToDesk,
            pushFillToDesk,
        )
        from utils.tradeLogHook import (
            setAcntRefreshHook,
            setFillLogHook,
            setTradeLogHook,
        )

        bus = initTradeLogBus(self)
        # bus 는 GUI 스레드 객체라 Auto면 WS emit 이 Direct 로 타고 창이 죽는다
        queued = Qt.ConnectionType.QueuedConnection
        bus.logged.connect(self.appendTradeLine, queued)
        bus.tradeRow.connect(self.appendDayTrade, queued)
        bus.acntRefresh.connect(self.scheduleAcntRefresh, queued)
        setTradeLogHook(pushAlgEvtToDesk)
        setFillLogHook(pushFillToDesk)
        # 체결 스레드에서 QTimer.start 직접 호출 금지 — 시그널만 보낸다
        setAcntRefreshHook(lambda: bus.acntRefresh.emit())

    def _bindDeskErr(self):
        """termLog 실패·예외를 오류 탭으로만 받는다."""
        from ui.deskErrBus import initDeskErrBus, pushDeskErr
        from utils.termLog import setDeskErrHook

        bus = initDeskErrBus(self)
        bus.logged.connect(
            self.appendErrLine, Qt.ConnectionType.QueuedConnection
        )
        setDeskErrHook(pushDeskErr)

    def _rollDeskIfNewDay(self):
        """
        거래일 08:00 이후면 전날 화면을 지우고 당일 DB만 다시 읽는다.
        일자가 바뀌었으면 True.
        """
        from utils.deskLogStore import deskLogYmd
        from utils.termLog import termLog

        ymd = deskLogYmd()
        if ymd == getattr(self, "_deskLogYmd", None):
            return False
        self._deskLogYmd = ymd
        self.fillList.clear()
        if hasattr(self, "errList"):
            self.errList.clear()
        self.dayTbl.setRowCount(0)
        if hasattr(self, "_dayAlgHdr"):
            self._dayAlgHdr.clearRowMeta()
        self._dayCost = {}
        self._dayLedger = {}
        self._loadTodayTradeLog()
        self._loadTodayDayTrades()
        termLog(f"[매매창] 로그/거래 일자 전환 {ymd} (거래일 08:00)")
        return True

    def _loadTodayTradeLog(self):
        """표시 일자의 복기 이벤트 + 00 체결을 거래로그에 보여 준다 (deskTradeMode)."""
        try:
            from ui.tradeLogBus import evtKindOf, formatAlgEvtLine
            from utils.deskDataApi import listAlgEvt
            from utils.deskLogStore import deskLogYmd, listDeskLog
            from utils.tradeSession import getDeskTradeMode

            ymd = deskLogYmd()
            mode = getDeskTradeMode()
            self._deskLogYmd = ymd
            merged = []
            try:
                from trading.deskLiveAlgs import getDeskAlgTabs

                algCds = list(getDeskAlgTabs() or [])
            except Exception:
                algCds = []
            if not algCds:
                algCds = ["MTS01"]
            for algCd in algCds:
                for row in listAlgEvt(algCd, ymd, limit=400, mode=mode) or []:
                    tm = str(row.get("EVT_TM") or "").zfill(6)[-6:]
                    eid = int(row.get("EVT_ID") or 0)
                    merged.append(
                        (tm, eid, 0, evtKindOf(row), formatAlgEvtLine(row))
                    )
            for row in listDeskLog(ymd, src="FILL", limit=400, mode=mode) or []:
                tm = str(row.get("LOG_TM") or "").zfill(6)[-6:]
                lid = int(row.get("LOG_ID") or 0)
                merged.append(
                    (tm, lid, 1, str(row.get("KIND") or "FILL"), str(row.get("LINE") or ""))
                )
            merged.sort(key=lambda x: (x[0], x[2], x[1]))
            for _tm, _id, _src, kind, line in merged:
                if line:
                    # DB→화면 복원만 — 파일에는 이미 당일분 있으므로 다시 안 씀
                    self.appendTradeLine(kind, line, skipDayRoll=True, toFile=False)
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[매매창] 오늘 거래로그 로드 실패: {err}")

    def _loadDayLedger(self):
        """당일 매도 수익 — 전일 이월 평단(원장, deskTradeMode)."""
        try:
            from utils.algTradeHist import ledgerAvgMap
            from utils.deskLogStore import deskLogYmd
            from utils.tradeSession import getDeskTradeMode

            self._dayLedger = ledgerAvgMap(
                "MTS01", deskLogYmd(), mode=getDeskTradeMode()
            ) or {}
        except Exception:
            self._dayLedger = {}

    def _applyDayCost(self, sideKind, stkCd, pxRaw, qtyRaw):
        """
        당일 거래 수익률·수익금용 평단. 매수는 누적, 매도는 영웅문식(수수료·세금 차감).
        전일 이월 보유는 원장 BUY_PX 를 쓴다.
        반환: 매도 {pnlPct, pnlAmt}, 매수는 None.
        """
        from utils.algTradeHist import resolveSellAvgPx
        from utils.deskLogStore import deskLogYmd
        from utils.heroPnl import heroRealizedPnl, loadDeskFeeBuckets, takeSellFees
        from utils.stkCdUtil import normStkCd
        from utils.tradeSession import getDeskTradeMode

        cd = normStkCd(stkCd) or str(stkCd or "").strip()
        if not cd:
            return None
        try:
            px = abs(float(pxRaw))
        except (TypeError, ValueError):
            return None
        try:
            qty = int(float(qtyRaw or 0))
        except (TypeError, ValueError):
            qty = 0
        rec = self._dayCost.get(cd) or {"qty": 0, "avgPx": 0.0}
        if sideKind == "BUY":
            if qty < 1:
                qty = 1
            prevQty = int(rec.get("qty") or 0)
            prevAvg = float(rec.get("avgPx") or 0)
            newQty = prevQty + qty
            rec["avgPx"] = (prevAvg * prevQty + px * qty) / float(newQty)
            rec["qty"] = newQty
            self._dayCost[cd] = rec
            return None
        avgPx = resolveSellAvgPx(
            cd,
            deskLogYmd(),
            rec.get("avgPx"),
            getattr(self, "_dayLedger", None) or {},
        )
        if avgPx <= 0:
            return None
        if qty < 1:
            qty = 1
        left = int(rec.get("qty") or 0) - qty
        rec["qty"] = max(left, 0)
        if rec["qty"] <= 0:
            self._dayCost.pop(cd, None)
        else:
            self._dayCost[cd] = rec
        mode = getDeskTradeMode()
        ymd = deskLogYmd()
        # 거래탭 재적재 시 버킷을 매번 새로 쓰면 매도 순서가 꼬이므로
        # 일자·모드 캐시를 두고 소모한다.
        cacheKey = f"{ymd}:{mode}"
        if getattr(self, "_dayFeeCacheKey", None) != cacheKey:
            self._dayFeeBuckets = loadDeskFeeBuckets(ymd, mode=mode)
            self._dayFeeCacheKey = cacheKey
        buckets = getattr(self, "_dayFeeBuckets", None) or {}
        buyCmsn, sellCmsn, sellTax = takeSellFees(
            buckets.get(cd),
            qty,
            sellPx=px,
            buyPx=avgPx,
            mode=mode,
        )
        got = heroRealizedPnl(
            avgPx, px, qty, buyCmsn=buyCmsn, sellCmsn=sellCmsn, sellTax=sellTax
        )
        return {
            "pnlPct": float(got.get("pnlRt") or 0),
            "pnlAmt": float(got.get("netPnl") or 0),
            "evalPct": float(got.get("evalRt") or 0),
            "grossAmt": float(got.get("grossPnl") or 0),
        }

    def _loadTodayDayTrades(self):
        """오늘 주문·체결·실패만 시간순으로 당일 거래 그리드에 넣는다.
        주문취소 행과 짝이 되는 원주문(매수주문·매도주문)은 회색으로 남긴다.
        전량 미체결 취소된 매수 체결은 회색·평단 집계에서 뺀다.
        """
        try:
            from ui.tradeLogBus import (
                DAY_CNCL_EVT_TPS,
                DAY_ORD_EVT_TPS,
                DAY_TRADE_EVT_TPS,
                rowToDayTrade,
            )
            from utils.deskDataApi import listAlgEvt
            from utils.deskLogStore import deskLogYmd
            from utils.stkCdUtil import normStkCd
            from utils.tradeSession import getDeskTradeMode

            ymd = deskLogYmd()
            mode = getDeskTradeMode()
            self._loadDayLedger()
            # 수수료 버킷을 매도 순서대로 소모 — 로드 시작 시 초기화
            from utils.heroPnl import loadDeskFeeBuckets

            self._dayFeeBuckets = loadDeskFeeBuckets(ymd, mode=mode)
            self._dayFeeCacheKey = f"{ymd}:{mode}"
            try:
                from trading.deskLiveAlgs import getDeskAlgTabs

                algCds = list(getDeskAlgTabs() or [])
            except Exception:
                algCds = []
            if not algCds:
                algCds = ["MTS01"]
            rows = []
            for algCd in algCds:
                # 주문정보만 (SELL_FAIL 폭주·스킵 등 제외) — LIMIT 잘림 방지
                rows.extend(
                    listAlgEvt(
                        algCd,
                        ymd,
                        limit=2000,
                        mode=mode,
                        evtTps=DAY_TRADE_EVT_TPS,
                    )
                    or []
                )
            rows.sort(
                key=lambda r: (
                    str(r.get("EVT_TM") or "").zfill(6)[-6:],
                    int(r.get("EVT_ID") or 0),
                )
            )
            self._dayCost = {}
            self.dayTbl.setRowCount(0)
            if hasattr(self, "_dayAlgHdr"):
                self._dayAlgHdr.clearRowMeta()
            # (algCd, stkCd, sideKind) → 아직 취소 안 된 원주문
            ordStack = {}
            # (algCd, stkCd) → 아직 취소 안 된 매수 체결 (LIFO)
            buyStack = {}
            items = []  # [(item, srcRow)]
            for row in rows:
                tp = str(row.get("EVT_TP") or "").strip().upper()
                item = rowToDayTrade(row)
                if not item:
                    continue
                alg = str(item.get("algCd") or "").strip().upper()
                cd = normStkCd(item.get("stkCd"))
                sideKind = str(item.get("sideKind") or "")
                if tp in DAY_CNCL_EVT_TPS:
                    oKey = (alg, cd, sideKind)
                    oStack = ordStack.get(oKey) or []
                    if oStack:
                        # 취소된 원주문(매수주문/매도주문)도 회색
                        oStack.pop()["cancelled"] = True
                    if tp == "BUY_CNCL":
                        bKey = (alg, cd)
                        bStack = buyStack.get(bKey) or []
                        if bStack:
                            buyItem = bStack.pop()
                            try:
                                buyQty = int(float(buyItem.get("qty") or 0))
                            except (TypeError, ValueError):
                                buyQty = 0
                            try:
                                cnclQty = int(float(row.get("QTY") or 0))
                            except (TypeError, ValueError):
                                cnclQty = 0
                            # 전량 미체결 취소만 체결 행도 회색 — 부분체결 잔량은 매수 유지
                            if buyQty > 0 and cnclQty >= buyQty:
                                buyItem["cancelled"] = True
                    items.append((item, row))
                    continue
                items.append((item, row))
                if tp in DAY_ORD_EVT_TPS:
                    ordStack.setdefault((alg, cd, sideKind), []).append(item)
                # 평단 집계·전량취소 매칭은 실제 매수체결(BUY)만
                if tp == "BUY":
                    buyStack.setdefault((alg, cd), []).append(item)
            for item, srcRow in items:
                if item.get("cancelled"):
                    self._putDayTradeRow(item)
                    continue
                evtTp = str(item.get("evtTp") or "").upper()
                # 손익·평단은 체결 BUY/SELL 만
                if evtTp in ("BUY", "SELL"):
                    pnl = self._applyDayCost(
                        item["sideKind"],
                        item["stkCd"],
                        item.get("pxRaw"),
                        item.get("qty"),
                    )
                    if evtTp == "SELL" and isinstance(pnl, dict):
                        item = rowToDayTrade(
                            srcRow,
                            pnlPct=pnl.get("pnlPct"),
                            pnlAmt=pnl.get("pnlAmt"),
                            evalPct=pnl.get("evalPct"),
                        )
                self._putDayTradeRow(item)
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[매매창] 당일 거래 로드 실패: {err}")

    def appendDayTrade(self, row):
        """실시간 주문·체결·실패 한 줄을 당일 그리드에 붙인다.
        주문취소면 취소 행과 원주문을 회색으로 바꾼다.
        """
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, self, lambda: self.appendDayTrade(row))
            return
        if not self._rowMatchesDeskMode(row):
            return
        if self._rollDeskIfNewDay():
            return
        if not getattr(self, "_dayLedger", None):
            self._loadDayLedger()
        tp = str((row or {}).get("EVT_TP") or (row or {}).get("evtTp") or "").strip().upper()
        from ui.tradeLogBus import DAY_CNCL_EVT_TPS, DAY_TRADE_EVT_TPS, rowToDayTrade

        if tp not in DAY_TRADE_EVT_TPS:
            return
        if tp in DAY_CNCL_EVT_TPS:
            self._markDayCancelled(row)
            item = rowToDayTrade(row)
            if item:
                self._putDayTradeRow(item)
                self.dayTbl.scrollToBottom()
            return

        item = rowToDayTrade(row)
        if not item:
            return
        if tp in ("BUY", "SELL"):
            pnl = self._applyDayCost(
                item["sideKind"],
                item["stkCd"],
                item.get("pxRaw"),
                item.get("qty"),
            )
            if tp == "SELL" and isinstance(pnl, dict):
                item = rowToDayTrade(
                    row,
                    pnlPct=pnl.get("pnlPct"),
                    pnlAmt=pnl.get("pnlAmt"),
                    evalPct=pnl.get("evalPct"),
                )
        self._todayBuyAlgCache = {}
        self._putDayTradeRow(item)
        self.dayTbl.scrollToBottom()

    def _daySideEvtTp(self, sideCell):
        """칸에 저장된 이벤트. 없으면 글자로 주문/체결을 가린다."""
        if sideCell is None:
            return ""
        evt = str(sideCell.data(_DAY_SIDE_EVT_ROLE) or "").strip().upper()
        if evt:
            return evt
        text = str(sideCell.text() or "")
        if "주문취소" in text:
            return "BUY_ORD_CNCL" if "매수" in text else "SELL_ORD_CNCL"
        if "취소" in text:
            return "BUY_CNCL" if "매수" in text else "SELL_CNCL"
        if "주문" in text:
            return "BUY_ORD" if "매수" in text else "SELL_ORD"
        if text == "매수":
            return "BUY"
        if text == "매도":
            return "SELL"
        return ""

    def _paintDayRowCancelled(self, rowIdx):
        """한 행을 취소 거래로 보이게 회색 처리한다."""
        sideCell = self.dayTbl.item(rowIdx, 2)
        if sideCell is not None:
            sideCell.setData(_DAY_SIDE_CNCL_ROLE, 1)
        for c in range(self.dayTbl.columnCount()):
            cell = self.dayTbl.item(rowIdx, c)
            if cell is not None:
                cell.setForeground(QBrush(_DAY_CNCL_FG))

    def _markDayCancelled(self, row):
        """
        주문취소 — 같은 종목의 원주문(매수주문·매도주문)을 회색으로.
        BUY_CNCL 전량 미체결이면 매수 체결 행도 회색·평단에서 뺀다.
        부분체결 후 잔량 취소는 체결 색을 유지한다.
        """
        from utils.stkCdUtil import normStkCd

        cd = normStkCd((row or {}).get("STK_CD") or (row or {}).get("stkCd"))
        if not cd:
            return
        tp = str((row or {}).get("EVT_TP") or (row or {}).get("evtTp") or "").strip().upper()
        wantOrd = "BUY_ORD" if tp.startswith("BUY") else "SELL_ORD"
        try:
            cnclQty = int(float((row or {}).get("QTY") or (row or {}).get("qty") or 0))
        except (TypeError, ValueError):
            cnclQty = 0
        markedOrd = False
        for r in range(self.dayTbl.rowCount() - 1, -1, -1):
            sideCell = self.dayTbl.item(r, 2)
            if sideCell is None:
                continue
            if bool(sideCell.data(_DAY_SIDE_CNCL_ROLE)):
                continue
            rowCd = normStkCd(sideCell.data(_DAY_SIDE_STK_ROLE))
            if rowCd != cd:
                continue
            evt = self._daySideEvtTp(sideCell)
            if not markedOrd and evt == wantOrd:
                self._paintDayRowCancelled(r)
                markedOrd = True
                if tp != "BUY_CNCL":
                    self.dayTbl.viewport().update()
                    return
                continue
            # 전량 미체결 취소일 때만 매수 체결 행도 회색
            if tp == "BUY_CNCL" and evt == "BUY":
                try:
                    buyQty = int(float(sideCell.data(_DAY_SIDE_QTY_ROLE) or 0))
                except (TypeError, ValueError):
                    buyQty = 0
                if buyQty > 0 and cnclQty > 0 and cnclQty < buyQty:
                    self.dayTbl.viewport().update()
                    return
                self._paintDayRowCancelled(r)
                self._reverseDayBuy(cd, buyQty if buyQty > 0 else cnclQty)
                self.dayTbl.viewport().update()
                return
        self.dayTbl.viewport().update()

    def _reverseDayBuy(self, stkCd, qtyRaw):
        """미체결 취소로 무효가 된 매수를 당일 평단 집계에서 뺀다."""
        from utils.stkCdUtil import normStkCd

        cd = normStkCd(stkCd)
        rec = self._dayCost.get(cd)
        if not rec or not cd:
            return
        try:
            qty = int(float(qtyRaw or 0))
        except (TypeError, ValueError):
            qty = 0
        prevQty = int(rec.get("qty") or 0)
        if qty <= 0 or qty >= prevQty:
            self._dayCost.pop(cd, None)
            return
        rec["qty"] = prevQty - qty
        self._dayCost[cd] = rec

    def _putDayTradeRow(self, item):
        """당일 거래 1행. 수익금·수익률은 +/- 색, 배경은 매수/매도 칸만."""
        if not item:
            return
        r = self.dayTbl.rowCount()
        self.dayTbl.insertRow(r)
        vals = [
            item.get("tm") or "",
            item.get("stkNm") or "",
            item.get("side") or "",
            item.get("pnlAmt") or "",
            item.get("pnl") or "",
            item.get("px") or "",
            item.get("reason") or "",
        ]
        for c, val in enumerate(vals):
            cell = QTableWidgetItem(str(val))
            cell.setForeground(QBrush(QColor("#000000")))
            if c in (3, 4, 5):
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if c == 2:
                # 매수/매도 칸 — 색 구분·취소 매칭용
                cell.setData(_DAY_SIDE_KIND_ROLE, item.get("sideKind") or "")
                cell.setData(_DAY_SIDE_STK_ROLE, item.get("stkCd") or "")
                cell.setData(_DAY_SIDE_EVT_ROLE, item.get("evtTp") or "")
                cell.setData(
                    _DAY_SIDE_CNCL_ROLE, 1 if item.get("cancelled") else 0
                )
                try:
                    cell.setData(
                        _DAY_SIDE_QTY_ROLE,
                        int(float(item.get("qty") or 0)),
                    )
                except (TypeError, ValueError):
                    cell.setData(_DAY_SIDE_QTY_ROLE, 0)
            if item.get("cancelled"):
                cell.setForeground(QBrush(_DAY_CNCL_FG))
            self.dayTbl.setItem(r, c, cell)
        # 맨 왼쪽 행번호 — 해당 알고리즘 바탕색
        algCd = str(item.get("algCd") or "").strip()
        tip = algCd or "-"
        bgHex = "#E0E0E0"
        try:
            from trading.deskLiveAlgs import algBgColor, algDisplayName, isKnownAlgCd

            if isKnownAlgCd(algCd):
                bgHex = algBgColor(algCd)
                tip = algDisplayName(algCd) or algCd
        except Exception:
            pass
        if hasattr(self, "_dayAlgHdr"):
            self._dayAlgHdr.setRowMeta(r, bgHex, tip)
            self._dayAlgHdr.viewport().update()
    @staticmethod
    def _acctTail4(acctNo):
        """계좌번호 표시용 — 끝 4자리만 (전체 노출 방지)."""
        raw = str(acctNo or "").strip().replace("-", "").replace(" ", "")
        if not raw:
            return ""
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return ""
        return digits[-4:]

    def refreshSessionLabel(self):
        """로그인 모드를 상단에 표시. 설정 deskTradeMode 와 실제 토큰을 맞춘다."""
        try:
            from config.settingsStore import getDeskTradeMode

            want = getDeskTradeMode()
        except Exception:
            want = "paper"
        # 상단에는 끝 4자리만. 전체 번호는 계좌 탭·툴팁
        acctTail = self._acctTail4(getattr(self, "_deskAcctNo", ""))
        acctFull = str(getattr(self, "_deskAcctNo", "") or "").strip()
        acctSuffix = f" {acctTail}" if acctTail else ""
        tipBase = "투자모드·매매방식은 오른쪽 설정 탭에서 변경"
        if acctFull:
            tipBase = f"계좌 {acctFull}\n{tipBase}"
        self.sessionLbl.setToolTip(tipBase)
        if want == "real" and runtimeState.isRealLoggedIn():
            self.sessionLbl.setText(f"[실전투자]{acctSuffix}")
            self.sessionLbl.setStyleSheet(
                "font-weight: 600; font-size: 14px;"
                " background-color: #FFD400; color: #000000;"
                " padding: 2px 8px;"
            )
        elif want == "paper" and runtimeState.isPaperLoggedIn():
            self.sessionLbl.setText(f"[모의투자]{acctSuffix}")
            self.sessionLbl.setStyleSheet("font-weight: 600; font-size: 14px;")
        elif runtimeState.isRealLoggedIn():
            self.sessionLbl.setText(f"[실전투자·설정불일치]{acctSuffix}")
            self.sessionLbl.setStyleSheet(
                "font-weight: 600; font-size: 14px;"
                " background-color: #FFD400; color: #000000;"
                " padding: 2px 8px;"
            )
        elif runtimeState.isPaperLoggedIn():
            self.sessionLbl.setText(f"[모의투자·설정불일치]{acctSuffix}")
            self.sessionLbl.setStyleSheet("font-weight: 600; font-size: 14px;")
        else:
            bootErr = str(os.environ.get("SATP_BOOT_LOGIN_ERR") or "").strip()
            if bootErr:
                short = bootErr.splitlines()[0][:40]
                self.sessionLbl.setText(f"[미로그인] {short}")
                self.sessionLbl.setToolTip(bootErr)
            else:
                self.sessionLbl.setText("[미로그인]")
            self.sessionLbl.setStyleSheet("font-weight: 600; font-size: 14px;")
        self._refreshDeskRoleLbl()
        self._loadTradeModeCmb()
        self._refreshTokenAliveLbl()

    def _refreshDeskRoleLbl(self):
        """창 제목: 천둥이 MAIN|Client · 버전 (실전투자 옆 버전 라벨 없음)."""
        try:
            from config.settingsStore import getDeskRole

            role = getDeskRole()
        except Exception:
            role = "main"
        roleTxt = "Client" if role == "client" else "MAIN"
        try:
            from utils.appVersion import formatAppVersion

            ver = formatAppVersion()
        except Exception:
            ver = ""
        verTxt = f" · {ver}" if ver else ""
        self.setWindowTitle(f"천둥이 {roleTxt}{verTxt}")

    def _refreshTokenAliveLbl(self):
        """상단 토큰유효/무효/확인중. 만료시각은 표시하지 않는다."""
        if not hasattr(self, "tokenAliveLbl"):
            return
        try:
            from utils.tokenAuth import tokenAliveLabel
            from utils.tradeSession import getDeskTradeMode

            lab = tokenAliveLabel(getDeskTradeMode())
        except Exception:
            lab = ""
        self.tokenAliveLbl.setText(lab)
        if lab == "토큰유효":
            self.tokenAliveLbl.setStyleSheet(
                "font-weight: 600; font-size: 13px; padding: 2px 6px;"
                " background-color: #C8E6C9; color: #1B5E20;"
            )
        elif lab == "토큰무효":
            self.tokenAliveLbl.setStyleSheet(
                "font-weight: 600; font-size: 13px; padding: 2px 6px;"
                " background-color: #FFCDD2; color: #B71C1C;"
            )
        elif lab == "토큰확인중":
            self.tokenAliveLbl.setStyleSheet(
                "font-weight: 600; font-size: 13px; padding: 2px 6px;"
                " background-color: #FFF9C4; color: #F57F17;"
            )
        else:
            self.tokenAliveLbl.setStyleSheet(
                "font-weight: 600; font-size: 13px; padding: 2px 6px;"
            )
            self.tokenAliveLbl.setText("")

    def _loadTradeModeCmb(self):
        """저장된 투자모드를 콤보에 맞춘다."""
        if not hasattr(self, "tradeModeCmb"):
            return
        try:
            from config.settingsStore import getDeskTradeMode

            mode = getDeskTradeMode()
        except Exception:
            mode = "paper"
        idx = self.tradeModeCmb.findData(mode)
        if idx < 0:
            idx = 0
        self.tradeModeCmb.blockSignals(True)
        self.tradeModeCmb.setCurrentIndex(idx)
        self.tradeModeCmb.blockSignals(False)
        self._refreshTradeModeStatus()


    def _effectivePaperOnly(self):
        """설정상 자동매매 paperOnly (True면 실전 모드여도 주문 차단)."""
        try:
            from config.settingsStore import getMaTouchLive

            return bool((getMaTouchLive() or {}).get("paperOnly", True))
        except Exception:
            return True

    def _refreshTradeModeStatus(self):
        """설정탭에 현재 모드·주문가능 여부를 표시."""
        lbl = getattr(self, "tradeModeStatusLbl", None)
        if lbl is None:
            return
        try:
            from config.settingsStore import getDeskTradeMode

            mode = getDeskTradeMode()
        except Exception:
            mode = "paper"
        paper_only = self._effectivePaperOnly()
        if mode == "real" and not paper_only:
            lbl.setText("현재: 실전투자 · 자동매매 주문 가능")
            lbl.setStyleSheet(
                "font-size: 12px; font-weight: 600; color: #c62828; padding: 2px 0;"
            )
        elif mode == "real" and paper_only:
            lbl.setText(
                "현재: 실전 로그인 설정인데 paperOnly=ON → 자동매매 주문 차단.\n"
                "아래에서 실전투자로 다시 선택해 해제하세요."
            )
            lbl.setStyleSheet(
                "font-size: 12px; font-weight: 600; color: #ef6c00; padding: 2px 0;"
            )
        else:
            lbl.setText("현재: 모의투자 · 모의 계좌만 주문")
            lbl.setStyleSheet(
                "font-size: 12px; font-weight: 600; color: #1565c0; padding: 2px 0;"
            )

    def _syncPaperOnlyForMode(self, mode):
        """deskTradeMode 와 paperOnly 를 같이 맞춘다."""
        from utils.tradeSession import applyPaperOnlyOn, applyRealPaperOnlyOff

        if mode == "real":
            applyRealPaperOnlyOff()
        else:
            applyPaperOnlyOn()

    def _onTradeModeChanged(self, _idx=None):
        """모의↔실전 전환: 설정 저장 → paperOnly 동기 → 로그인 → WS 재연결."""
        mode = self.tradeModeCmb.currentData()
        if mode not in ("paper", "real"):
            return
        try:
            from config.settingsStore import getDeskTradeMode

            cur = getDeskTradeMode()
        except Exception:
            cur = "paper"
        paper_only = self._effectivePaperOnly()
        # 모드 문자열은 같아도 paperOnly 만 어긋난 경우(실전인데 주문차단) 재적용
        need_switch = (mode != cur) or (mode == "real" and paper_only) or (
            mode == "paper" and (not paper_only)
        )
        if not need_switch:
            self._refreshTradeModeStatus()
            return
        if mode == "real":
            ans = QMessageBox.warning(
                self,
                "실전투자 전환",
                "실전 API·실계좌로 전환합니다.\n"
                "자동매매 주문도 실전으로 나갑니다.\n\n"
                "계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                self._loadTradeModeCmb()
                return
        else:
            ans = QMessageBox.question(
                self,
                "모의투자 전환",
                "모의 API로 전환합니다. 계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                self._loadTradeModeCmb()
                return

        def _work():
            from telegram.commands.loginCommand import handleLogin
            from utils.termLog import termLog
            from utils.tradeSession import setDeskTradeMode

            setDeskTradeMode(mode)
            self._syncPaperOnlyForMode(mode)
            msg = handleLogin(mode, force=True)
            termLog(msg)
            try:
                from realtime.wsHub import restartWsHub

                restartWsHub()
            except Exception as err:
                termLog(f"[매매창] WS 재연결 실패: {err}")
            return msg

        try:
            _work()
        except Exception as err:
            QMessageBox.critical(self, "모드 전환 실패", str(err))
            self._loadTradeModeCmb()
            return
        # 모드 바뀌면 이전 계좌번호는 지우고, 새 모드 조회 후 표시
        self._deskAcctNo = ""
        self.refreshSessionLabel()
        # 거래로그·당일거래·알고손익도 모드별로 다시 읽는다
        try:
            self._reloadDeskLogsForMode()
        except Exception:
            pass
        try:
            # 로그인·WS 직후 TR 한도 여유를 두고 실전/모의 계좌를 다시 읽는다
            QTimer.singleShot(1200, self.refreshAcnt)
        except Exception:
            pass
        # 전략 파라미터 캐시 갱신
        try:
            from trading.maTouchLive import invalidateLiveParams

            invalidateLiveParams()
        except Exception:
            pass
        self._refreshTradeModeStatus()

    def _reloadDeskLogsForMode(self):
        """모의↔실전 전환 시 로그·거래·알고손익 화면을 해당 모드만 다시 채운다."""
        if hasattr(self, "fillsList"):
            self.fillList.clear()
        if hasattr(self, "dayTbl"):
            self.dayTbl.setRowCount(0)
        if hasattr(self, "_dayAlgHdr"):
            self._dayAlgHdr.clearRowMeta()
        self._dayCost = {}
        self._dayLedger = {}
        self._todayBuyAlgCache = {}
        self._loadTodayTradeLog()
        self._loadTodayDayTrades()
        self.refreshAlgPnl()

    def _deskModeOfRow(self, row):
        """이벤트/로그 행의 MODE. 비어 있으면 구데이터로 paper."""
        raw = str(
            (row or {}).get("MODE")
            or (row or {}).get("mode")
            or ""
        ).strip().lower()
        if raw in ("real", "live", "실전"):
            return "real"
        return "paper"

    def _rowMatchesDeskMode(self, row):
        try:
            from utils.tradeSession import getDeskTradeMode

            return self._deskModeOfRow(row) == getDeskTradeMode()
        except Exception:
            return True

    def _loadTradeExchCmb(self):
        """저장된 매매 거래소를 콤보에 맞춘다."""
        from config.settingsStore import getDeskTradeExch

        exch = getDeskTradeExch()
        idx = self.tradeExchCmb.findData(exch)
        if idx < 0:
            idx = 0
        self.tradeExchCmb.blockSignals(True)
        self.tradeExchCmb.setCurrentIndex(idx)
        self.tradeExchCmb.blockSignals(False)

    def _paintIndsBar(self):
        """상단 코스피·코스닥·선물(코스피200) 현재가·등락. 0J 없으면 마지막 종가."""
        try:
            from realtime.indsRtMonitor import getIndsLastMap
        except Exception:
            return
        try:
            pxMap = getIndsLastMap() or {}
        except Exception:
            pxMap = {}
        chips = []
        closeHint = False
        for cd, title in (("001", "코스피"), ("101", "코스닥"), ("201", "선물")):
            info = pxMap.get(cd) or {}
            prc = info.get("prc")
            flu = info.get("fluRt")
            src = str(info.get("src") or "")
            if prc is None:
                chips.append(f'<span style="color:#000000">{title} -</span>')
                continue
            fluTxt = ""
            valColor = "#000000"
            if flu is not None:
                fluTxt = f" {flu:+.2f}%"
                if flu > 0:
                    valColor = _PNL_PLUS.name()
                elif flu < 0:
                    valColor = _PNL_MINUS.name()
            # 실시간(0J)이 아니면 종가 표시. 등락은 직전 거래일 종가 대비.
            closeTag = ""
            if src != "0J":
                closeHint = True
                closeTag = (
                    ' <span style="color:#757575;font-weight:500;font-size:12px">'
                    "종가</span>"
                )
            chips.append(
                f'<span style="color:#000000">{title} </span>'
                f'<span style="color:{valColor}">{prc:,.2f}{fluTxt}</span>'
                f"{closeTag}"
            )
        self.indsLbl.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(chips))
        if closeHint:
            dts = []
            for cd in ("001", "101", "201"):
                ymd = str((pxMap.get(cd) or {}).get("tradeDt") or "")
                if len(ymd) == 8 and ymd not in dts:
                    dts.append(ymd)
            dtTxt = ""
            if dts:
                ymd = dts[0]
                dtTxt = f" ({ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]})"
            self.indsLbl.setToolTip(
                "실시간(0J) 없음 — 일봉 마지막 종가"
                f"{dtTxt}. 등락은 그 전 거래일 종가 대비. "
                "선물=코스피200 지수(근월물 아님)."
            )
        else:
            self.indsLbl.setToolTip(
                "코스피(001)·코스닥(101)·선물=코스피200(201) 업종지수. "
                "장중은 실시간(0J), 없으면 일봉 마지막 종가. "
                "선물은 근월물이 아니라 코스피200 지수다."
            )

    def _onTradeExchChanged(self, _idx):
        """매매 주문 거래소만 저장. 시세(0B)는 KRX+NXT 를 항상 받는다."""
        from config.settingsStore import setDeskTradeExch
        from utils.termLog import termLog

        exch = str(self.tradeExchCmb.currentData() or "KRX")
        saved = setDeskTradeExch(exch)
        # 0B 는 KRX+NXT 시세를 항상 받으므로 재구독하지 않는다. 주문 거래소만 바뀐다.
        termLog(f"[매매창] 매매 설정 저장: {saved}")

    def refreshOpsAlgBar(self):
        """하단: 유효 가동 알고리즘만. 꺼진 알고리즘 이름·설명은 두지 않는다."""
        try:
            from trading.deskLiveAlgs import formatOpsAlgBar, listDeskLiveAlgs, operatingDeskRows

            shown = formatOpsAlgBar(operatingDeskRows(listDeskLiveAlgs()))
            if shown.get("visible") and shown.get("text"):
                self.opsAlgLbl.setText(shown["text"])
                self.opsAlgLbl.setVisible(True)
            else:
                self.opsAlgLbl.clear()
                self.opsAlgLbl.setVisible(False)
        except Exception:
            self.opsAlgLbl.clear()
            self.opsAlgLbl.setVisible(False)
        # 설정이 바뀌었으면 전략 탭도 맞춤
        try:
            if hasattr(self, "stratAlgTabBar"):
                self._rebuildStratAlgTabs()
                if getattr(self, "_sideKey", None) == "strat":
                    self.refreshStratInfo()
            if hasattr(self, "watchAlgTabBar"):
                self._rebuildWatchAlgTabs()
        except Exception:
            pass

    def _rebuildStratAlgTabs(self):
        """전략 설명 탭 — 유효 가동 알고리즘만. 보유로 켜짐 여부를 판단하지 않는다."""
        try:
            from trading.deskLiveAlgs import algBgColor, listDeskLiveAlgs, operatingDeskRows

            algs = operatingDeskRows(listDeskLiveAlgs())
        except Exception:
            algs = []
            algBgColor = None
        cds = [r.get("algCd") or "" for r in algs if r.get("algCd")]
        bar = self.stratAlgTabBar

        def _paintTabBg(idx, alg):
            try:
                bg = algBgColor(alg) if algBgColor is not None else "#ECEFF1"
            except Exception:
                bg = "#ECEFF1"
            if hasattr(bar, "setTabBg"):
                bar.setTabBg(idx, bg)

        # 동일하면 이름·색만 갱신. 가동이 없으면 이전 설명을 지운다.
        if cds == getattr(self, "_stratAlgTabCds", None):
            for i, r in enumerate(algs):
                if i < bar.count():
                    alg = r.get("algCd") or ""
                    bar.setTabText(i, r.get("name") or alg)
                    bar.setTabToolTip(i, str(alg))
                    _paintTabBg(i, alg)
            if not cds or self._stratAlgCd not in cds:
                self._stratAlgCd = cds[0] if cds else ""
                self._syncStratInfoVisibility()
            if hasattr(bar, "update"):
                bar.update()
            return
        self._stratAlgTabsBusy = True
        try:
            if hasattr(bar, "clearTabBg"):
                bar.clearTabBg()
            while bar.count():
                bar.removeTab(0)
            self._stratAlgTabCds = []
            for r in algs:
                alg = r.get("algCd") or ""
                name = r.get("name") or alg
                idx = bar.addTab(name)
                bar.setTabToolTip(idx, str(alg))
                bar.setTabData(idx, alg)
                _paintTabBg(idx, alg)
                self._stratAlgTabCds.append(alg)
            cur = self._stratAlgCd if self._stratAlgCd in self._stratAlgTabCds else (
                self._stratAlgTabCds[0] if self._stratAlgTabCds else ""
            )
            self._stratAlgCd = cur
            if cur in self._stratAlgTabCds:
                bar.setCurrentIndex(self._stratAlgTabCds.index(cur))
            self._syncStratInfoVisibility()
        finally:
            self._stratAlgTabsBusy = False
            bar.update()

    def _onStratAlgTabChanged(self, idx):
        if getattr(self, "_stratAlgTabsBusy", False):
            return
        if idx < 0:
            return
        alg = self.stratAlgTabBar.tabData(idx)
        if not alg and 0 <= idx < len(getattr(self, "_stratAlgTabCds", [])):
            alg = self._stratAlgTabCds[idx]
        self._stratAlgCd = str(alg or "")
        if self._stratAlgCd not in (getattr(self, "_stratAlgTabCds", None) or []):
            cds = getattr(self, "_stratAlgTabCds", None) or []
            self._stratAlgCd = cds[0] if cds else ""
        self.refreshStratInfo()

    def _rebuildWatchAlgTabs(self):
        """조회 탭. 기본은 급등주만. 실계좌·주도주 코드는 남기고 설정으로 되돌린다."""
        try:
            from trading.deskLiveAlgs import resolveMonitorSelection, visibleMonitorTabs

            tabs = tuple(
                (row["key"], row["name"], row["bg"]) for row in visibleMonitorTabs()
            )
        except Exception:
            tabs = (("surge", "급등주", "#80DEEA"),)

            def resolveMonitorSelection(current, visibleKeys):
                keys = [str(k) for k in (visibleKeys or []) if str(k)]
                cur = str(current or "")
                return cur if cur in keys else (keys[0] if keys else "")
        cds = [k for k, _n, _c in tabs]
        bar = self.watchAlgTabBar
        if cds == getattr(self, "_watchAlgTabCds", None) and bar.count() == len(cds):
            return
        self._watchAlgTabsBusy = True
        try:
            bar.clearTabBg()
            while bar.count():
                bar.removeTab(0)
            self._watchAlgTabCds = []
            for key, name, bg in tabs:
                idx = bar.addTab(name)
                bar.setTabToolTip(idx, name)
                bar.setTabData(idx, key)
                try:
                    bar.setTabBg(idx, bg)
                except Exception:
                    pass
                self._watchAlgTabCds.append(key)
            cur = resolveMonitorSelection(self._watchAlgCd, self._watchAlgTabCds)
            self._watchAlgCd = cur
            if cur in self._watchAlgTabCds:
                bar.setCurrentIndex(self._watchAlgTabCds.index(cur))
        finally:
            self._watchAlgTabsBusy = False
            bar.update()
        self.refreshTradeWatch()

    def _onWatchAlgTabChanged(self, idx):
        if getattr(self, "_watchAlgTabsBusy", False):
            return
        if idx < 0:
            return
        alg = self.watchAlgTabBar.tabData(idx)
        if not alg and 0 <= idx < len(getattr(self, "_watchAlgTabCds", [])):
            alg = self._watchAlgTabCds[idx]
        self._watchAlgCd = str(alg or "")
        cds = getattr(self, "_watchAlgTabCds", None) or []
        if self._watchAlgCd not in cds:
            self._watchAlgCd = cds[0] if cds else ""
        try:
            _f = self.tradeWatchFrozen
            _f.setItemDelegateForColumn(1, SignedPnlDelegate(_f))
            _f.setItemDelegateForColumn(2, RsiDelegate(_f))
            _f.setItemDelegateForColumn(3, NetOrderDelegate(_f))
            _f.setItemDelegateForColumn(4, PriceOrderDelegate(_f))
        except Exception:
            pass
        self._syncLeaderRankTools()
        self._syncSurgeTools()
        self.refreshTradeWatch()

    def _listWatchRowsForAlg(self, algCd):
        """
        해당 알고리즘 관리종목 ∪ 원장 미청산.
        client/서버모드: deskDataApi(STP), main 로컬: algStkStore.
        """
        import time

        from utils.stkCdUtil import normStkCd

        alg = str(algCd or "").strip()
        if not alg:
            return []
        now = time.time()
        ent = (getattr(self, "_watchRowCache", None) or {}).get(alg)
        if ent and (now - float(ent.get("at") or 0)) < 20:
            return list(ent.get("rows") or [])
        byCd = {}
        try:
            from utils.deskDataApi import listAlgStk

            for row in listAlgStk(alg) or []:
                cd = normStkCd(row.get("stkCd") or row.get("STK_CD"))
                if not cd:
                    continue
                byCd[cd] = {
                    "STK_CD": cd,
                    "STK_NM": row.get("stkNm") or row.get("STK_NM") or cd,
                    "PRIORITY": 9999,
                }
        except Exception:
            pass
        try:
            from utils.stratOrdLedgerStore import listOpenLedgerRows

            for row in listOpenLedgerRows(alg) or []:
                cd = normStkCd(row.get("STK_CD"))
                if not cd:
                    continue
                nm = row.get("STK_NM") or cd
                if cd not in byCd:
                    byCd[cd] = {"STK_CD": cd, "STK_NM": nm, "PRIORITY": 0}
                else:
                    byCd[cd]["PRIORITY"] = 0
                    if not byCd[cd].get("STK_NM"):
                        byCd[cd]["STK_NM"] = nm
        except Exception:
            pass
        rows = list(byCd.values())
        cache = getattr(self, "_watchRowCache", None)
        if cache is None:
            self._watchRowCache = {}
            cache = self._watchRowCache
        cache[alg] = {"at": now, "rows": rows}
        return list(rows)

    def _syncWatchAlgTabCounts(self):
        """관심·주도주 탭 이름 유지."""
        bar = getattr(self, "watchAlgTabBar", None)
        if bar is None or bar.count() < 1:
            return
        cds = list(getattr(self, "_watchAlgTabCds", None) or [])
        labels = {"acnt": "실계좌", "leader": "주도주", "rank20": "20일누적", "surge": "급등주"}
        for i, key in enumerate(cds):
            if i >= bar.count():
                break
            bar.setTabText(i, labels.get(key, key))
        bar.update()

    def _syncStratInfoVisibility(self):
        """가동 알고리즘이 없으면 설명 칸을 비우고 숨긴다."""
        box = getattr(self, "stratBox", None)
        if box is None:
            return
        cds = getattr(self, "_stratAlgTabCds", None) or []
        if cds and self._stratAlgCd in cds:
            box.setVisible(True)
            return
        self._stratAlgCd = cds[0] if cds else ""
        box.clear()
        box.setVisible(bool(cds))

    def refreshStratInfo(self):
        """선택된 가동 알고리즘 명세만. 꺼진 알고리즘 설명은 남기지 않는다."""
        cds = getattr(self, "_stratAlgTabCds", None) or []
        algCd = str(getattr(self, "_stratAlgCd", "") or "")
        if algCd not in cds:
            self._syncStratInfoVisibility()
            return
        try:
            from trading.deskLiveAlgs import stratIdForAlg

            stratId = stratIdForAlg(algCd) or (
                "maTouchScale_v1" if algCd == "MTS01" else ""
            )
        except Exception:
            stratId = "maTouchScale_v1" if algCd == "MTS01" else ""
        if not stratId:
            self.stratBox.clear()
            return
        # 탭 선택 동기
        cds = getattr(self, "_stratAlgTabCds", []) or []
        if algCd in cds:
            want = cds.index(algCd)
            if self.stratAlgTabBar.currentIndex() != want:
                self._stratAlgTabsBusy = True
                try:
                    self.stratAlgTabBar.setCurrentIndex(want)
                finally:
                    self._stratAlgTabsBusy = False
        from utils.stratDeskSummary import buildStratDeskText, maTouchDeskHtml

        text = buildStratDeskText(algCd, stratId)
        # 보유 갱신 타이머가 자주 setPlainText 하면 스크롤이 맨 위로 리셋됨
        if self.stratBox.toPlainText() == text:
            return
        bar = self.stratBox.verticalScrollBar()
        oldPos = bar.value() if bar is not None else 0
        if str(algCd) == "MTS01" or str(stratId or "").startswith("maTouchScale"):
            self.stratBox.setHtml(maTouchDeskHtml(text))
        else:
            self.stratBox.setPlainText(text)
        if bar is not None:
            bar.setValue(min(oldPos, bar.maximum()))

    def appendTradeLine(self, kind, text, skipDayRoll=False, toFile=True):
        """거래로그 한 줄을 맨 위에 넣는다. toFile=False 는 당일 파일 재적재 시."""
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(
                0,
                self,
                lambda: self.appendTradeLine(kind, text, skipDayRoll, toFile),
            )
            return
        if not skipDayRoll:
            if self._rollDeskIfNewDay():
                return
        line = str(text or "").strip()
        if not line:
            return
        item = QListWidgetItem(line)
        # 종류색 대신 알고리즘 글자색. [00]·태그 없음은 검정
        item.setForeground(QBrush(QColor(_tradeLogFgHex(line))))
        self.fillList.insertItem(0, item)
        while self.fillList.count() > 400:
            self.fillList.takeItem(self.fillList.count() - 1)
        if toFile:
            try:
                from utils.deskFileLog import appendDeskFileLog

                appendDeskFileLog(line, tag=str(kind or "TRADE").upper())
            except Exception:
                pass

    def appendFillLine(self, text):
        """하위 호환 — 종류 없이 한 줄."""
        self.appendTradeLine("FILL", text)

    def appendErrLine(self, text):
        """오류 탭 맨 위에 한 줄. 거래로그와 섞지 않는다."""
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, self, lambda: self.appendErrLine(text))
            return
        line = str(text or "").strip()
        if not line:
            return
        lst = getattr(self, "errList", None)
        if lst is None:
            return
        item = QListWidgetItem(line)
        item.setForeground(QBrush(QColor("#c41e3a")))
        lst.insertItem(0, item)
        while lst.count() > 400:
            lst.takeItem(lst.count() - 1)
        try:
            from utils.deskFileLog import appendDeskFileLog

            appendDeskFileLog(line, tag="ERR")
        except Exception:
            pass

    def scheduleAcntRefresh(self):
        """체결 직후 2초 뒤 1회. 연속 체결은 타이머를 다시 잡는다."""
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, self, self.scheduleAcntRefresh)
            return
        self._acntSoon.start(2000)

    def _setAcntRows(self, rows):
        """계좌 탭 2열 표. rows: [(항목, 내용, color|None), ...]"""
        tbl = self.acctTbl
        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(rows or []))
            for i, item in enumerate(rows or []):
                key = item[0] if len(item) > 0 else ""
                val = item[1] if len(item) > 1 else ""
                color = item[2] if len(item) > 2 else None
                kCell = QTableWidgetItem(str(key))
                vCell = QTableWidgetItem(str(val))
                kCell.setFlags(kCell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                vCell.setFlags(vCell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                vCell.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if color:
                    vCell.setForeground(QBrush(QColor(color)))
                tbl.setItem(i, 0, kCell)
                tbl.setItem(i, 1, vCell)
            self._fitAcntCols()
        finally:
            tbl.setUpdatesEnabled(True)

    def _fitAcntCols(self):
        """항목 열은 글자폭보다 조금 넓게. 내용 열은 남은 칸을 채운다."""
        tbl = self.acctTbl
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tbl.resizeColumnToContents(0)
        tbl.setColumnWidth(0, max(tbl.columnWidth(0) + 24, 96))
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _pnlColor(self, pnl):
        try:
            n = float(pnl)
        except (TypeError, ValueError):
            return None
        if n > 0:
            return _PNL_PLUS.name()
        if n < 0:
            return _PNL_MINUS.name()
        return None

    def refreshAlgPnl(self):
        """계좌 탭 — 알고리즘별 당일/월간/누적 손익 (AI_ALG_PNL_DAY, deskTradeMode)."""
        try:
            from utils.tradeSession import getDeskTradeMode

            mode = getDeskTradeMode()
        except Exception:
            mode = "paper"
        try:
            from utils.algPnlDayStore import buildAlgPnlView, formatAlgPnlCell

            data = buildAlgPnlView(mode=mode, refresh=True) or {}
            rows = data.get("rows") or []
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[매매창] 알고리즘손익 조회 실패: {err}")
            rows = []
        tbl = self.algPnlTbl
        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(rows))
            for i, r in enumerate(rows):
                nm = f"{r.get('algNm') or r.get('algCd')}\n({r.get('algCd')})"
                cells = [
                    nm,
                    formatAlgPnlCell(
                        r.get("tdyPnl"), r.get("tdyPnlRt"), r.get("tdyWin"), r.get("tdyLoss")
                    ),
                    formatAlgPnlCell(
                        r.get("monPnl"), r.get("monPnlRt"), r.get("monWin"), r.get("monLoss")
                    ),
                    formatAlgPnlCell(
                        r.get("totPnl"), r.get("totPnlRt"), r.get("totWin"), r.get("totLoss")
                    ),
                ]
                pnls = [None, r.get("tdyPnl"), r.get("monPnl"), r.get("totPnl")]
                for col, txt in enumerate(cells):
                    item = QTableWidgetItem(txt)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col == 0:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                        )
                    else:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                        c = self._pnlColor(pnls[col])
                        if c:
                            item.setForeground(QBrush(QColor(c)))
                    tbl.setItem(i, col, item)
                tbl.setRowHeight(i, 52)
            hdr = tbl.horizontalHeader()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            for col in (1, 2, 3):
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            n = len(rows)
            modeTag = "실전" if mode == "real" else "모의"
            self.algPnlTitle.setText(self._titleText(f"알고리즘 손익 · {modeTag} ({n})"))
        finally:
            tbl.setUpdatesEnabled(True)

    def refreshAcnt(self):
        """deskTradeMode 기준 kt00004(+계좌번호) 백그라운드 조회."""
        if self._acntBusy:
            return
        try:
            from utils.tradeSession import isModeLoggedIn

            loggedIn = isModeLoggedIn()
        except Exception:
            loggedIn = runtimeState.isLoggedIn()
        if not loggedIn:
            self._deskAcctNo = ""
            bootErr = str(os.environ.get("SATP_BOOT_LOGIN_ERR") or "").strip()
            if bootErr:
                self._setAcntRows(
                    [
                        ("상태", "키움 미로그인"),
                        ("원인", bootErr[:180]),
                    ]
                )
            else:
                self._setAcntRows([("상태", "미로그인 — 계좌를 조회할 수 없습니다.")])
            try:
                from utils.tokenAuth import setTokenAlive
                from utils.tradeSession import getDeskTradeMode

                setTokenAlive(getDeskTradeMode(), None)
            except Exception:
                pass
            self._refreshTokenAliveLbl()
            return
        self._acntBusy = True
        threading.Thread(target=self._fetchAcntBg, daemon=True, name="deskAcnt").start()

    def _fetchAcntBg(self):
        import time

        from api.getAcntEvlt import fetchAcntEvlt
        from api.getAcntNo import fetchAcntNo
        from utils.tokenAuth import (
            isTokenAuthFailText,
            setTokenAlive,
            tryRenewOnAuthFail,
        )
        from utils.tradeSession import getDeskTradeMode, getOrdSession, modeLabel

        try:
            mode = getDeskTradeMode()
            sess = getOrdSession(paperOnly=False)
            if sess is None:
                setTokenAlive(mode, None)
                self.acntFail.emit(f"{modeLabel(mode)} 미로그인")
                return
            hostUrl, token, tokenType = sess
            label = modeLabel(mode)

            def _doEvlt(h, tok, tt):
                # 계좌번호(ka00001) — 실패해도 평가현황은 계속 조회
                acct = ""
                noRes = fetchAcntNo(h, tok, tokenType=tt)
                if noRes.get("ok"):
                    acct = str(noRes.get("acctNo") or "").strip()
                if mode == "paper":
                    time.sleep(1.1)
                res = fetchAcntEvlt(
                    h, tok, qryTp="1", dmstStexTp="KRX", tokenType=tt
                )
                return res, acct

            result, acctNo = _doEvlt(hostUrl, token, tokenType)
            if not result.get("ok"):
                err = result.get("errMsg") or "조회 실패"
                # 토큰 무효면 재발급 후 계좌조회 1회 재시도
                if isTokenAuthFailText(err):
                    setTokenAlive(mode, False)
                    if tryRenewOnAuthFail(mode, reportTelegram=True):
                        sess2 = getOrdSession(paperOnly=False)
                        if sess2 is not None:
                            if mode == "paper":
                                time.sleep(1.1)
                            hostUrl, token, tokenType = sess2
                            result, acctNo = _doEvlt(hostUrl, token, tokenType)
                if not result.get("ok"):
                    err = result.get("errMsg") or "조회 실패"
                    if isTokenAuthFailText(err):
                        setTokenAlive(mode, False)
                    if acctNo:
                        err = f"{err} (계좌 {acctNo})"
                    self.acntFail.emit(err)
                    return
            setTokenAlive(mode, True)
            self.acntOk.emit(result.get("body") or {}, label, acctNo)
        except Exception as err:
            self.acntFail.emit(str(err))

    def _applyAcntFail(self, errMsg):
        self._acntBusy = False
        self._setAcntRows([("상태", f"계좌 조회 실패: {errMsg}")])
        from utils.termLog import termLog
        from utils.tokenAuth import isTokenAuthFailText, setTokenAlive
        from utils.tradeSession import getDeskTradeMode

        termLog(f"[매매창] 계좌 조회 실패: {errMsg}")
        try:
            if isTokenAuthFailText(errMsg):
                setTokenAlive(getDeskTradeMode(), False)
        except Exception:
            pass
        self._refreshTokenAliveLbl()

    def _deskHoldFilterSets(self):
        """
        관리종목(전 운영 alg ∪ 매매대상), 관심종목, 미청산 원장(전 운영 전략).
        """
        from utils.stkCdUtil import normStkCd
        from utils.deskDataApi import listActiveWatch, listAlgStk, listTradeWatch

        try:
            from trading.deskLiveAlgs import listDeskLiveAlgCds

            algCds = listDeskLiveAlgCds(enabledOnly=False) or ["MTS01", "MSE01"]
        except Exception:
            algCds = ["MTS01", "MSE01"]

        algSet = set()
        for alg in algCds:
            try:
                for row in listAlgStk(alg) or []:
                    cd = normStkCd(row.get("stkCd") or row.get("STK_CD"))
                    if cd:
                        algSet.add(cd)
            except Exception:
                pass
        try:
            for row in listTradeWatch(limit=180) or []:
                cd = normStkCd(row.get("STK_CD"))
                if cd:
                    algSet.add(cd)
        except Exception:
            pass
        watchSet = set()
        try:
            for row in listActiveWatch(mktTp="KR") or []:
                cd = normStkCd(row.get("STK_CD"))
                if cd:
                    watchSet.add(cd)
        except Exception:
            watchSet = set()
        openSet = set()
        openAlgMap = {}
        try:
            from utils.stratOrdLedgerStore import listOpenLedgerRows

            for row in listOpenLedgerRows(None) or []:
                cd = normStkCd(row.get("STK_CD"))
                alg = str(row.get("ALG_CD") or "").strip()
                if not cd:
                    continue
                openSet.add(cd)
                openAlgMap.setdefault(cd, [])
                if alg and alg not in openAlgMap[cd]:
                    openAlgMap[cd].append(alg)
        except Exception:
            openSet = set()
            openAlgMap = {}
        # 당일 BUY 복기·메모리 포지션 — 원장 INSERT 전에 행번호 색이 빠지지 않게
        self._mergeHoldAlgExtras(openSet, openAlgMap)
        self._holdOpenAlgMap = openAlgMap
        return algSet, watchSet, openSet

    def _mergeHoldAlgExtras(self, openSet, openAlgMap):
        """원장에 없는 당일 신규매수 알고리즘을 맵에 보탠다."""
        from utils.stkCdUtil import normStkCd

        extra = {}
        try:
            from trading.maTouchLive import listOpenPosAlgMap

            for cd, alg in (listOpenPosAlgMap() or {}).items():
                ncd = normStkCd(cd)
                if ncd and alg:
                    extra.setdefault(ncd, [])
                    if alg not in extra[ncd]:
                        extra[ncd].append(alg)
        except Exception:
            pass
        try:
            buyMap = self._peekTodayBuyAlgMap() or {}
            for cd, algs in buyMap.items():
                ncd = normStkCd(cd)
                if not ncd:
                    continue
                extra.setdefault(ncd, [])
                for alg in algs or []:
                    if alg and alg not in extra[ncd]:
                        extra[ncd].append(alg)
        except Exception:
            pass
        for cd, algs in extra.items():
            openSet.add(cd)
            openAlgMap.setdefault(cd, [])
            for alg in algs:
                if alg and alg not in openAlgMap[cd]:
                    openAlgMap[cd].append(alg)

    def _peekTodayBuyAlgMap(self):
        """당일 BUY 종목→algCd. 20초 캐시 (관심 3초 갱신마다 DB를 치지 않음)."""
        import time

        from utils.deskLogStore import deskLogYmd

        ymd = deskLogYmd()
        now = time.time()
        ent = getattr(self, "_todayBuyAlgCache", None) or {}
        if ent.get("ymd") == ymd and (now - float(ent.get("at") or 0)) < 20:
            return dict(ent.get("map") or {})
        mp = {}
        try:
            from trading.deskLiveAlgs import getDeskAlgTabs
            from utils.deskDataApi import listBuyAlgMap
            from utils.tradeSession import getDeskTradeMode

            mp = listBuyAlgMap(
                ymd, getDeskAlgTabs() or [], mode=getDeskTradeMode()
            ) or {}
        except Exception:
            mp = {}
        self._todayBuyAlgCache = {"ymd": ymd, "at": now, "map": mp}
        return dict(mp)

    def _applyAcnt(self, body, modeLabel, acctNo=""):
        """kt00004 본문을 계좌 텍스트·보유 표에 넣는다."""
        from api.getAcntEvlt import fmtAmt, fmtPct, heroHoldPnl, parsePadNum, parsePctNum
        from utils.acntPnlDayStore import formatPnlLine, upsertAcntPnlDay
        from utils.algTradeHist import sumDeskSellPnlDetail
        from utils.deskLogStore import deskLogYmd
        from utils.stkCdUtil import normStkCd
        from utils.termLog import termLog
        from utils.tradeSession import getDeskTradeMode

        self._acntBusy = False
        self._deskAcctNo = str(acctNo or "").strip()
        try:
            from utils.tokenAuth import setTokenAlive

            setTokenAlive(getDeskTradeMode(), True)
        except Exception:
            pass
        self.refreshSessionLabel()
        # 손익 집계·DB 키는 설정 모드 기준 (모의·실전 토큰이 둘 다 있어도)
        mode = getDeskTradeMode()

        # 당일 손익 = 당일 매도 거래만 (거래탭과 동일). 키움 tdy_lspft·예탁증감 사용 안 함.
        tdyTrade = {"pnl": 0.0, "pnlRt": 0.0}
        try:
            tdyTrade = sumDeskSellPnlDetail(tradeDt=deskLogYmd(), mode=mode) or tdyTrade
        except Exception as err:
            termLog(f"[매매창] 당일매도손익 집계 실패: {err}")
        tdyPnl = float(tdyTrade.get("pnl") or 0)
        tdyRt = float(tdyTrade.get("pnlRt") or 0)

        pnlSnap = {}
        try:
            pnlSnap = upsertAcntPnlDay(body, mode=mode) or {}
        except Exception as err:
            termLog(f"[매매창] 계좌손익 적재 실패: {err}")
            pnlSnap = {}
        try:
            from trading.maTouchLive import syncFromAcntEvlt

            # 하드스탑 평단을 키움 계좌평가와 같게 맞춘다
            # (내부에서 복수전략 가드용 계좌보유 캐시도 갱신)
            syncFromAcntEvlt(body)
        except Exception as err:
            termLog(f"[매매창] 이평터치 평단동기 실패: {err}")
        try:
            from trading.crossAlgBuyGuard import noteAcntHoldings

            # maTouch 포지션이 비어 sync가 early-return 해도 보유 캐시는 갱신
            noteAcntHoldings(body)
        except Exception as err:
            termLog(f"[매매창] 계좌보유캐시 실패: {err}")

        tdyVal = formatPnlLine("", tdyPnl, tdyRt).strip()
        monVal = (
            formatPnlLine("", pnlSnap.get("monPnl"), pnlSnap.get("monRt")).strip()
            if pnlSnap.get("ok")
            else f"{fmtAmt(body.get('lspft2'))}원 ({fmtPct(body.get('lspft_ratio'))}%)"
        )
        yearVal = (
            formatPnlLine("", pnlSnap.get("yearPnl"), pnlSnap.get("yearRt")).strip()
            if pnlSnap.get("ok")
            else "-원 (-%)"
        )
        acctDisp = self._deskAcctNo or "-"
        # 매수가능금액: 주문가능 필드가 있으면 그것, 없으면 D+2 추정예수금
        buyAbleNum = None
        buyAbleKey = ""
        cashRtTxt = "-"
        stkEvltNum = 0
        try:
            for k in ("ord_alow_amt", "ord_alowa", "100stk_ord_alow_amt", "d2_entra", "entr"):
                v = parsePadNum(body.get(k))
                if v is not None:
                    buyAbleNum = v
                    buyAbleKey = k
                    break
            stkEvltNum = parsePadNum(body.get("tot_est_amt")) or 0
            cashNum = max(int(buyAbleNum or 0), 0)
            totAsetNum = cashNum + max(int(stkEvltNum), 0)
            cashRtTxt = f"{cashNum / totAsetNum * 100:.1f}%" if totAsetNum > 0 else "-"
        except Exception as err:
            termLog(f"[매매창] 매수가능/현금비율 계산 실패: {err}")
        dbgSig = (buyAbleKey, buyAbleNum, cashRtTxt)
        if getattr(self, "_acntCashDbgSig", None) != dbgSig:
            self._acntCashDbgSig = dbgSig
            termLog(
                f"[매매창] 계좌표 매수가능={buyAbleNum}({buyAbleKey or '필드없음'}) "
                f"현금비율={cashRtTxt} 필드={sorted(k for k in body.keys() if 'alow' in k or 'entr' in k)}"
            )
        self._deskTotEvltForWeight = int(stkEvltNum)
        self._setAcntRows(
            [
                ("구분", modeLabel),
                ("계좌번호", acctDisp),
                ("예수금", f"{fmtAmt(body.get('entr'))}원"),
                ("D+2추정", f"{fmtAmt(body.get('d2_entra'))}원"),
                ("매수가능금액", f"{fmtAmt(buyAbleNum)}원" if buyAbleNum is not None else "-"),
                ("현금비율", cashRtTxt),
                ("유가평가", f"{fmtAmt(body.get('tot_est_amt'))}원"),
                ("예탁자산", f"{fmtAmt(body.get('aset_evlt_amt'))}원"),
                ("총매입", f"{fmtAmt(body.get('tot_pur_amt'))}원"),
                ("당일손익", tdyVal, self._pnlColor(tdyPnl)),
                ("월간손익", monVal, self._pnlColor(pnlSnap.get("monPnl"))),
                ("년간손익", yearVal, self._pnlColor(pnlSnap.get("yearPnl"))),
            ]
        )
        self.refreshAlgPnl()

        algSet, watchSet, openSet = self._deskHoldFilterSets()
        firstBuyMap = {}
        try:
            from utils.stratOrdLedgerStore import openFirstBuyDtMap

            firstBuyMap = openFirstBuyDtMap() or {}
        except Exception:
            firstBuyMap = {}
        holds = []
        skipCds = set()
        rawList = body.get("stk_acnt_evlt_prst") or []
        if isinstance(rawList, list):
            for item in rawList:
                if not isinstance(item, dict):
                    continue
                qtyNum = parsePadNum(item.get("rmnd_qty"))
                # 잔량 0 은 청산으로 보고 표에서 뺀다.
                if qtyNum is None or qtyNum <= 0:
                    continue
                cd = normStkCd(item.get("stk_cd")) or ""
                if not cd:
                    continue
                # 잔량>0 은 일단 모은 뒤, 매매 등록(RAM01 TRADE)만 좌측에 남긴다.
                deskOpen = cd in openSet
                managedWatch = cd in algSet and cd in watchSet
                if not deskOpen and not managedWatch:
                    # 전략 외 보유 — 표에는 두고 로그만 (숨기지 않음)
                    skipCds.add(cd)
                avgNum = parsePadNum(item.get("avg_prc")) or 0
                curNum = parsePadNum(item.get("cur_prc")) or 0
                purNum = parsePadNum(item.get("pur_amt"))
                if purNum is None:
                    purNum = int(avgNum) * int(qtyNum)
                # 영웅문과 동일: 평가손익·수익률은 매입금액(수수료 포함) 기준
                # 0B=계좌 cur 이면 pl_rt 그대로, 시세가 바뀌면 pur_amt 로 재계산
                apiPnl = parsePadNum(item.get("pl_amt"))
                apiRt = parsePctNum(item.get("pl_rt"))
                pnlNum, plRtNum = heroHoldPnl(
                    curNum,
                    qtyNum,
                    purNum,
                    avgNum,
                    apiPnl,
                    apiRt,
                    apiCurPx=curNum,
                )
                if pnlNum is None:
                    pnlNum = 0
                evltNum = parsePadNum(item.get("evlt_amt"))
                if evltNum is None and curNum:
                    evltNum = int(curNum) * int(qtyNum)
                holds.append(
                    {
                        "stkNm": str(item.get("stk_nm") or "").strip(),
                        "stkCd": cd,
                        "qty": fmtAmt(qtyNum),
                        "avgPrc": fmtAmt(item.get("avg_prc")),
                        "curPrc": fmtAmt(item.get("cur_prc")),
                        "pnl": f"{fmtAmt(pnlNum)} ({fmtPct(plRtNum if plRtNum is not None else item.get('pl_rt'))}%)",
                        "qtyNum": int(qtyNum),
                        "avgNum": int(avgNum),
                        "curNum": int(curNum),
                        "apiCurNum": int(curNum),
                        "apiPnlNum": int(apiPnl) if apiPnl is not None else None,
                        "apiPlRtNum": float(apiRt) if apiRt is not None else None,
                        "purNum": int(purNum or 0),
                        "evltNum": int(evltNum or 0),
                        "pnlNum": int(pnlNum or 0),
                        "plRtNum": float(plRtNum) if plRtNum is not None else None,
                        "algCds": list(
                            (getattr(self, "_holdOpenAlgMap", {}) or {}).get(cd) or []
                        ),
                        # 원장 FIRST_BUY_DT. 없으면 표에서 '-'
                        "firstBuyDt": firstBuyMap.get(cd) or "",
                    }
                )
        # 동일 전략끼리 전열 (algCd → 종목명)
        try:
            from trading.deskLiveAlgs import algDisplayName

            def _holdSortKey(row):
                algs = row.get("algCds") or []
                primary = algs[0] if algs else "ZZZZ"
                return (primary, str(row.get("stkNm") or ""), str(row.get("stkCd") or ""))

            holds.sort(key=_holdSortKey)
            for row in holds:
                algs = row.get("algCds") or []
                if algs:
                    row["algLabel"] = " · ".join(algDisplayName(a) for a in algs)
                else:
                    row["algLabel"] = "-"
        except Exception:
            for row in holds:
                row["algLabel"] = ",".join(row.get("algCds") or []) or "-"
            holds.sort(
                key=lambda r: (
                    (r.get("algCds") or ["ZZZZ"])[0],
                    str(r.get("stkNm") or ""),
                )
            )
        # 실계좌관리: 보유 전체는 관심, 매매 버튼 종목만 좌측
        self._acntAllHolds = list(holds)
        syncGot = {}
        try:
            from trading.realAcntMgr import isTradeOn, syncAcntHolds

            syncGot = syncAcntHolds(self._acntAllHolds) or {}
        except Exception as err:
            termLog(f"[매매창] 실계좌 관심 동기화 실패: {err}")

            def isTradeOn(_cd):
                return False

        tradeHolds = []
        for row in self._acntAllHolds:
            if not isTradeOn(row.get("stkCd")):
                continue
            row["algCds"] = ["RAM01"]
            row["algLabel"] = "실계좌"
            tradeHolds.append(row)
        holds = tradeHolds
        self._holdSkipCds = set()
        self._holdRows = holds
        if syncGot.get("changed"):
            try:
                from trading.watchRtMonitor import reregWatchForExch

                reregWatchForExch("실계좌동기화")
            except Exception:
                pass
        self._acntReady = True
        self._paintHoldings()
        # 원장 청산 반영 후 관심 행번호 색을 보유와 맞춘다
        self.refreshTradeWatch()
        if self._sideKey == "strat":
            self.refreshStratInfo()

    def _updateHoldSum(self, rows, pxMap):
        """화면에 보이는 보유만 합산. 0B 현재가가 있으면 영웅문식(매입금액 기준)으로 재계산."""
        from api.getAcntEvlt import heroHoldPnl

        totPur = 0
        totEvlt = 0
        totPnl = 0
        cnt = 0
        for row in rows or []:
            qty = int(row.get("qtyNum") or 0)
            if qty <= 0:
                continue
            cnt += 1
            avg = int(row.get("avgNum") or 0)
            pur = int(row.get("purNum") or 0)
            if pur <= 0 and avg > 0:
                pur = avg * qty
            totPur += pur
            cd = str(row.get("stkCd") or "")
            apiCur = row.get("apiCurNum") or row.get("curNum")
            live = _holdLivePx(pxMap.get(cd) or {}, apiCur)
            if live and qty > 0:
                totEvlt += live * qty
            else:
                totEvlt += int(row.get("evltNum") or 0)
            pnlWon, _rt = heroHoldPnl(
                live or int(row.get("curNum") or 0),
                qty,
                pur,
                avg,
                row.get("apiPnlNum", row.get("pnlNum")),
                row.get("apiPlRtNum", row.get("plRtNum")),
                apiCurPx=apiCur,
            )
            if pnlWon is not None:
                totPnl += int(pnlWon)
        if totPur > 0:
            # 영웅문 합계 수익률 = 총평가손익 / 총매입
            rtTxt = f"{totPnl / totPur * 100.0:+.2f}%"
        else:
            rtTxt = "-"
        if totPnl > 0:
            signedCss = "#d32f2f"
        elif totPnl < 0:
            signedCss = "#1976d2"
        else:
            signedCss = "#000000"
        self.holdSumLbl.setText(
            f'<span style="color:#000000">총매입 {totPur:,}원  ·  '
            f"총평가 {totEvlt:,}원  ·  {cnt}종  ·  "
            f"평가수익률 </span>"
            f'<span style="color:{signedCss}">{rtTxt}</span>'
            f'<span style="color:#000000">  ·  평가금 </span>'
            f'<span style="color:{signedCss}">{totPnl:+,}원</span>'
        )
        self.holdSumLbl.setStyleSheet("color: #000000; font-weight: 600;")

    def _paintHoldings(self):
        """보유 표. 현재가는 0B 캐시가 있으면 그걸로 덮고, 손익은 영웅문식(매입금액)으로."""
        from api.getAcntEvlt import heroHoldPnl
        from trading.watchRtMonitor import getWatchLastPxMap
        from utils.stkCdUtil import normStkCd

        pxMap = {}
        try:
            pxMap = getWatchLastPxMap() or {}
        except Exception:
            pxMap = {}
        rows = self._holdRows or []
        self._updateHoldSum(rows, pxMap)
        tbl = self.holdTbl
        tbl.setUpdatesEnabled(False)
        try:
            if hasattr(self, "_holdAlgHdr"):
                self._holdAlgHdr.clearRowMeta()
            tbl.setRowCount(len(rows))
            missFlu = []
            forceFlu = []
            pxHint = {}
            for row in rows:
                cd = normStkCd(row.get("stkCd"))
                tick = pxMap.get(cd) or {}
                apiCur = int(row.get("apiCurNum") or row.get("curNum") or 0)
                livePx = _holdLivePx(tick, apiCur) or apiCur
                if cd and livePx:
                    pxHint[cd] = livePx
                # 보유 등락은 키움 공식(ka10001). dayClose·빈칸은 force.
                if not cd:
                    continue
                src = str(tick.get("src") or "")
                hasFlu = (
                    _hasQuote(tick.get("fluRt"))
                    or _hasQuote(tick.get("krxFlu"))
                    or _hasQuote(tick.get("nxtFlu"))
                )
                if src == "dayClose" or not hasFlu:
                    forceFlu.append(cd)
            if forceFlu:
                try:
                    from trading.watchRtMonitor import fillMissingFluQuotes

                    # 보유표는 항상 키움 공식 우선 (STP/DB 억지 +8% 방지)
                    fillMissingFluQuotes(
                        list(dict.fromkeys(forceFlu)),
                        pxByCd=pxHint,
                        force=True,
                    )
                    pxMap = getWatchLastPxMap() or pxMap
                except Exception:
                    pass
            # 보유비중 분모 = 계좌 전체 유가평가(tot_est_amt), 없으면 표시 보유 합계
            holdTotEv = int(getattr(self, "_deskTotEvltForWeight", 0) or 0)
            if holdTotEv <= 0:
                for _r in rows:
                    _cd = normStkCd(_r.get("stkCd"))
                    _ac = int(_r.get("apiCurNum") or _r.get("curNum") or 0)
                    _lv = _holdLivePx(pxMap.get(_cd) or {}, _ac) or _ac
                    holdTotEv += int(_lv or 0) * int(_r.get("qtyNum") or 0)
            for i, row in enumerate(rows):
                cd = normStkCd(row.get("stkCd"))
                tick = pxMap.get(cd) or {}
                prcRaw, fluRaw = _dispQuote(tick)
                apiCur = int(row.get("apiCurNum") or row.get("curNum") or 0)
                live = _holdLivePx(tick, apiCur)
                # dayClose 시드면 화면 현재가도 계좌가로 맞춤
                if str(tick.get("src") or "") == "dayClose" and live:
                    cur = _fmtPx(live)
                else:
                    cur = _fmtPx(prcRaw) or str(row.get("curPrc") or "")
                if not live:
                    live = apiCur
                avg = int(row.get("avgNum") or 0)
                qty = int(row.get("qtyNum") or 0)
                pur = int(row.get("purNum") or 0)
                pnlWon, pnlRt = heroHoldPnl(
                    live,
                    qty,
                    pur,
                    avg,
                    row.get("apiPnlNum", row.get("pnlNum")),
                    row.get("apiPlRtNum", row.get("plRtNum")),
                    apiCurPx=apiCur,
                )
                if pnlWon is None:
                    pnlWon = 0
                pnlAmtTxt = f"{int(pnlWon):+,}"
                pnlRtTxt = f"{float(pnlRt):+.2f}%" if pnlRt is not None else "-"
                fluTxt = _fmtFlu(fluRaw)
                # dayClose 캐시 등락이 남아 있으면 키움 공식값으로 한 번 더 맞춤
                if str(tick.get("src") or "") == "dayClose" and cd:
                    try:
                        from utils.liveOhlcSnap import fetchOfficialFlu

                        offFlu = fetchOfficialFlu(cd)
                        if offFlu is not None:
                            fluTxt = _fmtFlu(f"{float(offFlu):+.2f}")
                    except Exception:
                        pass
                purTxt = f"{pur:,}" if pur > 0 else f"{int(row.get('purNum') or 0):,}"
                if live and qty > 0:
                    evltTxt = f"{live * qty:,}"
                else:
                    evltTxt = f"{int(row.get('evltNum') or 0):,}"
                try:
                    _evN = int(evltTxt.replace(",", "") or 0)
                except ValueError:
                    _evN = 0
                wtTxt = f"{_evN / holdTotEv * 100:.1f}%" if holdTotEv > 0 else "-"
                try:
                    from utils.stratOrdLedgerStore import fmtYmdDash

                    firstBuyTxt = fmtYmdDash(row.get("firstBuyDt")) or "-"
                except Exception:
                    firstBuyTxt = "-"
                vals = [
                    str(row.get("stkNm") or ""),
                    str(row.get("avgPrc") or ""),
                    pnlAmtTxt,
                    pnlRtTxt,
                    str(row.get("qty") or ""),
                    fluTxt,
                    wtTxt,
                    purTxt,
                    evltTxt,
                    cur,
                    firstBuyTxt,
                ]
                if pnlWon > 0:
                    pnlColor = _PNL_PLUS
                elif pnlWon < 0:
                    pnlColor = _PNL_MINUS
                else:
                    pnlColor = _PNL_ZERO
                # 미체결 주문(이평터치 대기) 있으면 종목명 연노랑
                ordPending = False
                if cd:
                    try:
                        from trading.maTouchLive import hasOpenOrdWait

                        ordPending = bool(hasOpenOrdWait(cd))
                    except Exception:
                        ordPending = False
                for col, val in enumerate(vals):
                    item = QTableWidgetItem(val)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(QBrush(QColor("#000000")))
                    # 0종목명 좌측, 마지막(최초매수일) 가운데, 나머지 우측
                    if col == len(vals) - 1:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                    elif col >= 1:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    if col in (2, 3):
                        item.setForeground(QBrush(pnlColor))
                    if col == 0:
                        item.setBackground(
                            QBrush(_HOLD_ORD_BG if ordPending else _HOLD_ORD_BG_CLEAR)
                        )
                    tbl.setItem(i, col, item)
                # 행번호 바탕색 = 알고리즘 구분, 툴팁 = 알고리즘명
                algs = list(row.get("algCds") or [])
                primary = algs[0] if algs else ""
                tip = str(row.get("algLabel") or "-")
                try:
                    from trading.deskLiveAlgs import algBgColor, algDisplayName

                    bgHex = algBgColor(primary) if primary else "#E0E0E0"
                    if algs:
                        tip = " · ".join(algDisplayName(a) for a in algs)
                except Exception:
                    bgHex = "#E0E0E0"
                if hasattr(self, "_holdAlgHdr"):
                    self._holdAlgHdr.setRowMeta(i, bgHex, tip)
            if hasattr(self, "_holdAlgHdr"):
                self._holdAlgHdr.viewport().update()
            self._fitHoldCols()
        finally:
            tbl.setUpdatesEnabled(True)

    def _fitHoldCols(self):
        """보유 그리드: 헤더·셀 중 가장 긴 글자폭으로 컬럼을 맞춰 잘리지 않게 한다."""
        tbl = self.holdTbl
        hdr = tbl.horizontalHeader()
        cellFm = tbl.fontMetrics()
        hdrFm = hdr.fontMetrics()
        # 셀 좌우 패딩(delegate 10) + 헤더 정렬표시·여백
        cellPad = 28
        hdrPad = 32
        minPx = 56

        def textW(fm, text):
            s = str(text or "")
            return max(fm.horizontalAdvance(s), fm.boundingRect(s).width())

        for col in range(tbl.columnCount()):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            hi = tbl.horizontalHeaderItem(col)
            maxW = textW(hdrFm, hi.text() if hi else "") + hdrPad
            for row in range(tbl.rowCount()):
                item = tbl.item(row, col)
                if item is None:
                    continue
                maxW = max(maxW, textW(cellFm, item.text()) + cellPad)
            tbl.setColumnWidth(col, max(maxW, minPx))

    def setHoldings(self, rows):
        """rows: [{stkNm, stkCd, qty, avgPrc, curPrc}, ...]"""
        self._holdRows = list(rows or [])
        self._paintHoldings()

    def _watchBoughtAlgMap(self):
        """
        관심종목 행번호 색: 계좌 보유(kt00004) ∩ 미청산 원장.
        원장만 남은 유령 포지션(솔트룩스 등)은 색을 주지 않는다.
        """
        from utils.stkCdUtil import normStkCd

        openMap = getattr(self, "_holdOpenAlgMap", None) or {}
        if not openMap:
            out = {}
            try:
                from utils.stratOrdLedgerStore import listOpenLedgerRows

                for row in listOpenLedgerRows(None) or []:
                    cd = normStkCd(row.get("STK_CD"))
                    alg = str(row.get("ALG_CD") or "").strip()
                    if not cd or not alg:
                        continue
                    lst = out.setdefault(cd, [])
                    if alg not in lst:
                        lst.append(alg)
            except Exception:
                out = {}
            openMap = out
        openMap = {cd: list(algs) for cd, algs in openMap.items() if cd and algs}
        try:
            from trading.maTouchLive import listOpenPosAlgMap

            for cd, alg in (listOpenPosAlgMap() or {}).items():
                ncd = normStkCd(cd)
                if not ncd or not alg:
                    continue
                lst = openMap.setdefault(ncd, [])
                if alg not in lst:
                    lst.append(alg)
        except Exception:
            pass
        try:
            for cd, algs in (self._peekTodayBuyAlgMap() or {}).items():
                ncd = normStkCd(cd)
                if not ncd:
                    continue
                lst = openMap.setdefault(ncd, [])
                for alg in algs or []:
                    if alg and alg not in lst:
                        lst.append(alg)
        except Exception:
            pass
        # 계좌 조회 전이면 원장·당일매수로 색을 준다
        if not getattr(self, "_acntReady", False):
            return openMap
        holdCds = {
            normStkCd(r.get("stkCd"))
            for r in (self._holdRows or [])
            if int(r.get("qtyNum") or 0) > 0
        }
        return {cd: algs for cd, algs in openMap.items() if cd in holdCds}

    def _peekWatchNetMap(self, algCd, stkCds):
        """캐시만 본다. DB는 치지 않는다."""
        import time

        alg = str(algCd or "").strip()
        cds = tuple(sorted({c for c in (stkCds or []) if c}))
        ent = (getattr(self, "_watchNetCache", None) or {}).get(alg)
        if not ent:
            return None
        if ent.get("cds") != cds:
            return None
        if (time.time() - float(ent.get("at") or 0)) >= 60:
            return None
        return ent.get("map") if ent.get("map") is not None else {}

    def _kickWatchNetLoad(self, algCd, stkCds):
        """수급 20일을 백그라운드에서 읽는다. 캐시·진행 중이면 건너뛴다."""
        alg = str(algCd or "").strip()
        cds = tuple(sorted({c for c in (stkCds or []) if c}))
        if not alg:
            return
        if self._peekWatchNetMap(alg, cds) is not None:
            return
        loading = getattr(self, "_watchNetLoading", None)
        if loading is None:
            self._watchNetLoading = set()
            loading = self._watchNetLoading
        if alg in loading:
            return
        loading.add(alg)
        self._watchNetWantCds[alg] = cds

        def _work():
            mp = {}
            try:
                from utils.dayInvSrStore import loadNetSumLastNDays

                if cds:
                    mp = loadNetSumLastNDays(cds, days=20) or {}
            except Exception:
                mp = {}
            self.watchNetOk.emit(alg, mp)

        threading.Thread(target=_work, daemon=True).start()

    def _onWatchNetOk(self, algCd, mp):
        """워커 완료 — 캐시 저장 후 현재 탭이면 순매수 칸만 갱신."""
        import time

        alg = str(algCd or "").strip()
        loading = getattr(self, "_watchNetLoading", None)
        if loading is not None:
            loading.discard(alg)
        cds = (getattr(self, "_watchNetWantCds", None) or {}).get(alg, ())
        cache = getattr(self, "_watchNetCache", None)
        if cache is None:
            self._watchNetCache = {}
            cache = self._watchNetCache
        cache[alg] = {"at": time.time(), "cds": cds, "map": mp or {}}
        if alg == str(getattr(self, "_watchAlgCd", "") or ""):
            self._watchLastNetMap = mp or {}
            self._watchLastNetLoaded = True
            self._applyWatchNetToGrid(mp or {}, loaded=True)

    def _watchSnapFor(self, stkCd, curPx=None):
        """업종(DB) + 시총/영업이익/ROE(캐시)."""
        from utils.stkWatchTipSnap import getCachedBasic, mergeWatchTipSnap

        cd = str(stkCd or "")
        meta = (getattr(self, "_watchTipMeta", None) or {}).get(cd) or {}
        return mergeWatchTipSnap(meta, getCachedBasic(cd), curPx=curPx)

    def _kickWatchSnapLoad(self, stkCds):
        """ka10001 시총·영업이익·ROE. 캐시 없는 종목만."""
        from utils.stkWatchTipSnap import fillBasicsFromKa10001, listMissingBasicCds

        cds = [c for c in (stkCds or []) if c]
        if not cds or getattr(self, "_watchSnapLoading", False):
            if cds:
                prev = list(getattr(self, "_watchSnapPending", None) or [])
                self._watchSnapPending = list(dict.fromkeys(prev + cds))
            return
        if not listMissingBasicCds(cds):
            return
        self._watchSnapLoading = True
        self._watchSnapPending = []

        def _work():
            try:
                fillBasicsFromKa10001(cds)
            except Exception:
                pass
            self.watchSnapOk.emit()

        threading.Thread(target=_work, daemon=True).start()

    def _onWatchSnapOk(self):
        self._watchSnapLoading = False
        alg = str(getattr(self, "_watchAlgCd", "") or "")
        netMap = getattr(self, "_watchLastNetMap", None)
        loaded = bool(getattr(self, "_watchLastNetLoaded", False))
        if netMap is None:
            peeked = self._peekWatchNetMap(
                alg, getattr(self, "_watchPaintCds", None) or []
            )
            loaded = peeked is not None
            netMap = peeked or {}
        self._applyWatchNetToGrid(netMap or {}, loaded=loaded)
        pending = list(getattr(self, "_watchSnapPending", None) or [])
        self._watchSnapPending = []
        if pending:
            self._kickWatchSnapLoad(pending)

    def _applyWatchNetToGrid(self, netMap, loaded=True):
        """이미 그려 둔 관심종목 행의 순매수 칸만 채운다."""
        frz = getattr(self, "tradeWatchFrozen", None)
        if frz is None:
            return
        cds = list(getattr(self, "_watchPaintCds", None) or [])
        n = min(frz.rowCount(), len(cds))
        for i in range(n):
            rec = (netMap or {}).get(cds[i]) or {}
            px = None
            tbl = getattr(self, "tradeWatchTbl", None)
            if tbl is not None:
                pxItem = tbl.item(i, 0)
                if pxItem is not None:
                    px = _absPxNum(pxItem.text())
            snap = self._watchSnapFor(cds[i], curPx=px)
            netTxt, netTip, netMeta = _watchNetCellParts(
                rec if rec else None, loaded=loaded, snap=snap
            )
            item = frz.item(i, 3)
            if item is None:
                item = QTableWidgetItem(netTxt)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                frz.setItem(i, 3, item)
            else:
                item.setText(netTxt)
            item.setToolTip(netTip)
            item.setData(Qt.ItemDataRole.UserRole, netMeta)
            nmItem = frz.item(i, 0)
            if nmItem is not None:
                nmItem.setToolTip(netTip)
        frz.viewport().update()

    def _prefetchWatchAlgs(self):
        """다른 알고리즘 탭 수급을 미리 읽어 클릭 지연을 줄인다."""
        self._watchPrefetchQueued = False
        from utils.stkCdUtil import normStkCd

        allCds = []
        for alg in getattr(self, "_watchAlgTabCds", None) or []:
            try:
                rows = self._listWatchRowsForAlg(alg) or []
                cds = [normStkCd(r.get("STK_CD")) for r in rows]
                allCds.extend(cds)
                self._kickWatchNetLoad(alg, cds)
            except Exception:
                pass
        try:
            self._kickWatchSnapLoad(allCds)
        except Exception:
            pass

    def _watchNet20MapFor(self, stkCds):
        """호환용. 캐시만 반환하고 없으면 백그라운드 적재를 건다."""
        alg = str(getattr(self, "_watchAlgCd", None) or "MTS01")
        mp = self._peekWatchNetMap(alg, stkCds)
        if mp is None:
            self._kickWatchNetLoad(alg, stkCds)
            return {}
        return mp

    def refreshTradeWatch(self):
        """현재 알고리즘 탭의 관리종목(AI_ALG_STK ∪ 미청산)을 그리드에 채운다."""
        if getattr(self, "_watchRefreshBusy", False):
            return
        if not hasattr(self, "tradeWatchFrozen") or not hasattr(self, "tradeWatchTbl"):
            return
        self._watchRefreshBusy = True
        try:
            self._fillTradeWatch()
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[매매창] 관심종목 갱신 실패: {err}")
        finally:
            self._watchRefreshBusy = False

    def _fillRiseRankWatch(self):
        """금일·주간·월간 상위 20, 업종 등락 순위."""
        from utils.riseRankBoard import riseRank20Snapshot, riseRankSnapshot

        kind = str(getattr(self, "_watchAlgCd", None) or "day")
        if kind == "leader":
            # 주도주 탭 = 20일누적 순위. 20일 집계 전이면 금일 순위로 대체.
            kind = "rank20"
        if kind not in ("day", "week", "month", "inds", "rank20"):
            kind = "day"
        snap = riseRankSnapshot()
        try:
            r20 = riseRank20Snapshot()
        except Exception:
            r20 = {"rows": [], "meta": {}}
        r20Meta = r20.get("meta") or {}
        if kind == "rank20":
            rows = list(r20.get("rows") or [])[:30]
            if not rows:
                kind = "day"
                rows = list(snap.get("day") or [])
        else:
            rows = list(snap.get(kind) or [])
        titles = {
            "rank20": "주도주 20일누적",
            "day": "주도주(금일·20일 집계 대기)",
            "week": "주간상승",
            "month": "월간상승",
            "inds": "테마강세",
        }
        title = titles.get(kind) or kind
        msg = str(snap.get("msg") or "")
        at = str(snap.get("updatedAt") or "")
        extra = f" · {msg}" if msg else ""
        if at:
            extra += f" · {at}"
        if kind == "rank20":
            _d = str(r20Meta.get("date") or "")
            _pd = str(r20Meta.get("prevDate") or "")
            extra = ""
            if _d:
                extra = f" · {_d[4:6]}/{_d[6:8]} {'장중잠정' if r20Meta.get('live') else '확정'}"
            if _pd:
                extra += f" · 비교 {_pd[4:6]}/{_pd[6:8]}"
            if not rows:
                msg = msg or "20일 순위 집계 중(봉 조회 완료 후 표시)"
        self.tradeWatchTitle.setText(self._titleText(f"관심종목 {title}{extra}"))
        try:
            self._syncWatchAlgTabCounts()
        except Exception:
            pass
        try:
            self._updateLeaderRankStamp(r20Meta, snap)
        except Exception:
            pass

        frz = self.tradeWatchFrozen
        tbl = self.tradeWatchTbl
        if kind == "inds":
            frzHeaders = ["테마", "등락률", "구분", "", ""]
            self._WATCH_FRZ_W = (180, 64, 48, 24, 24)
        elif kind == "rank20":
            frzHeaders = ["변동", "순위·종목", "20일누적", "테마", "이슈"]
            self._WATCH_FRZ_W = (40, 104, 64, 110, 220)
        else:
            frzHeaders = ["종목", "등락률", "거래대금", "테마", "이슈"]
            self._WATCH_FRZ_W = (88, 64, 72, 110, 260)
        frz.setHorizontalHeaderLabels(frzHeaders)
        tbl.setHorizontalHeaderLabels(["현재가", "코드", "시장", "당일%"] if kind == "rank20" else ["현재가", "코드", "", ""])
        # 이슈 칸 배경색이 보이도록: 실계좌/기본 탭의 커스텀 델리게이트(배열·손익) 해제
        _plainDel = QStyledItemDelegate(frz)
        for _c in range(5):
            frz.setItemDelegateForColumn(_c, _plainDel)
        frz.setItemDelegateForColumn(2 if kind == "rank20" else 1, SignedPnlDelegate(frz))

        def _amtTxt(v):
            try:
                n = float(v or 0)
            except (TypeError, ValueError):
                return "-"
            if n >= 100000000:
                return f"{n / 100000000:.1f}억"
            if n >= 10000:
                return f"{n / 10000:.0f}만"
            return f"{int(n):,}"

        from trading.leaderIssueWords import LEADER_EVENT_MAP, LEADER_ISSUE_MAP

        _ISSUE_MAP = LEADER_ISSUE_MAP
        _EVENT_MAP = LEADER_EVENT_MAP
        _PASTELS = ("#FFF3C4", "#FFD6D6", "#E4D9F7", "#D3E6FF", "#D6F2DD", "#CFF1EF",
                    "#FAD9EC", "#DCE2F8", "#DCEFFA", "#FFE2C6", "#EEEEDC", "#E8F5C8")

        def _hashClr(txt):
            import zlib
            return _PASTELS[zlib.crc32(str(txt).encode("utf-8")) % len(_PASTELS)]

        # 이슈 그룹(유사 이슈 묶음) 순서와 연한 배경색
        _ISSUE_GRP = (
            (("실적", "밸류업", "정책", "금융"), "#FFF3C4"),
            (("방산", "전쟁", "재건"), "#FFD6D6"),
            (("해킹", "보안"), "#E4D9F7"),
            (("HBM", "반도체", "반도체장비"), "#D3E6FF"),
            (("원전", "전선", "전력", "에너지", "태양광", "수소"), "#D6F2DD"),
            (("2차전지", "자동차"), "#CFF1EF"),
            (("바이오", "비만약"), "#FAD9EC"),
            (("로봇", "AI", "통신"), "#DCE2F8"),
            (("조선", "우주항공", "철강", "건설"), "#DCEFFA"),
            (("엔터", "게임", "화장품", "K푸드"), "#FFE2C6"),
            (("가상자산",), "#EEEEDC"),
            (("지배구조·경영권",), "#E8F5C8"),
        )
        _ISSUE_ORD = {}
        _ISSUE_CLR = {}
        for gi, (labs, clr) in enumerate(_ISSUE_GRP):
            for li, lab in enumerate(labs):
                _ISSUE_ORD[lab] = (gi, li)
                _ISSUE_CLR[lab] = clr

        def _issueList(row):
            src = " ".join(
                str(row.get(k) or "")
                for k in ("issue", "news", "newsTitle", "thmNm", "theme", "indsNm", "stkNm")
            ).lower()
            out = []
            for key, lab in _ISSUE_MAP:
                if key.lower() in src and lab not in out:
                    out.append(lab)
            if not out:
                return []
            # 지주사/경영권 종목은 업종 분류상 '금융'이 붙어도 금융 이슈로 보지 않는다
            if "지배구조·경영권" in out and "금융" in out:
                out.remove("금융")
            # 첫 매칭(주 이슈)의 그룹을 먼저, 같은 그룹끼리 붙여서
            g0 = _ISSUE_ORD.get(out[0], (99, 0))[0]
            out.sort(key=lambda x: (_ISSUE_ORD.get(x, (99, 0))[0] != g0,) + _ISSUE_ORD.get(x, (99, 0)))
            return out[:2]

        from utils.riseRankBoard import riseNewsTitle

        def _issueTxt(row):
            try:
                news = riseNewsTitle(row.get("stkCd"))
            except Exception:
                news = ""
            if news:
                row["newsTitle"] = news
            out = []
            for srcTxt in (news, str(row.get("indsNm") or "")):
                low = str(srcTxt or "").lower()
                if not low:
                    continue
                for key, word in _EVENT_MAP:
                    if key in low and word not in out:
                        out.append(word)
                        if len(out) >= 2:
                            break
                if len(out) >= 2:
                    break
            return ",".join(out[:2]) if out else "-"

        def _issueKey(row):
            out = _issueList(row)
            return _ISSUE_ORD.get(out[0], (99, 0)) if out else (100, 0)

        def _scoreTxt(v):
            try:
                return f"{float(v) * 100:.1f}"
            except (TypeError, ValueError):
                return "-"

        if kind not in ("inds", "rank20") and rows:
            # 이슈 그룹별 강도(평균 등락률 → 종목수 → 거래대금 합) 순으로, 그룹 안은 등락률 순
            def _num(v):
                try:
                    return float(str(v).replace("%", "").replace(",", "").replace("+", ""))
                except (TypeError, ValueError):
                    return 0.0

            grpStat = {}
            for r in rows:
                g = _issueKey(r)[0]
                r["_issueGrp"] = g
                st = grpStat.setdefault(g, [0.0, 0, 0.0])
                st[0] += _num(r.get("fluRt"))
                st[1] += 1
                st[2] += _num(r.get("amt"))

            def _grpRank(g):
                tot, cnt, amt = grpStat.get(g, (0.0, 0, 0.0))
                # 이슈 미분류(100)는 항상 맨 아래
                return (g >= 100, -(tot / cnt if cnt else 0.0), -cnt, -amt, g)

            rows.sort(key=lambda r: _grpRank(r["_issueGrp"]) + (-_num(r.get("fluRt")),))
        if not rows:
            rows = [{"_empty": msg or snap.get("status") or "집계 중"}]
        # ★추천 배너 비활성 (요청: 주도주 탭 상단 추천 행 제거)
        n = len(rows)
        try:
            frz.clearSpans()
        except Exception:
            pass
        frz.setUpdatesEnabled(False)
        tbl.setUpdatesEnabled(False)
        try:
            # 주도주는 순위 표시 전용: 실계좌 탭에서 남은 매매/해지 버튼 제거
            for r in range(tbl.rowCount()):
                if tbl.cellWidget(r, 0) is not None:
                    tbl.removeCellWidget(r, 0)
            if hasattr(self, "_watchAlgHdr"):
                self._watchAlgHdr.clearRowMeta()
            frz.setRowCount(n)
            tbl.setRowCount(n)
            paintCds = []
            for i, row in enumerate(rows):
                if row.get("_banner"):
                    paintCds.append("")
                    item = QTableWidgetItem(str(row["_banner"]))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QColor("#FFF8E1"))
                    item.setForeground(QColor("#BF360C"))
                    _f = item.font()
                    _f.setBold(True)
                    item.setFont(_f)
                    item.setToolTip(str(row.get("_bannerTip") or ""))
                    frz.setItem(i, 0, item)
                    for _c in range(1, 5):
                        frz.setItem(i, _c, QTableWidgetItem(""))
                    frz.setSpan(i, 0, 1, 5)
                    for _c in range(4):
                        tbl.setItem(i, _c, QTableWidgetItem(""))
                    if hasattr(self, "_watchAlgHdr"):
                        self._watchAlgHdr.setRowMeta(i, "#FFE0B2", "추천")
                    continue
                if row.get("_empty"):
                    vals = [str(row.get("_empty")), "", "", "", ""]
                    scroll = ["", "", "", ""]
                    cd = ""
                elif kind == "inds":
                    vals = [
                        str(row.get("indsNm") or ""),
                        _fmtFlu(row.get("fluRt")),
                        str(row.get("mkt") or ""),
                        "",
                        "",
                    ]
                    scroll = ["", "", "", ""]
                    cd = ""
                elif kind == "rank20":
                    cd = str(row.get("stkCd") or "")
                    vals = [
                        str(row.get("chg") or "-"),
                        f"{row.get('rank') or ''}. {row.get('stkNm') or ''}",
                        _fmtFlu(row.get("fluRt")),
                        str(row.get("indsNm") or ""),
                        _issueTxt(row),
                    ]
                    scroll = [
                        _fmtPx(row.get("curPrc")),
                        cd,
                        str(row.get("mkt") or ""),
                        _fmtFlu(row.get("dayFlu")) if row.get("dayFlu") is not None else "",
                    ]
                else:
                    cd = str(row.get("stkCd") or "")
                    vals = [
                        str(row.get("stkNm") or ""),
                        _fmtFlu(row.get("fluRt")),
                        _amtTxt(row.get("amt")),
                        str(row.get("indsNm") or ""),
                        _issueTxt(row),
                    ]
                    scroll = [
                        _fmtPx(row.get("curPrc")),
                        cd,
                        str(row.get("mkt") or ""),
                        "",
                    ]
                paintCds.append(cd)
                for col, val in enumerate(vals):
                    item = QTableWidgetItem(val)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    _nmCol = 1 if kind == "rank20" else 0
                    if col == _nmCol and cd:
                        item.setData(Qt.ItemDataRole.UserRole + 1, cd)
                    if (col == 2 if kind == "rank20" else col in (1, 2)) or (col == 4 and kind == "inds"):
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    if col == 3 and kind != "inds" and not row.get("_empty"):
                        # 테마 칸: 테마 그룹 색 배경
                        _il = _issueList(row)
                        if _il:
                            _bg = _ISSUE_CLR.get(_il[0]) or _hashClr(_il[0])
                        else:
                            _bg = _hashClr(row.get("indsNm") or row.get("stkNm") or "-")
                        item.setBackground(QColor(_bg))
                        item.setForeground(QColor("#222222"))
                        item.setToolTip(f"테마: {row.get('indsNm') or '-'}")
                    if col == 4 and kind != "inds" and not row.get("_empty"):
                        # 이슈 칸: 배경 없이 짧은 이슈 단어만
                        _tip = [f"테마: {row.get('indsNm') or '-'}"]
                        if row.get("newsTitle"):
                            _tip.append(f"뉴스: {row.get('newsTitle')}")
                        item.setToolTip("\n".join(_tip))
                    if col == 0 and kind == "rank20" and not row.get("_empty"):
                        # 변동(맨 앞 좁은 칸): NEW 주황, ▲ 빨강, ▼ 파랑, 작은 글씨
                        _cv = str(val)
                        _sf = item.font()
                        _sf.setPointSizeF(max(6.5, _sf.pointSizeF() - 1.5))
                        item.setFont(_sf)
                        if _cv == "NEW":
                            item.setForeground(QColor("#E65100"))
                            _f = item.font()
                            _f.setBold(True)
                            item.setFont(_f)
                        elif _cv.startswith("▲"):
                            item.setForeground(QColor("#D32F2F"))
                        elif _cv.startswith("▼"):
                            item.setForeground(QColor("#1565C0"))
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                    frz.setItem(i, col, item)
                for col, val in enumerate(scroll):
                    item = QTableWidgetItem(val)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    tbl.setItem(i, col, item)
                if hasattr(self, "_watchAlgHdr"):
                    self._watchAlgHdr.setRowMeta(i, "#E0E0E0", title)
            self._watchPaintCds = paintCds
            if kind != "inds":
                # 이슈 칸: 글자 길이에 맞춰 자동 폭 (최소 48, 최대 260)
                try:
                    _fm = frz.fontMetrics()
                    _iw = _fm.horizontalAdvance("이슈")
                    for _r in range(n):
                        if rows[_r].get("_banner") or rows[_r].get("_empty"):
                            continue
                        _it = frz.item(_r, 4)
                        if _it is not None:
                            _iw = max(_iw, _fm.horizontalAdvance(_it.text()))
                    _w = list(self._WATCH_FRZ_W)
                    _w[4] = max(48, min(260, _iw + 16))
                    self._WATCH_FRZ_W = tuple(_w)
                except Exception:
                    pass
            self._fitWatchFrozenWidth()
            for i in range(n):
                frz.setRowHeight(i, 22)
                tbl.setRowHeight(i, 22)
        finally:
            frz.setUpdatesEnabled(True)
            tbl.setUpdatesEnabled(True)
        self._syncWatchScrollBars()

    def _onRamMgrBtn(self):
        """관심 행의 매매/해지. 매매만 좌측 보유로 올린다."""
        btn = self.sender()
        if btn is None:
            return
        cd = str(btn.property("stkCd") or "")
        tradeOn = bool(btn.property("tradeOn"))
        from trading.realAcntMgr import isTradeOn, registerTrade, unregisterTrade
        from utils.termLog import termLog

        got = unregisterTrade(cd) if tradeOn else registerTrade(cd)
        if not got.get("ok"):
            termLog(f"[실계좌] 버튼 실패 {cd}: {got.get('errMsg')}")
            return
        tradeHolds = []
        for row in getattr(self, "_acntAllHolds", None) or []:
            if not isTradeOn(row.get("stkCd")):
                continue
            row["algCds"] = ["RAM01"]
            row["algLabel"] = "실계좌"
            tradeHolds.append(row)
        self._holdRows = tradeHolds
        self._paintHoldings()
        self.refreshTradeWatch()

    def _fillRealAcntWatch(self):
        """계좌 보유를 관심에 그린다. 매매 등록 전은 모니터링만."""
        from trading.realAcntMgr import isTradeOn

        rows = list(getattr(self, "_acntAllHolds", None) or [])
        tradeN = 0
        for row in rows:
            if isTradeOn(row.get("stkCd")):
                tradeN += 1
        self.tradeWatchTitle.setText(
            self._titleText(
                f"관심종목 실계좌 · {len(rows)}종 · 매매 {tradeN} · 나머지 모니터링"
            )
        )
        try:
            self._syncWatchAlgTabCounts()
        except Exception:
            pass

        frz = self.tradeWatchFrozen
        tbl = self.tradeWatchTbl
        frzHeaders = ["종목", "수량", "평단", "현재가", "손익%"]
        self._WATCH_FRZ_W = (96, 56, 72, 72, 64)
        frz.setHorizontalHeaderLabels(frzHeaders)
        tbl.setHorizontalHeaderLabels(["매매", "", "", ""])
        plain = QStyledItemDelegate(frz)
        for col in range(5):
            frz.setItemDelegateForColumn(col, plain)
        frz.setItemDelegateForColumn(4, SignedPnlDelegate(frz))

        if not rows:
            rows = [{"_empty": "계좌 보유 없음"}]
        n = len(rows)
        frz.setUpdatesEnabled(False)
        tbl.setUpdatesEnabled(False)
        try:
            for r in range(tbl.rowCount()):
                old = tbl.cellWidget(r, 0)
                if old is not None:
                    tbl.removeCellWidget(r, 0)
            if hasattr(self, "_watchAlgHdr"):
                self._watchAlgHdr.clearRowMeta()
            frz.setRowCount(n)
            tbl.setRowCount(n)
            paintCds = []
            for i, row in enumerate(rows):
                if row.get("_empty"):
                    vals = [str(row.get("_empty")), "", "", "", ""]
                    cd = ""
                    tradeOn = False
                else:
                    cd = str(row.get("stkCd") or "")
                    tradeOn = isTradeOn(cd)
                    rt = row.get("plRtNum")
                    rtTxt = f"{float(rt):+.2f}" if rt is not None else "-"
                    vals = [
                        str(row.get("stkNm") or cd),
                        str(row.get("qty") or ""),
                        str(row.get("avgPrc") or ""),
                        str(row.get("curPrc") or ""),
                        rtTxt,
                    ]
                paintCds.append(cd)
                for col, val in enumerate(vals):
                    item = QTableWidgetItem(val)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if col == 0 and cd:
                        item.setData(Qt.ItemDataRole.UserRole + 1, cd)
                    if col in (1, 2, 3, 4):
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    frz.setItem(i, col, item)
                for col in range(4):
                    item = QTableWidgetItem("")
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    tbl.setItem(i, col, item)
                if cd:
                    btn = QPushButton("해지" if tradeOn else "매매")
                    btn.setProperty("stkCd", cd)
                    btn.setProperty("tradeOn", tradeOn)
                    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                    # 매매(미등록)=파랑, 해지(매매중)=주황. 같은 회색이면 상태가 안 보인다.
                    if tradeOn:
                        btn.setStyleSheet(
                            "QPushButton { font-size: 11px; padding: 0 4px; "
                            "background: #EF6C00; color: #FFFFFF; border: 1px solid #E65100; }"
                            "QPushButton:hover { background: #F57C00; }"
                        )
                    else:
                        btn.setStyleSheet(
                            "QPushButton { font-size: 11px; padding: 0 4px; "
                            "background: #1565C0; color: #FFFFFF; border: 1px solid #0D47A1; }"
                            "QPushButton:hover { background: #1976D2; }"
                        )
                    btn.clicked.connect(self._onRamMgrBtn)
                    tbl.setCellWidget(i, 0, btn)
                if hasattr(self, "_watchAlgHdr"):
                    bg = "#CE93D8" if tradeOn else "#E0E0E0"
                    tip = "매매" if tradeOn else "모니터링"
                    self._watchAlgHdr.setRowMeta(i, bg, tip)
            self._watchPaintCds = paintCds
            hdr = tbl.horizontalHeader()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            tbl.setColumnWidth(0, 52)
            self._fitWatchFrozenWidth()
            for i in range(n):
                frz.setRowHeight(i, 22)
                tbl.setRowHeight(i, 22)
        finally:
            frz.setUpdatesEnabled(True)
            tbl.setUpdatesEnabled(True)
        self._syncWatchScrollBars()
        self._tickRealAcntWatchPx()

    def _tickRealAcntWatchPx(self):
        """실계좌 탭: 계좌조회 스냅샷 대신 0B 실시간 현재가로 현재가·손익% 셀만 갱신."""
        try:
            if str(getattr(self, "_watchAlgCd", None) or "acnt") in ("leader", "rank20"):
                return
            frz = getattr(self, "tradeWatchFrozen", None)
            if frz is None or not frz.isVisible():
                return
            from api.getAcntEvlt import heroHoldPnl
            from trading.watchRtMonitor import getWatchLastPxMap
            from utils.stkCdUtil import normStkCd

            pxMap = getWatchLastPxMap() or {}
            byCd = {}
            for row in getattr(self, "_acntAllHolds", None) or []:
                byCd[normStkCd(row.get("stkCd"))] = row
            cds = list(getattr(self, "_watchPaintCds", None) or [])
            for i in range(min(frz.rowCount(), len(cds))):
                cd = normStkCd(cds[i]) if cds[i] else ""
                row = byCd.get(cd)
                if not cd or row is None:
                    continue
                apiCur = int(row.get("apiCurNum") or row.get("curNum") or 0)
                live = _holdLivePx(pxMap.get(cd) or {}, apiCur) or apiCur
                if not live:
                    continue
                qty = int(row.get("qtyNum") or 0)
                _pnl, rt = heroHoldPnl(
                    live,
                    qty,
                    int(row.get("purNum") or 0),
                    int(row.get("avgNum") or 0),
                    row.get("apiPnlNum", row.get("pnlNum")),
                    row.get("apiPlRtNum", row.get("plRtNum")),
                    apiCurPx=apiCur,
                )
                curTxt = f"{int(live):,}"
                rtTxt = f"{float(rt):+.2f}" if rt is not None else "-"
                for col, txt in ((3, curTxt), (4, rtTxt)):
                    item = frz.item(i, col)
                    if item is not None and item.text() != txt:
                        item.setText(txt)
        except Exception as err:
            if not getattr(self, "_acntWatchPxErrLogged", False):
                self._acntWatchPxErrLogged = True
                from utils.termLog import termLog

                termLog(f"[매매창] 실계좌탭 실시간 갱신 실패: {err}")

    def _isLeaderWatchTab(self):
        """관심종목 하위 탭이 주도주인지 (실계좌/기타와 구분)."""
        return str(getattr(self, "_watchAlgCd", None) or "") in ("leader", "rank20")

    def _updateLeaderRankStamp(self, r20Meta=None, snap=None):
        """최종 업데이트 라벨: 스냅샷 savedAt(날짜+시각) 우선, 없으면 금일 updatedAt."""
        lbl = getattr(self, "_leaderRankUpdatedLbl", None)
        if lbl is None:
            return
        stamp = ""
        try:
            if r20Meta is None:
                from utils.riseRankBoard import riseRank20Snapshot
                r20Meta = (riseRank20Snapshot() or {}).get("meta") or {}
            stamp = str((r20Meta or {}).get("savedAt") or "").strip()
        except Exception:
            stamp = ""
        if not stamp:
            try:
                if snap is None:
                    from utils.riseRankBoard import riseRankSnapshot
                    snap = riseRankSnapshot() or {}
                at = str((snap or {}).get("updatedAt") or "").strip()
                if at:
                    # 금일 스냅샷은 HH:MM만 올 수 있어 오늘 날짜를 붙인다
                    if len(at) <= 5 and ":" in at:
                        stamp = f"{datetime.now().strftime('%Y-%m-%d')} {at}"
                    else:
                        stamp = at
            except Exception:
                stamp = ""
        lbl.setText(f"최종 업데이트: {stamp or '-'}")

    def _syncLeaderRankTools(self):
        """주도주 탭일 때만 업데이트 라벨·수동 버튼 표시."""
        tools = getattr(self, "_leaderRankTools", None)
        if tools is None:
            return
        show = self._isLeaderWatchTab()
        tools.setVisible(show)
        if not show:
            # 수동 집계 폴링은 탭을 바꿔도 유지(완료 시 버튼·라벨 갱신)
            return
        self._updateLeaderRankStamp()
        self._applyLeaderRankBtnState()
        # 이미 집계 중이면 완료 감지용 폴링 시작
        if self._leaderRankScanning():
            poll = getattr(self, "_leaderRankPoll", None)
            if poll is not None and not poll.isActive():
                poll.start()

    def _leaderRankScanning(self):
        try:
            from utils.riseRankBoard import riseRankSnapshot
            st = str((riseRankSnapshot() or {}).get("status") or "")
            return st in ("scan", "daybar")
        except Exception:
            return False

    def _applyLeaderRankBtnState(self):
        btn = getattr(self, "_leaderRankRefreshBtn", None)
        if btn is None:
            return
        busy = self._leaderRankScanning() or bool(getattr(self, "_leaderRankManualBusy", False))
        btn.setEnabled(not busy)
        btn.setText("집계 중…" if busy else "수동 업데이트")

    def _onLeaderRankRefresh(self):
        """주도주 20일누적·금일 순위 강제 재조회. 진행 중이면 중복 시작 안 함."""
        if not self._isLeaderWatchTab():
            return
        if self._leaderRankScanning():
            self._applyLeaderRankBtnState()
            return
        from utils.riseRankBoard import restartRiseRankBoard
        from utils.termLog import termLog

        self._leaderRankManualBusy = True
        self._applyLeaderRankBtnState()
        try:
            restartRiseRankBoard()
            termLog("[매매창] 주도주 수동 업데이트 시작")
        except Exception as err:
            self._leaderRankManualBusy = False
            self._applyLeaderRankBtnState()
            termLog(f"[매매창] 주도주 수동 업데이트 실패: {err}")
            return
        poll = getattr(self, "_leaderRankPoll", None)
        if poll is not None and not poll.isActive():
            poll.start()
        # 상태가 scan으로 잡힐 때까지 한 번 더
        QTimer.singleShot(500, self._applyLeaderRankBtnState)

    def _pollLeaderRankScan(self):
        """수동/자동 집계 완료 시 버튼 복구 + 화면·일시 갱신."""
        if self._leaderRankScanning():
            self._applyLeaderRankBtnState()
            return
        self._leaderRankManualBusy = False
        poll = getattr(self, "_leaderRankPoll", None)
        if poll is not None:
            poll.stop()
        self._applyLeaderRankBtnState()
        self._updateLeaderRankStamp()
        if self._isLeaderWatchTab():
            try:
                self.refreshTradeWatch()
            except Exception:
                pass

    def _fillTradeWatch(self):
        """관심종목=계좌보유, 주도주=상승순위, 급등주=조회순위."""
        allowed = list(getattr(self, "_watchAlgTabCds", None) or [])
        kind = str(getattr(self, "_watchAlgCd", None) or "")
        frame = getattr(self, "watchGridFrame", None)
        table = getattr(self, "surgeRankTbl", None)
        if not allowed or kind not in allowed:
            if allowed:
                kind = allowed[0]
                self._watchAlgCd = kind
            else:
                self._watchAlgCd = ""
                if frame is not None:
                    frame.setVisible(False)
                if table is not None:
                    table.setVisible(False)
                self._syncLeaderRankTools()
                self._syncSurgeTools()
                return
        surge = kind == "surge"
        if frame is not None:
            frame.setVisible(not surge)
        if table is not None:
            table.setVisible(surge)
        if surge:
            self._fillSurgeWatch()
        elif kind in ("leader", "rank20"):
            self._fillRiseRankWatch()
        else:
            self._fillRealAcntWatch()
        try:
            self._syncLeaderRankTools()
            self._syncSurgeTools()
        except Exception:
            pass

    def _isSurgeWatchTab(self):
        return str(getattr(self, "_watchAlgCd", None) or "") == "surge"

    def _syncSurgeTools(self):
        tools = getattr(self, "_surgeTools", None)
        if tools is None:
            return
        show = self._isSurgeWatchTab()
        tools.setVisible(show)
        timer = getattr(self, "_surgeSnapTimer", None)
        if timer is None:
            return
        if show and not timer.isActive():
            timer.start()
        if not show and timer.isActive():
            timer.stop()

    def _fillSurgeWatch(self):
        """저장된 스냅샷만 그린다. 여기서 주문을 내지 않는다. 다시 그리는 동안 차트 탭을 열지 않는다."""
        table = getattr(self, "surgeRankTbl", None)
        if table is None:
            return
        outerBusy = getattr(self, "_watchRefreshBusy", False)
        self._watchRefreshBusy = True
        try:
            self._paintSurgeWatch(table)
        finally:
            if not outerBusy:
                self._watchRefreshBusy = False

    def _paintSurgeWatch(self, table):
        """급등주 표 칸만 채운다."""
        from trading.surgeTradeData import (
            SnapStore,
            TAB_COLUMNS,
            buildTabRows,
            dailyPctCellColor,
            formatGroupedInt,
            formatNetEok,
            formatYearRisePct,
            nameBadges,
            netFlowColor,
            rsiCellColor,
            selectTabDisplay,
        )
        from trading.surgeTradeEnrich import displayExtras

        try:
            from utils.appPaths import configDataDir

            store = SnapStore(configDataDir() / "surgeTrade")
            picked = selectTabDisplay(store, datetime.now())
            asof = picked.get("asof") or ""
            extras = displayExtras(store.dir, asof)
            snap = picked.get("snap")
            view = buildTabRows(snap, asof, extras)
            view["slot"] = (snap or {}).get("slot") or ""
            view["observedAt"] = (snap or {}).get("observedAt") or (snap or {}).get("notedAt") or ""
            view["queryLabel"] = picked.get("queryLabel") or "조회일시 -"
            view["fallback"] = picked.get("fallback") is True
        except Exception as err:
            view = {"state": "ERROR", "lastUpdated": "", "rows": [], "note": str(err), "queryLabel": "조회일시 -"}
        rows = view.get("rows") or []
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (key, _label) in enumerate(TAB_COLUMNS):
                val = row.get(key)
                sortNum = None
                flowSign = ""
                if key == "price":
                    text = formatGroupedInt(val)
                    sortNum = val
                elif key == "turnoverEok":
                    text = formatGroupedInt(val)
                    sortNum = val
                elif key in ("orgnNetMillionKrw", "frgnNetMillionKrw", "penfndNetMillionKrw"):
                    text, flowSign = formatNetEok(val)
                    sortNum = val
                elif key == "yearRisePct":
                    text, yearSign = formatYearRisePct(val)
                    sortNum = val
                else:
                    text = "-" if val in (None, "") else str(val)
                item = SurgeSortItem(text) if sortNum not in (None, "") else QTableWidgetItem(text)
                if sortNum not in (None, ""):
                    item.setData(Qt.ItemDataRole.UserRole + 1, sortNum)
                if key == "name":
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        nameBadges(row.get("newHigh"), row.get("newSurge")),
                    )
                if key == "dailyPct":
                    dailyBg = dailyPctCellColor(val)
                    if dailyBg:
                        item.setBackground(QBrush(QColor(dailyBg)))
                if key == "rsi14":
                    rsiBg = rsiCellColor(val)
                    if rsiBg:
                        item.setBackground(QBrush(QColor(rsiBg)))
                        item.setForeground(QBrush(QColor("#212121")))
                if key == "issues" and row.get("issueColor"):
                    item.setBackground(QBrush(QColor(str(row.get("issueColor")))))
                    item.setForeground(QBrush(QColor("#212121")))
                flowFg = netFlowColor(flowSign)
                if flowFg:
                    item.setForeground(QBrush(QColor(flowFg)))
                if key == "yearRisePct" and yearSign == "up":
                    item.setForeground(QBrush(_PNL_PLUS))
                elif key == "yearRisePct" and yearSign == "down":
                    item.setForeground(QBrush(_PNL_MINUS))
                if key in ("issues", "orgnNetMillionKrw", "frgnNetMillionKrw", "penfndNetMillionKrw"):
                    tip = row.get("flowStatus") or ""
                    details = row.get("issueDetails") or []
                    if details:
                        tip = tip + " " + str(details[0].get("sourceUrl") or "")
                    item.setToolTip(tip.strip())
                table.setItem(r, c, item)
        nameCol = next((i for i, (key, _label) in enumerate(TAB_COLUMNS) if key == "name"), None)
        if nameCol is not None:
            if getattr(self, "_surgeNameDel", None) is None:
                self._surgeNameDel = SurgeNameDelegate(table)
            table.setItemDelegateForColumn(nameCol, self._surgeNameDel)
        label = getattr(self, "_surgeStateLbl", None)
        if label is not None:
            stamp = view.get("queryLabel") or "조회일시 -"
            word = str(getattr(self, "_surgeStatusWord", "") or "")
            if word:
                label.setText(f"{word} · {stamp}")
            else:
                label.setText(stamp)

    def _onSurgeRefresh(self):
        """수동 갱신. 백그라운드에서 ka00198 만 읽고 ADHOC 으로 저장한다."""
        if not self._isSurgeWatchTab():
            return
        from trading.surgeTradeData import SnapStore
        from trading.surgeTradeLive import (
            beginManualRankRefresh,
            loadPaperOrderSession,
            paperRankTransport,
        )
        from utils.appPaths import configDataDir

        btn = getattr(self, "_surgeRefreshBtn", None)
        if btn is not None:
            btn.setEnabled(False)
        self._surgeStatusWord = "조회중"
        label = getattr(self, "_surgeStateLbl", None)
        if label is not None:
            label.setText("조회중")
        session = None
        try:
            session = loadPaperOrderSession()
        except Exception:
            session = None
        if not session:
            self._surgeStatusWord = "실패 모의 조회 세션 없음"
            if label is not None:
                label.setText(self._surgeStatusWord)
            if btn is not None:
                btn.setEnabled(True)
            return
        session = dict(session)
        session["purpose"] = "market_read"
        beginManualRankRefresh(
            session,
            SnapStore(configDataDir() / "surgeTrade"),
            datetime.now(),
            paperRankTransport(session),
        )
        poll = getattr(self, "_surgePoll", None)
        if poll is not None and not poll.isActive():
            poll.start()

    def _applySurgeRefreshState(self, state):
        btn = getattr(self, "_surgeRefreshBtn", None)
        busy = str(state.get("state") or "") == "BUSY"
        if btn is not None:
            btn.setEnabled(not busy)
        word = str(state.get("label") or "")
        if word:
            self._surgeStatusWord = word
        label = getattr(self, "_surgeStateLbl", None)
        if label is not None and word:
            label.setText(word)

    def _pollSurgeRefresh(self):
        from trading.surgeTradeLive import refreshStatus

        state = refreshStatus()
        if str(state.get("state") or "") in ("", "IDLE", "BUSY"):
            if str(state.get("state") or "") == "BUSY":
                self._applySurgeRefreshState(state)
            return
        poll = getattr(self, "_surgePoll", None)
        if poll is not None:
            poll.stop()
        self._applySurgeRefreshState(state)
        if self._isSurgeWatchTab():
            self._fillSurgeWatch()
