"""
주요재무정보 표 병합:
- 표 골격·분기·추정(E)·DART에 없는 행: 네이버
- 연간 실적(E 제외) 중 DART 매칭 가능 계정: DART 원 단위 → 억원 환산
"""

from __future__ import annotations

from research.dartFinMatrix import fetchDartFinByYears, _yearFromLabel
from research.naverFinSummary import fetchNaverFinSummary


def _wonToEok(v):
    """원 → 억원 (반올림)."""
    if v is None:
        return None
    try:
        return int(round(float(v) / 1e8))
    except (TypeError, ValueError):
        return None


def _fmtEok(num):
    if num is None:
        return ""
    try:
        n = int(num) if float(num) == int(float(num)) else float(num)
    except (TypeError, ValueError):
        return str(num)
    s = f"{n:,}"
    return s


def buildFinSummaryTable(stkCd):
    """
    종목 주요재무정보 표 (이미지 스타일 데이터).
    """
    naver = fetchNaverFinSummary(stkCd)
    if not naver.get("ok"):
        return {
            "ok": False,
            "errMsg": naver.get("errMsg") or "네이버 재무표 없음",
            "stkCd": stkCd,
            "yearCols": [],
            "qtrCols": [],
            "rows": [],
        }

    yearCols = naver.get("yearCols") or []
    qtrCols = naver.get("qtrCols") or []
    rows = naver.get("rows") or []

    # DART 연간 실적 보강 대상 연도 (E 제외)
    years = []
    for c in yearCols:
        if c.get("isEst"):
            continue
        y = _yearFromLabel(c.get("label"))
        if y and y not in years:
            years.append(y)

    dart = fetchDartFinByYears(stkCd, years) if years else {"ok": False, "byYear": {}}
    byYear = dart.get("byYear") or {}
    dartApplyCnt = 0

    # 행 병합
    outRows = []
    for r in rows:
        name = r.get("name") or ""
        yVals = []
        for i, col in enumerate(yearCols):
            cell = dict((r.get("yearVals") or [{}] * (i + 1))[i] if i < len(r.get("yearVals") or []) else {
                "text": "",
                "num": None,
                "isEst": col.get("isEst"),
                "neg": False,
                "src": "NAVER",
            })
            # 기본 소스 표시
            cell.setdefault("src", "NAVER")
            cell["isEst"] = bool(col.get("isEst") or cell.get("isEst"))

            y = _yearFromLabel(col.get("label"))
            # 추정 연도는 네이버 유지
            if not cell["isEst"] and y and y in byYear:
                accMap = (byYear[y].get("accounts") or {})
                # 정확한 행 이름 매칭
                # DART 키는 행 이름 정규화 (공백·들여쓰기 제거)
                nameKey = name.replace(" ", "").lstrip()
                dartWon = accMap.get(name) or accMap.get(nameKey)
                if dartWon is None:
                    for k, v in accMap.items():
                        kk = k.replace(" ", "")
                        if kk == nameKey:
                            dartWon = v
                            break
                    # 영업이익(발표기준)은 영업이익과 동일 매칭 허용
                    if dartWon is None and nameKey.startswith("영업이익"):
                        dartWon = accMap.get("영업이익")
                eok = _wonToEok(dartWon) if dartWon is not None else None
                if eok is not None:
                    cell = {
                        "text": _fmtEok(eok),
                        "num": eok,
                        "isEst": False,
                        "neg": eok < 0,
                        "src": "DART",
                        "dartWon": dartWon,
                    }
                    dartApplyCnt += 1
            yVals.append(cell)

        qVals = []
        for i, col in enumerate(qtrCols):
            if i < len(r.get("qtrVals") or []):
                cell = dict(r["qtrVals"][i])
            else:
                cell = {
                    "text": "",
                    "num": None,
                    "isEst": col.get("isEst"),
                    "neg": False,
                    "src": "NAVER",
                }
            cell.setdefault("src", "NAVER")
            cell["isEst"] = bool(col.get("isEst") or cell.get("isEst"))
            # 분기는 DART 단일 계정 표와 연도가 달라 네이버 유지 (기본)
            qVals.append(cell)

        outRows.append({"name": name, "yearVals": yVals, "qtrVals": qVals})

    return {
        "ok": True,
        "stkCd": naver.get("stkCd") or stkCd,
        "unitNote": naver.get("unitNote") or "단위 : 억원, %, 배, 주",
        "yearCols": yearCols,
        "qtrCols": qtrCols,
        "rows": outRows,
        "sourceUrl": naver.get("sourceUrl"),
        "dartOk": bool(dart.get("ok")),
        "dartCorpCode": dart.get("corpCode"),
        "dartApplyCnt": dartApplyCnt,
        "notes": [
            "연간 핵심계정(매출·영업이익·순이익·자산/부채/자본 등): Open DART 연결 우선 → 억원 환산",
            "예상실적(E)·분기·비율·주당지표 등 DART 미제공/미매핑: 네이버 페이 증권",
            "투자 권유 아님 · 공시·포털 수치 차이 가능",
        ],
        "srcLabel": "DART+네이버 주요재무정보",
    }
