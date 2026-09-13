"""DB 적재 배치 스케줄 목록 (시각순) + on/off 상태."""

from config.opsSettings import DEFAULT_BATCH_SCHED, getBatchSchedMap
from config.settingsStore import getMktHours


def _hmToMin(hm):
    try:
        h, m = str(hm).split(":")
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return 0


def _closePlusMin(closeHm, addMin):
    try:
        total = _hmToMin(closeHm) + int(addMin)
        return f"{total // 60:02d}:{total % 60:02d}"
    except (TypeError, ValueError):
        return closeHm


def listBatchSchedJobs():
    """
    DB 적재 배치를 시작 시각 순으로 반환.
    각 항목: id, label, whenLabel, sortHm, table, desc, weekday, enabled
    """
    _, closeHm = getMktHours()
    ntlStart = _closePlusMin(closeHm, 10)

    jobs = [
        {
            "id": "usMorningLoad",
            "label": "미국순위·급등락",
            "sortHm": "07:00",
            "whenLabel": "07:00",
            "table": "AI_US_DAY_INQ_RANK · AI_US_DAY_FLU_ISSUE · 미국일봉",
            "desc": "직전 미장 세션 당일누적 순위 + 급등락(usa20930) + 선정종목 일봉",
            "weekday": "미장 개장일 다음날 아침(토 포함)",
        },
        {
            "id": "watchNewsDisc",
            "label": "관심 공시·뉴스",
            "sortHm": "08:00",
            "whenLabel": "08:00~22:00 · 30분",
            "table": "AI_STK_DISC · AI_STK_NEWS",
            "desc": "관심종목 DART 공시 + 네이버 뉴스 주기 수집",
            "weekday": "국내 거래일",
        },
        {
            "id": "dayInqRank",
            "label": "국내 조회순위",
            "sortHm": closeHm,
            "whenLabel": f"{closeHm}~18:00",
            "table": "AI_DAY_INQ_RANK · AI_DAY_STK_PRC · AI_WATCH_STK",
            "desc": "당일누적 실시간조회순위 + 선정종목 1년 일봉 + 관심종목 반영",
            "weekday": "국내 거래일",
        },
        {
            "id": "rankDayPrc",
            "label": "순위종목 일봉",
            "sortHm": _closePlusMin(closeHm, 1),
            "whenLabel": "조회순위 적재 직후",
            "table": "AI_DAY_STK_PRC",
            "desc": "순위 대상 종목 1년(12개월) 일봉 — 150·200 이평용, 200봉 이상은 건너뜀",
            "weekday": "국내 거래일",
        },
        {
            "id": "ntlFirst",
            "label": "최초 52주 신고",
            "sortHm": ntlStart,
            "whenLabel": f"{ntlStart}~18:30",
            "table": "AI_DAY_NTL_FIRST",
            "desc": "장마감 후 최초 52주 신고 진입 종목 (ka10016+일봉)",
            "weekday": "국내 거래일",
        },
        {
            "id": "dayInvSr",
            "label": "종목별 투자자",
            "sortHm": "16:00",
            "whenLabel": "16:00~19:30",
            "table": "AI_DAY_INV_SR",
            "desc": "관심∪순위 종목 ka10059 순매수·단주",
            "weekday": "국내 거래일",
        },
        {
            "id": "maTouchUniv",
            "label": "이평터치 매매대상",
            "sortHm": "16:00",
            "whenLabel": "16:00~18:30",
            "table": "AI_ALG_STK · AI_WATCH_STK",
            "desc": (
                "16시 조회순위 상위20 ∪ 거래대금 상위30(중복제거·코스피/코스닥)을 다음 거래일 관리종목(MTS01)"
                " — 매일 교체, 미청산 보유는 유지 (20이평은 당일 매매 조건)"
            ),
            "weekday": "국내 거래일",
        },
        {
            "id": "algDayReview",
            "label": "알고리즘 당일 복기",
            "sortHm": "16:05",
            "whenLabel": "16:05~16:45",
            "table": "AI_ALG_DAY_REVIEW · AI_WATCH_STK_DAY · AI_ALG_PNL_DAY",
            "desc": "운영 알고리즘별 당일 복기 + 관심종목 일별 스냅샷 + JSON·텔레그램",
            "weekday": "국내 거래일",
        },
        {
            "id": "prcRefix",
            "label": "시세 정합 보정",
            "sortHm": "16:02",
            "whenLabel": "수동 (기본 직전 거래일)",
            "table": "AI_DAY_STK_PRC · AI_MIN_STK_PRC",
            "desc": (
                "1분봉 결측 보강 후, 1분봉 종가와 어긋난 장중값 일봉을"
                " ka10086 재조회로 정정 (SMA 오염 복구)"
            ),
            "weekday": "국내 거래일",
        },
        {
            "id": "rankScore",
            "label": "관심종목 순위",
            "sortHm": "16:01",
            "whenLabel": "수급 적재 완료 직후",
            "table": "AI_RANK_SCORE",
            "desc": "조회순위 누적 종목 조건 채점 (수급 실패 1~2종목은 제외 후 계산, 3종목 이상이면 미실행)",
            "weekday": "국내 거래일",
        },
    ]

    from utils.batchRunner import getBatchRunState, isBatchRunnable

    enabledMap = getBatchSchedMap()
    runStates = getBatchRunState()
    for job in jobs:
        job["enabled"] = bool(enabledMap.get(job["id"], True))
        job["known"] = job["id"] in DEFAULT_BATCH_SCHED
        job["runnable"] = isBatchRunnable(job["id"])
        job["run"] = runStates.get(job["id"]) or {}

    jobs.sort(key=lambda j: (_hmToMin(j.get("sortHm")), j.get("id")))
    return jobs
