"""코스피 종합지수(001) 일봉. ka20006 조회 후 없는 날짜만 넣는다.

저장은 기존 AI_DAY_INDS_PRC. 테이블이 없으면 새로 만들지 않고
config/data/kospiDay.jsonl 에 없는 날짜만 붙인다.
기존 행은 수정하거나 삭제하지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from utils.termLog import termLog

KOSPI_CD = "001"
KOSPI_TR = "ka20006"
DEFAULT_MONTHS = 24


def kospiJsonlPath():
    from utils.appPaths import configDataDir

    return configDataDir() / "kospiDay.jsonl"


def _rangeYmd(months=DEFAULT_MONTHS, endDt=None):
    end = datetime.strptime(str(endDt), "%Y%m%d") if endDt else datetime.now()
    start = end - timedelta(days=int(months) * 30 + 5)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _sessionClosed(ymd, now=None):
    """요청일이 오늘보다 이전이거나, 오늘이면 장 마감 시각 이후."""
    now = now or datetime.now()
    day = str(ymd or "")[:8]
    today = now.strftime("%Y%m%d")
    if len(day) != 8:
        return False
    if day < today:
        return True
    if day > today:
        return False
    closeHm = "15:30"
    try:
        from config.settingsStore import getMktHours

        _, closeHm = getMktHours()
    except Exception:
        closeHm = "15:30"
    closeHm = str(closeHm or "15:30")[:5]
    return now.strftime("%H:%M") >= closeHm


def _dropOpenToday(rows, now=None):
    now = now or datetime.now()
    today = now.strftime("%Y%m%d")
    if _sessionClosed(today, now=now):
        return list(rows or [])
    return [row for row in (rows or []) if str(row.get("TRADE_DT") or "")[:8] != today]


def _usableRows(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ymd = str(row.get("TRADE_DT") or "")[:8]
        if len(ymd) != 8 or not ymd.isdigit():
            continue
        if row.get("CLOSE_PRC") is None:
            continue
        out.append(row)
    return out


def _readJsonl(path):
    filePath = Path(path)
    if not filePath.is_file():
        return []
    rows = []
    try:
        text = filePath.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _jsonlDates(path):
    dates = set()
    for row in _readJsonl(path):
        ymd = str(row.get("tradeDt") or row.get("TRADE_DT") or "")[:8]
        if len(ymd) == 8:
            dates.add(ymd)
    return dates


def appendKospiJsonl(rows, path=None):
    """없는 날짜만 jsonl 끝에 붙인다. 기존 줄은 고치거나 지우지 않는다."""
    filePath = Path(path) if path else kospiJsonlPath()
    have = _jsonlDates(filePath)
    fresh = []
    for row in _usableRows(rows):
        ymd = str(row.get("TRADE_DT") or "")[:8]
        if ymd in have:
            continue
        have.add(ymd)
        fresh.append({
            "tradeDt": ymd,
            "open": row.get("OPEN_PRC"),
            "high": row.get("HIGH_PRC"),
            "low": row.get("LOW_PRC"),
            "close": row.get("CLOSE_PRC"),
            "indsCd": str(row.get("INDS_CD") or KOSPI_CD),
            "srcTr": str(row.get("SRC_TR") or KOSPI_TR),
        })
    if not fresh:
        return 0
    filePath.parent.mkdir(parents=True, exist_ok=True)
    with filePath.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def barsFromJsonl(path=None, asOf=None):
    day = str(asOf or "").replace("-", "")[:8]
    bars = []
    for row in _readJsonl(path or kospiJsonlPath()):
        ymd = str(row.get("tradeDt") or row.get("TRADE_DT") or "")[:8]
        if len(ymd) != 8:
            continue
        if len(day) == 8 and ymd > day:
            continue
        try:
            close = row.get("close")
            if close is None:
                close = row.get("CLOSE_PRC")
            close = float(close) if close is not None else None
        except (TypeError, ValueError):
            close = None
        if close is None:
            continue
        bars.append({"tradeDt": ymd, "close": close})
    bars.sort(key=lambda item: item["tradeDt"])
    return bars


def loadStoredKospiBars(asOf=None, path=None):
    """REG01 계산용 종가. 테이블이 있으면 테이블, 없으면 jsonl."""
    from utils.dayIndsPrcStore import loadKospiDayBars

    got = loadKospiDayBars(asOf=asOf, indsCd=KOSPI_CD)
    if got.get("ok"):
        return got.get("bars") or []
    if got.get("missingTable"):
        return barsFromJsonl(path, asOf=asOf)
    termLog(f"[kospiIdx] 일봉 조회 실패: {got.get('errMsg')}")
    return []


def _keepMissing(rows, have):
    out = []
    for row in _usableRows(rows):
        ymd = str(row.get("TRADE_DT") or "")[:8]
        if ymd not in have:
            out.append(row)
    return out


def _storeMissing(rows, have, writer=None, path=None):
    fresh = _keepMissing(rows, have)
    if not fresh:
        return {"ok": True, "upsertCnt": 0, "skipped": len(rows or []), "store": "none"}
    if writer is not None:
        got = writer(fresh) or {"ok": False, "errMsg": "저장 결과 없음"}
        got["skipped"] = len(rows or []) - len(fresh)
        return got
    from utils.dayIndsPrcStore import insertIgnoreDayIndsPrcRows

    got = insertIgnoreDayIndsPrcRows(fresh)
    if got.get("ok"):
        got["skipped"] = len(rows or []) - len(fresh)
        return got
    if got.get("missingTable"):
        n = appendKospiJsonl(fresh, path)
        termLog(f"[kospiIdx] AI_DAY_INDS_PRC 없음 — jsonl에 {n}건")
        return {
            "ok": True,
            "upsertCnt": n,
            "store": "jsonl",
            "missingTable": True,
            "skipped": len(rows or []) - len(fresh),
        }
    return got


def _fetchRange(session, startDt, endDt, fetcher=None):
    mode, hostUrl, token, tokenType = session
    fn = fetcher
    if fn is None:
        from api.getDayIndsPrc import INDS_NM_MAP, fetchDayIndsPrcRange

        fn = fetchDayIndsPrcRange
        indsNm = INDS_NM_MAP.get(KOSPI_CD, "KOSPI")
    else:
        indsNm = "KOSPI"
    return fn(
        hostUrl,
        token,
        KOSPI_CD,
        startDt,
        endDt,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
        indsNm=indsNm,
    )


def _knownDates(startDt, endDt, existing=None, path=None):
    if existing is not None:
        got = existing(startDt, endDt) if callable(existing) else existing
        if isinstance(got, dict):
            if not got.get("ok") and not got.get("missingTable"):
                return None, got
            if got.get("missingTable"):
                return _jsonlDates(path or kospiJsonlPath()), "jsonl"
            return set(got.get("dates") or []), "db"
        return set(got or []), "db"
    from utils.dayIndsPrcStore import listKospiTradeDates

    got = listKospiTradeDates(startDt, endDt, indsCd=KOSPI_CD)
    if got.get("ok"):
        return set(got.get("dates") or []), "db"
    if got.get("missingTable"):
        return _jsonlDates(path or kospiJsonlPath()), "jsonl"
    return None, got


def loadKospiIdxDays(months=DEFAULT_MONTHS, endDt=None, now=None, session=None, fetcher=None, writer=None, existing=None, path=None):
    """최근 months개월 코스피 일봉 중 없는 날짜만 넣는다.

    데스크가 이 함수를 호출하지 않는다. 데이터가 부족할 때 한 번 실행한다.

        from utils.kospiIdxLoad import loadKospiIdxDays
        loadKospiIdxDays(months=24)
    """
    startDt, endYmd = _rangeYmd(months=months, endDt=endDt)
    if session is None:
        from utils.dayPrcLoadService import acquireKiwoomSession

        session = acquireKiwoomSession()
    if not session:
        return {"ok": False, "errMsg": "키움 토큰 없음", "upsertCnt": 0}
    fetched = _fetchRange(session, startDt, endYmd, fetcher=fetcher)
    if not fetched.get("ok"):
        return {"ok": False, "errMsg": fetched.get("errMsg"), "upsertCnt": 0}
    rows = _dropOpenToday(fetched.get("rows") or [], now=now)
    have, store = _knownDates(startDt, endYmd, existing=existing, path=path)
    if have is None:
        return {"ok": False, "errMsg": store.get("errMsg") if isinstance(store, dict) else "일자 조회 실패", "upsertCnt": 0}
    saved = _storeMissing(rows, have, writer=writer, path=path)
    saved["startDt"] = startDt
    saved["endDt"] = endYmd
    saved["indsCd"] = KOSPI_CD
    saved["srcTr"] = KOSPI_TR
    saved["months"] = months
    return saved


def appendKospiIdxToday(endDt=None, now=None, session=None, fetcher=None, writer=None, existing=None, path=None):
    """장마감 일봉 적재에서 그날 한 건만 추가한다. 이미 있으면 건드리지 않는다."""
    ymd = str(endDt or (now or datetime.now()).strftime("%Y%m%d"))[:8]
    if not _sessionClosed(ymd, now=now):
        return {"ok": True, "skipped": "beforeClose", "tradeDt": ymd, "upsertCnt": 0}
    if session is None:
        from utils.dayPrcLoadService import acquireKiwoomSession

        session = acquireKiwoomSession()
    if not session:
        return {"ok": False, "errMsg": "키움 토큰 없음", "tradeDt": ymd, "upsertCnt": 0}
    have, store = _knownDates(ymd, ymd, existing=existing, path=path)
    if have is None:
        return {
            "ok": False,
            "errMsg": store.get("errMsg") if isinstance(store, dict) else "일자 조회 실패",
            "tradeDt": ymd,
            "upsertCnt": 0,
        }
    if ymd in have:
        return {"ok": True, "skipped": "exists", "tradeDt": ymd, "upsertCnt": 0, "store": store}
    fetched = _fetchRange(session, ymd, ymd, fetcher=fetcher)
    if not fetched.get("ok"):
        return {"ok": False, "errMsg": fetched.get("errMsg"), "tradeDt": ymd, "upsertCnt": 0}
    rows = [row for row in (fetched.get("rows") or []) if str(row.get("TRADE_DT") or "")[:8] == ymd]
    saved = _storeMissing(rows, have, writer=writer, path=path)
    saved["tradeDt"] = ymd
    saved["indsCd"] = KOSPI_CD
    saved["srcTr"] = KOSPI_TR
    n = int(saved.get("upsertCnt") or 0)
    if saved.get("ok"):
        termLog(f"[kospiIdx] {ymd} 없는 날짜만 {n}건 ({saved.get('store') or store})")
    return saved
