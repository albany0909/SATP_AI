"""
거래일 16:00 종목별투자자(ka10059) 자동 적재.
- 대상: 관심종목 ∪ 실시간조회순위(당일누적) ∪ 운영 알고리즘 관리종목
- 순매수 · 수량 · 단주 (HTS 종목별투자자와 동일)
- 16:00~19:30 1일 1회, 실패 시 잔여 종목만 재시도
- 적재 후 관심종목 순위 점수(AI_RANK_SCORE)를 이어서 계산한다.
  실패 1~2종목은 건너뛰고 적재된 종목만 순위를 매긴다.
  실패가 3종목 이상이면 순위 계산은 하지 않는다.
"""

import threading
import time
from datetime import datetime

from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from telegram.telSend import sendTelegramMsg
from utils.dayInvSrLoad import collectInvSrUniverse, loadInvSrUniverse
from utils.dayInvSrStore import listLoadedStkCdsByDt
from utils.termLog import termLog

_LOAD_START_HM = "16:00"
_LOAD_DEADLINE_HM = "19:30"

_schedulerStarted = False
_loadedYmd = ""
_lastAttemptKey = ""
_running = False
_scoreRunningYmd = ""
# 수급 실패가 이 수 이상이면 순위 점수를 돌리지 않는다 (3종목부터 중단)
_RANK_FAIL_STOP = 3


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="batch")
    except Exception as err:
        termLog(f"[invSrSched] 텔레그램 전송 실패: {err}")


def _canRunRankAfterInvSr(failCnt):
    """수급 실패 종목이 중단 기준 미만일 때만 순위 계산."""
    try:
        n = int(failCnt or 0)
    except (TypeError, ValueError):
        n = 0
    return n < _RANK_FAIL_STOP


def _runRankScoreBg(ymd, skipStkCds=None):
    """수급 적재 완료 후 순위 점수 계산 — 적재 스레드를 막지 않게 별도 스레드."""
    global _scoreRunningYmd
    try:
        from utils.rankScoreCalc import calcRankScore

        result = calcRankScore(tradeDt=ymd, skipStkCds=skipStkCds)
        if result.get("ok"):
            _notify(result.get("summary") or f"[관심종목순위] 완료 {ymd}")
        else:
            _notify(f"[관심종목순위실패] {result.get('errMsg')}")
    except Exception as err:
        _notify(f"[관심종목순위실패] {err}")
    finally:
        _scoreRunningYmd = ""


def _maybeRunRankScore(ymd, skipStkCds=None, failCnt=0):
    """
    수급 적재 후 점수 계산.
    실패 1~2종목은 제외하고 진행하고, 3종목 이상이면 계산하지 않는다.
    이미 계산된 날이면 건너뛰므로 재기동해도 중복 계산되지 않는다.
    """
    global _scoreRunningYmd
    if not isBatchSchedEnabled("rankScore"):
        return False
    if not _canRunRankAfterInvSr(failCnt):
        termLog(
            f"[rankScoreSched] 수급 실패 {failCnt}종목 "
            f"(기준 {_RANK_FAIL_STOP}종목 이상) — 점수 계산 생략 {ymd}"
        )
        return False
    if _scoreRunningYmd == ymd:
        return False
    try:
        from api.getInqRank import QRY_TP_DAY
        from utils.rankScoreStore import hasRankScore

        if hasRankScore(ymd, "001"):
            return False
        # 순위 스냅샷이 없으면 대상 종목을 못 잡으므로 계산하지 않는다
        from utils.dayInqRankStore import resolveInqRankTradeDt

        resolved = resolveInqRankTradeDt(ymd, QRY_TP_DAY)
        if not resolved.get("ok"):
            termLog(f"[rankScoreSched] 조회순위 미적재 — 점수 계산 보류 {ymd}")
            return False
        if resolved.get("fallback"):
            ymd = resolved.get("tradeDt")
            termLog(
                f"[rankScoreSched] 당일 순위 없음 — 직전 거래일 {ymd} 로 점수 계산"
            )
    except Exception as err:
        termLog(f"[rankScoreSched] 사전 점검 실패: {err}")
        return False

    _scoreRunningYmd = ymd
    termLog(
        f"[rankScoreSched] 순위 점수 계산 시작 {ymd}"
        + (f" · 제외 {len(skipStkCds or [])}종목" if skipStkCds else "")
    )
    th = threading.Thread(
        target=_runRankScoreBg,
        args=(ymd, skipStkCds),
        name="rankScoreCalc",
        daemon=True,
    )
    th.start()
    return True


def _tryLoadInvSr(now=None):
    global _loadedYmd, _lastAttemptKey, _running

    dt = now or datetime.now()
    ymd = dt.strftime("%Y%m%d")
    curHm = dt.strftime("%H:%M")

    if _loadedYmd == ymd:
        return False
    if _running:
        return False
    if not isBatchSchedEnabled("dayInvSr"):
        return False
    if not isTradingDay(dt):
        return False
    if curHm < _LOAD_START_HM or curHm > _LOAD_DEADLINE_HM:
        return False

    univ = collectInvSrUniverse(ymd)
    loaded = listLoadedStkCdsByDt(ymd)
    univCds = {x["stkCd"] for x in univ}
    if univCds and univCds.issubset(loaded):
        _loadedYmd = ymd
        termLog(f"[invSrSched] 이미 적재됨 — {ymd} {len(univCds)}종목")
        # 재기동 등으로 점수 계산만 빠진 경우를 여기서 보완
        _maybeRunRankScore(ymd)
        return False

    attemptKey = f"{ymd}|{curHm}"
    if attemptKey == _lastAttemptKey:
        return False
    _lastAttemptKey = attemptKey

    termLog(
        f"[invSrSched] 종목별투자자 적재 시도 — "
        f"{_LOAD_START_HM}~{_LOAD_DEADLINE_HM} now={curHm} 대상={len(univ)}"
    )
    _running = True
    try:
        result = loadInvSrUniverse(tradeDt=ymd, skipLoaded=True)
    finally:
        _running = False

    failCnt = int(result.get("failCnt") or 0)
    failCds = result.get("failCds") or []

    if result.get("ok"):
        _loadedYmd = ymd
        _notify(result.get("summary") or f"[종목별투자자] 완료 {ymd}")
        _maybeRunRankScore(ymd, failCnt=0)
        return True

    if result.get("partial"):
        # 실패 1~2종목: 잔여 재시도는 하되 순위는 적재분으로 먼저 매긴다
        if _canRunRankAfterInvSr(failCnt):
            _notify(
                (result.get("summary") or "[종목별투자자] 부분실패")
                + f"\n※ 실패 {failCnt}종목 제외하고 관심종목 순위를 계산합니다."
                + f"\n(거래일·{_LOAD_DEADLINE_HM}까지 잔여 재시도)"
            )
            _maybeRunRankScore(ymd, skipStkCds=failCds, failCnt=failCnt)
        else:
            _notify(
                (result.get("summary") or "[종목별투자자] 부분실패")
                + f"\n(거래일·{_LOAD_DEADLINE_HM}까지 잔여 재시도)"
                + f"\n※ 수급 실패 {failCnt}종목(기준 {_RANK_FAIL_STOP} 이상)"
                + " — 관심종목 순위 점수 계산은 하지 않습니다."
            )
        return True

    _notify(
        f"[종목별투자자적재실패] {result.get('errMsg')}\n"
        f"(거래일·{_LOAD_DEADLINE_HM}까지 재시도)"
    )
    return True


def runDayInvSrNow(tradeDt=None):
    """
    수동 실행 — 시간창·ON/OFF 무시.
    실패 1~2종목은 제외하고 순위 점수를 이어서 계산한다.
    실패 3종목 이상이면 순위 계산은 하지 않는다.
    """
    global _loadedYmd

    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    if _running:
        return {"ok": False, "errMsg": "이미 수급 적재가 진행 중입니다."}

    result = loadInvSrUniverse(tradeDt=ymd, skipLoaded=True)
    failCnt = int(result.get("failCnt") or 0)
    failCds = result.get("failCds") or []
    loadedOk = bool(result.get("ok") or result.get("partial"))
    if not loadedOk:
        return {
            "ok": False,
            "errMsg": result.get("errMsg") or result.get("summary") or "수급 적재 실패",
        }

    if result.get("ok"):
        _loadedYmd = ymd
    summary = result.get("summary") or f"[종목별투자자:수동] 완료 {ymd}"
    _notify(summary)

    scoreMsg = ""
    if not _canRunRankAfterInvSr(failCnt):
        scoreMsg = (
            f"[관심종목순위] 수급 실패 {failCnt}종목"
            f"(기준 {_RANK_FAIL_STOP} 이상) — 계산하지 않습니다."
        )
        _notify(scoreMsg)
        return {
            "ok": False,
            "tradeDt": ymd,
            "failCnt": failCnt,
            "errMsg": scoreMsg,
            "summary": summary + "\n\n" + scoreMsg,
        }

    if isBatchSchedEnabled("rankScore"):
        try:
            from utils.rankScoreCalc import calcRankScore

            scoreRes = calcRankScore(tradeDt=ymd, skipStkCds=failCds)
            scoreMsg = scoreRes.get("summary") or scoreRes.get("errMsg") or ""
            if scoreRes.get("ok"):
                _notify(scoreMsg)
            else:
                _notify(f"[관심종목순위실패] {scoreMsg}")
        except Exception as err:
            scoreMsg = f"[관심종목순위실패] {err}"
            _notify(scoreMsg)
    return {
        "ok": True,
        "tradeDt": ymd,
        "failCnt": failCnt,
        "summary": summary + (("\n\n" + scoreMsg) if scoreMsg else ""),
    }


def _schedulerLoop():
    while True:
        try:
            _tryLoadInvSr()
        except Exception as err:
            termLog(f"[invSrSched] 루프 오류: {err}")
        time.sleep(30)


def startDayInvSrScheduler():
    """백그라운드 종목별투자자 일 적재 스케줄러."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    th = threading.Thread(target=_schedulerLoop, name="dayInvSrSched", daemon=True)
    th.start()
    termLog(
        f"[invSrSched] 기동 — 거래일 {_LOAD_START_HM}~{_LOAD_DEADLINE_HM} "
        f"관심∪순위 → AI_DAY_INV_SR (ka10059 순매수/단주) "
        f"→ 완료 시 AI_RANK_SCORE 순위 점수"
    )
