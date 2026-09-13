"""
기업리포트(증권사 리서치) 분석.

- 목록/제목/의견/목표가 메타 기반 규칙 분석
- PDF 전문 다운로드·재배포 없음
- 참고용, 투자 권유 아님
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from research.naverCorpReport import enrichReportDetail, fetchCorpReportList
from utils.stkResolve import formatStkLabel, resolveStkRef

# 제목·본문에서 쓰는 톤 키워드
_BULL = (
    "상향",
    "호조",
    "회복",
    "성장",
    "확대",
    "개선",
    "수혜",
    "우호",
    "신고",
    "강세",
    "긍정적",
    "어닝 서프라이즈",
    "턴어라운드",
    "모멘텀",
    "HBM",
)
_BEAR = (
    "하향",
    "둔화",
    "부진",
    "감소",
    "우려",
    "리스크",
    "부담",
    "약세",
    "실망",
    "적자",
    "보수적",
    "지연",
    "과열",
    "차익실현",
)
_GUIDE = (
    "가이던스",
    "전망",
    "예상",
    "추정치",
    "컨센서스",
    "목표",
    "outlook",
    "guidance",
    "실적",
    "Review",
    "preview",
    "프리뷰",
    "잠정",
)
_OPINION_SCORE = {
    "강력매수": 2,
    "매수": 1,
    "Buy": 1,
    "Trading Buy": 1,
    "비중확대": 1,
    "Overweight": 1,
    "Outperform": 1,
    "중립": 0,
    "보유": 0,
    "Hold": 0,
    "Marketperform": 0,
    "비중축소": -1,
    "Underweight": -1,
    "매도": -2,
    "Sell": -2,
}


def _toneScore(text):
    t = text or ""
    bull = sum(1 for k in _BULL if k.lower() in t.lower() or k in t)
    bear = sum(1 for k in _BEAR if k.lower() in t.lower() or k in t)
    return bull - bear, bull, bear


def _hasGuide(text):
    t = text or ""
    return any(k.lower() in t.lower() or k in t for k in _GUIDE)


def _opinionScore(op):
    if not op:
        return None
    for k, v in _OPINION_SCORE.items():
        if k.lower() == str(op).lower() or k in str(op):
            return v
    return None


def analyzeReportItems(items, detailTopN=5):
    """
    목록 분석 + 최근 N건 상세 보강.
    items는 mutate 가능(상세 병합).
    """
    if not items:
        return {
            "summary": {
                "reportCnt": 0,
                "bullishCnt": 0,
                "bearishCnt": 0,
                "neutralCnt": 0,
                "guideCnt": 0,
                "brokerCnt": 0,
                "avgTone": 0.0,
                "targetPrcList": [],
                "opinionDist": {},
            },
            "highlights": [],
            "risks": [],
            "themes": [],
            "items": [],
        }

    # 상세 보강 (최신순 가정, 상위 N건만 — rate limit 배려)
    enriched = []
    topN = max(0, int(detailTopN or 0))
    for i, it in enumerate(items):
        row = dict(it)
        if i < topN:
            row = enrichReportDetail(row, row.get("stkCd"))
        enriched.append(row)

    tones = []
    guideCnt = 0
    bullish = bearish = neutral = 0
    brokers = Counter()
    opinions = Counter()
    targets = []
    themeWords = Counter()
    highlights = []
    risks = []

    for it in enriched:
        blob = " ".join(
            [
                str(it.get("title") or ""),
                str(it.get("summary") or ""),
                str(it.get("opinion") or ""),
            ]
        )
        tone, bullN, bearN = _toneScore(blob)
        tones.append(tone)
        if _hasGuide(blob):
            guideCnt += 1
        if tone > 0:
            bullish += 1
        elif tone < 0:
            bearish += 1
        else:
            neutral += 1
        if it.get("broker"):
            brokers[it["broker"]] += 1
        op = it.get("opinion") or ""
        if op:
            opinions[op] += 1
        if it.get("targetPrc"):
            targets.append(
                {
                    "prc": it["targetPrc"],
                    "broker": it.get("broker") or "",
                    "pubDt": it.get("pubDt") or "",
                    "title": it.get("title") or "",
                }
            )
        # 단순 테마 토큰 (2글자 이상 한글/영문 키워드 제한)
        for w in re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", it.get("title") or ""):
            if w in ("삼성전자", "리포트", "관련", "대한", "의견", "목표"):
                continue
            if w in _BULL or w in _BEAR or w in _GUIDE or len(w) >= 2:
                themeWords[w] += 1
        # 하이라이트/리스크 문장
        title = it.get("title") or ""
        if tone >= 1 and bullN >= 1:
            highlights.append(
                {
                    "title": title,
                    "broker": it.get("broker"),
                    "pubDt": it.get("pubDt"),
                    "why": "제목·톤 상 우호 키워드",
                    "url": it.get("detailUrl") or it.get("pdfUrl"),
                }
            )
        if tone <= -1 and bearN >= 1:
            risks.append(
                {
                    "title": title,
                    "broker": it.get("broker"),
                    "pubDt": it.get("pubDt"),
                    "why": "제목·톤 상 경계 키워드",
                    "url": it.get("detailUrl") or it.get("pdfUrl"),
                }
            )

    avgTone = sum(tones) / len(tones) if tones else 0.0
    if avgTone >= 0.6:
        bias = "우호(다수 제목 톤)"
    elif avgTone <= -0.6:
        bias = "경계(다수 제목 톤)"
    else:
        bias = "혼조·중립"

    # 목표가 통계
    tgtVals = [t["prc"] for t in targets if t.get("prc")]
    tgtStat = None
    if tgtVals:
        tgtStat = {
            "cnt": len(tgtVals),
            "min": min(tgtVals),
            "max": max(tgtVals),
            "avg": int(sum(tgtVals) / len(tgtVals)),
        }

    return {
        "summary": {
            "reportCnt": len(enriched),
            "bullishCnt": bullish,
            "bearishCnt": bearish,
            "neutralCnt": neutral,
            "guideCnt": guideCnt,
            "brokerCnt": len(brokers),
            "avgTone": round(avgTone, 2),
            "biasLabel": bias,
            "targetPrcList": targets[:10],
            "targetPrcStat": tgtStat,
            "opinionDist": dict(opinions),
            "topBrokers": brokers.most_common(8),
        },
        "highlights": highlights[:8],
        "risks": risks[:8],
        "themes": [w for w, _ in themeWords.most_common(12)],
        "items": enriched,
    }


def analyzeCorpReports(stkRef, maxPages=2, maxItems=20, detailTopN=5):
    """
    종목코드|이름 → 기업리포트 수집·분석.
    반환: ok, stkCd, stkNm, analysis, listUrl, msgText, errMsg?
    """
    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "errMsg": resolved.get("errMsg") or "종목 해석 실패",
            "matches": resolved.get("matches") or [],
        }
    stkCd = resolved["stkCd"]
    stkNm = resolved.get("stkNm") or ""
    listed = fetchCorpReportList(stkCd, maxPages=maxPages, maxItems=maxItems)
    if not listed.get("ok") and not listed.get("items"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": listed.get("errMsg") or "기업리포트 목록 없음",
            "listUrl": listed.get("listUrl"),
        }
    analysis = analyzeReportItems(listed.get("items") or [], detailTopN=detailTopN)
    label = formatStkLabel(stkCd, stkNm)
    msg = formatCorpReportMsg(label, listed, analysis)
    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "listUrl": listed.get("listUrl"),
        "cnt": listed.get("cnt") or 0,
        "analysis": analysis,
        "msgText": msg,
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "disclaimer": (
            "증권사 리포트 요약·토널 분석(참고). PDF 전문 재배포 없음. "
            "투자의견·매수 권유 아님."
        ),
    }


def formatCorpReportMsg(stkLabel, listed, analysis):
    """텔레그램/콘솔용 짧은 텍스트."""
    sm = (analysis or {}).get("summary") or {}
    lines = [
        f"[기업리포트 분석] {stkLabel}",
        f"수집 {sm.get('reportCnt') or 0}건 · 톤 {sm.get('biasLabel') or '-'}"
        f" (우호{sm.get('bullishCnt')}/경계{sm.get('bearishCnt')}/중립{sm.get('neutralCnt')})",
        f"실적·전망 키워드 포함 {sm.get('guideCnt') or 0}건 · 증권사 {sm.get('brokerCnt') or 0}곳",
    ]
    tgt = sm.get("targetPrcStat")
    if tgt:
        lines.append(
            f"목표가(추출) {tgt['cnt']}건 avg {tgt['avg']:,} "
            f"(min {tgt['min']:,} ~ max {tgt['max']:,})"
        )
    if sm.get("opinionDist"):
        od = ", ".join(f"{k}:{v}" for k, v in sm["opinionDist"].items())
        lines.append(f"투자의견 분포: {od}")
    themes = (analysis or {}).get("themes") or []
    if themes:
        lines.append("키워드: " + ", ".join(themes[:8]))
    highs = (analysis or {}).get("highlights") or []
    if highs:
        lines.append("- 우호 제목 -")
        for h in highs[:3]:
            lines.append(
                f"· [{h.get('pubDt') or ''}] {h.get('broker') or ''} "
                f"{h.get('title') or ''}"
            )
    risks = (analysis or {}).get("risks") or []
    if risks:
        lines.append("- 경계 제목 -")
        for h in risks[:3]:
            lines.append(
                f"· [{h.get('pubDt') or ''}] {h.get('broker') or ''} "
                f"{h.get('title') or ''}"
            )
    # 최신 5건 목록
    items = (analysis or {}).get("items") or []
    if items:
        lines.append("- 최근 리포트 -")
        for it in items[:5]:
            op = it.get("opinion") or ""
            tp = it.get("targetPrc")
            extra = ""
            if op or tp:
                extra = f" | {op}" + (f" 목표가 {tp:,}" if tp else "")
            lines.append(
                f"· {it.get('pubDtRaw') or it.get('pubDt') or ''} "
                f"{it.get('broker') or ''} | {it.get('title') or ''}{extra}"
            )
    if listed.get("listUrl"):
        lines.append(f"목록: {listed['listUrl']}")
    lines.append("※ 참고용(투자 권유 아님). PDF 전문 미포함.")
    return "\n".join(lines)
