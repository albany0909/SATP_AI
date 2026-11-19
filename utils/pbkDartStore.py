"""PBK01 실적 공시 저장. jsonl 에만 붙인다. 인증키는 넣지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

from strategies.impl.pullbackTwoStep import disclosureAccepted


def dartPath():
    from utils.pbk01Store import pbkPath

    return pbkPath("pbk01_dart.jsonl")


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


def appendDisclosures(items, path=None):
    """없는 접수번호만 붙인다. 기존 줄은 고치지 않는다."""
    filePath = Path(path) if path else dartPath()
    have = {str(row.get("rceptNo") or "") for row in _read(filePath)}
    fresh = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if not disclosureAccepted(item.get("reportNm") or item.get("title")):
            continue
        rcept = str(item.get("rceptNo") or "").strip()
        code = str(item.get("stkCd") or "")[:6]
        day = str(item.get("rceptDt") or item.get("date") or "").replace("-", "")[:8]
        if not rcept or rcept in have or len(code) != 6 or len(day) != 8:
            continue
        have.add(rcept)
        fresh.append({
            "rceptNo": rcept,
            "stkCd": code,
            "reportNm": str(item.get("reportNm") or item.get("title") or ""),
            "rceptDt": day,
        })
    if not fresh:
        return 0
    filePath.parent.mkdir(parents=True, exist_ok=True)
    with filePath.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def disclosuresByCode(path=None):
    out = {}
    for row in _read(path or dartPath()):
        code = str(row.get("stkCd") or "")
        if len(code) != 6:
            continue
        out.setdefault(code, []).append({
            "title": row.get("reportNm") or "",
            "date": row.get("rceptDt") or "",
            "rceptNo": row.get("rceptNo") or "",
        })
    return out
