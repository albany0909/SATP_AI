"""매매창 설정 탭 — MMG01 종목당 최대 매수금액 입력 행."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton


def addMmgSettRow(owner, layout):
    """owner 에 mmgMaxAmtEdit / mmgMaxAmtLbl 을 붙이고 layout 에 행을 추가한다."""
    from config.mmgSettings import fmtWon, getMmgMaxBuyAmt, setMmgMaxBuyAmt

    row = QHBoxLayout()
    row.addWidget(QLabel("종목당 최대 매수금액"))
    edit = QLineEdit()
    edit.setPlaceholderText("3,000,000")
    edit.setToolTip("MMG01 추가매수 트랜치(40/35/25%)의 기준 금액. 원 단위 숫자 (예: 3,000,000 / 300만)")
    edit.setText(f"{getMmgMaxBuyAmt():,}")
    row.addWidget(edit, 1)
    row.addWidget(QLabel("원"))
    btn = QPushButton("저장")
    row.addWidget(btn)
    layout.addLayout(row)
    msg = QLabel(f"현재 {fmtWon(getMmgMaxBuyAmt())}")
    msg.setStyleSheet("color: #666; font-size: 12px;")
    layout.addWidget(msg)

    def _save():
        ok, got = setMmgMaxBuyAmt(edit.text())
        if ok:
            edit.setText(f"{int(got):,}")
            msg.setStyleSheet("color: #2e7d32; font-size: 12px;")
            msg.setText(f"저장됨: {fmtWon(got)}")
        else:
            msg.setStyleSheet("color: #c62828; font-size: 12px;")
            msg.setText(f"저장 실패: {got}")

    def _fmt():
        try:
            from config.mmgSettings import parseWonInput

            edit.setText(f"{parseWonInput(edit.text()):,}")
        except ValueError:
            pass

    btn.clicked.connect(_save)
    edit.returnPressed.connect(_save)
    edit.editingFinished.connect(_fmt)
    owner.mmgMaxAmtEdit = edit
    owner.mmgMaxAmtLbl = msg
    return edit
