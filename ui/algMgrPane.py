"""알고리즘 관리. 신규 진입만 켜고 끈다. 모의/실전은 바꾸지 않는다."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from utils.algMgr import KEEP_POSITION_NOTE, applyAlgRunToggle


def _docVersions(specs):
    from utils.stratVerHist import _mdPath, parseDocVerHist

    out = {}
    for spec in specs or []:
        path = _mdPath(spec)
        if path is None:
            continue
        rows = parseDocVerHist(path.read_text(encoding="utf-8"))
        if rows:
            last = rows[-1]
            out[spec.get("algCd")] = {"ymd": last.get("ymd")}
    return out


def _readFacts():
    """SELECT 만 한다. 테이블을 만들지 않는다."""
    counts, pnl, events = {}, {}, {}
    try:
        from utils.dbConn import isLocalDbEnabled, openDbConn

        if not isLocalDbEnabled():
            return counts, pnl, events
        conn = openDbConn()
    except Exception:
        return counts, pnl, events
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ALG_CD, COUNT(*) CNT FROM AI_ALG_STK WHERE STATUS='Y' GROUP BY ALG_CD"
            )
            for row in cur.fetchall() or []:
                if isinstance(row, dict):
                    counts[row.get("ALG_CD")] = int(row.get("CNT") or 0)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ALG_CD, EVT_TP, COUNT(*) CNT
                FROM AI_ALG_EVT
                WHERE TRADE_DT=%s AND EVT_TP IN ('BUY','SELL')
                GROUP BY ALG_CD, EVT_TP
                """,
                (datetime.now().strftime("%Y%m%d"),),
            )
            for row in cur.fetchall() or []:
                if isinstance(row, dict):
                    events.setdefault(row.get("ALG_CD"), {})[row.get("EVT_TP")] = int(row.get("CNT") or 0)
            cur.execute(
                """
                SELECT ALG_CD, TDY_PNL, TOT_PNL
                FROM AI_ALG_PNL_DAY
                ORDER BY TRADE_DT DESC
                LIMIT 300
                """
            )
            for row in cur.fetchall() or []:
                if not isinstance(row, dict):
                    continue
                code = row.get("ALG_CD")
                if code in pnl:
                    continue
                pnl[code] = {"today": row.get("TDY_PNL"), "cum": row.get("TOT_PNL")}
    except Exception:
        return counts, pnl, events
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return counts, pnl, events


class AlgMgrPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.note = QLabel(KEEP_POSITION_NOTE)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #555; font-size: 11px;")
        lay.addWidget(self.note)
        self.summary = QLabel("운행중 0 · 대기 0 · 중지 0 · 청산만 0 · 미기동 0")
        self.summary.setStyleSheet("font-weight: 700;")
        lay.addWidget(self.summary)
        self.onlyRun = QCheckBox("운행중만 보기")
        self.onlyRun.toggled.connect(lambda _c: self._paintRows())
        lay.addWidget(self.onlyRun)
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "코드", "이름", "요약", "버전", "상태", "운행", "모드", "버전일", "종목수", "당일", "누적",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btnStop = QPushButton("중단")
        self.btnStart = QPushButton("다시 사용")
        self.btnSpec = QPushButton("명세")
        self.btnStop.clicked.connect(lambda: self._toggle(False))
        self.btnStart.clicked.connect(lambda: self._toggle(True))
        self.btnSpec.clicked.connect(self._showSpec)
        row.addWidget(self.btnStop)
        row.addWidget(self.btnStart)
        row.addWidget(self.btnSpec)
        row.addStretch(1)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self.reload)
        self._timer.start()
        self.reload()

    def reload(self):
        from strategies.specLoader import listStratSpecs, loadStratSpec
        from utils.algMgr import algMgrRows

        specs = []
        for item in listStratSpecs() or []:
            spec = loadStratSpec(item.get("stratId")) or item
            specs.append(spec)
        settings = {}
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
        counts, pnl, self._events = _readFacts()
        versions = _docVersions(specs)
        from datetime import datetime as _dt
        from config.krHoliday import isTradingDay
        from utils.algMgr import readRunnerStarted

        now = _dt.now()
        self._rows = algMgrRows(
            specs, settings, versions, counts, pnl,
            runs=readRunnerStarted(),
            nowHm=now.strftime("%H%M%S"),
            tradingDay=bool(isTradingDay(now)),
        )
        self._paintRows()

    def _paintRows(self):
        rows = self._rows
        if self.onlyRun.isChecked():
            rows = [row for row in rows if row.get("runState") == "운행중"]
        counts = {"운행중": 0, "대기": 0, "중지": 0, "중지(청산만)": 0, "미기동": 0, "미구현": 0}
        modes = {"모의": 0, "실전": 0}
        for row in self._rows:
            counts[row.get("runState")] = counts.get(row.get("runState"), 0) + 1
            if row.get("runState") == "운행중":
                modes[row.get("mode")] = modes.get(row.get("mode"), 0) + 1
        buy = sell = 0
        for item in (getattr(self, "_events", None) or {}).values():
            buy += int(item.get("BUY") or 0)
            sell += int(item.get("SELL") or 0)
        self.summary.setText(
            f"운행중 {counts['운행중']} · 대기 {counts['대기']} · 중지 {counts['중지']} · 청산만 {counts['중지(청산만)']} · 미구현 {counts['미구현']} · 미기동 {counts['미기동']}"
            f"  (운행 모의 {modes.get('모의', 0)} · 실전 {modes.get('실전', 0)}"
            f" · 오늘 진입 {buy} · 청산 {sell})"
        )
        self._visible = rows
        self.table.setRowCount(len(rows))
        keys = (
            "algCd", "name", "summary", "version", "status", "runState",
            "mode", "verYmd", "stkCnt", "todayPnl", "cumPnl",
        )
        for r, row in enumerate(rows):
            live = row.get("runState") == "운행중"
            for c, key in enumerate(keys):
                item = QTableWidgetItem(str(row.get(key) if row.get(key) is not None else ""))
                if key == "summary":
                    item.setToolTip(str(row.get("easy") or ""))
                if live:
                    item.setBackground(QColor("#E8F5E9"))
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def _selected(self):
        row = self.table.currentRow()
        shown = getattr(self, "_visible", self._rows)
        if row < 0 or row >= len(shown):
            return None
        return shown[row]

    def _toggle(self, turnOn):
        picked = self._selected()
        if not picked:
            return
        code = picked["algCd"]
        if turnOn:
            text = f"{code} 를 다시 사용합니다. 모의/실전은 바꾸지 않습니다."
        else:
            text = f"{code} 신규 진입을 중단합니다. {KEEP_POSITION_NOTE}"
        if picked.get("specGate"):
            text += " SRG01 은 명세서 enabled 가 꺼져 있으면 이 스위치만으로 신규 매수가 나가지 않습니다."
        answer = QMessageBox.question(self, "알고리즘 관리", text)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            from config.settingsStore import loadSettings, saveSettings

            current = loadSettings() or {}
            got = applyAlgRunToggle(
                current,
                code,
                turnOn,
                reason="다시 사용" if turnOn else "중단",
                actor="desk",
                now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            if not got.get("ok"):
                return
            saveSettings(got["settings"])
            from utils.appPaths import configDataDir
            from utils.algMgr import appendAlgMgrLog

            appendAlgMgrLog(got["log"], configDataDir() / "algMgrLog.json")
        except Exception as err:
            from utils.termLog import termLog

            termLog(f"[algMgr] 저장 실패 {code}: {err}")
            return
        self.reload()

    def _showSpec(self):
        picked = self._selected()
        if not picked:
            return
        from utils.algSpecDoc import specFilePath

        path = specFilePath(picked["algCd"])
        text = path.read_text(encoding="utf-8") if path.is_file() else "명세 파일이 없습니다."
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{picked['algCd']} 상세명세서")
        dlg.resize(640, 520)
        lay = QVBoxLayout(dlg)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        lay.addWidget(box)
        dlg.exec()
