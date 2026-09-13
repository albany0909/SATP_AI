"""
예약 명령 목록(schedule.json) 로드·저장.
항목: { "runHm": "HH:MM", "cmdText": "buy 005930 5", "once": false }
"""

import json
from pathlib import Path

# config/data/schedule.json
_DATA_DIR = Path(__file__).resolve().parent / "data"
_SCHEDULE_PATH = _DATA_DIR / "schedule.json"


def _readJson(path):
    """JSON 파일 로드. 실패 시 None."""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data
    except Exception:
        return None


def _writeJson(path, data):
    """JSON 파일 저장. 디렉터리가 없으면 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def _normalizeItem(item):
    """
    예약 항목을 표준 dict로 정규화한다.
    once 키가 없으면 매일 예약(False)으로 본다.
    """
    if not isinstance(item, dict):
        return None
    runHm = str(item.get("runHm") or "").strip()
    cmdText = str(item.get("cmdText") or "").strip()
    if not runHm or not cmdText:
        return None
    once = bool(item.get("once", False))
    return {"runHm": runHm, "cmdText": cmdText, "once": once}


def loadSchedule():
    """
    예약 목록(list)을 반환한다.
    파일이 없거나 형식이 잘못되면 빈 목록으로 복구한다.
    """
    data = _readJson(_SCHEDULE_PATH)
    if not isinstance(data, list):
        empty = []
        _writeJson(_SCHEDULE_PATH, empty)
        return empty

    cleaned = []
    for item in data:
        normalized = _normalizeItem(item)
        if normalized:
            cleaned.append(normalized)
    return cleaned


def saveSchedule(items):
    """예약 목록을 schedule.json에 저장한다."""
    safe = []
    for item in items or []:
        normalized = _normalizeItem(item)
        if normalized:
            safe.append(normalized)
    _writeJson(_SCHEDULE_PATH, safe)
    return safe


def addSchedule(runHm, cmdText, once=False):
    """
    예약을 추가하고 저장한다.
    once=True면 1회 실행 후 삭제 대상.
    반환: 추가된 항목의 1-based 일련번호
    """
    items = loadSchedule()
    items.append({"runHm": runHm, "cmdText": cmdText, "once": bool(once)})
    saveSchedule(items)
    return len(items)


def removeSchedule(serialNo):
    """
    1-based 일련번호로 예약을 삭제한다.
    성공: (True, 삭제된 항목)
    실패: (False, None)
    """
    items = loadSchedule()
    idx = serialNo - 1
    if idx < 0 or idx >= len(items):
        return False, None
    removed = items.pop(idx)
    saveSchedule(items)
    return True, removed


def clearSchedule():
    """
    모든 예약을 삭제한다.
    반환: 삭제된 건수
    """
    items = loadSchedule()
    count = len(items)
    saveSchedule([])
    return count


def removeOnceItem(runHm, cmdText):
    """
    실행 완료된 1회 예약 중 일치하는 첫 항목을 삭제한다.
    성공: True, 없으면 False.
    """
    items = loadSchedule()
    for idx, item in enumerate(items):
        if (
            item.get("once")
            and item.get("runHm") == runHm
            and item.get("cmdText") == cmdText
        ):
            items.pop(idx)
            saveSchedule(items)
            return True
    return False
