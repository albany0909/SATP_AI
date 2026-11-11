"""차트 일·주·월봉 적재 계획. DB 와 키움 호출은 하지 않는다."""

from __future__ import annotations

from datetime import datetime, timedelta

from utils.stkCdUtil import normStkCd

CHART_PERIODS = ("day", "week", "month")
POLE_API = {
    "week": "ka10082",
    "month": "ka10083",
}
# 차트 조회·최신화가 이 시간을 넘기면 헤더를 실패로 바꾼다.
CHART_FETCH_TIMEOUT_SEC = 120
INTRADAY_CHART_REASONS = ("startup", "slot", "manual")


def chartFetchStartLine(code, kind):
    """메인 스레드에서 바로 남길 시작 로그."""
    return f"[deskChart] {kind} 시작 {code}"


def chartFetchExpired(startedMono, nowMono, timeoutSec=CHART_FETCH_TIMEOUT_SEC):
    try:
        return float(nowMono) - float(startedMono) >= float(timeoutSec)
    except (TypeError, ValueError):
        return False


def intradayChartWanted(reason):
    return str(reason or "") in INTRADAY_CHART_REASONS


def chartActionForWatchPick(userPick, code, currentCode="", refreshBusy=False, syncBusy=False, chartOpen=False):
    """관심종목 행 선택 때 차트 탭을 열지 결정한다.

    반환: open(탭을 연다), reload(이미 열린 차트만 바꾼다), ignore.
    표 갱신·동기 재선택은 userPick 이 아니거나 busy 이므로 탭을 열지 않는다.
    """
    if refreshBusy or syncBusy or not userPick:
        return "ignore"
    if not str(code or "").strip():
        return "ignore"
    if chartOpen:
        if str(code) == str(currentCode or ""):
            return "ignore"
        return "reload"
    return "open"


def mergeLoadCodes(*groups):
    """순위·관심·급등 코드를 한 목록으로. 먼저 들어온 순서를 유지한다."""
    out = []
    seen = set()
    for group in groups:
        for raw in group or []:
            if isinstance(raw, dict):
                raw = raw.get("stkCd") or raw.get("STK_CD") or raw.get("code")
            cd = normStkCd(raw)
            if cd and cd not in seen:
                seen.add(cd)
                out.append(cd)
    return out


def codesFromSurgeRows(rows):
    """급등주 탭에 남는 종목만. 탭 필터(+5%)를 다시 쓰지 않고 이미 걸러진 행도 받는다."""
    from trading.surgeTradeData import tabRankRows

    kept = tabRankRows(rows or [])
    return mergeLoadCodes(kept.get("rows") or [])


def seriesFor(bundle, period):
    """차트 주기에 해당하는 봉만."""
    key = str(period or "day")
    if key not in CHART_PERIODS:
        key = "day"
    data = bundle or {}
    rows = data.get(key)
    return list(rows or [])


def needsAutoFetch(bundle, period):
    """그 주기 봉이 없으면 일·주·월을 함께 받는다."""
    key = str(period or "day")
    if key not in CHART_PERIODS:
        return False
    return len(seriesFor(bundle, key)) == 0


def liveTickRedraws(period):
    """1초 현재가 갱신은 일봉 화면만 다시 그린다."""
    return str(period or "day") == "day"


def poleFetchDecision(stat, today, barTp, refetch=False):
    """이미 최신이면 skip, 아니면 fetch. 실패로 보지 않는다."""
    if refetch:
        return "fetch"
    info = stat or {}
    maxDt = str(info.get("maxDt") or "")[:8]
    cnt = int(info.get("cnt") or 0)
    if cnt <= 0 or len(maxDt) != 8:
        return "fetch"
    day = str(today or "")[:8]
    try:
        latest = datetime.strptime(maxDt, "%Y%m%d")
        now = datetime.strptime(day, "%Y%m%d") if len(day) == 8 else datetime.now()
    except ValueError:
        return "fetch"
    gap = (now - latest).days
    if barTp == "week" and cnt >= 8 and gap <= 10:
        return "skip"
    if barTp == "month" and cnt >= 6 and gap <= 40:
        return "skip"
    return "fetch"


def collectSurgeChartCds(now=None, directory=None):
    """급등주 탭에 보이는 종목. 장 시작 전에는 전일 목록. 폴더가 없으면 빈 목록."""
    from pathlib import Path

    now = now or datetime.now()
    if directory is None:
        try:
            from utils.appPaths import configDataDir

            directory = configDataDir() / "surgeTrade"
        except Exception:
            return []
    root = Path(directory)
    if not root.is_dir():
        return []
    try:
        from trading.surgeTradeData import SnapStore, selectTabDisplay

        picked = selectTabDisplay(SnapStore(root), now)
        snap = (picked or {}).get("snap") or {}
        return codesFromSurgeRows(snap.get("rows") or [])
    except Exception:
        return []
