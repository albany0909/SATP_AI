"""
시세 정합 보정 — 지정 거래일의 1분봉 결측 보강 + 장중값으로 굳은 일봉 재적재.

왜 필요한가
- 장중에 `ka10086` 을 호출하면 **당일 미완성 일봉**(그 시점까지의 O/H/L/C)이 저장된다.
- 장마감 일봉 배치는 "이미 최신일까지 있으면 스킵"이라, 장중에 들어온 그 봉이
  실제 종가로 갱신되지 않고 남을 수 있다. 이 상태로는 다음날 SMA 가 전부 틀어진다.
- 1분봉 마지막 봉 종가는 실제 종가(동시호가 반영)라서, 이를 기준으로 일봉이
  장중값인지 판별할 수 있다.

보정 순서
  1) 그 날 매매 유니버스의 1분봉 결측 보강 (판별 기준을 먼저 확보)
  2) 1분봉 종가와 어긋난 일봉만 ka10086 단일일자 재조회로 덮어쓰기
  3) 재판정 후 요약

모의는 TR 1개당 1초 1회 — 종목 사이 대기를 지킨다.
"""

import time
from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from config.krHoliday import isTradingDay
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 일봉 종가와 1분봉 마지막 종가 차이가 이 %를 넘으면 장중값으로 본다
DEFAULT_TOL_PCT = 0.3
# 1분봉이 이보다 적은 날은 비교 기준으로 쓰지 않는다 (반쪽 데이터)
_MIN_BARS_PER_DAY = 50

_MISMATCH_SQL = """
SELECT d.STK_CD, d.CLOSE_PRC dayClose, d.LOW_PRC dayLo,
       m.lastClose, m.loPrc, m.barCnt
FROM AI_DAY_STK_PRC d
JOIN (
  SELECT a.STK_CD, a.CLOSE_PRC lastClose, b.loPrc, b.barCnt
  FROM AI_MIN_STK_PRC a
  JOIN (
    SELECT STK_CD, MAX(BAR_TM) mx, MIN(LOW_PRC) loPrc, COUNT(*) barCnt
    FROM AI_MIN_STK_PRC
    WHERE TRADE_DT=%s AND TIC_SCOPE='1'
    GROUP BY STK_CD
  ) b ON b.STK_CD=a.STK_CD AND b.mx=a.BAR_TM
  WHERE a.TRADE_DT=%s AND a.TIC_SCOPE='1'
) m ON m.STK_CD=d.STK_CD
WHERE d.TRADE_DT=%s AND d.INDC_TP=%s
"""


def prevTradingDay(ymd):
    """기준일 직전 거래일 (휴장 반영)."""
    try:
        dt = datetime.strptime(str(ymd)[:8], "%Y%m%d")
    except ValueError:
        return None
    for _ in range(15):
        dt -= timedelta(days=1)
        if isTradingDay(dt):
            return dt.strftime("%Y%m%d")
    return None


def findDayPrcMismatch(tradeDt, tolPct=DEFAULT_TOL_PCT):
    """
    1분봉 대비 일봉 종가가 어긋난 종목 (DB 조회만).
    반환: {ok, tradeDt, cmpCnt, rows:[{stkCd, dayClose, minClose, diffPct}]}
    """
    ymd = str(tradeDt or "")[:8]
    if len(ymd) != 8 or not ymd.isdigit():
        return {"ok": False, "errMsg": f"거래일 형식 오류: {tradeDt}"}

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(_MISMATCH_SQL, (ymd, ymd, ymd, INDC_TP_QTY))
            found = cur.fetchall() or []
    except Exception as err:
        return {"ok": False, "errMsg": f"정합 조회 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    rows = []
    cmpCnt = 0
    for r in found:
        if int(r.get("barCnt") or 0) < _MIN_BARS_PER_DAY:
            continue
        dayClose = float(r.get("dayClose") or 0)
        minClose = float(r.get("lastClose") or 0)
        if dayClose <= 0 or minClose <= 0:
            continue
        cmpCnt += 1
        diffPct = (minClose - dayClose) / dayClose * 100
        if abs(diffPct) <= float(tolPct):
            continue
        rows.append(
            {
                "stkCd": normStkCd(r.get("STK_CD")),
                "dayClose": dayClose,
                "minClose": minClose,
                "diffPct": diffPct,
            }
        )
    rows.sort(key=lambda x: abs(x["diffPct"]), reverse=True)
    return {"ok": True, "tradeDt": ymd, "cmpCnt": cmpCnt, "rows": rows}


def _reloadOneDayBar(stkCd, ymd, session):
    """해당 종목의 그 날 일봉만 재조회 후 덮어쓰기."""
    mode, hostUrl, token, tokenType = session
    fetched = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCd,
        ymd,
        ymd,
        indcTp=INDC_TP_QTY,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    if not fetched.get("ok"):
        return False, fetched.get("errMsg") or "조회실패"
    rows = fetched.get("rows") or []
    if not rows:
        return False, "응답 없음"
    saved = upsertDayStkPrcRows(rows)
    if not saved.get("ok"):
        return False, saved.get("errMsg") or "DB실패"
    return True, ""


def refixDayPrc(tradeDt=None, tolPct=DEFAULT_TOL_PCT, fillMinBars=True):
    """
    지정 거래일 시세 보정. 반환: {ok, summary, ...}
    fillMinBars=True 면 판별 기준용 1분봉 결측을 먼저 채운다.
    """
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    minSum = ""

    if fillMinBars:
        try:
            from utils.rankMinPrcLoad import (
                collectCloseMinStkCds,
                loadMinPrcForRankStks,
            )

            collected = collectCloseMinStkCds(ymd)
            cds = collected.get("stkCds") or []
            if cds:
                minRes = loadMinPrcForRankStks(cds, ymd, skipIfEnough=True)
                minSum = minRes.get("summary") or ""
                termLog(f"[prcRefix] 1분봉 보강: {minSum}")
        except Exception as err:
            termLog(f"[prcRefix] 1분봉 보강 실패: {err}")

    before = findDayPrcMismatch(ymd, tolPct=tolPct)
    if not before.get("ok"):
        return {"ok": False, "errMsg": before.get("errMsg")}
    targets = before.get("rows") or []
    if not targets:
        summary = (
            f"[시세보정] {ymd} 일봉·1분봉 비교 {before.get('cmpCnt')}종 — "
            f"불일치 없음 (기준 {tolPct}%)"
            + (f"\n{minSum}" if minSum else "")
        )
        termLog(summary)
        return {
            "ok": True,
            "tradeDt": ymd,
            "cmpCnt": before.get("cmpCnt"),
            "fixCnt": 0,
            "summary": summary,
        }

    session = acquireKiwoomSession()
    if session is None:
        return {
            "ok": False,
            "errMsg": (
                f"키움 토큰 없음 — {ymd} 일봉 {len(targets)}종 보정 불가 "
                f"(login paper 후 재실행)"
            ),
        }
    waitSec = 1.05 if session[0] == "paper" else 0.35

    termLog(f"[prcRefix] {ymd} 장중값 일봉 {len(targets)}종 재적재 시작")
    okCnt = 0
    failCnt = 0
    fails = []
    for idx, t in enumerate(targets):
        if idx > 0:
            time.sleep(waitSec)
        ok, err = _reloadOneDayBar(t["stkCd"], ymd, session)
        if ok:
            okCnt += 1
            termLog(
                f"[prcRefix] ok {t['stkCd']} "
                f"{t['dayClose']:,.0f} → 실제 {t['minClose']:,.0f} "
                f"({idx + 1}/{len(targets)})"
            )
        else:
            failCnt += 1
            fails.append(f"{t['stkCd']}:{err}")
            termLog(f"[prcRefix] fail {t['stkCd']}: {err}")

    after = findDayPrcMismatch(ymd, tolPct=tolPct)
    leftCnt = len(after.get("rows") or []) if after.get("ok") else -1
    summary = (
        f"[시세보정] {ymd} 장중값 일봉 {len(targets)}종 → "
        f"재적재 {okCnt} / 실패 {failCnt}\n"
        f"비교대상 {before.get('cmpCnt')}종 · 남은 불일치 {leftCnt}종 "
        f"(기준 {tolPct}%)"
        + (f"\n실패: {', '.join(fails[:5])}" if fails else "")
        + (f"\n{minSum}" if minSum else "")
    )
    termLog(summary)
    return {
        "ok": failCnt == 0 and leftCnt == 0,
        "partial": failCnt > 0 or leftCnt > 0,
        "tradeDt": ymd,
        "cmpCnt": before.get("cmpCnt"),
        "fixCnt": okCnt,
        "failCnt": failCnt,
        "leftCnt": leftCnt,
        "summary": summary,
    }
