"""PBK01 정기보고서 실적 서프라이즈 저장. jsonl 에만 붙인다. 인증키는 넣지 않는다."""

from __future__ import annotations

import json
from pathlib import Path


def finPath():
    from utils.pbk01Store import pbkPath

    return pbkPath("pbk01_fin.jsonl")


def _read(path):
    filePath = Path(path)
    if not filePath.is_file():
        return []
    rows = []
    try:
        text = filePath.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def readFinRows(path=None):
    return _read(path or finPath())


def seenReceipts(path=None):
    """증감률까지 판정된 접수번호. 매출·영업 YoY 가 비면 다음에 다시 본다."""
    last = {}
    for row in _read(path or finPath()):
        rcept = str(row.get("rceptNo") or "")
        if rcept:
            last[rcept] = row
    done = set()
    for rcept, row in last.items():
        if row.get("salesYoy") is None or row.get("opYoy") is None:
            continue
        done.add(rcept)
    return done


def appendFinRows(items, path=None):
    """없는 접수번호만 붙인다. 기존 줄은 고치지 않는다."""
    filePath = Path(path) if path else finPath()
    have = seenReceipts(filePath)
    fresh = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        rcept = str(item.get("rceptNo") or "").strip()
        if not rcept or rcept in have:
            continue
        if "crtfc_key" in item or "crtfcKey" in item:
            continue
        have.add(rcept)
        fresh.append({key: item.get(key) for key in (
            "rceptNo", "stkCd", "rceptDt", "reportNm", "bsnsYear", "reprtCode",
            "quarter", "fsDiv", "sales", "salesLy", "op", "opLy",
            "salesYoy", "opYoy", "prevSalesYoy", "pass", "reason",
        ) if key in item or key in ("rceptNo", "stkCd", "pass")})
    if not fresh:
        return 0
    filePath.parent.mkdir(parents=True, exist_ok=True)
    with filePath.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def surprisesByCode(path=None):
    """S1 을 통과한 보고서만 공시처럼 돌려준다. 이벤트 기준일은 접수일이다."""
    out = {}
    for row in _read(path or finPath()):
        if row.get("pass") is not True:
            continue
        code = str(row.get("stkCd") or "")
        if len(code) != 6:
            continue
        out.setdefault(code, []).append({
            "title": row.get("reportNm") or "정기보고서",
            "date": row.get("rceptDt") or "",
            "rceptNo": row.get("rceptNo") or "",
            "source": "S1",
        })
    return out
