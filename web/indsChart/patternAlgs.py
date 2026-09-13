"""
종목 일봉 차트 패턴 탐지.

- 쌍바닥(더블 바텀) / W
- 박스권(횡보 고·저 채널)

차트 annotation 용 좌표를 반환한다.
"""

from __future__ import annotations


def _ohlc(points):
    highs = [float(p.get("high") or p["close"]) for p in points]
    lows = [float(p.get("low") or p["close"]) for p in points]
    closes = [float(p["close"]) for p in points]
    return highs, lows, closes


def _swingLows(lows, left, right):
    """국소 저점 인덱스 목록 (오름차순)."""
    n = len(lows)
    out = []
    if n < left + right + 1:
        return out
    for i in range(left, n - right):
        w = lows[i - left : i + right + 1]
        if lows[i] <= min(w) and lows[i] > 0:
            out.append(i)
    return out


def _maxHighBetween(highs, i, j):
    """i~j 사이(끝 제외 구간 포함) 최고가 인덱스·값. i < j."""
    if j <= i + 1:
        return None, None
    # 저점 사이 (i, j) 내부
    lo = i + 1
    hi = j - 1
    if lo > hi:
        return None, None
    k = max(range(lo, hi + 1), key=lambda t: highs[t])
    return k, highs[k]


def detectDblBottom(
    points,
    left=5,
    right=5,
    tolPct=0.035,
    minBouncePct=0.035,
    minGap=8,
    maxGap=120,
    maxPatterns=6,
):
    """
    쌍바닥/W 패턴 목록.
    각 패턴:
      {
        id, status: confirmed|forming,
        l1: {i, date, price}, peak: {...}, l2: {...},
        breakPt: {i, date, price}|None,
        neckline, score, label
      }
    """
    n = len(points or [])
    if n < left + right + minGap + 5:
        return []

    highs, lows, closes = _ohlc(points)
    swingL = _swingLows(lows, left, right)
    if len(swingL) < 2:
        return []

    cands = []
    for a in range(len(swingL)):
        for b in range(a + 1, len(swingL)):
            i1 = swingL[a]
            i2 = swingL[b]
            gap = i2 - i1
            if gap < minGap or gap > maxGap:
                continue
            p1 = lows[i1]
            p2 = lows[i2]
            if p1 <= 0 or p2 <= 0:
                continue
            avgL = (p1 + p2) / 2.0
            if avgL <= 0:
                continue
            # 두 저점 유사도 (허용 오차 %)
            if abs(p1 - p2) / avgL > tolPct:
                continue
            # 너무 깊은 재하락(2차 저점 과다 이탈)은 W로 보기 어려움 → 여유 1.5배
            if p2 < p1 * (1.0 - tolPct * 1.5):
                continue

            pkI, pkP = _maxHighBetween(highs, i1, i2)
            if pkI is None or pkP is None:
                continue
            # 중간 반등 최소 폭
            base = min(p1, p2)
            if base <= 0 or (pkP - base) / base < minBouncePct:
                continue
            # 넥라인이 저점과 붙으면 무의미
            if pkP <= max(p1, p2) * 1.01:
                continue

            neck = float(pkP)
            # 넥라인 돌파(확인): L2 이후 종가 > 넥라인
            brI = None
            for k in range(i2 + 1, n):
                if closes[k] > neck:
                    brI = k
                    break

            # 점수: 저점 유사 + 반등폭 + 최근성 + 확인 가점
            sim = 1.0 - (abs(p1 - p2) / avgL) / max(tolPct, 1e-6)
            bounce = min(1.5, (pkP - base) / base / max(minBouncePct, 1e-6))
            recency = (i2 + 1) / float(n)
            confBonus = 0.35 if brI is not None else 0.0
            score = sim * 0.35 + bounce * 0.25 + recency * 0.25 + confBonus

            status = "confirmed" if brI is not None else "forming"
            label = "쌍바닥 완성" if status == "confirmed" else "쌍바닥 진행"

            def _pt(idx, price):
                return {
                    "i": int(idx),
                    "date": points[idx]["date"],
                    "dt": points[idx].get("dt") or "",
                    "price": round(float(price), 2),
                }

            pat = {
                "status": status,
                "label": label,
                "l1": _pt(i1, p1),
                "peak": _pt(pkI, pkP),
                "l2": _pt(i2, p2),
                "breakPt": _pt(brI, closes[brI]) if brI is not None else None,
                "neckline": round(neck, 2),
                "score": round(score, 4),
                "tolPct": tolPct,
                "gapBars": gap,
            }
            cands.append(pat)

    if not cands:
        return []

    # 점수 순 정렬 후 겹치는 L2 구간 정리 (높은 점수 우선)
    cands.sort(key=lambda x: x["score"], reverse=True)
    picked = []
    used = []  # (i1, i2) 구간

    def _overlap(a1, a2, b1, b2):
        return not (a2 < b1 or b2 < a1)

    for c in cands:
        i1 = c["l1"]["i"]
        i2 = c["l2"]["i"]
        if any(_overlap(i1, i2, u[0], u[1]) for u in used):
            # 동일 저점 재사용 과다 방지 — 완전 포함/높은 겹침만 스킵
            skip = False
            for u0, u1 in used:
                inter = max(0, min(i2, u1) - max(i1, u0))
                span = max(i2, u1) - min(i1, u0) + 1
                if span > 0 and inter / span > 0.55:
                    skip = True
                    break
            if skip:
                continue
        used.append((i1, i2))
        picked.append(c)
        if len(picked) >= maxPatterns:
            break

    # 시간순 표시
    picked.sort(key=lambda x: x["l1"]["i"])
    for idx, p in enumerate(picked):
        p["id"] = f"db{idx + 1}"
    return picked


def buildDblBottomPatterns(points):
    """쌍바닥 API용 래퍼."""
    return detectDblBottom(points)


def detectBoxRange(
    points,
    minLen=12,
    maxLen=90,
    maxRangePct=0.18,
    step=2,
    minTouches=2,
    breakBufPct=0.005,
    maxPatterns=5,
):
    """
    박스권: 구간 내 고-저 폭이 제한 + 상·하단 근처 터치 반복.
    반환: {
      id, status: active|brokenUp|brokenDn,
      start/end: {i,date,price}, top, bottom, widthPct,
      breakPt, score, label, kind: box
    }
    """
    n = len(points or [])
    if n < minLen + 2:
        return []

    highs, lows, closes = _ohlc(points)
    cands = []

    # 종료일을 슬라이드하며 가능한 박스 길이 후보 평가
    for end in range(minLen - 1, n, step):
        best = None
        for length in range(minLen, min(maxLen, end + 1) + 1, step):
            start = end - length + 1
            if start < 0:
                continue
            wh = highs[start : end + 1]
            wl = lows[start : end + 1]
            top = max(wh)
            bot = min(wl)
            if bot <= 0 or top <= bot:
                continue
            mid = (top + bot) / 2.0
            widthPct = (top - bot) / mid
            # 너무 넓으면 추세, 너무 얇으면(1% 미만) 노이즈
            if widthPct > maxRangePct or widthPct < 0.012:
                continue

            # 상·하단 밴드(박스 폭의 22%) 터치 횟수
            band = max((top - bot) * 0.22, mid * 0.003)
            touchTop = touchBot = 0
            for i in range(start, end + 1):
                if highs[i] >= top - band:
                    touchTop += 1
                if lows[i] <= bot + band:
                    touchBot += 1
            if touchTop < minTouches or touchBot < minTouches:
                continue

            # 종가가 박스 안 비율 (횡보 성격)
            inCnt = 0
            for i in range(start, end + 1):
                c = closes[i]
                if bot * 0.997 <= c <= top * 1.003:
                    inCnt += 1
            inRatio = inCnt / float(length)
            if inRatio < 0.65:
                continue

            # 돌파 여부 (박스 종료 이후)
            brI = None
            status = "active"
            brDir = None
            buf = mid * breakBufPct
            for k in range(end + 1, n):
                if closes[k] > top + buf:
                    brI = k
                    status = "brokenUp"
                    brDir = "up"
                    break
                if closes[k] < bot - buf:
                    brI = k
                    status = "brokenDn"
                    brDir = "dn"
                    break

            # 점수: 길수록·폭 적당하고 터치 많고 최근일수록
            lenScore = min(1.2, length / 40.0)
            widthScore = 1.0 - (widthPct / maxRangePct)
            touchScore = min(1.5, (touchTop + touchBot) / 8.0)
            recency = (end + 1) / float(n)
            breakBonus = 0.15 if brI is not None else 0.05
            score = (
                lenScore * 0.25
                + widthScore * 0.25
                + touchScore * 0.25
                + recency * 0.2
                + breakBonus
            )

            if status == "active":
                label = "박스권 진행"
            elif status == "brokenUp":
                label = "박스 상단돌파"
            else:
                label = "박스 하단이탈"

            def _pt(idx, price):
                return {
                    "i": int(idx),
                    "date": points[idx]["date"],
                    "dt": points[idx].get("dt") or "",
                    "price": round(float(price), 2),
                }

            cand = {
                "kind": "box",
                "status": status,
                "label": label,
                "start": _pt(start, closes[start]),
                "end": _pt(end, closes[end]),
                "top": round(float(top), 2),
                "bottom": round(float(bot), 2),
                "widthPct": round(widthPct * 100, 2),
                "touchTop": touchTop,
                "touchBot": touchBot,
                "breakPt": _pt(brI, closes[brI]) if brI is not None else None,
                "breakDir": brDir,
                "score": round(score, 4),
                "bars": length,
            }
            if best is None or cand["score"] > best["score"]:
                best = cand
        if best:
            cands.append(best)

    if not cands:
        return []

    # 동일/포함 구간 정리: 점수 높은 것 우선
    cands.sort(key=lambda x: x["score"], reverse=True)
    picked = []
    used = []

    for c in cands:
        s, e = c["start"]["i"], c["end"]["i"]
        skip = False
        for u0, u1 in used:
            inter = max(0, min(e, u1) - max(s, u0) + 1)
            span = max(e, u1) - min(s, u0) + 1
            if span > 0 and inter / float(span) > 0.5:
                skip = True
                break
        if skip:
            continue
        used.append((s, e))
        picked.append(c)
        if len(picked) >= maxPatterns:
            break

    picked.sort(key=lambda x: x["start"]["i"])
    for idx, p in enumerate(picked):
        p["id"] = f"bx{idx + 1}"
    return picked


def buildBoxRangePatterns(points):
    """박스권 API용 래퍼."""
    return detectBoxRange(points)


def buildChartPatterns(points):
    """종목 차트에 쓰는 패턴 묶음."""
    return {
        "dblBottom": buildDblBottomPatterns(points),
        "boxRange": buildBoxRangePatterns(points),
    }
