"""매도세 일자표. config/data/sellTax.json 을 고치면 다음 계산부터 반영된다."""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "sellTax.json"
_cache = None


def loadSellTaxTable(path=None):
    global _cache
    if path is None and _cache is not None:
        return _cache
    filePath = Path(path) if path else _PATH
    data = json.loads(filePath.read_text(encoding="utf-8"))
    rows = []
    for row in data.get("rows") or []:
        rows.append({
            "from": str(row.get("from") or ""),
            "to": str(row.get("to") or "99991231"),
            "rate": float(row.get("rate") or 0),
        })
    rows.sort(key=lambda item: item["from"])
    if path is None:
        _cache = rows
    return rows


def sellTaxFor(ymd, table=None):
    """매도일 세율. 날짜가 없으면 표의 마지막 구간을 쓴다."""
    rows = list(table if table is not None else loadSellTaxTable())
    if not rows:
        return 0.0020
    day = str(ymd or "")[:8]
    if len(day) == 8 and day.isdigit():
        for row in rows:
            if row["from"] <= day <= row["to"]:
                return float(row["rate"])
    return float(rows[-1]["rate"])
