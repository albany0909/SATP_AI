"""
장마감 시 실시간조회순위(당일누적) 자동 적재 스케줄러.
- 거래일(월~금·공휴일 제외)만 적재
- mkhr 종료시각(mktCloseHm) 이후 ~ 18:00 사이 1일 1회
- 순위 적재 성공 후 선정 종목 1년 일봉 + 당일 1분봉(장중 15분 편입 포함) 백그라운드 적재
"""

import threading
import time
from datetime import datetime

from api.getInqRank import QRY_TP_DAY
from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from config.settingsStore import getMktHours
from telegram.telSend import sendTelegramMsg
from utils.dayInqRankLoad import loadDayInqRank
from utils.rankDayPrcLoad import DEFAULT_MONTHS, loadDayPrcForRankStks
from utils.rankMinPrcLoad import collectCloseMinStkCds, loadMinPrcForRankStks
from utils.termLog import termLog

# 장마감 후 재시도 허용 상한 (정규장 이후 여유)
_LOAD_DEADLINE_HM = "18:00"

_schedulerStarted = False
# 성공 적재한 일자 YYYYMMDD
_loadedYmd = ""
# 분 단위 중복 시도 방지
_lastAttemptKey = ""
# 직전 거래일 순위로 관심을 맞춘 키 (오늘|순위일)
_appliedWatchKey = ""


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="batch")
    except Exception as err:
        termLog(f"[inqRankSched] 텔레그램 전송 실패: {err}")


def _loadDayPrcBg(stkCds, tradeDt):
    """순위+15분편입 일봉·당일 1분봉 — 백그라운드 후 관심종목 반영."""
    global _appliedWatchKey
    try:
        # 관심 교체 전에 15분 편입 SRC/NOTE 를 읽어야 당일 추가분이 남는다
        collected = collectCloseMinStkCds(tradeDt, rankStkCds=stkCds)
        allCds = collected.get("stkCds") or list(stkCds or [])
        extraN = int(collected.get("extra15mCnt") or 0)
        if extraN:
            termLog(
                f"[inqRankSched] 15분편입 추가 {extraN}종 포함 "
                f"→ 일봉/1분봉 대상 {len(allCds)}종"
            )

        result = loadDayPrcForRankStks(
            allCds,
            months=DEFAULT_MONTHS,
            endDt=tradeDt,
            skipIfEnough=True,
            onProgress=None,
            # 장중에 저장된 미완성 당일봉을 실제 종가로 덮어쓴다
            refetchLastDay=True,
        )
        _notify(result.get("summary") or str(result))

        # 일봉 확보 후: 순위→관심종목 (코스피/코스닥 + 양봉)
        from utils.rankToWatchLoad import syncWatchFromRankClose

        watchRes = syncWatchFromRankClose(tradeDt, qryTp=QRY_TP_DAY)
        if watchRes.get("ok"):
            _appliedWatchKey = (
                f"{datetime.now().strftime('%Y%m%d')}|"
                f"{watchRes.get('tradeDt') or tradeDt}"
            )
        if watchRes.get("summary"):
            _notify(watchRes.get("summary"))
        elif not watchRes.get("ok"):
            _notify(f"[관심종목실패] {watchRes.get('errMsg')}")

        # 일봉 적재 후 EMA 대소 포함 순위 요약
        from utils.rankEmaUtil import formatRankEmaSummaryFromDb

        emaSum = formatRankEmaSummaryFromDb(tradeDt, qryTp=QRY_TP_DAY, topN=20)
        if emaSum:
            _notify(emaSum)

        # 일봉·관심 반영 후 당일 1분봉 (검증 시 매수·매도 시각용)
        minRes = loadMinPrcForRankStks(
            allCds,
            tradeDt,
            skipIfEnough=True,
            onProgress=None,
        )
        minSum = minRes.get("summary") or str(minRes)
        if extraN:
            minSum += f"\n15분편입 추가: {extraN}종"
        _notify(minSum)

        # 실계좌·관심: DB 마지막일 이후분만 증분 적재 (차트용)
        try:
            from utils.watchDayPrcLoad import loadDayPrcForWatchHolds

            wres = loadDayPrcForWatchHolds(endDt=tradeDt, refetchLastDay=True)
            if wres.get("summary"):
                termLog(wres.get("summary"))
        except Exception as err:
            termLog(f"[inqRankSched] 관심일봉 증분 실패: {err}")
    except Exception as err:
        _notify(f"[순위일봉실패] {err}")


def _tryLoadCloseRank(now=None):
    """
    조건 충족 시 당일누적 순위 적재 + 일봉 연계.
    반환: True=이번 틱에서 처리(성공/실패 메시지 전송), False=스킵
    """
    global _loadedYmd, _lastAttemptKey

    dt = now or datetime.now()
    ymd = dt.strftime("%Y%m%d")
    curHm = dt.strftime("%H:%M")

    # 이미 오늘 성공 적재
    if _loadedYmd == ymd:
        return False

    if not isBatchSchedEnabled("dayInqRank"):
        return False

    # 주말·공휴일은 API 적재 생략. 관심은 직전 거래일 순위로 맞춘다.
    if not isTradingDay(dt):
        return False

    _, closeHm = getMktHours()
    if curHm < closeHm or curHm > _LOAD_DEADLINE_HM:
        return False

    # 장중 15분 폴이 AI_DAY_INQ_RANK 에 먼저 쓸 수 있어
    # hasDayInqRank 로는 스킵하지 않는다. 장마감에 최종 순위를 다시 받고
    # 일봉/1분봉은 이미 있으면 스킵한다. 프로세스 내 _loadedYmd 가 중복 방지.

    # 같은 분에는 1회만 시도
    attemptKey = f"{ymd}|{curHm}"
    if attemptKey == _lastAttemptKey:
        return False
    _lastAttemptKey = attemptKey

    termLog(f"[inqRankSched] 장마감 순위 적재 시도 — close={closeHm} now={curHm}")
    result = loadDayInqRank(qryTp=QRY_TP_DAY)
    if result.get("ok"):
        _loadedYmd = ymd
        msg = (
            f"[순위적재] ({result.get('modeLabel')}) 완료\n"
            f"구분: {result.get('qryLabel')}\n"
            f"일자: {result.get('tradeDt')}\n"
            f"건수: {result.get('upsertCnt')}건"
            f" (업종 {result.get('indFilled') or 0}건"
            f" / 테마매핑 {result.get('themeFilled') or 0}건)\n"
            f"→ 선정 {len(result.get('stkCds') or [])}종목 "
            f"{DEFAULT_MONTHS}개월 일봉 + 당일 1분봉(15분편입 포함) "
            f"적재 시작(백그라운드)\n"
            f"→ 완료 후 코스피/코스닥 양봉만 관심종목 반영"
        )
        _notify(msg)

        stkCds = result.get("stkCds") or []
        th = threading.Thread(
            target=_loadDayPrcBg,
            args=(stkCds, result.get("tradeDt") or ymd),
            name="rankDayPrcLoad",
            daemon=True,
        )
        th.start()
        return True

    failMsg = (
        f"[순위적재실패] {result.get('errMsg')}\n"
        f"(거래일·장마감 후 {_LOAD_DEADLINE_HM}까지 재시도)"
    )
    _notify(failMsg)
    return True


def runDayInqRankNow():
    """
    수동 실행 — 시간창·거래일·ON/OFF를 따지지 않고 지금 적재한다.
    화면에서 누른 경우이므로 일봉·관심종목 연계까지 동기로 끝내고 결과를 돌려준다.
    """
    global _loadedYmd

    result = loadDayInqRank(qryTp=QRY_TP_DAY)
    if not result.get("ok"):
        return {"ok": False, "errMsg": result.get("errMsg") or "순위 적재 실패"}

    tradeDt = result.get("tradeDt") or datetime.now().strftime("%Y%m%d")
    _loadedYmd = tradeDt
    summary = (
        f"[순위적재:수동] ({result.get('modeLabel')}) 완료\n"
        f"일자: {tradeDt} · {result.get('qryLabel')}\n"
        f"건수: {result.get('upsertCnt')}건 · 선정 {len(result.get('stkCds') or [])}종목"
    )
    _notify(summary)

    stkCds = result.get("stkCds") or []
    _loadDayPrcBg(stkCds, tradeDt)
    return {
        "ok": True,
        "tradeDt": tradeDt,
        "upsertCnt": result.get("upsertCnt"),
        "stkCnt": len(stkCds),
        "summary": summary,
    }


def _tryApplyLastRankWatch(now=None):
    """
    당일 장마감 순위가 없으면
    휴일순위 → 직전영업일 → 실시간 조회로 관심을 맞춘다.
    같은 날·같은 기준일은 1회만.
    """
    global _appliedWatchKey

    if not isBatchSchedEnabled("dayInqRank"):
        return False
    dt = now or datetime.now()
    today = dt.strftime("%Y%m%d")
    from utils.dayInqRankStore import resolveInqRankTradeDt

    resolved = resolveInqRankTradeDt(today, QRY_TP_DAY)
    usedDt = str(resolved.get("tradeDt") or "")
    # 거래일 당일 적재분은 장마감 적재→일봉 경로가 담당
    if usedDt == today and not resolved.get("fallback"):
        return False
    key = f"{today}|{usedDt or 'live'}"
    if key == _appliedWatchKey:
        return False
    _appliedWatchKey = key
    src = resolved.get("source") or ("live" if resolved.get("needLive") else "")
    termLog(
        f"[inqRankSched] 당일 순위 없음 — {src or '대체'} "
        f"{usedDt or '실시간조회'} 로 관심 반영"
    )
    from utils.rankToWatchLoad import syncWatchFromRankClose

    watchRes = syncWatchFromRankClose(today, qryTp=QRY_TP_DAY)
    if watchRes.get("summary"):
        _notify(watchRes.get("summary"))
    elif not watchRes.get("ok"):
        _notify(f"[관심종목실패] {watchRes.get('errMsg')}")
        _appliedWatchKey = ""
        return False
    return True


def _schedulerLoop():
    """20초마다 장마감 적재·휴장 시 직전 순위 관심 반영."""
    while True:
        try:
            _tryLoadCloseRank()
            _tryApplyLastRankWatch()
        except Exception as err:
            termLog(f"[inqRankSched] 루프 오류: {err}")
        time.sleep(20)


def startDayInqRankScheduler():
    """백그라운드 스레드로 장마감 순위 적재 스케줄러 기동."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    th = threading.Thread(target=_schedulerLoop, name="dayInqRankSched", daemon=True)
    th.start()
    _, closeHm = getMktHours()
    termLog(
        f"[inqRankSched] 기동 — 거래일 {closeHm}~{_LOAD_DEADLINE_HM} "
        f"당일누적(ka00198) → AI_DAY_INQ_RANK "
        f"+ 선정종목 {DEFAULT_MONTHS}개월 일봉·당일 1분봉 "
        f"/ 휴장 시 휴일순위→직전영업일→실시간조회"
    )
