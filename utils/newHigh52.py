"""52주 신고가 최초봉. 차트 표시와 백테스트가 같은 정의를 쓴다.

주문·DB·설정을 쓰지 않는다. 입력 봉만 본다.
"""

from __future__ import annotations

import json

NEW_HIGH_DAY_LOOKBACK = 250
NEW_HIGH_WEEK_LOOKBACK = 52
NEW_HIGH_GAP_BARS = 20


def newHighLookback(barTp):
    """일봉 250, 주봉 52. 월봉은 None 이라 표시하지 않는다."""
    kind = str(barTp or "").lower()
    if kind in ("day", "d", "일"):
        return NEW_HIGH_DAY_LOOKBACK
    if kind in ("week", "w", "주"):
        return NEW_HIGH_WEEK_LOOKBACK
    return None


def _price(bar, basis):
    """양수 가격. 고가가 없거나 0이면 종가를 쓴다."""
    if isinstance(bar, (int, float)) and not isinstance(bar, bool):
        val = float(bar)
        return val if val > 0 else None
    if not isinstance(bar, dict):
        return None
    key = "high" if basis == "high" else "close"
    raw = bar.get(key)
    if raw is None or raw == "":
        raw = bar.get("close")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val > 0:
        return val
    if key == "high":
        try:
            close = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            return None
        return close if close > 0 else None
    return None


def firstNewHighIndices(bars, lookback=NEW_HIGH_DAY_LOOKBACK, gapBars=NEW_HIGH_GAP_BARS, basis="close"):
    """최초 신고가 봉의 인덱스. 오래된 봉이 앞이다.

    bars: 숫자 목록이면 종가. dict 이면 close 또는 high.
    lookback: 그 봉을 뺀 직전 봉 수. 이 개수보다 이력이 짧으면 표시하지 않는다.
    gapBars: 직전 신고가 봉과의 사이에 신고가가 아닌 봉이 이 개수 이상일 때만 새 최초봉.
             그 사이가 더 짧으면 같은 신고가 구간이라 첫 봉만 남긴다. 기본 20.
    basis: close(기본) 또는 high.
    판정은 bars[i] 와 그 앞 봉만 쓴다.
    """
    try:
        window = int(lookback)
    except (TypeError, ValueError):
        window = NEW_HIGH_DAY_LOOKBACK
    try:
        gap = int(gapBars)
    except (TypeError, ValueError):
        gap = NEW_HIGH_GAP_BARS
    if window < 1:
        return []
    if gap < 0:
        gap = 0
    mode = "high" if str(basis or "").lower() == "high" else "close"
    prices = [_price(bar, mode) for bar in (bars or [])]
    out = []
    lastHigh = None
    for i, px in enumerate(prices):
        if i < window or px is None:
            continue
        prev = prices[i - window:i]
        if any(p is None for p in prev):
            continue
        if px <= max(prev):
            continue
        quiet = (i - lastHigh - 1) if lastHigh is not None else gap
        if lastHigh is None or quiet >= gap:
            out.append(i)
        lastHigh = i
    return out


def loadNewHigh52Options(settings=None):
    """chartNewHigh52.gapBars / basis. 파일이 없어도 기본값만 돌려주고 쓰지 않는다."""
    data = settings
    if data is None:
        data = _readSettings()
    block = {}
    if isinstance(data, dict) and isinstance(data.get("chartNewHigh52"), dict):
        block = data["chartNewHigh52"]
    try:
        gap = int(block.get("gapBars", NEW_HIGH_GAP_BARS))
    except (TypeError, ValueError):
        gap = NEW_HIGH_GAP_BARS
    if gap < 0:
        gap = 0
    basis = str(block.get("basis") or "close").lower()
    if basis not in ("close", "high"):
        basis = "close"
    return {"gapBars": gap, "basis": basis}


def _readSettings():
    try:
        from config.settingsStore import _SETTINGS_PATH

        path = _SETTINGS_PATH
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
