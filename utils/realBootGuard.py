"""실전 기동 거부. PBK01이 켜졌거나 설정을 복구했으면 모의로만 연다."""

from __future__ import annotations


def _isReal(mode):
    return str(mode or "").strip().lower() in ("real", "live", "실전")


def decideBootMode(requested, pbkEnabled=False, settingsRecovered=False):
    """실전이면 막고 paper 를 돌려준다. 이미 모의면 그대로 둔다."""
    if not _isReal(requested):
        return {"mode": "paper", "blocked": False, "reason": ""}
    reasons = []
    if pbkEnabled:
        reasons.append("PBK01이 켜져 있음")
    if settingsRecovered:
        reasons.append("settings.json을 읽지 못했거나 기본값으로 복구됨")
    if not reasons:
        return {"mode": "real", "blocked": False, "reason": ""}
    return {"mode": "paper", "blocked": True, "reason": " · ".join(reasons)}


def warnRealBlocked(reason, sender=None, log=None):
    text = f"[기동] 실전 모드 거부 — {reason}. 모의로만 기동합니다."
    if log is not None:
        log(text)
    else:
        try:
            from utils.termLog import termLog

            termLog(text)
        except Exception:
            pass
    if sender is not None:
        try:
            sender(text)
        except Exception:
            pass
    return text
