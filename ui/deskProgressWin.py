"""
설치/업데이트 전용 진행 창 — 로그인 창과 분리.
경로 표시 + 프로그레스바 + 상태 메시지.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)


class DeskProgressWin(QDialog):
    """로그인 UI와 별도인 다운로드·설치·업데이트 진행 창."""

    def __init__(self, parent=None, title="진행 중", pathText=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        # 비모달 — 로그인 창과 겹치지 않고 단독으로 동작
        self.setModal(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        try:
            from utils.appIcon import applyWindowIcon

            applyWindowIcon(self, kind="login")
        except Exception:
            pass

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        head = QLabel(title)
        head.setStyleSheet("font-size: 14px; font-weight: 700; color: #263238;")
        lay.addWidget(head)

        pathTitle = QLabel("경로")
        pathTitle.setStyleSheet("font-size: 11px; color: #78909C;")
        lay.addWidget(pathTitle)

        self.pathLbl = QLabel(str(pathText or "-"))
        self.pathLbl.setWordWrap(True)
        self.pathLbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.pathLbl.setStyleSheet(
            "font-size: 12px; color: #37474F; background: #ECEFF1;"
            " padding: 8px; border-radius: 4px;"
        )
        lay.addWidget(self.pathLbl)

        self.prog = QProgressBar()
        self.prog.setRange(0, 0)  # busy
        self.prog.setTextVisible(False)
        self.prog.setMinimumHeight(18)
        lay.addWidget(self.prog)

        self.statusLbl = QLabel("준비 중…")
        self.statusLbl.setWordWrap(True)
        self.statusLbl.setStyleSheet("font-size: 12px; color: #1565C0;")
        lay.addWidget(self.statusLbl)

        tip = QLabel("창을 닫지 마세요. 완료되면 자동으로 닫힙니다.")
        tip.setStyleSheet("font-size: 10px; color: #90A4AE;")
        lay.addWidget(tip)

    def setPath(self, pathText):
        self.pathLbl.setText(str(pathText or "-"))

    def setBusy(self, busy=True):
        if busy:
            self.prog.setRange(0, 0)
        else:
            self.prog.setRange(0, 100)

    def setProgress(self, pct, status=""):
        self.prog.setRange(0, 100)
        self.prog.setValue(max(0, min(100, int(pct))))
        if status:
            self.statusLbl.setText(str(status))

    def setStatus(self, msg):
        self.statusLbl.setText(str(msg or ""))
        # 로그인 창 processEvents 와 함께 쓰일 때 갱신
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        except Exception:
            pass
