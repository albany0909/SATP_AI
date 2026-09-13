"""
DART 주요계정을 연간 열 형태로 정리 (연결 우선).
네이버 표와 병합 시 실적(E 아님) 열을 채우는 데 사용.
"""

from __future__ import annotations

from datetime import datetime

from research.dartCorpCode import resolveCorpCode
from research.dartFinFetch import fetchFnlttSinglAcnt
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 네이버 행 이름 ↔ DART 계정 매칭 키워드 (포함 매칭)
# DART 단일계정 표에 있는 항목만 — 비율·추정·FCF 등은 네이버 유지
_ROW_MAP = (
    ("매출액", ("매출액",)),
    ("영업이익", ("영업이익",)),
    ("영업이익(발표기준)", ("영업이익",)),
    ("세전계속사업이익", ("법인세차감전", "세전계속", "법인세비용차감전순이익")),
    ("당기순이익", ("당기순이익",)),
    ("당기순이익(지배)", ("당기순이익(지배", "지배주주순이익")),
    ("당기순이익(비지배)", ("당기순이익(비지배", "비지배주주순이익")),
    ("자산총계", ("자산총계",)),
    ("부채총계", ("부채총계",)),
    ("자본총계", ("자본총계",)),
    ("자본총계(지배)", ("자본총계(지배", "지배주주지분")),
    ("자본총계(비지배)", ("자본총계(비지배", "비지배주주지분")),
    ("자본금", ("자본금",)),
)


def _pickAmt(items, keywords):
    """연결(CFS) 우선 exact/포함 매칭 → thstrm_amount."""
    best = None
    bestScore = -1
    for it in items or []:
        nm = (it.get("accountNm") or "").replace(" ", "")
        fs = (it.get("fsDiv") or "").upper()
        for kw in keywords:
            k = kw.replace(" ", "")
            if k not in nm and nm != k:
                continue
            score = 0
            if nm == k:
                score += 5
            elif nm.startswith(k):
                score += 3
            else:
                score += 1
            if fs == "CFS":
                score += 2
            if score > bestScore:
                bestScore = score
                best = it
    if not best:
        return None
    raw = str(best.get("thstrmAmount") or "").replace(",", "").strip()
    if not raw or raw == "-":
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def _yearFromLabel(label):
    """2023.12 → 2023"""
    m = str(label or "")
    if len(m) >= 4 and m[:4].isdigit():
        return int(m[:4])
    return None


def fetchDartFinByYears(stkCd, years):
    """
    연도별 주요 계정(원 단위) dict 목록.
    years: [2023, 2024, ...]
    반환: {ok, byYear: {2023: {매출액: n, ...}}, corpCode}
    """
    code = normStkCd(stkCd)
    corp = resolveCorpCode(code)
    if not corp.get("ok"):
        return {"ok": False, "errMsg": corp.get("errMsg"), "byYear": {}}

    byYear = {}
    for y in years:
        y = int(y)
        # 사업 → 반기 순 시도
        items = []
        used = None
        for rc in ("11011", "11012", "11014", "11013"):
            r = fetchFnlttSinglAcnt(corp["corpCode"], y, rc)
            if r.get("ok") and r.get("items"):
                items = r["items"]
                used = rc
                break
        if not items:
            continue
        acc = {}
        for rowNm, kws in _ROW_MAP:
            v = _pickAmt(items, kws)
            if v is not None:
                acc[rowNm] = v
                # 들여쓰기 변형 키 (당기순이익(지배) 등)
                acc[rowNm.replace(" ", "")] = v
        if acc:
            byYear[y] = {"accounts": acc, "reprtCode": used}
        termLog(f"[dartFinMatrix] {code} {y} accounts={len(acc)}")

    return {
        "ok": bool(byYear),
        "stkCd": code,
        "corpCode": corp.get("corpCode"),
        "corpNm": corp.get("corpNm"),
        "byYear": byYear,
    }


def defaultTargetYears():
    y = datetime.now().year
    return [y - 2, y - 1, y, y + 1]
