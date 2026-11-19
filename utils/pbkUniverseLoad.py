"""코스피·코스닥 전 종목 일봉과 실적 공시.

일봉은 AI_DAY_STK_PRC 에 없는 날짜만 넣는다.
새 종목은 최초 1회 최근 80거래일 분량을 받는다.
공시는 config/data/pbk01_dart.jsonl 에만 붙인다. 새 테이블은 만들지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

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


def splitDartWindows(start, end, stepDays=30):
    """corp_code 없는 조회는 3개월을 넘기면 거절된다. 30일 차이 이하로 나눈다."""
    cur = datetime.strptime(str(start)[:8], "%Y%m%d")
    last = datetime.strptime(str(end)[:8], "%Y%m%d")
    step = max(1, int(stepDays))
    out = []
    while cur <= last:
        chunkEnd = min(cur + timedelta(days=step), last)
        out.append({
            "start": cur.strftime("%Y%m%d"),
            "end": chunkEnd.strftime("%Y%m%d"),
        })
        if chunkEnd >= last:
            break
        cur = chunkEnd + timedelta(days=1)
    return out


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


def _gapPath(storePath):
    path = Path(storePath)
    return path.with_name(f"{path.stem}_gap.json")


def _readGaps(storePath):
    path = _gapPath(storePath)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if isinstance(row, dict) and len(str(row.get("start") or "")) == 8:
            out.append({"start": str(row["start"])[:8], "end": str(row.get("end") or row["start"])[:8]})
    return out


def _writeGaps(storePath, gaps):
    path = _gapPath(storePath)
    path.parent.mkdir(parents=True, exist_ok=True)
    from config.settingsStore import _writeJson

    _writeJson(path, list(gaps or []))


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


def _fetchWindows(dartFetch, windows, pblntfTy, log):
    """구간마다 코스피·코스닥을 받고, 실패한 구간은 남긴다."""
    items = []
    failed = []
    calls = 0
    for piece in windows:
        for cls in ("Y", "K"):
            try:
                got = dartFetch(piece["start"], piece["end"], corpCls=cls, pblntfTy=pblntfTy) or {}
            except TypeError:
                got = dartFetch(piece["start"], piece["end"], corpCls=cls) or {}
            calls += int(got.get("calls") or 1)
            items.extend(got.get("items") or [])
            if not got.get("ok"):
                if piece not in failed:
                    failed.append(piece)
                log(
                    f"[PBK01] 공시 구간 실패 {piece['start']}~{piece['end']} "
                    f"{got.get('errMsg') or ''} — 다음에 다시"
                )
    return items, failed, calls


def backfillPeriodic(end, windows, dartFetch=None, finPath=None, callsLeft=None, log=None):
    """정기보고서(pblntf_ty=A)를 30일 구간으로 받고 S1 을 jsonl 에 붙인다."""
    from research.pbkSurprise import (
        DART_DAILY_LIMIT,
        countMissingYoy,
        evaluateReports,
        estimateSurpriseCalls,
        loadStatement,
        parsePeriodic,
    )
    from utils.pbkFinStore import appendFinRows, finPath as defaultFin, readFinRows, seenReceipts

    log = log or termLog
    store = finPath or defaultFin()
    est = estimateSurpriseCalls()
    if not windows:
        log("[PBK01] 정기보고서 최신")
        return {"listCalls": 0, "finCalls": 0, "added": 0, "pending": 0, "reports": [], "estimate": est}
    log(
        f"[PBK01] 정기보고서 예상 목록 {est['listCalls']}회 · 재무 {est['finCalls']}회 · "
        f"합계 {est['total']}회 · 일일 한도 {est['dailyLimit']} · 약 {est['seconds'] // 60}분"
    )
    if dartFetch is None:
        from research.dartDiscFetch import fetchMarketDisclosures

        dartFetch = fetchMarketDisclosures
    items, failed, listCalls = _fetchWindows(dartFetch, windows, "A", log)
    listed = None
    try:
        listed = set(listKospiKosdaq())
    except Exception:
        listed = None
    reports = []
    for item in items:
        spec = parsePeriodic(item.get("reportNm") or "", item.get("rceptDt") or "")
        if not spec:
            continue
        code = str(item.get("stkCd") or "")
        if listed is not None and code not in listed:
            continue
        if listed is None and not isCommonStock(code, item.get("corpNm") or "", "", ""):
            continue
        if not item.get("corpCode"):
            continue
        reports.append(item)
    budget = DART_DAILY_LIMIT if callsLeft is None else int(callsLeft)
    budget = max(0, budget - listCalls)

    def _fetch(corp, year, reprt):
        return loadStatement(corp, year, reprt)

    judged = evaluateReports(reports, seenReceipts(store), _fetch, budget, pauseSec=0.05)
    added = appendFinRows(judged.get("saved") or [], path=store)
    pending = list(judged.get("pending") or [])
    if pending:
        day = str(pending[0].get("rceptDt") or end)[:8]
        failed.append({"start": day, "end": str(end)[:8]})
    _writeGaps(store, failed)
    finCalls = int(judged.get("calls") or 0)
    yoyStat = countMissingYoy(readFinRows(store))
    log(
        f"[PBK01] 정기보고서 목록 {listCalls}회 · 재무 {finCalls}회 · "
        f"일일 한도 {DART_DAILY_LIMIT} · 저장 {added}건 · 보류 {len(pending)}건"
    )
    log(
        f"[PBK01] 정기보고서 {yoyStat['total']}건 중 "
        f"salesYoy 또는 opYoy 없음 {yoyStat['missing']}건"
    )
    return {
        "listCalls": listCalls,
        "finCalls": finCalls,
        "added": added,
        "pending": len(pending),
        "reports": reports,
        "estimate": est,
    }


def runUniverseClose(endDt=None, settings=None, dartFetch=None, codes=None, statFn=None, fetcher=None, writer=None, slot=None, log=None, dartPath=None, finPath=None):
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
    windows = []
    if window is not None:
        windows.extend(splitDartWindows(window["start"], window["end"]))
        termLog(f"[PBK01] 실적 공시 {window['kind']} {window['start']}~{window['end']} 구간 {len(windows)}개")
    else:
        termLog(f"[PBK01] 실적 공시 최신 — {newest} 이후 없음")
    for gap in _readGaps(storePath):
        if gap not in windows:
            windows.append(gap)
    items, failed, calls = _fetchWindows(dartFetch, windows, "I", termLog)
    _writeGaps(storePath, failed)
    termLog(f"[PBK01] 실적 공시 호출 {calls}회 · 실패 구간 {len(failed)}개")
    added = saveMarketEarnings(items, path=storePath)
    termLog(f"[PBK01] 실적 공시 추가 {added}건")
    from utils.pbkFinStore import finPath as defaultFinPath

    finStore = defaultFinPath() if finPath is None else finPath
    finOld, finNew = _savedDartSpan(finStore)
    finWindow = planDartRange(finOld, finNew, end, lookbackDays=lookback)
    finWindows = []
    if finWindow is not None:
        finWindows.extend(splitDartWindows(finWindow["start"], finWindow["end"]))
    for gap in _readGaps(finStore):
        if gap not in finWindows:
            finWindows.append(gap)
    try:
        from research.pbkSurprise import DART_DAILY_LIMIT

        surprise = backfillPeriodic(
            end, finWindows, dartFetch=dartFetch, finPath=finStore,
            callsLeft=DART_DAILY_LIMIT - calls, log=termLog,
        )
    except Exception as err:
        termLog(f"[PBK01] 정기보고서 판정 실패: {err}")
        surprise = {}
    items.extend(surprise.get("reports") or [])
    try:
        _ensureDisclosureBars(items, end, statFn, fetcher, writer, slot, log)
    except Exception as err:
        termLog(f"[PBK01] 후보 일봉 우선 적재 실패: {err}")
    try:
        _refreshNextCandidates(end)
    except Exception as err:
        termLog(f"[PBK01] 공시 후 후보 재계산 실패: {err}")
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


def _ensureDisclosureBars(items, end, statFn, fetcher, writer, slot, log):
    """공시가 있는 종목은 전 종목 적재보다 먼저 일봉을 채운다."""
    codes = []
    seen = set()
    for item in items or []:
        code = str((item or {}).get("stkCd") or "")
        if len(code) == 6 and code not in seen:
            seen.add(code)
            codes.append(code)
    if not codes:
        return {"stkCnt": 0}
    if statFn is None or fetcher is None or writer is None:
        return _loadWithKiwoom(codes, end, log=log)
    return loadMissingDayBars(codes, end, statFn, fetcher, writer, slot=slot, log=log)


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
