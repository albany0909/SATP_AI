"""
이평터치용 일봉 보장.
신규 관심 편입 시 SMA(최대 20)를 계산할 수 있게 ka10086 을 한 종목씩 조회한다.
모의는 TR당 1초 1회 — 여러 종목을 동시에 치지 않는다.
"""

import time
from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# SMA20 + 휴장 여유. 차트 1년치까지는 장마감 적재가 담당
_SMA_MONTHS = 6
_DEFAULT_MIN_BARS = 30
# 장마감 후 이 시간이 지나야 당일 일봉을 확정으로 본다 (동시호가 정정 여유)
_CLOSE_BUF_MIN = 10


def _countBarsBefore(stkCd, beforeDt):
    """beforeDt 미만 확정 일봉 수 (당일 미완성 봉 제외)."""
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP=%s AND TRADE_DT < %s
                """,
                (str(stkCd), INDC_TP_QTY, str(beforeDt)),
            )
            row = cur.fetchone() or {}
            return int(row.get("cnt") or 0)
    finally:
        conn.close()


def _capEndDtBeforeClose(endDt):
    """
    장마감 전에는 당일 봉을 받지 않는다.
    장중 ka10086 은 미완성 일봉(그 시점까지 O/H/L/C)을 주는데, 그게 저장되면
    다음날 SMA 가 틀어진다. SMA 는 전일까지 확정 종가만 쓰므로 손실이 없다.
    """
    ymd = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    now = datetime.now()
    if ymd != now.strftime("%Y%m%d"):
        return ymd
    try:
        from config.settingsStore import getMktHours

        _, closeHm = getMktHours()
    except Exception:
        closeHm = "15:30"
    # 동시호가 정정 여유를 두고 마감 10분 뒤부터 당일 봉을 신뢰
    try:
        closeAt = datetime.strptime(
            f"{ymd} {closeHm or '15:30'}", "%Y%m%d %H:%M"
        ) + timedelta(minutes=_CLOSE_BUF_MIN)
    except ValueError:
        closeAt = datetime.strptime(f"{ymd} 15:40", "%Y%m%d %H:%M")
    if now >= closeAt:
        return ymd
    try:
        from utils.dayPrcRefixLoad import prevTradingDay

        prev = prevTradingDay(ymd)
    except Exception:
        prev = None
    if not prev:
        return ymd
    termLog(f"[dayPrcSma] 장중 — 당일봉 제외하고 {prev} 까지만 적재")
    return prev


def ensureDayPrcForSma(stkCds, minBars=_DEFAULT_MIN_BARS, endDt=None):
    """
    종목 목록을 한 종목씩 순회하며 일봉이 부족하면 ka10086 적재.
    이미 minBars 이상이면 API 를 치지 않는다.
    장중 호출이면 당일 미완성 봉을 저장하지 않도록 전 거래일까지만 받는다.
    반환: {ok, stkCnt, loadedCnt, skipCnt, failCnt}
    """
    today = _capEndDtBeforeClose(endDt)
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return {
            "ok": True,
            "stkCnt": 0,
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
        }

    need = max(int(minBars or _DEFAULT_MIN_BARS), 20)
    startDt = (
        datetime.strptime(today, "%Y%m%d") - timedelta(days=_SMA_MONTHS * 30 + 5)
    ).strftime("%Y%m%d")

    missing = []
    skipCnt = 0
    for cd in codes:
        cnt = _countBarsBefore(cd, today)
        if cnt >= need:
            skipCnt += 1
            continue
        missing.append(cd)

    if not missing:
        return {
            "ok": True,
            "stkCnt": len(codes),
            "loadedCnt": 0,
            "skipCnt": skipCnt,
            "failCnt": 0,
        }

    session = acquireKiwoomSession()
    if session is None:
        termLog("[dayPrcSma] 키움 토큰 없음 — 일봉 보강 생략")
        return {
            "ok": False,
            "errMsg": "키움 토큰 없음",
            "stkCnt": len(codes),
            "loadedCnt": 0,
            "skipCnt": skipCnt,
            "failCnt": len(missing),
        }

    mode, hostUrl, token, tokenType = session
    isPaper = mode == "paper"
    waitSec = 1.05 if isPaper else 0.35
    termLog(
        f"[dayPrcSma] 일봉 부족 {len(missing)}종 — 한 종목씩 조회 "
        f"(필요 {need}봉, {startDt}~{today})"
    )

    loadedCnt = 0
    failCnt = 0
    called = 0
    for cd in missing:
        # 종목 사이 대기. 연속조회 페이지 대기는 fetchDayStkPrcRange 가 지킨다.
        if called > 0:
            time.sleep(waitSec)
        called += 1
        fetched = fetchDayStkPrcRange(
            hostUrl,
            token,
            cd,
            startDt,
            today,
            indcTp=INDC_TP_QTY,
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not fetched.get("ok"):
            failCnt += 1
            termLog(f"[dayPrcSma] fail {cd}: {fetched.get('errMsg')}")
            continue
        rows = fetched.get("rows") or []
        if not rows:
            failCnt += 1
            termLog(f"[dayPrcSma] empty {cd}")
            continue
        saved = upsertDayStkPrcRows(rows)
        if not saved.get("ok"):
            failCnt += 1
            termLog(f"[dayPrcSma] db fail {cd}: {saved.get('errMsg')}")
            continue
        loadedCnt += 1
        termLog(
            f"[dayPrcSma] ok {cd} {saved.get('upsertCnt')}건 "
            f"({called}/{len(missing)})"
        )

    termLog(
        f"[dayPrcSma] 완료 적재 {loadedCnt} / 스킵 {skipCnt} / 실패 {failCnt}"
    )
    return {
        "ok": failCnt == 0,
        "stkCnt": len(codes),
        "loadedCnt": loadedCnt,
        "skipCnt": skipCnt,
        "failCnt": failCnt,
    }
