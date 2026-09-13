"""미국 순위 선정 종목 → 최근 N개월 일봉(AI_US_DAY_STK_PRC) 적재"""

import time
from datetime import datetime, timedelta

from api.getUsDayChart import fetchUsDayChartRange
from api.getUsInqRank import normUsStkCd
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dbConn import openDbConn
from utils.termLog import termLog
from utils.usDayStkPrcStore import upsertUsDayStkPrcRows

DEFAULT_MONTHS = 6
_MIN_EXIST_BARS = 60


def _pickSession():
    # 웹 프로세스(수동 배치)에서도 쓰이도록 .env 앱키 발급까지 위임
    return acquireKiwoomSession()


def _rangeYmd(months=DEFAULT_MONTHS, endDt=None):
    end = datetime.strptime(endDt, "%Y%m%d") if endDt else datetime.now()
    start = end - timedelta(days=int(months) * 30 + 5)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _countDayBars(stkCd, startDt, endDt):
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM AI_US_DAY_STK_PRC
                WHERE STK_CD=%s AND TRADE_DT BETWEEN %s AND %s
                """,
                (str(stkCd), str(startDt), str(endDt)),
            )
            row = cur.fetchone() or {}
            return int(row.get("cnt") or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def loadUsDayPrcForRankStks(
    stkItems,
    months=DEFAULT_MONTHS,
    endDt=None,
    skipIfEnough=True,
    onProgress=None,
):
    """
    stkItems: [{"stkCd":"NVDA","stexTp":"ND"}, ...] 또는 티커 문자열 목록(stex 없으면 스킵).
    """
    items = []
    seen = set()
    for raw in stkItems or []:
        if isinstance(raw, dict):
            cd = normUsStkCd(raw.get("stkCd") or raw.get("STK_CD") or "")
            stex = str(raw.get("stexTp") or raw.get("STEX_TP") or "").strip().upper()
        else:
            cd = normUsStkCd(raw)
            stex = ""
        if not cd or cd in seen:
            continue
        seen.add(cd)
        items.append({"stkCd": cd, "stexTp": stex})

    if not items:
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
            "errMsg": "미국일봉 적재 전 login paper 또는 login real이 필요합니다.",
            "stkCnt": len(items),
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
        f"[usRankDayPrc] ({modeLabel}) {len(items)}종목 "
        f"{startDt}~{endYmd} ({months}개월) 일봉 적재 시작"
    )

    loadedCnt = 0
    skipCnt = 0
    failCnt = 0
    upsertCnt = 0
    fails = []

    for i, it in enumerate(items):
        stkCd = it["stkCd"]
        stex = it["stexTp"]
        if i > 0:
            time.sleep(waitSec)

        if stex not in ("ND", "NY", "NA"):
            failCnt += 1
            fails.append(f"{stkCd}:stex없음")
            termLog(f"[usRankDayPrc] fail {stkCd}: stex_tp 없음")
            continue

        if skipIfEnough:
            existCnt = _countDayBars(stkCd, startDt, endYmd)
            if existCnt >= _MIN_EXIST_BARS:
                skipCnt += 1
                termLog(f"[usRankDayPrc] skip {stkCd} (기존 {existCnt}봉)")
                continue

        fetched = fetchUsDayChartRange(
            hostUrl,
            token,
            stex,
            stkCd,
            startDt,
            endYmd,
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not fetched.get("ok"):
            failCnt += 1
            err = fetched.get("errMsg") or "조회실패"
            fails.append(f"{stkCd}:{err}")
            termLog(f"[usRankDayPrc] fail {stkCd}: {err}")
            continue

        rows = fetched.get("rows") or []
        if not rows:
            skipCnt += 1
            termLog(f"[usRankDayPrc] empty {stkCd}")
            continue

        saved = upsertUsDayStkPrcRows(rows)
        if not saved.get("ok"):
            failCnt += 1
            err = saved.get("errMsg") or "DB실패"
            fails.append(f"{stkCd}:{err}")
            continue

        loadedCnt += 1
        upsertCnt += int(saved.get("upsertCnt") or 0)
        termLog(
            f"[usRankDayPrc] ok {stkCd}({stex}) "
            f"{rows[0]['TRADE_DT']}~{rows[-1]['TRADE_DT']} "
            f"{saved.get('upsertCnt')}건 ({i + 1}/{len(items)})"
        )

    summary = (
        f"[미국순위일봉] ({modeLabel}) 완료\n"
        f"기간: {startDt} ~ {endYmd}\n"
        f"대상: {len(items)}종목 / 적재 {loadedCnt} / "
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
        "stkCnt": len(items),
        "loadedCnt": loadedCnt,
        "skipCnt": skipCnt,
        "failCnt": failCnt,
        "upsertCnt": upsertCnt,
        "startDt": startDt,
        "endDt": endYmd,
        "fails": fails,
        "modeLabel": modeLabel,
        "summary": summary,
    }
