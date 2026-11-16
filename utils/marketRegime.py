"""KODEX200 일봉으로 시장 국면을 판정한다. 매매 주문은 하지 않는다.

장중에는 전일까지 확정된 판정을 보여주고, 현재가는 참고로만 붙인다.
판정은 그 봉과 그 앞 봉만 사용한다.
"""

from __future__ import annotations

import json
import math

from strategies.indicators import calcSmaSeries

ADX_PERIOD = 14
ADX_MIN = 20.0
SMA_SLOPE_BARS = 5
VOL_BARS = 20
SYMBOL = "069500"


def loadMarketRegimeOptions(settings=None):
    """chartMarketRegime 키. 파일이 없어도 기본값만 돌려주고 쓰지 않는다."""
    data = settings if settings is not None else _readSettings()
    block = {}
    if isinstance(data, dict) and isinstance(data.get("chartMarketRegime"), dict):
        block = data["chartMarketRegime"]
    return {
        "symbol": str(block.get("symbol") or SYMBOL),
        "smaSlopeBars": _posInt(block.get("smaSlopeBars"), SMA_SLOPE_BARS),
        "adxPeriod": _posInt(block.get("adxPeriod"), ADX_PERIOD),
        "adxMin": _posFloat(block.get("adxMin"), ADX_MIN),
        "volBars": _posInt(block.get("volBars"), VOL_BARS),
        "autoBackfill": _asBool(block.get("autoBackfill"), False),
    }


def _posInt(raw, default):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _asBool(raw, default):
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "true", "y", "yes", "on"):
        return True
    if text in ("0", "false", "n", "no", "off"):
        return False
    return default


def _posFloat(raw, default):
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _readSettings():
    try:
        from config.settingsStore import _SETTINGS_PATH

        if not _SETTINGS_PATH.is_file():
            return {}
        return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _f(bar, key):
    if not isinstance(bar, dict):
        return None
    raw = bar.get(key)
    if raw is None or raw == "":
        raw = bar.get("close") if key != "close" else None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def wilderAdx(bars, period=ADX_PERIOD):
    """Wilder ADX/+DI/-DI. 각 인덱스는 그 봉까지의 값만 쓴다. 준비 전은 None."""
    n = len(bars or [])
    out = [None] * n
    p = _posInt(period, ADX_PERIOD)
    if n <= p * 2:
        return out
    trs = [0.0] * n
    plus = [0.0] * n
    minus = [0.0] * n
    for i in range(1, n):
        high = _f(bars[i], "high")
        low = _f(bars[i], "low")
        close = _f(bars[i], "close")
        prevClose = _f(bars[i - 1], "close")
        prevHigh = _f(bars[i - 1], "high")
        prevLow = _f(bars[i - 1], "low")
        if None in (high, low, close, prevClose, prevHigh, prevLow):
            continue
        up = high - prevHigh
        down = prevLow - low
        trs[i] = max(high - low, abs(high - prevClose), abs(low - prevClose))
        plus[i] = up if up > down and up > 0 else 0.0
        minus[i] = down if down > up and down > 0 else 0.0
    atr = sum(trs[1:p + 1]) / p
    sp = sum(plus[1:p + 1]) / p
    sm = sum(minus[1:p + 1]) / p
    dxs = [None] * n

    def _dx(atrNow, spNow, smNow, i):
        if atrNow <= 0:
            dxs[i] = (0.0, 0.0, 0.0)
            return
        pdi = 100.0 * spNow / atrNow
        mdi = 100.0 * smNow / atrNow
        denom = pdi + mdi
        dx = 0.0 if denom <= 0 else 100.0 * abs(pdi - mdi) / denom
        dxs[i] = (dx, pdi, mdi)

    _dx(atr, sp, sm, p)
    for i in range(p + 1, n):
        atr = (atr * (p - 1) + trs[i]) / p
        sp = (sp * (p - 1) + plus[i]) / p
        sm = (sm * (p - 1) + minus[i]) / p
        _dx(atr, sp, sm, i)
    end = p + p - 1
    if end >= n or any(dxs[i] is None for i in range(p, end + 1)):
        return out
    seed = sum(dxs[i][0] for i in range(p, end + 1)) / p
    out[end] = {"adx": seed, "plusDi": dxs[end][1], "minusDi": dxs[end][2]}
    prev = seed
    for i in range(end + 1, n):
        if dxs[i] is None:
            continue
        prev = (prev * (p - 1) + dxs[i][0]) / p
        out[i] = {"adx": prev, "plusDi": dxs[i][1], "minusDi": dxs[i][2]}
    return out


def annVol(closes, bars=VOL_BARS):
    """최근 bars개 수익률의 표본표준편차 × sqrt(252). 부족하면 None."""
    n = _posInt(bars, VOL_BARS)
    if len(closes or []) < n + 1:
        return None
    seg = [float(x) for x in closes[-(n + 1):]]
    rets = []
    for i in range(1, len(seg)):
        if seg[i - 1] <= 0:
            return None
        rets.append(seg[i] / seg[i - 1] - 1.0)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def drawdown(bars):
    """고점(고가, 없으면 종가) 대비 마지막 종가 낙폭. 미래 봉은 인자로 주지 않는다."""
    peak = None
    peakDt = ""
    lastClose = None
    for bar in bars or []:
        close = _f(bar, "close")
        high = _f(bar, "high")
        mark = high if high is not None else close
        if close is None or mark is None:
            continue
        lastClose = close
        if peak is None or mark >= peak:
            peak = mark
            peakDt = str(bar.get("tradeDt") or "")[:8]
    if peak is None or lastClose is None or peak <= 0:
        return None
    return {
        "peak": peak,
        "peakDt": peakDt,
        "ddPct": (lastClose / peak - 1.0) * 100.0,
    }


def reg01Side(monthEndCloses):
    """월말 종가 10개가 있으면 ON/OFF. 마지막 종가가 10개 평균보다 크면 ON."""
    rows = [float(x) for x in (monthEndCloses or []) if x is not None]
    if len(rows) < 10:
        return None
    last10 = rows[-10:]
    avg = sum(last10) / 10.0
    return "ON" if last10[-1] > avg else "OFF"


def classifySnapshot(snap, adxMin=ADX_MIN):
    """지표가 이미 계산된 한 봉의 국면.

    우선순위:
    1. 필요한 값이 없으면 판정불가.
    2. ADX < adxMin 이면 횡보장.
       종가가 200일선 아래이고 60일선이 하락해도 횡보다.
       -DI가 +DI보다 커도 ADX가 기준 미만이면 하락장이 아니다.
    3. 종가 > 200일선, (20>60>120 또는 60일선 상승), +DI > -DI 이면 상승장.
    4. 종가 < 200일선, (60일선 하락 또는 -DI > +DI) 이면 하락장.
       여기 도달한 경우는 이미 ADX >= adxMin 이다.
    5. 나머지는 횡보장.
    """
    need = (
        "close", "sma200", "sma20", "sma60", "sma120", "sma60Prev",
        "adx", "plusDi", "minusDi",
    )
    if any(snap.get(k) is None for k in need):
        return {"regime": "판정불가", "reason": "데이터 부족", "ok": False}
    close = float(snap["close"])
    sma200 = float(snap["sma200"])
    sma20 = float(snap["sma20"])
    sma60 = float(snap["sma60"])
    sma120 = float(snap["sma120"])
    prev60 = float(snap["sma60Prev"])
    adx = float(snap["adx"])
    plus = float(snap["plusDi"])
    minus = float(snap["minusDi"])
    floor = float(adxMin)
    rising = sma60 > prev60
    falling = sma60 < prev60
    aligned = sma20 > sma60 > sma120
    dist = (close / sma200 - 1.0) * 100.0 if sma200 else None
    base = {
        "ok": True,
        "close": close,
        "sma200": sma200,
        "sma20": sma20,
        "sma60": sma60,
        "sma120": sma120,
        "adx": adx,
        "plusDi": plus,
        "minusDi": minus,
        "dist200Pct": dist,
        "reg01": snap.get("reg01"),
        "vol20Ann": snap.get("vol20Ann"),
        "ddPct": snap.get("ddPct"),
        "peak": snap.get("peak"),
        "peakDt": snap.get("peakDt"),
    }
    if adx < floor:
        base.update({
            "regime": "횡보장",
            "reason": (
                f"ADX {adx:.1f}<{floor:g}. 종가와 60일선이 하락 쪽이어도 횡보. "
                "DI만으로 하락장을 만들지 않음"
            ),
        })
        return base
    if close > sma200 and (aligned or rising) and plus > minus:
        why = "20>60>120" if aligned else "60일선 상승"
        base.update({
            "regime": "상승장",
            "reason": f"종가>200일선 · {why} · +DI>-DI · ADX {adx:.1f}",
        })
        return base
    if close < sma200 and (falling or minus > plus):
        parts = ["종가<200일선"]
        if falling:
            parts.append("60일선 하락")
        if minus > plus:
            parts.append("-DI>+DI")
        parts.append(f"ADX {adx:.1f}")
        base.update({"regime": "하락장", "reason": " · ".join(parts)})
        return base
    base.update({"regime": "횡보장", "reason": f"상승·하락 조건 아님 · ADX {adx:.1f}"})
    return base


def judgeBars(bars, slopeBars=SMA_SLOPE_BARS, adxPeriod=ADX_PERIOD, adxMin=ADX_MIN, reg01=None, volBars=VOL_BARS):
    """마지막 봉까지의 국면. bars 보다 앞선 값은 만들지 않는다."""
    rows = list(bars or [])
    closes = []
    for bar in rows:
        px = _f(bar, "close")
        closes.append(px if px is not None else 0.0)
    sma200 = calcSmaSeries(closes, 200)
    sma20 = calcSmaSeries(closes, 20)
    sma60 = calcSmaSeries(closes, 60)
    sma120 = calcSmaSeries(closes, 120)
    adx = wilderAdx(rows, adxPeriod)
    if not rows:
        return {"regime": "판정불가", "reason": "데이터 부족", "ok": False}
    i = len(rows) - 1
    step = _posInt(slopeBars, SMA_SLOPE_BARS)
    prevI = i - step
    adxRow = adx[i] if i < len(adx) else None
    snap = {
        "close": _f(rows[i], "close"),
        "sma200": sma200[i] if i < len(sma200) else None,
        "sma20": sma20[i] if i < len(sma20) else None,
        "sma60": sma60[i] if i < len(sma60) else None,
        "sma120": sma120[i] if i < len(sma120) else None,
        "sma60Prev": sma60[prevI] if prevI >= 0 and prevI < len(sma60) else None,
        "adx": None if not adxRow else adxRow["adx"],
        "plusDi": None if not adxRow else adxRow["plusDi"],
        "minusDi": None if not adxRow else adxRow["minusDi"],
        "reg01": reg01,
        "vol20Ann": annVol(closes, volBars),
    }
    dd = drawdown(rows)
    if dd:
        snap["ddPct"] = dd["ddPct"]
        snap["peak"] = dd["peak"]
        snap["peakDt"] = dd["peakDt"]
    got = classifySnapshot(snap, adxMin=adxMin)
    got["tradeDt"] = str(rows[i].get("tradeDt") or "")[:8]
    return got


def presentRegime(bars, today, nowHm, closeHm="153000", livePx=None, **kw):
    """장중이면 전일 확정 판정. 현재가는 livePx 참고값으로만 붙인다."""
    rows = list(bars or [])
    day = str(today or "")[:8]
    hm = str(nowHm or "000000").zfill(6)[-6:]
    close = str(closeHm or "153000").zfill(6)[-6:]
    intraday = bool(rows) and str(rows[-1].get("tradeDt") or "")[:8] == day and hm < close
    confirmed = rows[:-1] if intraday else rows
    got = judgeBars(confirmed, **kw)
    got["intraday"] = intraday
    got["livePx"] = livePx if intraday else None
    got["asOf"] = str((confirmed[-1].get("tradeDt") if confirmed else "") or "")[:8]
    return got


def judgmentLine(regime, ymd, reason):
    return f"[marketRegime] 판정 {regime} {ymd} {reason}"


def unjudgedLine(symbol, ymd, reason, barCount):
    """봉이 부족해 판정이 안 될 때. 저장은 하지 않는다."""
    why = reason or "데이터 부족"
    return f"[marketRegime] 판정 판정불가 {ymd} {why} ({symbol} 봉 {int(barCount or 0)}개)"


def changeLine(prev, cur, ymd, reason):
    return f"[marketRegime] {prev}→{cur} {ymd} {reason}"


def badgeColors(regime):
    """상승 빨강, 하락 파랑, 그 외 회색."""
    if regime == "상승장":
        return "#C62828", "#FFFFFF"
    if regime == "하락장":
        return "#1565C0", "#FFFFFF"
    return "#757575", "#FFFFFF"


def detailLine(view):
    """배지 옆 한 줄."""
    if not view or not view.get("ok"):
        return "데이터 부족"
    bits = [str(view.get("reason") or "")]
    reg = view.get("reg01")
    if reg in ("ON", "OFF"):
        bits.append(f"REG01 {reg}")
    if view.get("adx") is not None:
        bits.append(f"ADX {float(view['adx']):.1f} (+DI {float(view['plusDi']):.1f}/-DI {float(view['minusDi']):.1f})")
    if view.get("vol20Ann") is not None:
        bits.append(f"20일변동성 {float(view['vol20Ann']) * 100:.0f}%")
    if view.get("ddPct") is not None:
        peak = view.get("peakDt") or ""
        bits.append(f"고점대비 {float(view['ddPct']):+.0f}%{(' ' + peak) if peak else ''}")
    if view.get("dist200Pct") is not None:
        bits.append(f"200일선 {float(view['dist200Pct']):+.1f}%")
    if view.get("intraday") and view.get("livePx"):
        bits.append(f"장중참고 {float(view['livePx']):,.0f}")
    when = view.get("changedOn") or view.get("asOf") or ""
    if when:
        bits.append(f"변경 {when}")
    return " · ".join(bit for bit in bits if bit)
