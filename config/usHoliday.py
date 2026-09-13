"""미국 주식시장(NYSE/NASDAQ) 휴장·거래일 판별"""

import json
from datetime import datetime, timedelta
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "usHolidays.json"
_holidaySet = None


def _loadHolidaySet():
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


def reloadUsHolidays():
    global _holidaySet
    _holidaySet = None
    return _loadHolidaySet()


def isUsHoliday(dt=None):
    """미국 휴장일이면 True (주말 제외 — 파일 기준)."""
    day = dt or datetime.now()
    ymd = day.strftime("%Y%m%d") if hasattr(day, "strftime") else str(day).strip()
    return ymd in _loadHolidaySet()


def isUsWeekend(dt=None):
    day = dt or datetime.now()
    return day.weekday() >= 5


def isUsTradingDay(dt=None):
    """미국 정규장 개장일이면 True."""
    day = dt or datetime.now()
    if isUsWeekend(day):
        return False
    if isUsHoliday(day):
        return False
    return True


def resolveUsSessionDtForKstMorning(kstDt=None):
    """
    KST 아침 리포트 시점 기준, 직전에 마감된 미국 세션 일자.
    예) KST 화 07:00 → 월(US), KST 월 07:00 → 금(US), KST 일 → None
    """
    dt = kstDt or datetime.now()
    # 직전 캘린더일부터 최대 7일 역추적
    d = (dt - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(7):
        if isUsTradingDay(d):
            return d
        d -= timedelta(days=1)
    return None
