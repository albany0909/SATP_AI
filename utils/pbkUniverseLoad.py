"""코스피·코스닥 전 종목 일봉과 실적 공시.

일봉은 AI_DAY_STK_PRC 에 없는 날짜만 넣는다.
새 종목은 최초 1회 최근 80거래일 분량을 받는다.
공시는 config/data/pbk01_dart.jsonl 에만 붙인다. 새 테이블은 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from utils.termLog import termLog

BACKFILL_BARS = 80
# 80거래일 ≈ 달력 120일. 휴장 여유를 더한다.
BACKFILL_CALENDAR_DAYS = 130
MIN_AMT_MN = 300  # 백만원. 300 = 3억 원
# 모의 ka10086 은 약 1.05초 1회. 2,500종목 1페이지면 약 44분.
PAPER_GAP_SEC = 1.05


def universeLoadEnabled(settings=None):
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    row = ((settings or {}).get("stratLive") or {}).get("PBK01")
    if not isinstance(row, dict) or "universeLoad" not in row:
        return True
    return bool(row.get("universeLoad"))


def estimateDayTr(nNames, pages=1, gapSec=PAPER_GAP_SEC):
    """전 종목 1회 적재에 드는 조회 횟수와 초."""
    calls = max(0, int(nNames)) * max(1, int(pages))
    return {"calls": calls, "seconds": int(calls * float(gapSec))}


def planDayFetch(stat, endDt, backfillBars=BACKFILL_BARS):
    """없으면 80거래일 백필, 있으면 없는 날짜만. 이미 최신이면 None."""
    end = str(endDt or "")[:8]
    cnt = int((stat or {}).get("cnt") or 0)
    maxDt = str((stat or {}).get("maxDt") or "")[:8]
    if cnt < int(backfillBars) or not maxDt:
        start = (
            datetime.strptime(end, "%Y%m%d") - timedelta(days=BACKFILL_CALENDAR_DAYS)
        ).strftime("%Y%m%d")
        return {"start": start, "end": end, "kind": "backfill"}
    if maxDt >= end:
        return None
    nxt = (datetime.strptime(maxDt, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    if nxt > end:
        return None
    return {"start": nxt, "end": end, "kind": "increment"}


def isCommonStock(code, name="", marketName="", mrktTp=""):
    """코스피·코스닥 보통주. ETF·ETN·스팩·리츠·우선주·코넥스는 뺀다."""
    text = str(code or "")
    if len(text) != 6 or not text.endswith("0"):
        return False
    blob = f"{name} {marketName}".lower()
    for word in ("etf", "etn", "etp", "스팩", "리츠", "코넥스", "konex"):
        if word in blob:
            return False
    lowName = str(name or "").lower().strip()
    for brand in ("kodex", "tiger", "kbstar", "hanaro", "kosef", "arirang", "timefolio"):
        if lowName.startswith(brand):
            return False
    nm = str(name or "")
    if nm.endswith("우") or nm.endswith("우B") or nm.endswith("우C") or "우B" in nm:
        return False
    tp = str(mrktTp or "").strip()
    if tp in ("8", "30", "50", "60"):
        return False
    return True


def listKospiKosdaq(reader=None):
    """AI_STK_INFO 의 코스피·코스닥 보통주. reader 가 없으면 DB."""
    if reader is not None:
        rows = list(reader() or [])
    else:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT STK_CD, STK_NM, MARKET_NAME, MRKT_TP
                    FROM AI_STK_INFO
                    WHERE MARKET_NAME IN ('거래소', '코스닥', 'KOSPI', 'KOSDAQ')
                       OR MRKT_TP IN ('0', '10', '001', '101')
                    """
                )
                rows = cur.fetchall() or []
        finally:
            conn.close()
    from utils.stkCdUtil import normStkCd

    out = []
    seen = set()
    for row in rows:
        if isinstance(row, str):
            code = normStkCd(row)
            name = market = tp = ""
        else:
            code = normStkCd((row or {}).get("STK_CD") or (row or {}).get("stkCd"))
            name = (row or {}).get("STK_NM") or (row or {}).get("name") or ""
            market = (row or {}).get("MARKET_NAME") or (row or {}).get("marketName") or ""
            tp = (row or {}).get("MRKT_TP") or (row or {}).get("mrktTp") or ""
        if code and code not in seen and isCommonStock(code, name, market, tp):
            seen.add(code)
            out.append(code)
    return out


def dartLookbackDays(settings=None):
    if settings is None:
        try:
            from config.settingsStore import loadSettings

            settings = loadSettings() or {}
        except Exception:
            settings = {}
    row = ((settings or {}).get("stratLive") or {}).get("PBK01")
    try:
        days = int((row or {}).get("dartLookbackDays") or 120)
    except (TypeError, ValueError):
        days = 120
    return days if days > 0 else 120


def planDartRange(oldest, newest, today, lookbackDays=120):
    """가장 오래된 공시가 창보다 최근이면 빈 구간을 채우고, 아니면 마지막 다음날만."""
    end = str(today or "")[:8]
    floor = (datetime.strptime(end, "%Y%m%d") - timedelta(days=int(lookbackDays))).strftime("%Y%m%d")
    old = str(oldest or "")[:8]
    new = str(newest or "")[:8]
    if len(old) != 8 or old > floor:
        return {"start": floor, "end": end, "kind": "backfill"}
    if len(new) != 8 or new >= end:
        return None
    nxt = (datetime.strptime(new, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    if nxt > end:
        return None
    return {"start": nxt, "end": end, "kind": "increment"}


def _savedDartSpan(path):
    from utils.pbkDartStore import _read

    days = []
    for row in _read(path):
        day = str(row.get("rceptDt") or "")[:8]
        if len(day) == 8 and day.isdigit():
            days.append(day)
    if not days:
        return "", ""
    return min(days), max(days)


def loadMissingDayBars(codes, endDt, statFn, fetcher, writer, slot=None, log=None, backfillBars=BACKFILL_BARS):
    """없는 날짜만 writer 로 넣는다. 진행은 50종목마다 로그."""
    log = log or termLog
    loaded = skipped = failed = calls = 0
    total = len(list(codes or []))
    for i, code in enumerate(list(codes or []), start=1):
        stat = statFn(code) or {}
        plan = planDayFetch(stat, endDt, backfillBars=backfillBars)
        if plan is None:
            skipped += 1
        else:
            if slot is not None:
                slot("ka10086")
            calls += 1
            got = fetcher(code, plan["start"], plan["end"]) or {}
            if not got.get("ok"):
                failed += 1
            else:
                saved = writer(got.get("rows") or []) or {}
                if saved.get("ok"):
                    loaded += 1
                else:
                    failed += 1
        if i == 1 or i == total or i % 50 == 0:
            log(f"[PBK01] 일봉 진행 {i}/{total} 적재 {loaded} 스킵 {skipped} 실패 {failed} 조회 {calls}")
    return {
        "ok": failed == 0,
        "stkCnt": total,
        "loadedCnt": loaded,
        "skipCnt": skipped,
        "failCnt": failed,
        "calls": calls,
    }


def saveMarketEarnings(items, path=None):
    from utils.pbkDartStore import appendDisclosures

    return appendDisclosures(items, path=path)


def runUniverseClose(endDt=None, settings=None, dartFetch=None, codes=None, statFn=None, fetcher=None, writer=None, slot=None, log=None, dartPath=None):
    """장마감 후 실적 공시와 없는 일봉을 채운다. universeLoad 가 꺼지면 하지 않는다."""
    if not universeLoadEnabled(settings):
        return {"ok": True, "skipped": "off", "summary": "[PBK01] 전 종목 적재 꺼짐"}
    end = str(endDt or datetime.now().strftime("%Y%m%d"))[:8]
    lookback = dartLookbackDays(settings)
    from utils.pbkDartStore import dartPath as defaultDartPath

    storePath = dartPath or defaultDartPath()
    oldest, newest = _savedDartSpan(storePath)
    window = planDartRange(oldest, newest, end, lookbackDays=lookback)
    items = []
    if dartFetch is None:
        from research.dartDiscFetch import fetchMarketDisclosures

        dartFetch = fetchMarketDisclosures
    if window is None:
        termLog(f"[PBK01] 실적 공시 최신 — {newest} 이후 없음")
    else:
        termLog(f"[PBK01] 실적 공시 {window['kind']} {window['start']}~{window['end']}")
    for cls in ("Y", "K"):
        if window is None:
            break
        got = dartFetch(window["start"], window["end"], corpCls=cls) or {}
        if not got.get("ok"):
            termLog(f"[PBK01] 공시 조회 실패 {got.get('errMsg') or ''}")
        items.extend(got.get("items") or [])
    added = saveMarketEarnings(items, path=dartPath)
    termLog(f"[PBK01] 실적 공시 추가 {added}건")
    if codes is None:
        try:
            codes = listKospiKosdaq()
        except Exception as err:
            termLog(f"[PBK01] 종목 마스터 실패: {err}")
            codes = []
    if statFn is None or fetcher is None or writer is None:
        bars = _loadWithKiwoom(codes, end, log=log)
    else:
        bars = loadMissingDayBars(
            codes, end, statFn, fetcher, writer, slot=slot, log=log,
        )
    est = estimateDayTr(len(codes or []))
    summary = (
        f"[PBK01] 공시 {added}건 · 일봉 대상 {bars.get('stkCnt') or 0} "
        f"적재 {bars.get('loadedCnt') or 0} 스킵 {bars.get('skipCnt') or 0} "
        f"조회 {bars.get('calls') or 0} "
        f"(전량 1회면 약 {est['calls']}회 · {est['seconds'] // 60}분)"
    )
    termLog(summary)
    try:
        _refreshNextCandidates(end)
    except Exception as err:
        termLog(f"[PBK01] 적재 후 후보 재계산 실패: {err}")
    out = dict(bars or {})
    out["dartAdded"] = added
    out["summary"] = summary
    return out


def _refreshNextCandidates(endDt):
    """장마감 적재가 끝나면 다음 거래일 후보를 다시 저장한다."""
    from datetime import datetime

    from config.krHoliday import nextTradingDay

    nxt = nextTradingDay(endDt) or str(endDt)[:8]
    now = datetime.strptime(str(nxt)[:8] + "080000", "%Y%m%d%H%M%S")
    from trading.pbk01Live import runPbkCycle

    runPbkCycle(now=now, always=True)


def _loadWithKiwoom(codes, endDt, log=None):
    from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
    from utils.dayPrcLoadService import acquireKiwoomSession
    from utils.dayStkPrcStore import insertIgnoreDayStkPrcRows
    from utils.rankDayPrcLoad import _existDayBarStat
    from utils.trRateLimit import acquireTrSlot

    session = acquireKiwoomSession()
    if not session:
        termLog("[PBK01] 키움 토큰 없음 — 일봉 적재 생략")
        return {"ok": False, "stkCnt": len(codes or []), "loadedCnt": 0, "skipCnt": 0, "failCnt": 0, "calls": 0}
    mode, host, token, tokenType = session
    isPaper = mode != "real"

    def _fetch(code, start, end):
        return fetchDayStkPrcRange(
            host, token, code, start, end,
            indcTp=INDC_TP_QTY, tokenType=tokenType, isPaper=isPaper,
            maxBars=BACKFILL_BARS,
        )

    def _slot(apiId):
        acquireTrSlot(apiId, mode="paper" if isPaper else "real")

    start = (
        datetime.strptime(str(endDt)[:8], "%Y%m%d") - timedelta(days=BACKFILL_CALENDAR_DAYS)
    ).strftime("%Y%m%d")

    def _stat(code):
        return _existDayBarStat(code, start, endDt)

    return loadMissingDayBars(
        codes, endDt, _stat, _fetch, insertIgnoreDayStkPrcRows,
        slot=_slot, log=log,
    )


def liquidCodes(amountRows, minAmtMn=MIN_AMT_MN, bars=20):
    """최근 봉의 평균 거래대금(백만원)이 3억 이상인 코드."""
    grouped = {}
    for row in amountRows or []:
        code = str((row or {}).get("stkCd") or "")
        if len(code) != 6:
            continue
        grouped.setdefault(code, []).append(row)
    out = []
    for code, rows in grouped.items():
        rows.sort(key=lambda item: str(item.get("tradeDt") or ""))
        tail = rows[-int(bars):]
        vals = []
        for item in tail:
            try:
                vals.append(float(item.get("amtMn")))
            except (TypeError, ValueError):
                continue
        if len(vals) < int(bars):
            continue
        if sum(vals) / len(vals) >= float(minAmtMn):
            out.append(code)
    return sorted(out)
