"""MMG01 설정 (settings.json) — 종목당 최대 매수금액."""

from __future__ import annotations

KEY_MAX_BUY_AMT = "mmgMaxBuyAmtPerStk"
DEFAULT_MAX_BUY_AMT = 3_000_000
MIN_MAX_BUY_AMT = 100_000
MAX_MAX_BUY_AMT = 1_000_000_000


def parseWonInput(raw):
    """'3,000,000' / '3000000원' / '300만' → int 원. 잘못된 값은 ValueError."""
    text = str(raw if raw is not None else "").strip().replace(",", "").replace(" ", "")
    if text.endswith("원"):
        text = text[:-1]
    mult = 1
    if text.endswith("만"):
        text, mult = text[:-1], 10_000
    if not text:
        raise ValueError("금액이 비어 있습니다")
    try:
        val = float(text)
    except ValueError:
        raise ValueError(f"숫자가 아닙니다: {raw}")
    won = int(round(val * mult))
    if won <= 0:
        raise ValueError("0보다 커야 합니다")
    if won < MIN_MAX_BUY_AMT or won > MAX_MAX_BUY_AMT:
        raise ValueError(f"{MIN_MAX_BUY_AMT:,}원 ~ {MAX_MAX_BUY_AMT:,}원 범위로 입력하세요")
    return won


def getMmgMaxBuyAmt():
    """종목당 최대 매수금액(원). 없거나 잘못되면 기본 3,000,000."""
    try:
        from config.settingsStore import loadSettings

        raw = (loadSettings() or {}).get(KEY_MAX_BUY_AMT)
        if raw in (None, ""):
            return DEFAULT_MAX_BUY_AMT
        val = int(float(raw))
        return val if val > 0 else DEFAULT_MAX_BUY_AMT
    except Exception:
        return DEFAULT_MAX_BUY_AMT


def setMmgMaxBuyAmt(raw):
    """검증 후 저장. 반환 (ok, won|errMsg)."""
    try:
        won = parseWonInput(raw)
    except ValueError as err:
        return False, str(err)
    from config.settingsStore import patchSettings

    patchSettings({KEY_MAX_BUY_AMT: int(won)})
    return True, won


def fmtWon(val):
    try:
        return f"{int(val):,}원"
    except (TypeError, ValueError):
        return "-"
