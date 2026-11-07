"""
회원 로그인 런처 창 — 구 SATP_UPDATE 와 유사.
  - 로그인 후에도 창 유지
  - 실전/모의 콤보 + 선택 모드 앱키 등록(필수)
  - 실행 버튼으로 매매창 기동 (종료 후에도 재실행)
  - X 닫기 → 트레이로 숨김 (완전 종료 아님)
  - 트레이 우클릭: 열기 / 종료 (종료 시 매매창·세션 정리)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from utils.appVersion import formatAppVersion
from utils.localApiKeyStore import loadApiKeysFromEnv, saveApiKeysToEnv
from utils.localLoginStore import loadLoginLocal, saveLoginLocal
from utils.termLog import termLog

_WIN_W = 261
_WIN_H = 360
_FIELD_W = 182
_LOGO_W = 87
_LOGO_H = 114


def _normMode(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in ("real", "live", "실전"):
        return "real"
    return "paper"


class _ModeApiKeyDlg(QDialog):
    """선택 모드(실전/모의) 앱키 등록."""

    def __init__(self, parent=None, mode="paper", keys=None):
        super().__init__(parent)
        self._mode = _normMode(mode)
        lab = "실전" if self._mode == "real" else "모의"
        self.setWindowTitle(f"키움 앱키 · {lab}")
        self.setFixedWidth(320)
        keys = keys or {}
        lay = QVBoxLayout(self)
        tip = QLabel(f"{lab} 매매용 AppKey / AppSecret 을 입력하세요. (필수)")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#37474F; font-size: 11px;")
        lay.addWidget(tip)
        form = QFormLayout()
        if self._mode == "real":
            self.keyEdit = QLineEdit(str(keys.get("realAppKey") or ""))
            self.secEdit = QLineEdit(str(keys.get("realAppSecret") or ""))
        else:
            self.keyEdit = QLineEdit(str(keys.get("paperAppKey") or ""))
            self.secEdit = QLineEdit(str(keys.get("paperAppSecret") or ""))
        self.secEdit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("AppKey", self.keyEdit)
        form.addRow("AppSecret", self.secEdit)
        lay.addLayout(form)
        note = QLabel("앱키는 이 PC에만 저장됩니다.")
        note.setStyleSheet("color:#78909C; font-size: 10px;")
        lay.addWidget(note)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._onOk)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _onOk(self):
        if not self.keyEdit.text().strip() or not self.secEdit.text().strip():
            QMessageBox.warning(self, "앱키", "AppKey 와 AppSecret 을 모두 입력하세요.")
            return
        self.accept()

    def applyTo(self, keys: dict) -> dict:
        out = dict(keys or {})
        k = self.keyEdit.text().strip()
        s = self.secEdit.text().strip()
        if self._mode == "real":
            out["realAppKey"] = k
            out["realAppSecret"] = s
        else:
            out["paperAppKey"] = k
            out["paperAppSecret"] = s
        return out


class LoginWin(QDialog):
    """로그인 런처 — 닫기 전까지 유지. 매매창은 자식 프로세스."""

    def __init__(self, parent=None, launcherMode=False):
        super().__init__(parent)
        self._launcherMode = bool(launcherMode)
        self.setWindowTitle("천둥이 로그인")
        self.setModal(False)
        self.setFixedSize(_WIN_W, _WIN_H)
        self.setStyleSheet("QDialog { background-color: white; }")
        try:
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        except Exception:
            pass
        try:
            from utils.appIcon import applyWindowIcon

            applyWindowIcon(self, kind="login")
        except Exception:
            pass

        self._loginInfo = None
        self._loggedIn = False
        self._apiKeys = loadApiKeysFromEnv()
        self._tradeProc = None
        self._installRoot = None
        # 트레이: X=숨김, 메뉴「종료」만 완전 종료
        self._forceQuit = False
        self._tray = None
        self._trayHintShown = False
        self._build()
        self._loadRemembered()
        self._applyLoggedInUi(False)
        if self._launcherMode:
            self._setupTray()

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._pollTrade)
        self._poll.start()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(37, 16, 37, 8)
        lay.setSpacing(0)

        logoWrap = QHBoxLayout()
        try:
            from utils.appIcon import makeBoltLogoLabel

            logo = makeBoltLogoLabel(self, heightPx=_LOGO_H, widthPx=_LOGO_W)
        except Exception:
            logo = QLabel("⚡")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setFixedSize(_LOGO_W, _LOGO_H)
            logo.setStyleSheet("font-size: 56px; color: #FFD600;")
        logoWrap.addStretch(1)
        logoWrap.addWidget(logo)
        logoWrap.addStretch(1)
        lay.addLayout(logoWrap)
        lay.addSpacing(8)

        fieldFont = QFont("Microsoft Sans Serif", 12)
        btnFont = QFont("Microsoft Sans Serif", 12)
        btnFont.setBold(True)

        self.idEdit = QLineEdit()
        self.idEdit.setPlaceholderText("아이디")
        self.idEdit.setFixedSize(_FIELD_W, 28)
        self.idEdit.setFont(fieldFont)
        lay.addWidget(self.idEdit, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(2)

        self.pwEdit = QLineEdit()
        self.pwEdit.setPlaceholderText("패스워드")
        self.pwEdit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwEdit.setFixedSize(_FIELD_W, 28)
        self.pwEdit.setFont(fieldFont)
        self.pwEdit.setMaxLength(14)
        lay.addWidget(self.pwEdit, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(6)

        # 실전/모의 콤보
        modeRow = QHBoxLayout()
        modeLab = QLabel("모드")
        modeLab.setStyleSheet("font-size: 11px;")
        self.modeCombo = QComboBox()
        self.modeCombo.setFixedWidth(140)
        self.modeCombo.addItem("모의", "paper")
        self.modeCombo.addItem("실전", "real")
        modeRow.addWidget(modeLab)
        modeRow.addWidget(self.modeCombo, 1)
        lay.addLayout(modeRow)
        lay.addSpacing(4)

        # 로그인 정보 저장 / 앱키 — 완전 분리 (한 줄 겹침 방지)
        self.rememberChk = QCheckBox("로그인 정보 저장")
        self.rememberChk.setChecked(True)
        self.rememberChk.setStyleSheet("font-size: 11px;")
        self.rememberChk.setMinimumHeight(22)
        lay.addWidget(self.rememberChk)
        lay.addSpacing(4)

        self.keyBtn = QPushButton("앱키 등록")
        self.keyBtn.setFlat(True)
        self.keyBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyBtn.setMinimumHeight(22)
        self.keyBtn.setStyleSheet(
            "QPushButton { color: #1565C0; font-size: 11px; text-decoration: underline;"
            " text-align: left; padding: 2px 0; border: none; }"
        )
        self.keyBtn.clicked.connect(self._editApiKeys)
        lay.addWidget(self.keyBtn)
        lay.addSpacing(8)

        self.okBtn = QPushButton("로 그 인")
        self.okBtn.setDefault(True)
        self.okBtn.setFixedSize(_FIELD_W, 32)
        self.okBtn.setFont(btnFont)
        self.okBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.okBtn.setStyleSheet(
            "QPushButton {"
            " background-color: #6495ED; color: white; font-weight: 700;"
            " border: none;"
            "}"
            "QPushButton:hover { background-color: #4A7FD4; }"
            "QPushButton:disabled { background-color: #A0B4D8; }"
        )
        lay.addWidget(self.okBtn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(4)

        # 구 btnExe (ForestGreen) — 로그인 후 표시
        self.exeBtn = QPushButton("매매창 실행")
        self.exeBtn.setFixedSize(_FIELD_W, 32)
        self.exeBtn.setFont(btnFont)
        self.exeBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exeBtn.setStyleSheet(
            "QPushButton {"
            " background-color: #228B22; color: white; font-weight: 700;"
            " border: none;"
            "}"
            "QPushButton:hover { background-color: #1B6E1B; }"
            "QPushButton:disabled { background-color: #8FBC8F; }"
        )
        lay.addWidget(self.exeBtn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(6)

        self.msgLbl = QLabel("")
        self.msgLbl.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        # 버튼 폭과 동일 — 성공 문구는 한 줄, 긴 오류만 줄바꿈
        self.msgLbl.setFixedWidth(_FIELD_W)
        self.msgLbl.setMinimumHeight(18)
        self.msgLbl.setStyleSheet("color:#c62828; font-size: 8pt;")
        self.msgLbl.setWordWrap(True)
        lay.addWidget(self.msgLbl, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(4)

        self.verLbl = QLabel(formatAppVersion())
        self.verLbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verLbl.setStyleSheet("font-size: 11px; color: #90A4AE;")
        lay.addWidget(self.verLbl, 0, Qt.AlignmentFlag.AlignHCenter)

        self.okBtn.clicked.connect(self._onLogin)
        self.exeBtn.clicked.connect(self._onExe)
        self.pwEdit.returnPressed.connect(self._onLogin)
        self.idEdit.returnPressed.connect(self.pwEdit.setFocus)

    def selectedMode(self) -> str:
        return _normMode(self.modeCombo.currentData())

    def _setMsg(self, text, ok=False):
        self.msgLbl.setStyleSheet(
            f"color:{'#2E7D32' if ok else '#c62828'}; font-size: 8pt;"
        )
        self.msgLbl.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.msgLbl.setText(str(text or ""))

    def _syncExeBtn(self):
        """매매창 실행 중이면 버튼 비활성 + 문구 변경."""
        if not self._loggedIn:
            self.exeBtn.setVisible(False)
            self.exeBtn.setText("매매창 실행")
            self.exeBtn.setEnabled(False)
            return
        self.exeBtn.setVisible(True)
        if self._tradeRunning():
            self.exeBtn.setText("매매창 실행중")
            self.exeBtn.setEnabled(False)
            self.exeBtn.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.exeBtn.setText("매매창 실행")
            self.exeBtn.setEnabled(True)
            self.exeBtn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _modeKeysOk(self, mode=None) -> bool:
        mode = _normMode(mode or self.selectedMode())
        k = self._apiKeys or {}
        if mode == "real":
            return bool(k.get("realAppKey") and k.get("realAppSecret"))
        return bool(k.get("paperAppKey") and k.get("paperAppSecret"))

    def _ensureModeKeys(self, forceDlg=False) -> bool:
        """선택 모드 앱키 필수. 없으면 다이얼로그 강제."""
        mode = self.selectedMode()
        if self._modeKeysOk(mode) and not forceDlg:
            return True
        dlg = _ModeApiKeyDlg(self, mode=mode, keys=self._apiKeys)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._setMsg(f"{'실전' if mode == 'real' else '모의'} 앱키를 입력해야 합니다.")
            return False
        self._apiKeys = dlg.applyTo(self._apiKeys)
        try:
            saveApiKeysToEnv(self._apiKeys)
        except Exception as err:
            QMessageBox.warning(self, "앱키 저장 실패", str(err))
            return False
        self._setMsg("앱키가 저장되었습니다.", ok=True)
        return self._modeKeysOk(mode)

    def _editApiKeys(self):
        if not self._ensureModeKeys(forceDlg=True):
            return
        if self.rememberChk.isChecked() and self._loginInfo:
            saveLoginLocal(
                {
                    "remember": True,
                    "logId": self.idEdit.text().strip(),
                    "tradeMode": self.selectedMode(),
                    **(self._apiKeys or {}),
                }
            )

    def _loadRemembered(self):
        local = loadLoginLocal() or {}
        keys = loadApiKeysFromEnv()
        self._apiKeys = {
            "realAppKey": str(
                local.get("realAppKey") or keys.get("realAppKey") or ""
            ),
            "realAppSecret": str(
                local.get("realAppSecret") or keys.get("realAppSecret") or ""
            ),
            "paperAppKey": str(
                local.get("paperAppKey") or keys.get("paperAppKey") or ""
            ),
            "paperAppSecret": str(
                local.get("paperAppSecret") or keys.get("paperAppSecret") or ""
            ),
        }
        mode = _normMode(local.get("tradeMode") or "paper")
        idx = self.modeCombo.findData(mode)
        if idx >= 0:
            self.modeCombo.setCurrentIndex(idx)
        if local.get("remember"):
            self.rememberChk.setChecked(True)
            self.idEdit.setText(str(local.get("logId") or ""))
            self.pwEdit.clear()
        else:
            self.rememberChk.setChecked(False)

    def _applyLoggedInUi(self, loggedIn: bool):
        self._loggedIn = bool(loggedIn)
        self.okBtn.setVisible(not loggedIn)
        self.idEdit.setEnabled(not loggedIn)
        self.pwEdit.setEnabled(not loggedIn)
        # 로그인 후 모드만 변경 가능 — 앱키 등록 버튼은 숨김
        self.modeCombo.setEnabled(True)
        self.keyBtn.setVisible(not loggedIn)
        self.keyBtn.setEnabled(not loggedIn)
        self.rememberChk.setEnabled(not loggedIn)
        self._syncExeBtn()

    def _saveTradeMode(self):
        mode = self.selectedMode()
        try:
            from config.settingsStore import setDeskTradeMode

            setDeskTradeMode(mode)
        except Exception as err:
            termLog(f"[deskLogin] deskTradeMode 저장 실패: {err}")
        # 매매창(tradeApp) settings 에도 동기화
        try:
            import json
            from pathlib import Path

            from utils.deskAppUpdate import getInstallRoot

            root = Path(self._installRoot or getInstallRoot())
            sp = root / "config" / "data" / "settings.json"
            sp.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if sp.is_file():
                try:
                    data = json.loads(sp.read_text(encoding="utf-8")) or {}
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            data["deskTradeMode"] = mode
            sp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as err:
            termLog(f"[deskLogin] tradeApp deskTradeMode 동기화 실패: {err}")
        return mode

    def _onLogin(self):
        from utils.deskLoginAuth import loginMember

        if not self._ensureModeKeys(forceDlg=False):
            return

        logId = self.idEdit.text().strip()
        pswd = self.pwEdit.text().strip()
        self._setMsg("")
        self.okBtn.setEnabled(False)
        try:
            got = loginMember(logId, pswd)
        except Exception as err:
            got = {"ok": False, "errMsg": str(err)}
        finally:
            self.okBtn.setEnabled(True)

        if not got.get("ok"):
            self._setMsg(str(got.get("errMsg") or "로그인 실패"))
            QMessageBox.warning(self, "로그인 실패", str(got.get("errMsg") or "실패"))
            return

        mode = self._saveTradeMode()
        try:
            saveApiKeysToEnv(self._apiKeys or {})
        except Exception as err:
            QMessageBox.warning(self, "앱키 저장 실패", str(err))
            return

        if self.rememberChk.isChecked():
            saveLoginLocal(
                {
                    "remember": True,
                    "logId": logId,
                    "tradeMode": mode,
                    **(self._apiKeys or {}),
                }
            )
        else:
            saveLoginLocal({"remember": False})

        self._loginInfo = got
        self._applyLoggedInUi(True)
        try:
            from utils.deskLoginSession import writeLoginSession

            writeLoginSession(got)
        except Exception as err:
            termLog(f"[deskLogin] 세션파일 기록 실패: {err}")
        try:
            from utils.deskSessWatch import startDeskSessWatch

            startDeskSessWatch(intervalMs=45000, onKick=self._onSessKicked)
        except Exception as err:
            termLog(f"[deskLogin] 세션감시 실패: {err}")
        if got.get("sessKicked"):
            QMessageBox.information(
                self,
                "중복 로그인",
                "다른 곳의 기존 로그인을 종료하고 이 PC에서 로그인했습니다.",
            )
        termLog(
            f"[deskLogin] OK {logId} mode={mode} grade={got.get('deskGrade')} "
            f"role={got.get('deskRole')} csmno={got.get('csmno')}"
        )
        self._setMsg(
            f"로그인 완료 · {'실전' if mode == 'real' else '모의'}",
            ok=True,
        )

    def _runUpdate(self):
        from ui.deskProgressWin import DeskProgressWin
        from utils.deskAppUpdate import checkAndUpdate, getInstallRoot
        from utils.deskShortcut import ensureLoginShortcut
        from PySide6.QtWidgets import QApplication

        installPath = str(getInstallRoot())
        progWin = DeskProgressWin(
            parent=None,
            title="천둥이 설치·업데이트",
            pathText=installPath,
        )
        progWin.setBusy(True)
        progWin.setStatus("버전·패키지 확인 중…")
        progWin.show()
        progWin.raise_()
        QApplication.processEvents()

        def _cb(msg):
            progWin.setStatus(str(msg))
            QApplication.processEvents()

        try:
            upd = checkAndUpdate(
                self._loginInfo, progressCb=_cb, forceIfMissing=True
            )
        except Exception as err:
            termLog(f"[deskLogin] 업데이트 오류: {err}")
            upd = {
                "ok": False,
                "canLaunch": False,
                "msg": str(err),
                "installRoot": installPath,
            }
        finally:
            try:
                progWin.close()
                progWin.deleteLater()
                QApplication.processEvents()
            except Exception:
                pass

        try:
            root = upd.get("installRoot") or installPath
            lnk = ensureLoginShortcut(installRoot=root)
            if lnk:
                termLog(f"[deskLogin] 바로가기: {lnk}")
        except Exception as err:
            termLog(f"[deskLogin] 바로가기 실패: {err}")

        # 업데이트 후 로그인창 버전 표시 갱신 (tradeApp·설치루트 중 최신)
        try:
            if hasattr(self, "verLbl") and self.verLbl is not None:
                self.verLbl.setText(formatAppVersion())
        except Exception:
            pass

        return upd

    def _onSessKicked(self, msg):
        """다른 PC에서 같은 계정 로그인 → 매매창·로그인 상태 해제."""
        self._killTrade()
        self._loggedIn = False
        self._loginInfo = None
        try:
            self._applyLoggedInUi(False)
        except Exception:
            pass
        try:
            from utils.deskLoginSession import clearLoginSession

            clearLoginSession()
        except Exception:
            pass
        self._setMsg(str(msg or "다른 곳에서 로그인됨"), ok=False)
        QMessageBox.warning(
            self,
            "중복 로그인",
            str(msg or "다른 곳에서 같은 계정으로 로그인되어 종료합니다."),
        )

    def _killTrade(self):
        proc = self._tradeProc
        self._tradeProc = None
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return
        except Exception:
            return
        pid = getattr(proc, "pid", None)
        try:
            import subprocess

            if pid:
                # 자식 포함 종료 (로그인창 종료 시 매매창도)
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                proc.terminate()
        except Exception as err:
            termLog(f"[deskLogin] 매매창 종료 실패: {err}")
            try:
                proc.kill()
            except Exception:
                pass

    def _pollTrade(self):
        proc = self._tradeProc
        if proc is None:
            return
        try:
            code = proc.poll()
        except Exception:
            self._tradeProc = None
            self._syncExeBtn()
            return
        if code is None:
            # 실행 중 — 버튼 상태 유지
            self._syncExeBtn()
            return
        self._tradeProc = None
        self._syncExeBtn()
        if self._loggedIn:
            self._setMsg(
                f"매매창이 종료되었습니다 (code={code}). 다시 실행할 수 있습니다.",
                ok=True,
            )

    def _tradeRunning(self) -> bool:
        proc = self._tradeProc
        if proc is None:
            return False
        try:
            return proc.poll() is None
        except Exception:
            return False

    def _onExe(self):
        if not self._loggedIn or not self._loginInfo:
            self._setMsg("먼저 로그인하세요.")
            return
        if not self._ensureModeKeys(forceDlg=False):
            return

        mode = self._saveTradeMode()
        try:
            saveApiKeysToEnv(self._apiKeys or {})
        except Exception as err:
            QMessageBox.warning(self, "앱키 저장 실패", str(err))
            return

        if self._tradeRunning():
            # 실행 중이면 버튼이 비활성이라 여기 거의 안 옴 — 방어용
            self._syncExeBtn()
            return

        self.exeBtn.setEnabled(False)
        self.exeBtn.setText("매매창 준비중…")
        self._setMsg("매매창 준비 중…", ok=True)
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
            upd = self._runUpdate()
            if not upd.get("canLaunch"):
                QMessageBox.warning(
                    self,
                    "매매창 준비 실패",
                    str(
                        upd.get("msg")
                        or "매매창을 받을 수 없습니다. FTP/패키지·앱키를 확인하세요."
                    ),
                )
                self._setMsg(str(upd.get("msg") or "준비 실패"))
                return

            from utils.deskAppUpdate import launchTradeApp

            self._installRoot = upd.get("installRoot")
            launched = launchTradeApp(
                self._loginInfo,
                installRoot=self._installRoot,
                apiKeys=self._apiKeys or {},
            )
            if not launched.get("ok"):
                QMessageBox.critical(
                    self,
                    "실행 실패",
                    str(launched.get("msg") or "매매창을 실행할 수 없습니다."),
                )
                self._setMsg("매매창 실행 실패 — 메시지 확인", ok=False)
                return

            self._tradeProc = launched.get("proc")
            self._setMsg(
                f"매매창 실행 중 · {'실전' if mode == 'real' else '모의'}",
                ok=True,
            )
            termLog(f"[deskLogin] 매매창 실행 mode={mode} pid={launched.get('pid')}")
        finally:
            self._syncExeBtn()

    def _setupTray(self):
        """구 SATP_UPDATE 와 같이 트레이에 상주. X 닫기 ≠ 종료."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            termLog("[deskLogin] 시스템 트레이 없음 — X 닫기 시 종료")
            return
        icon = QIcon()
        try:
            from utils.appIcon import iconPath

            p = iconPath("login")
            if p is not None:
                icon = QIcon(str(p))
        except Exception:
            pass
        if icon.isNull():
            try:
                icon = self.windowIcon()
            except Exception:
                pass
        self._tray = QSystemTrayIcon(icon if not icon.isNull() else QIcon(), self)
        self._tray.setToolTip("천둥이 로그인")
        menu = QMenu()
        actOpen = QAction("열기", self)
        actQuit = QAction("종료", self)
        actOpen.triggered.connect(self._trayShowWin)
        actQuit.triggered.connect(self._trayQuitApp)
        menu.addAction(actOpen)
        menu.addSeparator()
        menu.addAction(actQuit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._onTrayActivated)
        self._tray.show()

    def _trayShowWin(self):
        """트레이「열기」— 로그인창 표시."""
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _trayQuitApp(self):
        """트레이「종료」— 매매창·세션 정리 후 프로세스 종료."""
        self._forceQuit = True
        self.close()

    def _onTrayActivated(self, reason):
        # 더블클릭·좌클릭(Trigger) 시에도 창 열기
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self._trayShowWin()

    def reject(self):
        # Esc / 기본 reject 도 X 와 같이 트레이 숨김
        if self._launcherMode and not self._forceQuit:
            self.hide()
            self._maybeTrayHint()
            return
        super().reject()

    def closeEvent(self, event):
        if self._launcherMode and not self._forceQuit:
            event.ignore()
            self.hide()
            self._maybeTrayHint()
            return
        self._doQuitCleanup()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _maybeTrayHint(self):
        if self._trayHintShown or self._tray is None:
            return
        self._trayHintShown = True
        try:
            self._tray.showMessage(
                "천둥이 로그인",
                "트레이로 최소화되었습니다. 우클릭 → 열기 / 종료",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        except Exception:
            pass

    def _doQuitCleanup(self):
        """완전 종료 시 매매창·세션·트레이 정리."""
        self._killTrade()
        try:
            from utils.deskSessWatch import releaseCurrentDeskSess, stopDeskSessWatch

            stopDeskSessWatch()
            releaseCurrentDeskSess()
        except Exception as err:
            termLog(f"[deskLogin] 세션 해제 실패: {err}")
        try:
            self._poll.stop()
        except Exception:
            pass
        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception:
                pass


def runLoginLauncher(app=None):
    """
    로그인 런처 상시 창. 트레이「종료」전까지 유지.
    X 닫기 → 트레이 숨김. 이미 실행 중이면 기존 창만 앞으로.
    반환: 프로세스 exit code (0=정상 종료)
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    try:
        from utils.appIcon import setAppUserModelId

        setAppUserModelId("SATP_AI.Login")
    except Exception:
        pass

    if app is None:
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
    # 창을 숨겨도(트레이) 프로세스가 죽지 않게
    app.setQuitOnLastWindowClosed(False)
    try:
        from utils.appIcon import applyAppIcon

        applyAppIcon(app, appId="SATP_AI.Login", kind="login")
    except Exception:
        pass

    # 단일 실행: 두 번째 기동 시 기존 창 활성화 후 종료
    holder = {"win": None}

    def _raiseExisting():
        win = holder.get("win")
        if win is None:
            return
        try:
            # 트레이에만 있어도 창을 다시 연다
            if hasattr(win, "_trayShowWin"):
                win._trayShowWin()
            else:
                win.showNormal()
                win.raise_()
                win.activateWindow()
        except Exception:
            pass

    try:
        from utils.loginSingleInst import tryClaimLoginInstance

        if not tryClaimLoginInstance(onRaise=_raiseExisting):
            QMessageBox.information(
                None,
                "천둥이 로그인",
                "로그인 창이 이미 실행 중입니다.",
            )
            return 0
    except Exception as err:
        try:
            from utils.termLog import termLog

            termLog(f"[login] 단일실행 잠금 실패(무시): {err}")
        except Exception:
            pass

    win = LoginWin(launcherMode=True)
    holder["win"] = win
    win.show()
    win.raise_()
    win.activateWindow()
    return int(app.exec())


# 구 호출 호환
def runLoginDialog(app=None, launcherMode=False):
    """비런처용: 로그인 성공 시 닫힘. 런처는 runLoginLauncher 사용."""
    from PySide6.QtWidgets import QApplication

    if app is None:
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
    dlg = LoginWin(launcherMode=False)

    def _finishLogin():
        dlg._onLogin()
        if dlg._loggedIn:
            dlg.accept()

    try:
        dlg.okBtn.clicked.disconnect()
    except Exception:
        pass
    try:
        dlg.pwEdit.returnPressed.disconnect()
    except Exception:
        pass
    dlg.okBtn.clicked.connect(_finishLogin)
    dlg.pwEdit.returnPressed.connect(_finishLogin)
    dlg.exeBtn.setVisible(False)
    result = dlg.exec()
    if result != QDialog.DialogCode.Accepted:
        return None
    return dlg._loginInfo or {}
