"""알고리즘 상세명세서. 스펙 JSON 과 문서 이력에 있는 내용만 적는다."""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC_DIR = _ROOT / "docs" / "algSpec"


def renderAlgSpec(spec, hist=None):
    """한글 명세. 없는 항목은 불명확으로 남긴다."""
    spec = spec or {}
    code = str(spec.get("algCd") or "")
    lines = [
        f"# {spec.get('name') or code} ({code})",
        "",
        f"- 전략ID: `{spec.get('stratId') or '불명확'}`",
        f"- 버전: {spec.get('version') if spec.get('version') is not None else '불명확'}",
        f"- 상태: {spec.get('status') or '불명확'}",
        f"- 명세 enabled: {spec.get('enabled')}",
        f"- 주기: {spec.get('timeframe') or '불명확'}",
        "",
        "## 목적",
        "",
        str(spec.get("notes") or "불명확. 스펙 notes 가 없다."),
        "",
        "## 대상 종목",
        "",
        _jsonBlock(spec.get("universe"), "universe 가 없다."),
        "",
        "## 진입",
        "",
        _rule(spec, "entry", "scope", "trend", "breakout", "prereq", "watch"),
        "",
        "## 청산·손절",
        "",
        _rule(spec, "stops", "stop", "exit", "takeProfit", "profit", "tp", "negCut", "maExit"),
        "",
        "## 수량·금액",
        "",
        _paramsBlock(spec.get("params")),
        "",
        "## 시간",
        "",
        _timeBlock(spec.get("params") or {}),
        "",
        "## 데이터",
        "",
        "스펙에 적힌 조회 이름만 보여 준다. 코드에서 더 부르는 TR 은 이 문서에 없을 수 있다.",
        "",
        _jsonBlock(spec.get("universe"), ""),
        "",
        "## 버전 이력",
        "",
    ]
    rows = list(hist or [])
    if not rows:
        lines.append("문서·DB 이력이 없다.")
    for row in rows:
        lines.append(f"- {row.get('ymd') or '-'} v{row.get('ver') or '?'} {row.get('note') or ''}")
    lines.append("")
    lines.append("보유를 중단 버튼으로 자동 매도하지 않는다.")
    lines.append("")
    return "\n".join(lines)


def _rule(spec, *keys):
    rules = (spec or {}).get("rules") or {}
    found = []
    for key in keys:
        if key in rules and rules.get(key):
            found.append(f"- {key}: {rules.get(key)}")
    if not found:
        return "불명확. 스펙 rules 에 해당 키가 없다."
    return "\n".join(str(item) for item in found)


def _paramsBlock(params):
    if not isinstance(params, dict) or not params:
        return "불명확. params 가 없다."
    return "```\n" + json.dumps(params, ensure_ascii=False, indent=2) + "\n```"


def _timeBlock(params):
    keys = [k for k in params if "tm" in k.lower() or "time" in k.lower() or k == "schedule"]
    if not keys:
        return "불명확. 시간 키가 params 에 없다."
    picked = {k: params.get(k) for k in keys}
    return "```\n" + json.dumps(picked, ensure_ascii=False, indent=2) + "\n```"


def _jsonBlock(value, empty):
    if not value:
        return empty or "없음"
    return "```\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def specFilePath(algCd):
    return _SPEC_DIR / f"{algCd}.md"


def writeAlgSpecFiles(specs, histByAlg=None):
    """등록 알고리즘마다 docs/algSpec/<코드>.md 를 다시 쓴다."""
    histByAlg = histByAlg or {}
    _SPEC_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in specs or []:
        code = str((spec or {}).get("algCd") or "").strip()
        if not code:
            continue
        path = specFilePath(code)
        path.write_text(renderAlgSpec(spec, histByAlg.get(code) or []), encoding="utf-8")
        written.append(path)
    return written
