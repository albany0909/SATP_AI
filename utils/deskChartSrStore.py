"""
매매창 차트 가로선(지지·저항) 저장.
전략/주문은 아직 읽지 않는다. 이후 listSrLines(stkCd) 로 가격을 가져가면 된다.
"""

import json
from datetime import datetime
from pathlib import Path

from utils.stkCdUtil import normStkCd

_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "deskChartSr.json"


def _loadAll():
    if not _PATH.is_file():
        return {}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _saveAll(data):
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def listSrLines(stkCd):
    """[{id, px, note, lineTp, createdAt}, ...] 가격 오름차순."""
    cd = normStkCd(stkCd)
    if not cd:
        return []
    rows = list((_loadAll().get(cd) or []))
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            px = abs(float(row.get("px") or 0))
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        out.append(
            {
                "id": str(row.get("id") or f"{cd}-{i}"),
                "px": px,
                "note": str(row.get("note") or "지지"),
                "lineTp": str(row.get("lineTp") or "sup"),
                "createdAt": str(row.get("createdAt") or ""),
            }
        )
    out.sort(key=lambda r: r["px"])
    return out


def listSrPx(stkCd):
    """매매 조건에 쓸 가격만."""
    return [r["px"] for r in listSrLines(stkCd)]


def addSrLine(stkCd, px, note="지지", lineTp="sup"):
    """가로선 1개 추가. 반환: 저장된 행 또는 None."""
    cd = normStkCd(stkCd)
    try:
        price = abs(float(px))
    except (TypeError, ValueError):
        return None
    if not cd or price <= 0:
        return None
    data = _loadAll()
    rows = list(data.get(cd) or [])
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    rec = {
        "id": f"{cd}-{now}",
        "px": round(price, 2),
        "note": str(note or "지지")[:20],
        "lineTp": str(lineTp or "sup")[:8],
        "createdAt": now,
    }
    rows.append(rec)
    data[cd] = rows
    _saveAll(data)
    return rec


def updSrLine(stkCd, lineId, px):
    """드래그로 옮긴 지지선 가격을 저장. 이후 매매는 listSrPx 로 읽는다."""
    cd = normStkCd(stkCd)
    try:
        price = abs(float(px))
    except (TypeError, ValueError):
        return False
    if not cd or not lineId or price <= 0:
        return False
    data = _loadAll()
    rows = list(data.get(cd) or [])
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id")) != str(lineId):
            continue
        row["px"] = round(price, 2)
        found = True
        break
    if not found:
        return False
    data[cd] = rows
    _saveAll(data)
    return True


def delSrLine(stkCd, lineId):
    cd = normStkCd(stkCd)
    if not cd or not lineId:
        return False
    data = _loadAll()
    rows = [r for r in (data.get(cd) or []) if str(r.get("id")) != str(lineId)]
    if len(rows) == len(data.get(cd) or []):
        return False
    if rows:
        data[cd] = rows
    else:
        data.pop(cd, None)
    _saveAll(data)
    return True
