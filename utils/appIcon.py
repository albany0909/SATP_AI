"""
앱 아이콘 — 로그인/매매창 구분.

- login: 원본 번개 + L(파랑) (satpLogin)
- trade: 원본 번개 + M(빨강) (satpTrade)
- 공통 폴백: satpBolt

Windows 작업표시줄: AppUserModelID + QApplication/QWidget 아이콘.
QApplication 생성 전에 ID 설정, 생성 직후 앱 아이콘 적용.
"""

from __future__ import annotations

import sys
from pathlib import Path

from utils.appPaths import projectRoot, resourceRoot

_APP_ID_LOGIN = "SATP_AI.Login"
_APP_ID_TRADE = "SATP_AI.TradeDesk"

# kind → 파일 stem (ico/png)
_KIND_STEM = {
    "login": "satpLogin",
    "trade": "satpTrade",
    "bolt": "satpBolt",
}


def _assetBases():
    return (resourceRoot() / "assets", projectRoot() / "assets")


def iconPath(kind="trade"):
    """kind=login|trade|bolt — ico 우선, 없으면 png."""
    stem = _KIND_STEM.get(str(kind or "trade").strip().lower(), "satpTrade")
    names = [f"{stem}.ico", f"{stem}.png"]
    # 전용 파일 없으면 공통 번개
    if stem != "satpBolt":
        names.extend(["satpBolt.ico", "satpBolt.png"])
    for base in _assetBases():
        for name in names:
            p = base / name
            if p.is_file():
                return p
    return None


def boltIconPath():
    """하위 호환 — 기본 매매창 아이콘."""
    return iconPath("trade")


def boltPngPath():
    """표시용 PNG 우선 (로그인 로고용은 공통 번개 유지)."""
    for base in _assetBases():
        for name in ("satpBolt.png", "satpBolt.ico"):
            p = base / name
            if p.is_file():
                return p
    return None


def setAppUserModelId(appId=None):
    """
    Windows 작업표시줄이 python.exe 아이콘으로 묶이지 않게 프로세스 ID 지정.
    QApplication / 창 생성보다 먼저 호출해야 한다.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        aid = str(appId or _APP_ID_TRADE).strip() or _APP_ID_TRADE
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aid)
        return True
    except Exception:
        return False


def kindQIcon(kind="trade"):
    """QIcon 인스턴스 또는 None."""
    path = iconPath(kind)
    if not path:
        return None
    try:
        from PySide6.QtGui import QIcon

        return QIcon(str(path))
    except Exception:
        return None


def boltQIcon():
    return kindQIcon("trade")


def applyAppIcon(app, appId=None, kind=None):
    """
    QApplication 에 기본 창/작업표시줄 아이콘 적용.
    kind 미지정 시 appId 로 login/trade 추정.
    """
    if appId:
        setAppUserModelId(appId)
    useKind = kind
    if not useKind:
        aid = str(appId or "").lower()
        useKind = "login" if "login" in aid else "trade"
    ico = kindQIcon(useKind)
    if ico is None or app is None:
        return False
    try:
        app.setWindowIcon(ico)
        return True
    except Exception:
        return False


def applyWindowIcon(widget, kind="trade"):
    """QWidget/QMainWindow 타이틀바·작업표시줄 아이콘. kind=login|trade."""
    ico = kindQIcon(kind)
    if ico is None or widget is None:
        return False
    try:
        widget.setWindowIcon(ico)
        return True
    except Exception:
        return False


def makeBoltLogoLabel(parent=None, heightPx=114, widthPx=87):
    """구 SATP_UPDATE pictureBox(87×114) 규격의 상단 중앙 번개 이미지."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel

    h = int(heightPx)
    w = int(widthPx) if widthPx else 0
    lbl = QLabel(parent)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    path = boltPngPath()
    if path:
        pm = QPixmap(str(path))
        if not pm.isNull():
            if w > 0:
                scaled = pm.scaled(
                    w,
                    h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                scaled = pm.scaledToHeight(
                    h, Qt.TransformationMode.SmoothTransformation
                )
            lbl.setPixmap(scaled)
            lbl.setFixedSize(w or scaled.width(), h)
            return lbl
    lbl.setText("⚡")
    lbl.setStyleSheet("font-size: 64px; color: #FFD600;")
    lbl.setFixedSize(w or 87, h)
    return lbl
