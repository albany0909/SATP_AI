"""
FnSpace 예상실적 적재 — 유료 정책으로 스킵.
"""

from __future__ import annotations

import os

from config.loadEnv import ensureEnvLoaded
from utils.termLog import termLog

ensureEnvLoaded()


def isFnSpaceEnabled():
    """유료 → 실연동 안 함."""
    return False


def loadCnsFromFnSpace(stkCd, **kwargs):
    """
    예상실적 FnSpace 적재 요청 시 항상 스킵.
    """
    _ = stkCd
    _ = kwargs
    ensureEnvLoaded()
    hasKey = bool(
        (os.environ.get("FNSPACE_API_KEY") or "").strip()
        or (os.environ.get("FN_SPACE_API_KEY") or "").strip()
    )
    msg = (
        "FnSpace 예상실적 적재 스킵(유료 Data Mart/API). "
        "대신 creport load 로 증권사 리포트 목표가·의견을 AI_CNS_EST 에 적재."
    )
    if hasKey:
        msg += " (.env 키 있음 → 유료 스킵 유지)"
    termLog(f"[fnSpaceCns] {msg}")
    return {
        "ok": True,
        "skipped": True,
        "paidSkip": True,
        "upsertCnt": 0,
        "msg": msg,
    }
