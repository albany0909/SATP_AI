"""
재무 에이전트 공개 서비스 API.

텔레그램·웹·CLI 는 research/* 를 직접 import 하지 않고
이 모듈을 통해서만 재무 기능을 호출한다.
(구현은 research / utils 에 위임 — 소유 경계용 파사드)
"""

from __future__ import annotations

# ── 소유 도메인 구현 위임 (financeAgent 밖 직접 호출 금지 권장) ──
from research.fnSpaceCnsLoad import loadCnsFromFnSpace
from research.reportAnalyze import analyzeCorpReports
from research.rptLoadService import (
    formatCnsLastMsg,
    formatRptLastMsg,
    loadAndStoreCorpReports,
)
from research.stkFinanceBundle import getStkFinanceBundle
from research.watchNewsDiscLoad import loadWatchNewsDisc
from utils.watchNewsDiscScheduler import (
    getWatchNewsDiscSchedStatus,
    runWatchNewsDiscNow,
)
from utils.watchNewsDiscStore import listRecentDisc, listRecentNews

__all__ = [
    "getStkFinanceBundle",
    "analyzeCorpReports",
    "loadAndStoreCorpReports",
    "loadCnsFromFnSpace",
    "formatRptLastMsg",
    "formatCnsLastMsg",
    "loadWatchNewsDisc",
    "runWatchNewsDiscNow",
    "listWatchRecentDisc",
    "listWatchRecentNews",
    "formatWatchNewsStatusMsg",
    "agentScope",
]


def listWatchRecentDisc(stkCd=None, limit=20):
    return listRecentDisc(stkCd=stkCd, limit=limit)


def listWatchRecentNews(stkCd=None, limit=20, issueOnly=False):
    return listRecentNews(stkCd=stkCd, limit=limit, issueOnly=issueOnly)


def formatWatchNewsStatusMsg():
    st = getWatchNewsDiscSchedStatus()
    return (
        "[관심 공시·뉴스 스케줄]\n"
        f"기동: {'Y' if st.get('started') else 'N'}\n"
        f"주기: {st.get('intervalMin')}분 · 창 {st.get('window')}\n"
        f"수집중: {'Y' if st.get('running') else 'N'}\n"
        f"최근키: {st.get('lastRunKey') or '-'}\n"
        f"{st.get('lastSummary') or '(아직 없음)'}"
    )


def agentScope():
    """재무 에이전트가 담당하는 범위 요약 (라우팅·디버그용)."""
    return {
        "agentId": "financeAgent",
        "owns": [
            "Open DART 재무·법인코드·공시목록",
            "관심종목 공시·뉴스 주기 수집",
            "증권사 리포트 수집·분석·DB(AI_RPT_ANAL)",
            "가이던스·목표가 파생(AI_CNS_EST)",
            "연구 소스 레지스트리(researchSources)",
            "웹 /api/stkFinance · /api/corpReport 비즈니스",
            "텔레그램 creport / cns / wnews 비즈니스",
        ],
        "doesNotOwn": [
            "키움 주문·체결·STLS",
            "매매 전략/백테스트 (strategies·trading·backtest)",
            "일봉/분봉 적재·차트 시리즈",
            "조건검색 실시간(cnsr)",
            "메인 프로세스 생명주기(main.py) — 스케줄 기동만 위임 수신",
        ],
        "implDirs": [
            "financeAgent/",
            "research/",
            "utils/rptAnal*",
            "utils/watchNewsDisc*",
        ],
        "skill": ".cursor/skills/financeAgent/SKILL.md",
        "roleDoc": "financeAgent/AGENT.md",
    }
