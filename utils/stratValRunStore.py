"""
전략 검증(validation-only) 실행 결과 저장.

저장: config/data/stratValRun/{stratId}_{runTs}.json
실주문 없음 — valStrat / val 명령 공통 사용.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_VAL_DIR = Path(__file__).resolve().parent.parent / "config" / "data" / "stratValRun"


def _runTs():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _latestPath(stratId):
    """최신 결과 파일 (mtime 기준)."""
    sid = str(stratId or "").strip()
    if not sid:
        return None
    files = sorted(_VAL_DIR.glob(f"{sid}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def saveStratValRun(stratId, payload):
    """검증 결과 JSON 저장. 경로 반환."""
    sid = str(stratId or "").strip()
    if not sid:
        return None
    ts = _runTs()
    path = _VAL_DIR / f"{sid}_{ts}.json"
    body = dict(payload or {})
    body.setdefault("stratId", sid)
    body.setdefault("runTs", ts)
    body.setdefault("savedAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _VAL_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def loadLatestStratValRun(stratId):
    path = _latestPath(stratId)
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def listStratValRuns(stratId=None, limit=20):
    """최근 검증 이력 (신→구)."""
    pattern = f"{stratId}_*.json" if stratId else "*.json"
    files = sorted(_VAL_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[: max(1, int(limit or 20))]:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            row["_path"] = str(path)
            out.append(row)
        except Exception:
            continue
    return out
