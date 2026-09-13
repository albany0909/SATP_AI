"""
재무 관리 AI 에이전트 경계 패키지.

매매(main/trading)와 분리된 재무·리서치 도메인 소유권 계층.
구현 본체는 research/ · utils(rpt*) 에 두고, 외부 호출은 serviceApi 경유.
"""

from financeAgent.serviceApi import (
    analyzeCorpReports,
    formatCnsLastMsg,
    formatRptLastMsg,
    getStkFinanceBundle,
    loadAndStoreCorpReports,
    loadCnsFromFnSpace,
    loadWatchNewsDisc,
    runWatchNewsDiscNow,
)

__all__ = [
    "getStkFinanceBundle",
    "analyzeCorpReports",
    "loadAndStoreCorpReports",
    "loadCnsFromFnSpace",
    "formatRptLastMsg",
    "formatCnsLastMsg",
    "loadWatchNewsDisc",
    "runWatchNewsDiscNow",
]
