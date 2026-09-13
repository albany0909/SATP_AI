"""
지수 차트 매수/매도 신호 알고리즘 레지스트리.

추가 방법:
  1) buildXxx(points, closes) → signals 리스트 작성
  2) SIG_ALGS 에 {id, name, desc, build} 등록
  3) 프론트 드롭다운은 /api/sigAlgs 자동 반영
"""

from __future__ import annotations


def _sma(closes, i, period):
    if i + 1 < period:
        return None
    return sum(closes[i - period + 1 : i + 1]) / float(period)


def _sig(points, i, side, label):
    return {
        "dt": points[i]["dt"],
        "date": points[i]["date"],
        "side": side,  # buy | sell
        "label": label,
    }


def buildNone(points, closes):
    """신호 없음."""
    return []


def buildSmaCross(points, closes, period=20):
    """종가 vs SMA(period) 골든/데드 크로스."""
    out = []
    p = int(period)
    if len(closes) < p + 1:
        return out
    for i in range(p, len(closes)):
        sma = _sma(closes, i, p)
        prevSma = _sma(closes, i - 1, p)
        if sma is None or prevSma is None:
            continue
        c, pc = closes[i], closes[i - 1]
        if pc <= prevSma and c > sma:
            out.append(_sig(points, i, "buy", f"SMA{p} 골든"))
        elif pc >= prevSma and c < sma:
            out.append(_sig(points, i, "sell", f"SMA{p} 데드"))
    return out


def buildSmaCross20(points, closes):
    return buildSmaCross(points, closes, 20)


def buildSmaCross50(points, closes):
    return buildSmaCross(points, closes, 50)


def buildSmaCross60(points, closes):
    return buildSmaCross(points, closes, 60)


def buildDualSmaCross(points, closes, fast=20, slow=60):
    """단기 SMA가 장기 SMA 상·하향 돌파."""
    out = []
    f, s = int(fast), int(slow)
    need = max(f, s) + 1
    if len(closes) < need:
        return out
    for i in range(need - 1, len(closes)):
        fa = _sma(closes, i, f)
        sa = _sma(closes, i, s)
        pf = _sma(closes, i - 1, f)
        ps = _sma(closes, i - 1, s)
        if fa is None or sa is None or pf is None or ps is None:
            continue
        if pf <= ps and fa > sa:
            out.append(_sig(points, i, "buy", f"SMA{f}/{s} 골든"))
        elif pf >= ps and fa < sa:
            out.append(_sig(points, i, "sell", f"SMA{f}/{s} 데드"))
    return out


def buildDualSma2060(points, closes):
    return buildDualSmaCross(points, closes, 20, 60)


def buildDualSma2050(points, closes):
    """장대양봉 2050 계열 참고 — 지수용 20/50 교차."""
    return buildDualSmaCross(points, closes, 20, 50)


def buildAlignMaBreakLike(points, closes):
    """
    정배열 이평 근사: SMA20>50>100 이고 종가가 SMA20 상향 돌파 → 매수
    종가가 SMA60 하향 돌파 → 매도
    """
    out = []
    n = len(closes)
    if n < 102:
        return out
    for i in range(100, n):
        s20 = _sma(closes, i, 20)
        s50 = _sma(closes, i, 50)
        s100 = _sma(closes, i, 100)
        s60 = _sma(closes, i, 60)
        ps20 = _sma(closes, i - 1, 20)
        ps60 = _sma(closes, i - 1, 60)
        if (
            s20 is None
            or s50 is None
            or s100 is None
            or s60 is None
            or ps20 is None
            or ps60 is None
        ):
            continue
        c, pc = closes[i], closes[i - 1]
        aligned = s20 > s50 > s100
        if aligned and pc <= ps20 and c > s20:
            out.append(_sig(points, i, "buy", "정배열·20돌파"))
        if pc >= ps60 and c < s60:
            out.append(_sig(points, i, "sell", "60선 이탈"))
    return out


def buildDonchianBreak(points, closes, lookback=20):
    """N일 고가 돌파 매수 / 저가 이탈 매도 (종가 기준)."""
    out = []
    lb = int(lookback)
    if len(closes) < lb + 1:
        return out
    highs = [float(p.get("high") or p["close"]) for p in points]
    lows = [float(p.get("low") or p["close"]) for p in points]
    for i in range(lb, len(closes)):
        prevHigh = max(highs[i - lb : i])
        prevLow = min(lows[i - lb : i])
        c = closes[i]
        if c > prevHigh:
            out.append(_sig(points, i, "buy", f"Donchian{lb} 상단돌파"))
        elif c < prevLow:
            out.append(_sig(points, i, "sell", f"Donchian{lb} 하단이탈"))
    return out


def buildDonchian20(points, closes):
    return buildDonchianBreak(points, closes, 20)


# id → 알고리즘 메타 (순서 = UI 표시 순서)
SIG_ALGS = [
    {
        "id": "none",
        "name": "신호 없음",
        "desc": "수직선 신호 생성 안 함",
        "build": buildNone,
    },
    {
        "id": "smaCross20",
        "name": "SMA20 골든/데드",
        "desc": "종가와 20일 단순이평 교차",
        "build": buildSmaCross20,
    },
    {
        "id": "smaCross50",
        "name": "SMA50 골든/데드",
        "desc": "종가와 50일 단순이평 교차",
        "build": buildSmaCross50,
    },
    {
        "id": "smaCross60",
        "name": "SMA60 골든/데드",
        "desc": "종가와 60일 단순이평 교차",
        "build": buildSmaCross60,
    },
    {
        "id": "dualSma2060",
        "name": "SMA20/60 교차",
        "desc": "단기·장기 이평 골든/데드",
        "build": buildDualSma2060,
    },
    {
        "id": "dualSma2050",
        "name": "SMA20/50 교차 (2050 참고)",
        "desc": "장대양봉 2050 이평 계열 참고",
        "build": buildDualSma2050,
    },
    {
        "id": "alignMaBreak",
        "name": "정배열 20돌파/60이탈",
        "desc": "정배열 이평선 돌파매매 근사",
        "build": buildAlignMaBreakLike,
    },
    {
        "id": "donchian20",
        "name": "Donchian 20 돌파",
        "desc": "20일 고점 돌파·저점 이탈",
        "build": buildDonchian20,
    },
]

_SIG_BY_ID = {a["id"]: a for a in SIG_ALGS}
DEFAULT_SIG_ALG = "smaCross20"


def listSigAlgs():
    """API 노출용 목록 (함수 제외)."""
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "desc": a.get("desc") or "",
        }
        for a in SIG_ALGS
    ]


def resolveSigAlgId(raw):
    key = str(raw or "").strip()
    if not key or key not in _SIG_BY_ID:
        return DEFAULT_SIG_ALG
    return key


def buildSignals(sigAlgId, points, closes):
    """
    신호 생성.
    반환: {sigAlgId, sigAlgName, signals, lastSignalDate}
    """
    sid = resolveSigAlgId(sigAlgId)
    meta = _SIG_BY_ID[sid]
    signals = meta["build"](points, closes) or []
    last = signals[-1]["date"] if signals else None
    return {
        "sigAlgId": sid,
        "sigAlgName": meta["name"],
        "signals": signals,
        "lastSignalDate": last,
    }
