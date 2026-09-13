"""순위 선정 종목 → 최근 N개월 일봉(AI_DAY_STK_PRC) 적재"""

import time
from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 기본 적재 기간(개월) — 150·200 이평까지 그리려면 1년치가 필요하다
DEFAULT_MONTHS = 12
# 이미 충분히 있으면 스킵 (1년 ≈ 245거래일 — 여유를 두고 200봉)
_MIN_EXIST_BARS = 200
# 시작일 이 일수 안에 첫 봉이 있으면 과거 구간은 채워진 것으로 본다 (상장일·휴장 여유)
_BACKFILL_TOL_DAYS = 10


def _pickSession():
    # 로그인 토큰 → 프로세스 캐시 → .env 앱키 발급 순.
    # 웹 서버는 main.py와 별 프로세스라 runtimeState가 비어 있어 발급 경로가 필요하다.
    return acquireKiwoomSession()


def _rangeYmd(months=DEFAULT_MONTHS, endDt=None):
    """종료일 기준 months개월 전 ~ 종료일."""
    end = datetime.strptime(endDt, "%Y%m%d") if endDt else datetime.now()
    # 대략 30일*개월 (달력 기준)
    start = end - timedelta(days=int(months) * 30 + 5)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _existDayBarStat(stkCd, startDt, endDt):
    """기간 내 일봉 건수·최초·최종 일자. 증분 조회 시작점 판단용."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt,
                       MIN(TRADE_DT) AS minDt,
                       MAX(TRADE_DT) AS maxDt
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND TRADE_DT BETWEEN %s AND %s
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            row = cur.fetchone() or {}
            return {
                "cnt": int(row.get("cnt") or 0),
                "minDt": str(row.get("minDt") or ""),
                "maxDt": str(row.get("maxDt") or ""),
            }
    finally:
        conn.close()


def _lastTradingDayOnOrBefore(ymd):
    """해당일 포함 직전 국내 거래일. 주말·공휴일에 헛조회하지 않도록."""
    from config.krHoliday import isTradingDay

    try:
        dt = datetime.strptime(str(ymd), "%Y%m%d")
    except ValueError:
        return str(ymd)
    for _ in range(12):
        if isTradingDay(dt):
            return dt.strftime("%Y%m%d")
        dt -= timedelta(days=1)
    return str(ymd)


def loadDayPrcForRankStks(
    stkCds,
    months=DEFAULT_MONTHS,
    endDt=None,
    skipIfEnough=True,
    onProgress=None,
    refetchLastDay=False,
):
    """
    순위 종목 목록에 대해 최근 months개월 일봉 적재.
    반환: {ok, stkCnt, loadedCnt, skipCnt, failCnt, upsertCnt, startDt, endDt, fails, errMsg?}
    """
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)

    if not codes:
        return {
            "ok": False,
            "errMsg": "일봉 적재 대상 종목이 없습니다.",
            "stkCnt": 0,
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
        }

    session = _pickSession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "키움 토큰 없음 — login paper|real 또는 .env 앱키 확인",
            "stkCnt": len(codes),
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
        }

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    isPaper = mode == "paper"
    startDt, endYmd = _rangeYmd(months=months, endDt=endDt)
    waitSec = 1.05 if isPaper else 0.35

    termLog(
        f"[rankDayPrc] ({modeLabel}) {len(codes)}종목 "
        f"{startDt}~{endYmd} ({months}개월) 일봉 적재 시작"
    )
    if onProgress:
        onProgress(
            f"[순위일봉] ({modeLabel}) {len(codes)}종목 "
            f"{startDt}~{endYmd} 적재 시작"
        )

    loadedCnt = 0
    skipCnt = 0
    failCnt = 0
    upsertCnt = 0
    incCnt = 0
    fails = []
    # 마지막 봉이 이 날짜까지 있으면 더 받을 게 없다
    lastTdYmd = _lastTradingDayOnOrBefore(endYmd)
    # 상장일이 늦거나 휴장이 겹쳐 시작일 정각에 봉이 없을 수 있어 여유를 둔다
    tolStartDt = (
        datetime.strptime(startDt, "%Y%m%d") + timedelta(days=_BACKFILL_TOL_DAYS)
    ).strftime("%Y%m%d")

    called = 0
    for stkCd in codes:
        fetchStart = startDt
        isInc = False

        if skipIfEnough:
            stat = _existDayBarStat(stkCd, startDt, endYmd)
            # 과거 구간까지 채워진 종목만 증분 대상 — 아니면 1년 전체를 다시 받아야 한다
            backfillDone = stat["cnt"] >= _MIN_EXIST_BARS or (
                stat["minDt"] and stat["minDt"] <= tolStartDt
            )
            # 장중에 들어온 미완성 당일봉이 그대로 굳지 않게, 장마감 배치는
            # 마지막 거래일 봉을 한 번 더 받아 덮어쓴다 (refetchLastDay=True)
            isLastDayOnly = (
                refetchLastDay
                and stat["maxDt"]
                and str(stat["maxDt"]) == str(lastTdYmd)
            )
            if backfillDone and stat["maxDt"] >= lastTdYmd and not isLastDayOnly:
                skipCnt += 1
                termLog(
                    f"[rankDayPrc] skip {stkCd} "
                    f"(기존 {stat['cnt']}봉 · {stat['maxDt']}까지 최신)"
                )
                continue
            if backfillDone and stat["maxDt"]:
                # 마지막 적재일부터 재조회 — 그날 봉 정정분도 함께 반영
                fetchStart = stat["maxDt"]
                isInc = True

        # 조회 한도는 실제 호출 사이에만 지키면 된다 (스킵은 DB만 보므로 대기 불필요)
        if called > 0:
            time.sleep(waitSec)
        called += 1

        fetched = fetchDayStkPrcRange(
            hostUrl,
            token,
            stkCd,
            fetchStart,
            endYmd,
            indcTp=INDC_TP_QTY,
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not fetched.get("ok"):
            failCnt += 1
            err = fetched.get("errMsg") or "조회실패"
            fails.append(f"{stkCd}:{err}")
            termLog(f"[rankDayPrc] fail {stkCd}: {err}")
            continue

        rows = fetched.get("rows") or []
        if not rows:
            skipCnt += 1
            termLog(f"[rankDayPrc] empty {stkCd}")
            continue

        saved = upsertDayStkPrcRows(rows)
        if not saved.get("ok"):
            failCnt += 1
            err = saved.get("errMsg") or "DB실패"
            fails.append(f"{stkCd}:{err}")
            termLog(f"[rankDayPrc] db fail {stkCd}: {err}")
            continue

        loadedCnt += 1
        if isInc:
            incCnt += 1
        upsertCnt += int(saved.get("upsertCnt") or 0)
        termLog(
            f"[rankDayPrc] ok {stkCd} {'증분' if isInc else '전체'} "
            f"{rows[0]['TRADE_DT']}~{rows[-1]['TRADE_DT']} "
            f"{saved.get('upsertCnt')}건 ({called}/{len(codes)} 호출)"
        )

    summary = (
        f"[순위일봉] ({modeLabel}) 완료\n"
        f"기간: {startDt} ~ {endYmd}\n"
        f"대상: {len(codes)}종목 / 적재 {loadedCnt}"
        f"(증분 {incCnt} / 전체 {loadedCnt - incCnt}) / "
        f"스킵 {skipCnt} / 실패 {failCnt}\n"
        f"일봉UPSERT: {upsertCnt}건"
    )
    if fails:
        summary += "\n실패예: " + "; ".join(fails[:5])
        if len(fails) > 5:
            summary += f" …외 {len(fails) - 5}건"

    termLog(summary)
    if onProgress:
        onProgress(summary)

    return {
        "ok": failCnt == 0 or loadedCnt > 0 or skipCnt > 0,
        "stkCnt": len(codes),
        "loadedCnt": loadedCnt,
        "incCnt": incCnt,
        "skipCnt": skipCnt,
        "failCnt": failCnt,
        "upsertCnt": upsertCnt,
        "startDt": startDt,
        "endDt": endYmd,
        "fails": fails,
        "modeLabel": modeLabel,
        "summary": summary,
    }
