"""대한민국 공휴일·거래일 판별 유틸"""

import json
from datetime import datetime, timedelta
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "krHolidays.json"
_holidaySet = None


def _loadHolidaySet():
    """krHolidays.json → set(YYYYMMDD). 실패 시 빈 set."""
    global _holidaySet
    if _holidaySet is not None:
        return _holidaySet
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        days = data.get("holidays") if isinstance(data, dict) else data
        _holidaySet = {str(d).strip() for d in (days or []) if str(d).strip()}
    except Exception:
        _holidaySet = set()
    return _holidaySet


def reloadKrHolidays():
    """파일 변경 후 캐시 갱신."""
    global _holidaySet
    _holidaySet = None
    return _loadHolidaySet()


def isKrHoliday(dt=None):
    """공휴일(대체공휴일 포함)이면 True."""
    day = dt or datetime.now()
    ymd = day.strftime("%Y%m%d") if hasattr(day, "strftime") else str(day).strip()
    return ymd in _loadHolidaySet()


def isWeekend(dt=None):
    """토·일이면 True."""
    day = dt or datetime.now()
    return day.weekday() >= 5


def _asDatetime(dt=None):
    """datetime 또는 YYYYMMDD → datetime."""
    if dt is None:
        return datetime.now()
    if hasattr(dt, "strftime") and hasattr(dt, "weekday"):
        return dt
    text = str(dt).replace("-", "").strip()[:8]
    return datetime.strptime(text, "%Y%m%d")


def isTradingDay(dt=None):
    """주말·공휴일이 아니면 거래일(True)."""
    day = _asDatetime(dt)
    if isWeekend(day):
        return False
    if isKrHoliday(day):
        return False
    return True


def prevTradingDay(dt=None):
    """직전 거래일 YYYYMMDD. 주말·공휴일은 건너뛴다."""
    day = _asDatetime(dt)
    for _ in range(1, 21):
        day = day - timedelta(days=1)
        if isTradingDay(day):
            return day.strftime("%Y%m%d")
    return ""


def nextTradingDay(dt=None):
    """다음 거래일 YYYYMMDD. 주말·공휴일은 건너뛴다."""
    day = _asDatetime(dt)
    for _ in range(1, 21):
        day = day + timedelta(days=1)
        if isTradingDay(day):
            return day.strftime("%Y%m%d")
    return ""


def tradingDaysBack(n, endDt=None):
    """
    endDt 포함 최근 n개 거래일.
    반환: (시작YYYYMMDD, [오래된→최근]).
    """
    want = max(1, int(n or 1))
    day = _asDatetime(endDt)
    days = []
    for _ in range(1, 80):
        if isTradingDay(day):
            days.append(day.strftime("%Y%m%d"))
            if len(days) >= want:
                break
        day = day - timedelta(days=1)
    days.reverse()
    start = days[0] if days else ""
    return start, days


def isNextDayClosed(dt=None):
    """
    달력상 다음날이 주말·공휴일이면 True.
    금요일·연휴 전날처럼 '내일 장이 안 열리는' 날.
    """
    day = _asDatetime(dt)
    nxt = day + timedelta(days=1)
    return not isTradingDay(nxt)
