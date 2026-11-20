"""PBK 상태 일회 보정. 기본은 드라이런이다. 운영 DB 는 쓰지 않는다.

예:
  python3 tools/pbkReconcileOnce.py --set 300080:1=526,2=350
  python3 tools/pbkReconcileOnce.py --set 300080:1=526,2=350 --apply

--set 은 종목:단계=체결수량. 1은 1차, 2는 2차. 합이 그 종목 잔고가 된다.
--apply 가 없으면 변경 전후만 출력한다.
상태 파일은 한 줄을 덧붙인다. 체결 기록(pbk01_fills_YYYYMMDD.jsonl)은
복기할 때 그 줄을 그대로 읽으므로, 같은 종목·단계 줄을 고쳐 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(path):
    from utils.pbk01Store import latestByKind

    return latestByKind(path, "state") or {}


def _parse_set(text):
    """300080:1=526,2=350 → {code: {1: 526, 2: 350}}"""
    out = {}
    for part in str(text or "").split(";"):
        part = part.strip()
        if not part:
            continue
        code, _, legs = part.partition(":")
        code = code.strip()
        bucket = {}
        for bit in legs.split(","):
            bit = bit.strip()
            if not bit or "=" not in bit:
                continue
            leg, qty = bit.split("=", 1)
            bucket[int(leg)] = int(qty)
        if code and bucket:
            out[code] = bucket
    return out


def _apply_set(positions, spec):
    out = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        item = dict(pos)
        code = str(item.get("stkCd") or "")
        legs = spec.get(code)
        if not legs:
            out.append(item)
            continue
        total = 0
        for legNo, qty in legs.items():
            key = f"leg{legNo}"
            leg = dict(item.get(key) or {})
            plan = int(leg.get("originalPlan") or leg.get("qty") or qty)
            leg["originalPlan"] = plan
            leg["filledQty"] = int(qty)
            leg["qty"] = plan
            leg["remainQty"] = max(0, plan - int(qty))
            leg["expiredQty"] = max(0, plan - int(qty))
            leg["rmnQty"] = 0
            leg["settled"] = True
            item[key] = leg
            total += int(qty)
        item["brokerQty"] = total
        item["qty"] = total
        item["buyHold"] = False
        item["released"] = total <= 0
        out.append(item)
    return out


def _brief(pos):
    legs = []
    for key in ("leg1", "leg2"):
        leg = pos.get(key) or {}
        if isinstance(leg, dict) and (leg.get("qty") or leg.get("filledQty")):
            legs.append(f"{key} 주문 {leg.get('qty')} 체결 {leg.get('filledQty')} 잔량 {leg.get('remainQty')}")
    return f"{pos.get('stkCd')} {pos.get('stkNm') or ''} 잔고 {pos.get('brokerQty')} " + " ".join(legs)


def _leg_no(row):
    if row.get("leg") in (1, 2, "1", "2"):
        return int(row.get("leg"))
    name = str(row.get("legName") or "")
    if name.startswith("1"):
        return 1
    if name.startswith("2"):
        return 2
    return None


def _fill_files(folder, positions, spec):
    days = set()
    for pos in positions or []:
        if str(pos.get("stkCd") or "") not in spec:
            continue
        for key in ("leg1", "leg2"):
            leg = pos.get(key) or {}
            ymd = str(leg.get("ymd") or "")
            if len(ymd) == 8:
                days.add(ymd)
    found = []
    for day in sorted(days):
        path = Path(folder) / f"pbk01_fills_{day}.jsonl"
        if path.is_file():
            found.append(path)
    return found


def correct_fill_rows(rows, spec):
    """같은 종목·단계의 복기 줄을 지정 체결 수량으로 고친 새 목록."""
    out = []
    changed = []
    for row in rows or []:
        if not isinstance(row, dict):
            out.append(row)
            continue
        code = str(row.get("stkCd") or "")
        leg = _leg_no(row)
        if code not in spec or leg not in spec[code]:
            out.append(row)
            continue
        filled = int(spec[code][leg])
        try:
            ordered = int(row.get("ordQty") or filled)
        except (TypeError, ValueError):
            ordered = filled
        new = dict(row)
        new["brokerQty"] = filled
        new["expiredQty"] = max(0, ordered - filled)
        if "remainQty" in new:
            new["remainQty"] = max(0, ordered - filled)
        changed.append((row, new))
        out.append(new)
    return out, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description="PBK 상태 일회 보정 (드라이런 기본)")
    parser.add_argument("--state", default="")
    parser.add_argument("--set", dest="spec", default="300080:1=526,2=350")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from utils.pbk01Store import appendRow, pbkPath

    path = Path(args.state) if args.state else pbkPath("pbk01_state.jsonl")
    saved = _load(path)
    before = list(saved.get("positions") or [])
    spec = _parse_set(args.spec)
    after = _apply_set(before, spec)
    print("변경 전")
    for pos in before:
        if str(pos.get("stkCd") or "") in spec:
            print("  " + _brief(pos))
    print("변경 후")
    for pos in after:
        if str(pos.get("stkCd") or "") in spec:
            print("  " + _brief(pos))
    fill_paths = _fill_files(path.parent, before, spec)
    from utils.pbk01Store import readRows

    planned = []
    for fill_path in fill_paths:
        rows = readRows(fill_path)
        updated, diffs = correct_fill_rows(rows, spec)
        for old, new in diffs:
            print(
                f"fills {fill_path.name} {old.get('stkCd')} {old.get('legName') or old.get('leg')} "
                f"brokerQty {old.get('brokerQty')}→{new.get('brokerQty')} "
                f"expiredQty {old.get('expiredQty')}→{new.get('expiredQty')}"
            )
        planned.append((fill_path, updated, bool(diffs)))
    if not planned:
        print("fills 파일 없음")
    if not args.apply:
        print("드라이런. 파일을 쓰지 않았다. 반영하려면 --apply")
        return 0
    row = dict(saved)
    row["kind"] = "state"
    row["positions"] = after
    row["reconcileOnce"] = args.spec
    appendRow(path, row)
    print(f"반영 {path}")
    for fill_path, updated, touched in planned:
        if not touched:
            continue
        text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in updated)
        fill_path.write_text(text, encoding="utf-8")
        print(f"반영 {fill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
