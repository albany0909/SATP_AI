"""
매매창 차트. 일봉은 AI_DAY_STK_PRC, 주봉은 AI_WK_STK_PRC, 월봉은 AI_MON_STK_PRC.
당일 일봉만 이미 수신 중인 0B 현재가로 덧그린다. 주·월은 1초마다 다시 그리지 않는다.
"""

from datetime import datetime, timedelta
import math

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QCandlestickSeries,
    QCandlestickSet,
    QChart,
    QChartView,
    QLineSeries,
    QValueAxis,
)
from PySide6.QtCore import QMargins, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
)

_MA_PERIODS = (5, 10, 20, 60, 120)
_MA_COLOR = {
    5: "#F5C518",
    10: "#FB8C00",
    20: "#E53935",
    60: "#43A047",
    120: "#9E9E9E",
}
# 20·60은 매매 기준선이라 조금 더 굵게
_MA_WIDTH = {5: 2, 10: 2, 20: 3, 60: 3, 120: 2}
_LOOKBACK_DAYS = 365 * 20
_SHOW_MIN = 20
_SHOW_STEP = 20
_SHOW_DEFAULT = 60
_SR_COLOR = "#6A1B9A"
_SR_HIT_PX = 10
_BAR_TP_NM = {"day": "일봉", "week": "주봉", "month": "월봉"}
_BAR_TP_UNIT = {"day": "일", "week": "주", "month": "월"}
_SHOW_BY_TP = {
    "day": {"min": 20, "step": 20, "default": 60},
    "week": {"min": 12, "step": 8, "default": 52},
    "month": {"min": 8, "step": 6, "default": 36},
}


def _absPx(raw):
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if not text:
        return None
    if text[:1] in "+-":
        text = text[1:]
    try:
        n = abs(float(text))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _smaAt(closes, i, period):
    if i + 1 < period:
        return None
    chunk = closes[i - period + 1 : i + 1]
    if any(v is None for v in chunk):
        return None
    return sum(chunk) / float(period)


def _fmtWon(n):
    """가격 축·지지선 — 천 단위 콤마."""
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return ""


def _dateSpanDays(dts):
    valid = [d for d in dts if len(d) == 8]
    if len(valid) < 2:
        return 0
    try:
        a = datetime.strptime(valid[0], "%Y%m%d")
        b = datetime.strptime(valid[-1], "%Y%m%d")
        return abs((b - a).days)
    except ValueError:
        return 0


def _axisDateMode(n, spanDays):
    """기간 +/− 에 따라 X축 단위. 넓으면 년, 중간은 월, 좁으면 일."""
    if n <= 12 or spanDays <= 25:
        return "day"
    if spanDays >= 280 or n >= 120:
        return "year"
    if n <= 45 and spanDays < 50:
        return "day"
    return "month"


def _boundaryIdx(dts, mode):
    """년·월이 바뀌는 봉을 우선 눈금으로."""
    n = len(dts)
    if n <= 0:
        return []
    out = [0]
    for i in range(1, n):
        a, b = dts[i - 1], dts[i]
        if len(a) < 8 or len(b) < 8:
            continue
        if mode == "year" and b[:4] != a[:4]:
            out.append(i)
        elif mode in ("month", "day") and b[:6] != a[:6]:
            out.append(i)
    last = n - 1
    if out[-1] != last:
        out.append(last)
    return out


def _pickTickIdx(n, bounds, want):
    if n <= 0:
        return []
    if n <= want:
        return list(range(n))
    want = max(3, min(want, n))
    idxs = list(bounds or [])
    if not idxs:
        idxs = [0, n - 1]
    if 0 not in idxs:
        idxs = [0] + idxs
    if n - 1 not in idxs:
        idxs.append(n - 1)
    idxs = sorted(set(i for i in idxs if 0 <= i < n))
    if len(idxs) < want:
        extra = [
            int(round(k * (n - 1) / float(want - 1))) for k in range(want)
        ]
        idxs = sorted(set(idxs) | set(extra))
    if len(idxs) > want:
        keep = [idxs[0]]
        mid = idxs[1:-1]
        need = want - 2
        if mid and need > 0:
            for k in range(need):
                j = int(round((k + 0.5) * len(mid) / need - 0.5))
                j = min(max(j, 0), len(mid) - 1)
                keep.append(mid[j])
        keep.append(idxs[-1])
        idxs = sorted(set(keep))
    return idxs


def _fmtAxisYm(d):
    """X축 — 2026-04 형식."""
    s = str(d or "")[:8]
    if len(s) < 6:
        return s
    return f"{s[:4]}-{s[4:6]}"


def _evenYmTickRows(vis):
    """
    최종 눈금 개수만큼 봉 인덱스를 균등 분할한다.
    많이 뽑은 뒤 같은 달을 빼면 6월은 왼쪽, 7·8월은 붙어 간격이 깨진다.
    """
    n = len(vis or [])
    if n <= 0:
        return []
    uniq = []
    for b in vis:
        text = _fmtAxisYm(str(b.get("tradeDt") or ""))
        if text and (not uniq or uniq[-1] != text):
            uniq.append(text)
    want = len(uniq)
    if n >= 2:
        want = max(2, min(want, n))
    else:
        want = 1
    if n <= 20:
        want = min(want, 5)
    elif n <= 40:
        want = min(want, 7)
    else:
        want = min(want, 10)
    rows = []
    for k in range(want):
        if want <= 1:
            i = 0
        else:
            i = int(round(k * (n - 1) / float(want - 1)))
        i = min(max(i, 0), n - 1)
        text = _fmtAxisYm(str(vis[i].get("tradeDt") or ""))
        if not text:
            continue
        rows.append({"i": i, "text": text})
    return rows


def _niceCeil(v):
    if v <= 0:
        return 0.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        t = m * mag
        if t >= v:
            return t
    return 10.0 * mag


def _fmtQtyAxis(n):
    """거래량 Y축 — 만·억 단위로 대략 읽히게."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return "0"
    if v >= 1e8:
        s = v / 1e8
        return f"{s:.0f}억" if s >= 10 else f"{s:.1f}억".replace(".0억", "억")
    if v >= 1e4:
        s = v / 1e4
        if s >= 100:
            return f"{s:.0f}만"
        return f"{s:.1f}만".replace(".0만", "만")
    return f"{v:,.0f}"


def _barKey(ymd, barTp):
    """일·주·월·년 묶음 키. 주봉은 ISO(월~일)."""
    dt = str(ymd or "")[:8]
    if len(dt) != 8:
        return dt
    if barTp == "week":
        d = datetime.strptime(dt, "%Y%m%d")
        isoY, isoW, _ = d.isocalendar()
        return f"{isoY}W{isoW:02d}"
    if barTp == "month":
        return dt[:6]
    if barTp == "year":
        return dt[:4]
    return dt


def _aggBars(dayBars, barTp):
    """
    일봉을 주·월·년봉으로 합친다.
    시가=첫 날, 고가=최대, 저가=최소, 종가=마지막, 거래량=합.
    """
    tp = str(barTp or "day")
    if tp == "day" or not dayBars:
        return [dict(b) for b in (dayBars or [])]
    groups = []
    curKey = None
    cur = None
    for b in dayBars:
        dt = str(b.get("tradeDt") or "")[:8]
        if len(dt) != 8:
            continue
        key = _barKey(dt, tp)
        o = float(b.get("open") or 0)
        h = float(b.get("high") or 0)
        l = float(b.get("low") or 0)
        c = float(b.get("close") or 0)
        q = float(b.get("qty") or 0)
        if o <= 0:
            o = c
        if h <= 0:
            h = c
        if l <= 0:
            l = c
        if cur is None or key != curKey:
            if cur is not None:
                groups.append(cur)
            curKey = key
            cur = {
                "tradeDt": dt,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "qty": q,
            }
            continue
        cur["tradeDt"] = dt
        if h > cur["high"]:
            cur["high"] = h
        if l > 0 and (cur["low"] <= 0 or l < cur["low"]):
            cur["low"] = l
        cur["close"] = c
        cur["qty"] = float(cur.get("qty") or 0) + q
    if cur is not None:
        groups.append(cur)
    return groups


def loadDeskDayBars(stkCd, endYmd=None):
    """저장된 일봉. 차트 워커는 이 UI 모듈을 거치지 않고 utils.chartBarLoad 를 쓴다."""
    from utils.chartBarLoad import loadStoredDayBars

    return loadStoredDayBars(stkCd, endYmd=endYmd) or []


class CandleChartView(QChartView):
    """
    지지선: 버튼 후 마우스 따라가기 → 클릭 추가.
    기존 선: 드래그 이동, 더블클릭 삭제.
    """

    srPxPicked = Signal(float)
    srMoveBegan = Signal(str)
    srLineMoved = Signal(str, float)
    srLineDeleted = Signal(str)

    def __init__(self, chart, parent=None):
        super().__init__(chart, parent)
        self.pickSr = False
        self._yAxis = None
        self._srHits = []
        self._draftPx = None
        self._dragging = False
        self._moveId = ""
        self._pressHit = None
        self._xBarN = 0
        self._xTicks = []
        self._plotReady = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def setSrHits(self, rows):
        self._srHits = list(rows or [])

    def setXTicks(self, barN, ticks):
        """월 눈금. Qt 카테고리 라벨은 폭이 줄어 안 보이므로 직접 그린다."""
        self._xBarN = int(barN or 0)
        self._xTicks = list(ticks or [])

    def _pxAtEvent(self, ev):
        """뷰 좌표 → 차트 Y → 가격. plotArea 세로만 클램프."""
        ch = self.chart()
        if ch is None or self._yAxis is None:
            return None
        scenePt = self.mapToScene(ev.position().toPoint())
        chartPt = ch.mapFromScene(scenePt)
        area = ch.plotArea()
        if area.height() <= 1:
            return None
        y = min(max(chartPt.y(), area.top()), area.bottom())
        y0 = self._yAxis.max()
        y1 = self._yAxis.min()
        if y0 == y1:
            return None
        ratio = (y - area.top()) / area.height()
        px = y0 - ratio * (y0 - y1)
        return float(px) if px > 0 else None

    def _sceneYFromPx(self, px):
        ch = self.chart()
        if ch is None or self._yAxis is None:
            return None
        area = ch.plotArea()
        y0 = self._yAxis.max()
        y1 = self._yAxis.min()
        if area.height() <= 1 or y0 == y1:
            return None
        ratio = (y0 - float(px)) / (y0 - y1)
        p0 = ch.mapToScene(area.topLeft())
        p1 = ch.mapToScene(area.bottomRight())
        return p0.y() + ratio * (p1.y() - p0.y())

    def _hitSr(self, ev):
        """마우스에 가장 가까운 지지선. 픽셀 허용 안이면 행, 아니면 None."""
        scenePt = self.mapToScene(ev.position().toPoint())
        best = None
        bestD = float(_SR_HIT_PX)
        for row in self._srHits:
            y = self._sceneYFromPx(row.get("px"))
            if y is None:
                continue
            d = abs(scenePt.y() - y)
            if d <= bestD:
                bestD = d
                best = row
        return best

    def _inPlotX(self, ev):
        ch = self.chart()
        if ch is None:
            return False
        scenePt = self.mapToScene(ev.position().toPoint())
        chartPt = ch.mapFromScene(scenePt)
        area = ch.plotArea()
        return area.left() - 8 <= chartPt.x() <= area.right() + 8

    def enterEvent(self, ev):
        super().enterEvent(ev)
        if self.pickSr:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def leaveEvent(self, ev):
        if not self._dragging:
            self._draftPx = None
            self.viewport().update()
        if not self.pickSr:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(ev)
            return
        # 추가 모드는 클릭(release)에서 확정. press 는 따라가기만
        if self.pickSr:
            px = self._pxAtEvent(ev)
            if px is not None:
                self._draftPx = px
                self.viewport().update()
            ev.accept()
            return
        hit = self._hitSr(ev) if self._inPlotX(ev) else None
        self._pressHit = hit
        self._dragging = False
        self._moveId = ""
        if hit is not None:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        px = self._pxAtEvent(ev)
        if self.pickSr:
            if px is not None:
                self._draftPx = px
                self.setCursor(Qt.CursorShape.CrossCursor)
                self.viewport().update()
            ev.accept()
            return
        if self._pressHit is not None and not self._dragging:
            self._dragging = True
            self._moveId = str(self._pressHit.get("id") or "")
            self.srMoveBegan.emit(self._moveId)
        if self._dragging:
            if px is not None:
                self._draftPx = px
                self.viewport().update()
            ev.accept()
            return
        hit = self._hitSr(ev) if self._inPlotX(ev) else None
        if hit is not None:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(ev)
            return
        px = self._pxAtEvent(ev) or self._draftPx
        if self.pickSr:
            self._draftPx = None
            self.pickSr = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.viewport().update()
            if px is not None and px > 0:
                self.srPxPicked.emit(float(px))
            ev.accept()
            return
        if self._dragging:
            lineId = self._moveId
            self._dragging = False
            self._moveId = ""
            self._pressHit = None
            self._draftPx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.viewport().update()
            if lineId and px is not None and px > 0:
                self.srLineMoved.emit(lineId, float(px))
            ev.accept()
            return
        self._pressHit = None
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(ev)
            return
        # 추가 모드 더블클릭은 무시(두 번 넣지 않음). 기존 선만 삭제
        if self.pickSr:
            ev.accept()
            return
        hit = self._hitSr(ev)
        self._dragging = False
        self._pressHit = None
        self._moveId = ""
        self._draftPx = None
        if hit is not None:
            self.srLineDeleted.emit(str(hit.get("id") or ""))
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        self._drawPriceLabels(painter)
        if self._plotReady:
            self._drawMonthLabels(painter)
        if self._draftPx is None or self._yAxis is None:
            return
        ch = self.chart()
        if ch is None:
            return
        area = ch.plotArea()
        y0 = self._yAxis.max()
        y1 = self._yAxis.min()
        if area.height() <= 1 or y0 == y1:
            return
        ratio = (y0 - self._draftPx) / (y0 - y1)
        p0 = ch.mapToScene(area.topLeft())
        p1 = ch.mapToScene(area.bottomRight())
        y = p0.y() + ratio * (p1.y() - p0.y())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_SR_COLOR))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(p0.x()), int(y), int(p1.x()), int(y))
        painter.setPen(QColor(_SR_COLOR))
        painter.setFont(QFont(self.font().family(), 9))
        painter.drawText(int(p0.x()) + 4, int(y) - 4, _fmtWon(self._draftPx))

    def _drawPriceLabels(self, painter):
        """기본 축 숫자(콤마 없음) 대신 금액 콤마 라벨을 같은 자리에 그린다."""
        ch = self.chart()
        ax = self._yAxis
        if ch is None or ax is None:
            return
        area = ch.plotArea()
        y0 = ax.max()
        y1 = ax.min()
        if area.height() <= 1 or y0 == y1:
            return
        p0 = ch.mapToScene(area.topLeft())
        p1 = ch.mapToScene(area.bottomRight())
        n = max(int(ax.tickCount() or 5), 2)
        painter.setPen(QColor("#424242"))
        painter.setFont(QFont(self.font().family(), 8))
        for i in range(n):
            val = y1 + (y0 - y1) * i / float(n - 1)
            ratio = (y0 - val) / (y0 - y1)
            y = p0.y() + ratio * (p1.y() - p0.y())
            box = QRectF(p0.x() - 78, y - 8, 74, 16)
            painter.drawText(
                box,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _fmtWon(val),
            )

    def _drawMonthLabels(self, painter):
        """plotArea 아래 YYYY-MM, 기울여 비율 간격. 겹치면 건너뛴다."""
        ch = self.chart()
        n = int(self._xBarN or 0)
        ticks = self._xTicks
        if ch is None or n <= 0 or not ticks:
            return
        area = ch.plotArea()
        if area.width() <= 1:
            return
        p0 = ch.mapToScene(area.topLeft())
        p1 = ch.mapToScene(area.bottomRight())
        w = p1.x() - p0.x()
        painter.setFont(QFont(self.font().family(), 8))
        for row in ticks:
            try:
                i = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            text = str(row.get("text") or "")
            if not text:
                continue
            x = p0.x() + (i + 0.5) * w / float(n)
            painter.save()
            painter.setPen(QColor("#BDBDBD"))
            painter.drawLine(int(x), int(p1.y()), int(x), int(p1.y() + 6))
            painter.translate(x - 2, p1.y() + 10)
            painter.rotate(45)
            painter.setPen(QColor("#616161"))
            painter.drawText(0, 0, text)
            painter.restore()


class VolBarView(QWidget):
    """일봉 바로 아래 거래량. Qt 막대(카테고리 많으면 폭 0) 대신 직접 그린다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._left = 52
        self._right = 8
        self.setMinimumHeight(88)
        self.setMaximumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setPlotPad(self, left, right):
        self._left = max(8, int(left))
        self._right = max(4, int(right))
        self.update()

    def setRows(self, rows):
        self._rows = list(rows or [])
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        rows = self._rows
        if not rows:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "거래량 없음")
            return
        maxQ = max(float(r.get("qty") or 0) for r in rows)
        if maxQ <= 0:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "거래량 없음")
            return
        left = self._left
        right = self._right
        top = 16
        bot = 4
        w = max(self.width() - left - right, 8)
        h = max(self.height() - top - bot, 8)
        n = len(rows)
        barW = max(2.0, w / float(n) * 0.72)
        topNice = _niceCeil(maxQ)
        if topNice <= 0:
            topNice = maxQ
        # 대략 3단: 최대 · 중간 · 0
        ticks = [topNice, topNice * 0.5, 0.0]
        p.setPen(QColor("#9E9E9E"))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(4, 12, "거래량")
        p.setFont(QFont(self.font().family(), 7))
        for val in ticks:
            ratio = 0.0 if topNice <= 0 else (val / topNice)
            y = top + h - ratio * h
            p.setPen(QColor("#EEEEEE"))
            p.drawLine(int(left), int(y), int(left + w), int(y))
            p.setPen(QColor("#616161"))
            box = QRectF(2, y - 7, max(left - 6, 8), 14)
            p.drawText(
                box,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _fmtQtyAxis(val),
            )
        for i, rec in enumerate(rows):
            q = float(rec.get("qty") or 0)
            if q <= 0:
                continue
            bh = max(1.0, h * (q / topNice))
            x = left + (i + 0.5) * (w / float(n)) - barW / 2.0
            y = top + h - bh
            up = bool(rec.get("up"))
            p.fillRect(
                int(x),
                int(y),
                max(int(barW), 1),
                int(bh),
                QColor("#EF9A9A") if up else QColor("#90CAF9"),
            )


class DeskStkChartPane(QWidget):
    """일·주·월봉 + 바로 아래 거래량 + SMA + 지지선."""

    barsMissing = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = []
        self._dayBars = []
        self._barTp = "day"
        self._stkCd = ""
        self._stkNm = ""
        self._liveYmd = ""
        self._showN = _SHOW_DEFAULT
        self._yAxis = None
        self._srHideId = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.btnZoomIn = QToolButton()
        self.btnZoomIn.setText("+")
        self.btnZoomIn.setToolTip("기간 늘리기 (더 많은 봉)")
        self.btnZoomIn.setFixedSize(26, 26)
        self.btnZoomOut = QToolButton()
        self.btnZoomOut.setText("−")
        self.btnZoomOut.setToolTip("기간 줄이기 (최근 봉만)")
        self.btnZoomOut.setFixedSize(26, 26)
        self.btnZoomIn.clicked.connect(self._zoomMore)
        self.btnZoomOut.clicked.connect(self._zoomLess)
        self.headLbl = QLabel("관심종목에서 종목을 선택하세요")
        self.headLbl.setStyleSheet("font-weight: 700; font-size: 13px; color: #111;")
        self.headLbl.setWordWrap(True)
        self.btnAddSr = QToolButton()
        self.btnAddSr.setText("지지선")
        self.btnAddSr.setToolTip("켠 뒤 차트 위로 마우스를 올리면 선이 따라가고, 클릭한 자리에 넣습니다")
        self.btnAddSr.setCheckable(True)
        self.btnAddSr.clicked.connect(self._toggleSrPick)
        top.addWidget(self.btnZoomIn)
        top.addWidget(self.btnZoomOut)
        self.barTpGroup = QButtonGroup(self)
        self.barTpGroup.setExclusive(True)
        self.barTpBtns = {}
        for tp, title in (
            ("day", "일봉"),
            ("week", "주봉"),
            ("month", "월봉"),
        ):
            btn = QToolButton()
            btn.setText(title)
            btn.setCheckable(True)
            btn.setProperty("barTp", tp)
            btn.setStyleSheet(
                "QToolButton { padding: 2px 6px; }"
                "QToolButton:checked { font-weight: 700; background: #E3F2FD; }"
            )
            if tp == "day":
                btn.setChecked(True)
            self.barTpGroup.addButton(btn)
            self.barTpBtns[tp] = btn
            top.addWidget(btn)
        self.barTpGroup.buttonClicked.connect(self._onBarTpClicked)
        top.addStretch(1)
        self.btnRefresh = QToolButton()
        self.btnRefresh.setText("차트 최신화")
        self.btnRefresh.setToolTip("선택 종목 일봉·주봉·월봉을 키움에서 받아 저장한 뒤 차트를 다시 그립니다")
        self.btnRefresh.setStyleSheet("QToolButton { padding: 2px 6px; }")
        top.addWidget(self.btnRefresh)
        top.addWidget(self.btnAddSr)
        lay.addLayout(top)
        lay.addWidget(self.headLbl)

        self.candleChart = QChart()
        self.candleChart.legend().setVisible(True)
        self.candleChart.legend().setAlignment(Qt.AlignmentFlag.AlignTop)
        self.candleChart.setBackgroundRoundness(0)
        self.candleChart.setMargins(QMargins(0, 0, 10, 52))
        self.candleView = CandleChartView(self.candleChart)
        self.candleView.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.candleView.setMinimumHeight(200)
        self.candleView.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.candleView.srPxPicked.connect(self._onSrPxPicked)
        self.candleView.srMoveBegan.connect(self._onSrMoveBegan)
        self.candleView.srLineMoved.connect(self._onSrLineMoved)
        self.candleView.srLineDeleted.connect(self._onSrLineDeleted)
        self.candleChart.plotAreaChanged.connect(self._onPlotArea)
        lay.addWidget(self.candleView, 1)

        self.volView = VolBarView()
        lay.addWidget(self.volView, 0)

        self.srHdr = QLabel("지지선 · 차트에서 드래그 이동, 더블클릭 삭제")
        self.srHdr.setStyleSheet("font-size: 11px; color: #555;")
        lay.addWidget(self.srHdr)
        self.srList = QListWidget()
        self.srList.setMaximumHeight(72)
        self.srList.setToolTip("더블클릭하면 지지선을 지웁니다")
        self.srList.itemDoubleClicked.connect(self._delSrItem)
        lay.addWidget(self.srList)

    def showEvent(self, ev):
        super().showEvent(ev)
        # 탭을 처음 열면 레이아웃 다음에 plotArea 가 잡힌다
        QTimer.singleShot(0, self._refreshAxisPaint)
        QTimer.singleShot(50, self._refreshAxisPaint)

    def _refreshAxisPaint(self):
        if not self.isVisible():
            return
        self._hideCandleLegend()
        self._onPlotArea(self.candleChart.plotArea())

    def _hideCandleLegend(self):
        """범례에서 캔들(일봉) 색·글자만 숨긴다. 이평 숫자 마커는 남긴다."""
        for ser in self.candleChart.series():
            if isinstance(ser, QCandlestickSeries):
                for mk in self.candleChart.legend().markers(ser):
                    mk.setVisible(False)

    def _showCfg(self):
        return _SHOW_BY_TP.get(self._barTp) or _SHOW_BY_TP["day"]

    def _barTpNm(self):
        return _BAR_TP_NM.get(self._barTp) or "일봉"

    def _setHeadTitle(self):
        if self._stkNm and self._stkCd:
            title = f"{self._stkNm}({self._stkCd})"
        elif self._stkNm:
            title = self._stkNm
        elif self._stkCd:
            title = self._stkCd
        else:
            title = "관심종목에서 종목을 선택하세요"
        self.headLbl.setText(title)

    def _rebuildBars(self):
        """선택 주기의 저장 봉을 쓴다. 주·월이 아직 없으면 일봉을 묶지 않는다."""
        if self._barTp not in _SHOW_BY_TP:
            self._barTp = "day"
        stored = None
        byTp = getattr(self, "_byTp", None)
        if isinstance(byTp, dict) and self._barTp in byTp:
            stored = byTp.get(self._barTp)
        if stored is None and self._barTp != "day":
            self._bars = _aggBars(self._dayBars, self._barTp)
        else:
            self._bars = [dict(b) for b in (stored if stored is not None else self._dayBars)]

    def _onBarTpClicked(self, btn):
        tp = str(btn.property("barTp") or "day")
        if tp == self._barTp:
            return
        self._barTp = tp
        cfg = self._showCfg()
        self._showN = int(cfg["default"])
        self._rebuildBars()
        if self._showN > len(self._bars) > 0:
            self._showN = len(self._bars)
        self._setHeadTitle()
        if not self._bars:
            self.headLbl.setText(f"{self._stkNm}({self._stkCd}) {self._barTpNm()} 없음")
            self.candleChart.removeAllSeries()
            self.volView.setRows([])
            self.barsMissing.emit(tp)
            return
        self._redraw()

    def _visibleBars(self):
        cfg = self._showCfg()
        n = max(int(cfg["min"]), int(self._showN or cfg["default"]))
        if n >= len(self._bars):
            return list(self._bars)
        return list(self._bars[-n:])

    def _zoomMore(self):
        """+ : 화면에 더 많은 봉 (기간 확대)."""
        total = len(self._bars)
        if total <= 0:
            return
        cfg = self._showCfg()
        self._showN = min(total, int(self._showN) + int(cfg["step"]))
        self._redraw()

    def _zoomLess(self):
        """− : 최근 봉만 (기간 축소)."""
        cfg = self._showCfg()
        self._showN = max(int(cfg["min"]), int(self._showN) - int(cfg["step"]))
        self._redraw()

    def _toggleSrPick(self, on):
        self.candleView.pickSr = bool(on)
        self.candleView._draftPx = None
        if on:
            self.candleView.setCursor(Qt.CursorShape.CrossCursor)
            self.candleView.setMouseTracking(True)
        else:
            self.candleView.setCursor(Qt.CursorShape.ArrowCursor)
        self.candleView.viewport().update()

    def _onSrPxPicked(self, px):
        price = _absPx(px)
        if price is None or not self._stkCd:
            return
        try:
            from utils.deskChartSrStore import addSrLine

            addSrLine(self._stkCd, price, note="지지", lineTp="sup")
        except Exception:
            pass
        self._srHideId = ""
        self.btnAddSr.setChecked(False)
        self._toggleSrPick(False)
        self._redraw()
        self._fillSrList()

    def _onSrMoveBegan(self, lineId):
        """드래그 시작 — 원래 선은 숨기고 미리보기만 따라가게."""
        self._srHideId = str(lineId or "")
        self._redraw()

    def _onSrLineMoved(self, lineId, px):
        price = _absPx(px)
        self._srHideId = ""
        if price is None or not self._stkCd or not lineId:
            self._redraw()
            return
        try:
            from utils.deskChartSrStore import updSrLine

            updSrLine(self._stkCd, lineId, price)
        except Exception:
            pass
        self._redraw()
        self._fillSrList()

    def _onSrLineDeleted(self, lineId):
        self._srHideId = ""
        if not lineId or not self._stkCd:
            return
        try:
            from utils.deskChartSrStore import delSrLine

            delSrLine(self._stkCd, lineId)
        except Exception:
            pass
        self._redraw()
        self._fillSrList()

    def _delSrItem(self, item):
        lineId = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._onSrLineDeleted(lineId)

    def _fillSrList(self):
        self.srList.clear()
        if not self._stkCd:
            return
        try:
            from utils.deskChartSrStore import listSrLines

            rows = listSrLines(self._stkCd)
        except Exception:
            rows = []
        for r in rows:
            it = QListWidgetItem(f"{_fmtWon(r['px'])}  {r.get('note') or '지지'}")
            it.setData(Qt.ItemDataRole.UserRole, r.get("id"))
            self.srList.addItem(it)

    def _srRows(self):
        if not self._stkCd:
            return []
        try:
            from utils.deskChartSrStore import listSrLines

            return listSrLines(self._stkCd)
        except Exception:
            return []

    def _onPlotArea(self, rect):
        # 캔들 plotArea(차트 좌표)를 뷰 픽셀로 바꿔 거래량 막대와 좌우를 맞춘다
        if rect is None or rect.width() <= 1:
            return
        ch = self.candleChart
        view = self.candleView
        topLeft = view.mapFromScene(ch.mapToScene(rect.topLeft()))
        botRight = view.mapFromScene(ch.mapToScene(rect.bottomRight()))
        left = int(topLeft.x())
        right = int(view.width() - botRight.x())
        self.volView.setPlotPad(left, max(right, 4))
        # 첫 표시 때 plotArea 가 늦게 잡히면 월 눈금이 한쪽으로 갔다가 돌아온다.
        # 영역이 확정된 뒤에만 월 글자를 그리고, 뷰를 다시 칠한다.
        vw = max(view.viewport().width(), view.width(), 1)
        if rect.width() >= vw * 0.35:
            view._plotReady = True
        view.viewport().update()

    def clearChart(self, msg="관심종목에서 종목을 선택하세요"):
        self._bars = []
        self._dayBars = []
        self._byTp = {}
        self._stkCd = ""
        self._stkNm = ""
        self._srHideId = ""
        self.headLbl.setText(msg)
        self.candleChart.removeAllSeries()
        for ax in list(self.candleChart.axes()):
            self.candleChart.removeAxis(ax)
        self.volView.setRows([])
        self.srList.clear()
        self.candleView.setSrHits([])
        self.candleView.setXTicks(0, [])

    def setDayBars(self, stkCd, stkNm, bars, liveYmd=None):
        """일봉만 넘어온 예전 호출. 주·월은 키가 없어 일봉을 묶는다."""
        self.setChartBundle(
            stkCd,
            stkNm,
            {"day": bars or []},
            liveYmd=liveYmd,
        )

    def setChartBundle(self, stkCd, stkNm, bundle, liveYmd=None):
        """일·주·월 저장 봉. 없는 주기는 빈 목록이라 일봉으로 대신 그리지 않는다."""
        data = bundle or {}
        self._stkCd = str(stkCd or "")
        self._stkNm = str(stkNm or "")
        self._liveYmd = str(liveYmd or datetime.now().strftime("%Y%m%d"))[:8]
        self._byTp = {}
        for key in ("day", "week", "month"):
            if key in data:
                self._byTp[key] = [dict(b) for b in (data.get(key) or [])]
        self._dayBars = list(self._byTp.get("day") or [])
        self._rebuildBars()
        note = str(data.get("note") or "").strip()
        if not self._bars:
            label = self._barTpNm()
            title = f"{self._stkNm}({self._stkCd}) {label} 없음" if self._stkCd else f"{label} 없음"
            if note:
                title = f"{title} · {note[:80]}"
            self.headLbl.setText(title)
            self.candleChart.removeAllSeries()
            self.volView.setRows([])
            self._fillSrList()
            return
        self._setHeadTitle()
        if self._showN > len(self._bars):
            self._showN = len(self._bars)
        self._fillSrList()
        self._redraw()

    def applyLivePx(self, pxRaw):
        """
        당일 일봉만 갱신한 뒤 주·월·년봉을 다시 묶는다.
        0B 명세에 거래량 FID 가 없어 당일 거래량은 일봉값을 유지한다.
        """
        px = _absPx(pxRaw)
        if px is None or not self._dayBars or not self._stkCd:
            return False
        last = self._dayBars[-1]
        lastDt = str(last.get("tradeDt") or "")[:8]
        if lastDt == self._liveYmd and abs(float(last.get("close") or 0) - px) < 0.5:
            return False
        if lastDt == self._liveYmd:
            last["close"] = px
            last["high"] = max(float(last.get("high") or px), px)
            lo = float(last.get("low") or 0)
            last["low"] = px if lo <= 0 else min(lo, px)
            if float(last.get("open") or 0) <= 0:
                last["open"] = px
        else:
            fresh = {
                "tradeDt": self._liveYmd,
                "open": px,
                "high": px,
                "low": px,
                "close": px,
                "qty": 0,
            }
            self._dayBars.append(fresh)
        if self._barTp != "day":
            return True
        self._rebuildBars()
        self._redraw()
        return True

    def _redraw(self):
        allBars = self._bars
        if not allBars:
            return
        self.candleView._plotReady = False
        vis = self._visibleBars()
        # 이평은 전체 봉으로 계산한 뒤 화면에 보이는 구간만 그린다
        allClose = [float(b.get("close") or 0) for b in allBars]
        visStart = len(allBars) - len(vis)
        xTicks = _evenYmTickRows(vis)
        # 카테고리는 빈 유일키만 — 날짜 글자를 넣으면 Qt 축과 커스텀 라벨이 겹친다
        labels = ["\u200b" * (i + 1) for i in range(len(vis))]
        volRows = []
        lo = None
        hi = None
        candle = QCandlestickSeries()
        # 범례에는 이평 색+기간만. 일봉(양/음) 마커·글자는 빼 둔다
        candle.setName("")
        candle.setIncreasingColor(QColor("#C62828"))
        candle.setDecreasingColor(QColor("#1565C0"))
        candle.setBodyOutlineVisible(False)
        for b in vis:
            o = float(b.get("open") or b.get("close") or 0)
            h = float(b.get("high") or o)
            l = float(b.get("low") or o)
            c = float(b.get("close") or 0)
            q = float(b.get("qty") or 0)
            candle.append(QCandlestickSet(o, h, l, c))
            lo = l if lo is None else min(lo, l)
            hi = h if hi is None else max(hi, h)
            volRows.append({"qty": q, "up": c >= o})

        self.volView.setRows(volRows)

        self.candleChart.removeAllSeries()
        for ax in list(self.candleChart.axes()):
            self.candleChart.removeAxis(ax)

        axX = QBarCategoryAxis()
        axX.append(labels)
        axX.setGridLineVisible(False)
        axX.setLabelsVisible(False)
        axX.setLineVisible(False)

        axY = QValueAxis()
        # 자리는 확보하고 기본 숫자는 숨김 — 콤마 금액은 drawForeground 에서
        axY.setLabelFormat("%11d")
        axY.setLabelsColor(QColor(0, 0, 0, 0))
        if lo is not None and hi is not None and hi > 0:
            pad = max((hi - lo) * 0.06, hi * 0.01)
            axY.setRange(max(0.0, lo - pad), hi + pad)
        self._yAxis = axY
        self.candleView._yAxis = axY

        self.candleChart.addSeries(candle)
        self.candleChart.addAxis(axX, Qt.AlignmentFlag.AlignBottom)
        self.candleChart.addAxis(axY, Qt.AlignmentFlag.AlignLeft)
        candle.attachAxis(axX)
        candle.attachAxis(axY)
        for mk in self.candleChart.legend().markers(candle):
            mk.setVisible(False)

        axXma = QValueAxis()
        axXma.setRange(-0.5, max(len(vis) - 0.5, 0.5))
        axXma.setVisible(False)
        self.candleChart.addAxis(axXma, Qt.AlignmentFlag.AlignBottom)

        for period in _MA_PERIODS:
            line = QLineSeries()
            line.setName(str(period))
            pen = QPen(QColor(_MA_COLOR[period]))
            pen.setWidth(int(_MA_WIDTH.get(period) or 2))
            pen.setCosmetic(True)
            line.setPen(pen)
            hasPt = False
            for j in range(len(vis)):
                i = visStart + j
                ma = _smaAt(allClose, i, period)
                if ma is None:
                    continue
                line.append(float(j), ma)
                hasPt = True
            if not hasPt:
                continue
            self.candleChart.addSeries(line)
            line.attachAxis(axXma)
            line.attachAxis(axY)

        hideId = str(self._srHideId or "")
        nVis = max(len(vis) - 1, 1)
        srRows = self._srRows()
        hits = []
        for sr in srRows:
            px = float(sr.get("px") or 0)
            if px <= 0:
                continue
            hits.append({"id": str(sr.get("id") or ""), "px": px})
            if hideId and str(sr.get("id") or "") == hideId:
                continue
            hn = QLineSeries()
            hn.setName(f"{sr.get('note') or '지지'} {_fmtWon(px)}")
            pen = QPen(QColor(_SR_COLOR))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            hn.setPen(pen)
            hn.append(0.0, px)
            hn.append(float(nVis), px)
            self.candleChart.addSeries(hn)
            hn.attachAxis(axXma)
            hn.attachAxis(axY)
            for mk in self.candleChart.legend().markers(hn):
                mk.setVisible(False)
            if lo is not None and hi is not None:
                if px < lo:
                    axY.setMin(px * 0.995)
                if px > hi:
                    axY.setMax(px * 1.005)
        self.candleView.setSrHits(hits)
        self.candleView.setXTicks(len(vis), xTicks)
        QTimer.singleShot(0, self._refreshAxisPaint)
