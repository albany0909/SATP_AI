"""네이티브 창 제목 막대 색. 국면 계산은 하지 않는다.

하락장은 연파랑, 상승장은 연빨강, 횡보장은 연노랑, 판정불가는 시스템 기본색.
COLORREF 는 0x00BBGGRR. 판정불가는 DWMWA_COLOR_DEFAULT(0xFFFFFFFF).
"""

from __future__ import annotations

import sys

DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWM_COLOR_DEFAULT = 0xFFFFFFFF

_LIGHT_BLUE = "#BBDEFB"
_LIGHT_RED = "#FFCDD2"
_LIGHT_YELLOW = "#FFF59D"


def colorrefFromHex(hexRgb):
    """#RRGGBB 를 COLORREF(0x00BBGGRR)로."""
    text = str(hexRgb or "").strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"색 형식 오류: {hexRgb}")
    red = int(text[0:2], 16)
    green = int(text[2:4], 16)
    blue = int(text[4:6], 16)
    return (blue << 16) | (green << 8) | red


def titleBarColors(regime):
    """국면 이름만으로 제목 막대 색을 정한다. 창을 건드리지 않는다."""
    name = str(regime or "").strip() or "판정불가"
    table = {
        "하락장": _LIGHT_BLUE,
        "상승장": _LIGHT_RED,
        "횡보장": _LIGHT_YELLOW,
    }
    hexRgb = table.get(name)
    if hexRgb is None:
        return {
            "label": name,
            "caption": DWM_COLOR_DEFAULT,
            "text": DWM_COLOR_DEFAULT,
            "hex": "default",
            "textHex": "default",
            "default": True,
        }
    return {
        "label": name,
        "caption": colorrefFromHex(hexRgb),
        "text": 0x00000000,
        "hex": hexRgb,
        "textHex": "#000000",
        "default": False,
    }


def titleBarLogLine(ok, colors, hr=None, why=""):
    """성공·실패 한 줄. 시각 접두어는 termLog 가 붙인다."""
    label = (colors or {}).get("label") or "판정불가"
    if ok:
        hexRgb = (colors or {}).get("hex") or "default"
        textHex = (colors or {}).get("textHex") or "default"
        return f"[titleBar] 성공 {label} caption={hexRgb} text={textHex}"
    if hr is not None:
        return f"[titleBar] 실패 {label} hr=0x{int(hr) & 0xFFFFFFFF:08X} — 커스텀 타이틀바"
    extra = f" {why}" if why else ""
    return f"[titleBar] 실패 {label}{extra} — 커스텀 타이틀바"


def applyNativeTitleBar(hwnd, colors, setter=None):
    """DwmSetWindowAttribute. 실패해도 예외를 올리지 않고 (False, 로그)를 돌려준다."""
    shown = colors or titleBarColors("판정불가")
    try:
        if not hwnd:
            return False, titleBarLogLine(False, shown, why="hwnd 없음")
        fn = setter if setter is not None else loadDwmSetter()
        if fn is None:
            return False, titleBarLogLine(False, shown, why="DWM 없음")
        hrCap = int(fn(int(hwnd), DWMWA_CAPTION_COLOR, int(shown["caption"])))
        if hrCap != 0:
            return False, titleBarLogLine(False, shown, hr=hrCap)
        hrText = int(fn(int(hwnd), DWMWA_TEXT_COLOR, int(shown["text"])))
        if hrText != 0:
            return False, titleBarLogLine(False, shown, hr=hrText)
        return True, titleBarLogLine(True, shown)
    except Exception as err:
        return False, titleBarLogLine(False, shown, why=type(err).__name__)


def loadDwmSetter():
    """Windows dwmapi. 없거나 Win32 가 아니면 None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        lib = ctypes.WinDLL("dwmapi")
        fn = lib.DwmSetWindowAttribute
        fn.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        fn.restype = ctypes.c_long

        def _set(hwnd, attr, color):
            buf = ctypes.c_uint(int(color) & 0xFFFFFFFF)
            return int(
                fn(
                    ctypes.c_void_p(int(hwnd)),
                    ctypes.c_uint(int(attr)),
                    ctypes.byref(buf),
                    ctypes.sizeof(buf),
                )
            )

        return _set
    except Exception:
        return None
