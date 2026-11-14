"""알고리즘 관리. 신규 진입만 켜고 끈다. 모의/실전은 바꾸지 않는다."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    counts, pnl = {}, {}
    try:
        from utils.dbConn import isLocalDbEnabled, openDbConn

        if not isLocalDbEnabled():
            return counts, pnl
        conn = openDbConn()
    except Exception:
        return counts, pnl
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
        return counts, pnl
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return counts, pnl


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
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "코드", "이름", "요약", "버전", "상태", "모드", "버전일", "종목수", "당일", "누적",
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
        counts, pnl = _readFacts()
        versions = _docVersions(specs)
        self._rows = algMgrRows(specs, settings, versions, counts, pnl)
        self.table.setRowCount(len(self._rows))
        keys = ("algCd", "name", "summary", "version", "status", "mode", "verYmd", "stkCnt", "todayPnl", "cumPnl")
        for r, row in enumerate(self._rows):
            for c, key in enumerate(keys):
                item = QTableWidgetItem(str(row.get(key) if row.get(key) is not None else ""))
                if key == "status":
                    item.setForeground(Qt.GlobalColor.darkGreen if row.get("enabled") else Qt.GlobalColor.darkRed)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

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
