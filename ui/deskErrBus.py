"""
매매창 오류 탭 버스.
워커/termLog 훅에서 Signal 로 GUI 스레드에 한 줄만 넘긴다.
콘솔 재출력은 하지 않는다 — termLog 가 이미 찍는다.
"""

from datetime import datetime

from PySide6.QtCore import QObject, Signal

_bus = None


class DeskErrBus(QObject):
    """오류 한 줄."""

    logged = Signal(str)


def initDeskErrBus(parent=None):
    """매매창 생성 시 1회. parent=창 이어야 GUI 스레드에 붙는다."""
    global _bus
    if _bus is None:
        _bus = DeskErrBus(parent)
    return _bus


def getDeskErrBus():
    return _bus


def pushDeskErr(text):
    """오류 탭용 한 줄. 시각만 앞에 붙인다."""
    raw = str(text or "").strip()
    if not raw:
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    line = raw if raw.startswith(stamp) else f"{stamp} {raw}"
    bus = _bus
    if bus is not None:
        bus.logged.emit(line)


def clearDeskErrBus():
    global _bus
    _bus = None
