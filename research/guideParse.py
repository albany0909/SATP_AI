"""
리포트 텍스트에서 가이던스·전망·목표가 등 추출 (규칙 기반).
PDF 전문 미사용.
"""

from __future__ import annotations

import re

_GUIDE_KW = (
    "가이던스",
    "guidance",
    "전망",
    "예상",
    "추정",
    "컨센서스",
    "목표가",
    "목표주가",
    "상향",
    "하향",
    "영업이익",
    "매출",
    "실적",
    "YoY",
    "QoQ",
    "EPS",
    "Review",
    "프리뷰",
    "preview",
    "잠정",
    "어닝",
)


def splitSentences(text):
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[\.。!!\?？])\s+|\n+", raw)
    return [p.strip() for p in parts if p and len(p.strip()) >= 6]


def extractGuideText(title, summary, maxLen=700):
    """
    제목·요약에서 가이던스/전망 관련 문장 추출.
    반환: (hasGuide Y/N, guideTxt)
    """
    blob = " ".join([str(title or ""), str(summary or "")]).strip()
    if not blob:
        return "N", ""
    sents = splitSentences(blob)
    hits = []
    for s in sents:
        low = s.lower()
        if any(k.lower() in low or k in s for k in _GUIDE_KW):
            hits.append(s)
    if not hits and any(k in blob or k.lower() in blob.lower() for k in _GUIDE_KW):
        # 문장 분리 실패 시 전체 앞부분
        hits = [blob[: maxLen]]
    guide = " / ".join(hits)[:maxLen]
    return ("Y" if guide else "N"), guide


def summarizeContent(title, summary, maxLen=900):
    """
    상세 요약 텍스트 축약 (규칙: 앞 문장 우선 + 가이던스 문장 보강).
    """
    title = str(title or "").strip()
    summary = str(summary or "").strip()
    if not summary:
        return title[:maxLen] if title else ""

    sents = splitSentences(summary)
    if not sents:
        return summary[:maxLen]

    picked = []
    # 가이던스 문장 우선
    for s in sents:
        if any(k in s or k.lower() in s.lower() for k in _GUIDE_KW):
            if s not in picked:
                picked.append(s)
        if len(picked) >= 4:
            break
    # 앞쪽 일반 문장
    for s in sents[:5]:
        if s not in picked:
            picked.append(s)
        if len(picked) >= 6:
            break

    out = (title + " — " if title else "") + " ".join(picked)
    return out[:maxLen]


def extractTargetAndOpinion(title, summary, opinionHint="", targetHint=None):
    """목표가·의견 보강."""
    blob = " ".join([str(title or ""), str(summary or ""), str(opinionHint or "")])
    target = targetHint
    if target is None:
        m = re.search(
            r"(?:목표주가|목표가)\s*[:\s]?\s*([0-9][0-9,]{2,})\s*원?",
            blob,
        )
        if m:
            try:
                target = int(m.group(1).replace(",", ""))
            except ValueError:
                target = None
    opinion = (opinionHint or "").strip()
    if not opinion:
        for kw in (
            "강력매수",
            "Trading Buy",
            "비중확대",
            "Overweight",
            "Outperform",
            "매수",
            "Buy",
            "중립",
            "보유",
            "Hold",
            "비중축소",
            "Underweight",
            "매도",
            "Sell",
        ):
            if kw in blob:
                opinion = kw
                break
    return opinion or "", target
