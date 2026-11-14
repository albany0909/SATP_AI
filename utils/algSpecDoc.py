"""알고리즘 상세명세서. 스펙 JSON 과 문서 이력에 있는 내용만 적는다."""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC_DIR = _ROOT / "docs" / "algSpec"

# 스펙·코드에 있는 규칙만. 없는 수는 불명확.
EASY_KO = {
    "SRG01": (
        "당일 10% 이상 오른 종목 가운데, 같은 이슈의 첫 급등만 모의로 삽니다. "
        "15:18부터 정규장 마감 전에 약 100만 원어치를 한 번 삽니다. "
        "수익률이 -5%면 1주만 남기고 팔고, -10%면 나머지를 팝니다. "
        "15:15 이후 수익 중인데 음봉으로 20일선 아래로 내려가면 전량 팝니다. "
        "오르는 날이 아니면 맞지 않습니다. "
        "예를 들어 10,000원에 사면 9,500원에서 대부분을 팔고 9,000원에서 나머지를 팝니다."
    ),
    "MTS01": (
        "전날 순위·거래대금으로 고른 종목이 20일선 위로 올라오면 삽니다. "
        "스펙 메모에는 1·2차 매수 전 하드 손절이 -1.5%로 적혀 있습니다. "
        "1차 익절 비율은 버전마다 바뀌어 현재 params 의 tp1 을 봅니다. "
        "장중에 새 종목을 집어넣지 않고, 이미 고른 목록만 봅니다. "
        "예를 들어 10,000원에 샀고 하드 손절이 1.5%이면 9,850원 근처에서 팝니다."
    ),
    "MSE01": (
        "이평선 위로 한 번 올라간 뒤, 약한 음봉이나 도지가 5일선에 받치면 장 마감 무렵 삽니다. "
        "매수 시간은 스펙의 15:18~15:20 구간입니다. "
        "청산 가격 규칙은 rules.exit 에 있고, 이 쉬운 설명에 숫자를 더 넣지 않습니다. "
        "오르막에서 쉬는 눌림에 가깝고, 하락장 신규 매수 조건은 불명확합니다."
    ),
    "MMG01": (
        "사람이 먼저 1주 이상 사 둔 종목만 대상으로, 15:15 무렵 종가 쪽으로 피라미딩합니다. "
        "자동으로 아무 종목이나 고르지는 않습니다. "
        "손절 퍼센트는 이 쉬운 문장에 단정하지 않습니다. 스펙 rules 에 stop 키가 없어 불명확합니다. "
        "이미 관심 있는 종목을 마감 전에 나누어 담는 용도입니다."
    ),
    "LDR01": (
        "같은 이슈로 오른 주도주를 지켜보다가 조건을 만족하면 나누어 삽니다. "
        "추가 매수·일부 매도 문구는 rules 의 add·trim 에 있습니다. "
        "손절 가격의 구체 숫자는 이 문장에서 불명확합니다. "
        "테마가 여러 종목으로 퍼지는 날에 맞습니다."
    ),
    "RAM01": (
        "실계좌에 남은 종목을 지켜보고, 매매 버튼을 켠 종목만 매매 대상으로 둡니다. "
        "자동으로 새 종목을 찾아 사는 전략이 아닙니다. "
        "익절·손절 숫자는 rules 에 한 줄로 고정돼 있지 않아 불명확합니다. "
        "이미 가진 계좌를 정리할 때 씁니다."
    ),
    "TPX01": (
        "시간대에 따라 보유 종목을 나누어 익절하거나 청산합니다. "
        "손실이 난 매도는 텔레그램 확인을 기다립니다. "
        "몇 시에 몇 퍼센트인지는 rules.tp 문장을 그대로 봐야 하고, 여기서 숫자를 만들지 않습니다. "
        "장중 보유를 시간에 맞춰 줄이는 용도입니다."
    ),
    "AMB01": (
        "이동평균이 정배열인 종목이 그 선을 위로 돌파하면 삽니다. "
        "손절·익절 문장은 rules.stop·takeProfit 에 있습니다. "
        "예시 가격은 스펙에 금액 예시가 없어 불명확합니다. "
        "오르막이 정렬된 종목에 맞습니다."
    ),
    "BBM01": (
        "긴 양봉이 20일·50일선과 함께 나온 종목을 삽니다. "
        "익절 문장은 rules.takeProfit 에 있습니다. "
        "손절 가격 예시는 스펙에 없어 불명확합니다. "
        "거래가 몰린 상승일에 가깝습니다."
    ),
    "HPB01": (
        "신고가 뒤 눌림이 나온 종목을 삽니다. "
        "손절·익절은 rules.stop·takeProfit 문장입니다. "
        "10,000원 같은 금액 예시는 스펙에 없어 불명확합니다. "
        "고점 이후 숨 고르기 장면에 맞습니다."
    ),
    "MHP01": (
        "정배열에서 눌린 뒤 프랙탈 매수 신호가 나오면 삽니다. "
        "손절·익절은 rules.stop·takeProfit 입니다. "
        "구체 원 단위 예시는 스펙에 없어 불명확합니다. "
        "상승 추세의 짧은 눌림에 맞습니다."
    ),
}


def easyText(algCd):
    return EASY_KO.get(str(algCd or "").strip(), "불명확. 쉬운 설명이 아직 없다.")


def easyFirstSentence(algCd):
    text = easyText(algCd).strip()
    if not text:
        return "불명확"
    for mark in (". ", "。"):
        if mark in text:
            return text.split(mark, 1)[0] + "."
    return text


def renderAlgSpec(spec, hist=None):
    """한글 명세. 없는 항목은 불명확으로 남긴다."""
    spec = spec or {}
    code = str(spec.get("algCd") or "")
    lines = [
        f"# {spec.get('name') or code} ({code})",
        "",
        "## 쉽게 말하면",
        "",
        easyText(code),
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
