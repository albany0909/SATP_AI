"""배치 수동 실행 (웹 배치 스케줄 화면의 '실행' 버튼)

- 스케줄 ON/OFF·시간창과 무관하게 즉시 1회 실행한다. (장마감 누락분 복구용)
- 실제 키움 TR을 호출하므로 배치별로 동시 1회만 허용하고 진행 상태를 남긴다.
- 실행은 백그라운드 스레드 — 웹 요청은 바로 돌려주고 화면이 상태를 폴링한다.
"""

import threading
from datetime import datetime

from utils.termLog import termLog

_lock = threading.Lock()
# jobId → {state, startedAt, endedAt, msg, ok}
_runState = {}


def _runnerDayInqRank():
    from utils.dayInqRankScheduler import runDayInqRankNow

    return runDayInqRankNow()


def _runnerWatchDayPrc():
    """실계좌·관심종목 일봉 증분 (DB maxDt 이후분만)."""
    from utils.watchDayPrcLoad import loadDayPrcForWatchHolds

    return loadDayPrcForWatchHolds(refetchLastDay=True)


def _runnerRankDayPrc():
    """순위 대상 종목 1년 일봉만 보강 (순위 API 재호출 없음)."""
    from utils.rankDayPrcLoad import DEFAULT_MONTHS, loadDayPrcForRankStks
    from utils.rankScoreCalc import listRankUnivCds

    univ = listRankUnivCds()
    if not univ.get("ok"):
        return {"ok": False, "errMsg": univ.get("errMsg")}
    stkCds = univ.get("stkCds") or []
    result = loadDayPrcForRankStks(
        stkCds,
        months=DEFAULT_MONTHS,
        endDt=univ.get("tradeDt"),
        skipIfEnough=True,
    )
    if result.get("ok"):
        result["summary"] = (
            f"[순위일봉:수동] 대상 {len(stkCds)}종목 "
            f"({univ.get('univFrom')}~{univ.get('tradeDt')} 순위 누적)\n"
            + (result.get("summary") or "")
        )
    return result


def _runnerDayInvSr():
    from utils.dayInvSrScheduler import runDayInvSrNow

    return runDayInvSrNow()


def _runnerMaTouchUniv():
    from trading.maTouchUnivLoad import runMaTouchUnivNow

    return runMaTouchUnivNow()


def _runnerPrcRefix():
    """직전 거래일 시세 정합 보정 (장중값 일봉 → 실제 종가)."""
    from datetime import datetime as _dt

    from utils.dayPrcRefixLoad import prevTradingDay, refixDayPrc

    # 장 시작 전 실행이 많으므로 기본 대상은 직전 거래일
    target = prevTradingDay(_dt.now().strftime("%Y%m%d"))
    result = refixDayPrc(target)
    # 일봉이 바뀌었으면 이평터치가 캐시해 둔 SMA 를 버려야 한다
    if int(result.get("fixCnt") or 0) > 0:
        try:
            from trading.maTouchLive import invalidateSmaCache

            invalidateSmaCache(reason=f"prcRefix {target}")
            result["summary"] = (
                (result.get("summary") or "") + "\n이평터치 SMA 캐시 무효화 — 재계산"
            )
        except Exception as err:
            result["summary"] = (
                (result.get("summary") or "")
                + f"\n※ SMA 캐시 무효화 실패({err}) — 프로그램 재시작 권장"
            )
    return result


def _runnerRankScore():
    from utils.rankScoreCalc import calcRankScore

    return calcRankScore()


def _runnerNtlFirst():
    from utils.ntlFirstScheduler import runNtlFirstNow

    return runNtlFirstNow()


def _runnerUsMorningLoad():
    from utils.usDayInqRankScheduler import runUsMorningLoadNow

    return runUsMorningLoadNow()


def _runnerWatchNewsDisc():
    from utils.watchNewsDiscScheduler import runWatchNewsDiscNow

    # force=True: 수집 주기와 무관하게 즉시 1회
    return runWatchNewsDiscNow(force=True)


# 배치 ID → 수동 실행 함수. batchSchedRegistry 의 id 와 맞춰야 화면에 버튼이 뜬다.
_RUNNERS = {
    "usMorningLoad": _runnerUsMorningLoad,
    "watchNewsDisc": _runnerWatchNewsDisc,
    "dayInqRank": _runnerDayInqRank,
    "watchDayPrc": _runnerWatchDayPrc,
    "rankDayPrc": _runnerRankDayPrc,
    "ntlFirst": _runnerNtlFirst,
    "dayInvSr": _runnerDayInvSr,
    "rankScore": _runnerRankScore,
    "maTouchUniv": _runnerMaTouchUniv,
    "prcRefix": _runnerPrcRefix,
}


def isBatchRunnable(jobId):
    return str(jobId or "") in _RUNNERS


def getBatchRunState(jobId=None):
    """단일 또는 전체 실행 상태 사본."""
    with _lock:
        if jobId:
            return dict(_runState.get(str(jobId)) or {})
        return {k: dict(v) for k, v in _runState.items()}


def _setState(jobId, **kv):
    with _lock:
        cur = dict(_runState.get(jobId) or {})
        cur.update(kv)
        _runState[jobId] = cur


def _work(jobId, fn):
    try:
        result = fn() or {}
        ok = bool(result.get("ok"))
        msg = (
            result.get("summary")
            or result.get("errMsg")
            or ("완료" if ok else "실패")
        )
        _setState(
            jobId,
            state="done" if ok else "fail",
            ok=ok,
            msg=str(msg)[:1200],
            endedAt=datetime.now().strftime("%H:%M:%S"),
        )
        termLog(f"[batchRun] {jobId} {'완료' if ok else '실패'}")
    except Exception as err:
        _setState(
            jobId,
            state="fail",
            ok=False,
            msg=f"실행 오류: {err}",
            endedAt=datetime.now().strftime("%H:%M:%S"),
        )
        termLog(f"[batchRun] {jobId} 실행 오류: {err}")


def runBatchJob(jobId):
    """
    배치 1회 수동 실행 시작. 즉시 반환하며 진행은 getBatchRunState 로 확인.
    반환: {ok, jobId, state, errMsg?}
    """
    code = str(jobId or "").strip()
    fn = _RUNNERS.get(code)
    if fn is None:
        return {"ok": False, "errMsg": f"수동 실행을 지원하지 않는 배치: {code}"}

    with _lock:
        cur = _runState.get(code) or {}
        if cur.get("state") == "running":
            return {
                "ok": False,
                "errMsg": "이미 실행 중입니다.",
                "jobId": code,
                "state": "running",
            }
        _runState[code] = {
            "state": "running",
            "ok": None,
            "msg": "실행 중…",
            "startedAt": datetime.now().strftime("%H:%M:%S"),
            "endedAt": "",
        }

    termLog(f"[batchRun] {code} 수동 실행 시작")
    th = threading.Thread(
        target=_work, args=(code, fn), name=f"batchRun-{code}", daemon=True
    )
    th.start()
    return {"ok": True, "jobId": code, "state": "running"}
