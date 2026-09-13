"""
종목 일봉 지지/저항 레벨 알고리즘.

추가 방법:
  1) buildXxx(points) → levels 리스트
  2) SR_ALGS 등록
  3) /api/srAlgs 자동 반영
"""

from __future__ import annotations


def _ohlc(points):
    highs = [float(p.get("high") or p["close"]) for p in points]
    lows = [float(p.get("low") or p["close"]) for p in points]
    closes = [float(p["close"]) for p in points]
    return highs, lows, closes


def _clusterLevels(prices, clusterPct, maxLevels):
    """가격 리스트를 허용 밴드(%)로 묶고 터치 횟수 상위 반환."""
    if not prices:
        return []
    sortedP = sorted(float(x) for x in prices if x and x > 0)
    if not sortedP:
        return []
    clusters = []  # {sum, cnt, samples}
    for p in sortedP:
        placed = False
        for c in clusters:
            mid = c["sum"] / c["cnt"]
            band = max(mid * clusterPct, 1.0)
            if abs(p - mid) <= band:
                c["sum"] += p
                c["cnt"] += 1
                c["samples"].append(p)
                placed = True
                break
        if not placed:
            clusters.append({"sum": p, "cnt": 1, "samples": [p]})
    ranked = sorted(clusters, key=lambda c: c["cnt"], reverse=True)
    out = []
    for c in ranked[: max(1, int(maxLevels))]:
        out.append(
            {
                "price": round(c["sum"] / c["cnt"], 2),
                "touches": c["cnt"],
            }
        )
    return out


def _kindByLastClose(price, lastClose):
    if lastClose is None:
        return "neutral"
    if price < lastClose:
        return "support"
    if price > lastClose:
        return "resistance"
    return "neutral"


def buildNone(points):
    return []


def buildRangeHl(points, maxLevels=2):
    """조회 구간 고가·저가 수평선."""
    if not points:
        return []
    highs, lows, closes = _ohlc(points)
    last = closes[-1]
    hiI = max(range(len(highs)), key=lambda i: highs[i])
    loI = min(range(len(lows)), key=lambda i: lows[i])
    levels = [
        {
            "price": round(highs[hiI], 2),
            "kind": "resistance",
            "label": "구간고점",
            "strength": 3,
            "touches": 1,
            "fromDate": points[hiI]["date"],
            "toDate": points[-1]["date"],
        },
        {
            "price": round(lows[loI], 2),
            "kind": "support",
            "label": "구간저점",
            "strength": 3,
            "touches": 1,
            "fromDate": points[loI]["date"],
            "toDate": points[-1]["date"],
        },
    ]
    # 종가와 동일 가격이면 유지
    for lv in levels:
        if abs(lv["price"] - last) < 1e-9:
            lv["kind"] = _kindByLastClose(lv["price"] - 0.01, last)
    return levels[:maxLevels]


def buildSwingSr(points, left=3, right=3, clusterPct=0.015, maxLevels=8):
    """
    좌·우 국소 고/저 스윙 피벗 → 가격 군집 → 지지/저항.
    strength = min(5, touches)
    """
    n = len(points)
    if n < left + right + 1:
        return []
    highs, lows, closes = _ohlc(points)
    last = closes[-1]
    swingH = []
    swingL = []
    for i in range(left, n - right):
        wH = highs[i - left : i + right + 1]
        wL = lows[i - left : i + right + 1]
        if highs[i] >= max(wH) and highs[i] > 0:
            swingH.append(highs[i])
        if lows[i] <= min(wL) and lows[i] > 0:
            swingL.append(lows[i])

    # 고·저를 합쳐 군집 후 종가 기준으로 종류 부여
    allPiv = swingH + swingL
    clustered = _clusterLevels(allPiv, clusterPct=clusterPct, maxLevels=maxLevels * 2)
    levels = []
    for c in clustered:
        kind = _kindByLastClose(c["price"], last)
        if kind == "neutral":
            continue
        levels.append(
            {
                "price": c["price"],
                "kind": kind,
                "label": f"스윙{kind[:3]}",
                "strength": min(5, int(c["touches"])),
                "touches": int(c["touches"]),
                "fromDate": points[0]["date"],
                "toDate": points[-1]["date"],
            }
        )
    # 지지·저항 각각 균형 있게
    supports = [x for x in levels if x["kind"] == "support"]
    resists = [x for x in levels if x["kind"] == "resistance"]
    half = max(1, maxLevels // 2)
    supports = sorted(supports, key=lambda x: x["touches"], reverse=True)[:half]
    resists = sorted(resists, key=lambda x: x["touches"], reverse=True)[:half]
    merged = sorted(supports + resists, key=lambda x: x["price"])
    return merged[:maxLevels]


def buildDonchianSr(points, lookback=20, maxLevels=4):
    """최근 lookback 고/저를 포함, 주요 스윙 밴드 근사."""
    if not points:
        return []
    levels = buildSwingSr(points, left=2, right=2, clusterPct=0.012, maxLevels=maxLevels)
    # 최근 창 고·저 강조
    highs, lows, closes = _ohlc(points)
    lb = min(int(lookback), len(points))
    winH = max(highs[-lb:])
    winL = min(lows[-lb:])
    last = closes[-1]
    extra = [
        {
            "price": round(winH, 2),
            "kind": "resistance",
            "label": f"Donchian{lb} 상단",
            "strength": 4,
            "touches": 1,
            "fromDate": points[-lb]["date"],
            "toDate": points[-1]["date"],
        },
        {
            "price": round(winL, 2),
            "kind": "support",
            "label": f"Donchian{lb} 하단",
            "strength": 4,
            "touches": 1,
            "fromDate": points[-lb]["date"],
            "toDate": points[-1]["date"],
        },
    ]
    # 중복 가격 병합
    byP = {}
    for lv in levels + extra:
        key = round(lv["price"], 0)  # 원 단위 반올림 키
        prev = byP.get(key)
        if not prev or lv["strength"] >= prev["strength"]:
            byP[key] = lv
    out = sorted(byP.values(), key=lambda x: x["price"])
    return out[:maxLevels]


def buildSwingSrDefault(points):
    return buildSwingSr(points)


def buildDonchian20Sr(points):
    return buildDonchianSr(points, 20)


SR_ALGS = [
    {
        "id": "none",
        "name": "지지/저항 없음",
        "desc": "레벨 표시 안 함",
        "build": buildNone,
    },
    {
        "id": "swing",
        "name": "스윙 피벗 군집",
        "desc": "국소 고·저 스윙을 가격 밴드(%)로 묶어 지지·저항",
        "build": buildSwingSrDefault,
    },
    {
        "id": "rangeHl",
        "name": "구간 고점·저점",
        "desc": "조회 기간 최고가/최저가 수평선",
        "build": buildRangeHl,
    },
    {
        "id": "donchian20",
        "name": "Donchian20 + 스윙",
        "desc": "20일 채널 상·하단 + 스윙 레벨",
        "build": buildDonchian20Sr,
    },
]

_SR_BY_ID = {a["id"]: a for a in SR_ALGS}
DEFAULT_SR_ALG = "swing"


def listSrAlgs():
    return [
        {"id": a["id"], "name": a["name"], "desc": a.get("desc") or ""}
        for a in SR_ALGS
    ]


def resolveSrAlgId(raw):
    key = str(raw or "").strip()
    if not key or key not in _SR_BY_ID:
        return DEFAULT_SR_ALG
    return key


def buildSrLevels(srAlgId, points):
    """
    반환: {srAlgId, srAlgName, levels}
    levels: [{price, kind, label, strength, touches, fromDate?, toDate?}]
    """
    sid = resolveSrAlgId(srAlgId)
    meta = _SR_BY_ID[sid]
    levels = meta["build"](points) or []
    return {
        "srAlgId": sid,
        "srAlgName": meta["name"],
        "levels": levels,
    }
