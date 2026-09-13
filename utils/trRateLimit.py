"""
키움 REST TR 호출 한도 게이트 (tr_docs/apiRateLimits.md).

- 모의: 계좌별(토큰별) **TR 1개당 1초 1회** → 같은 apiId 는 최소 1초 간격
- 실전: **조회 TR 1초 5회**(합산) → 여유를 두고 간격·초당 개수를 함께 본다

같은 TR 을 여러 모듈이 동시에 부를 수 있어(예: ka10001 을 이평지지와 관심툴팁이
같이 쓴다) 프로세스 공용 게이트로 둔다. 모듈마다 따로 sleep 하면 각자는 지켜도
합쳐서 초과할 수 있다.
"""

import threading
import time
from collections import deque

# 모의: 같은 TR 1초 1회 (여유 0.05초)
_PAPER_TR_GAP_SEC = 1.05
# 실전 조회: 1초 5회 → 0.25초 간격 + 초당 4회로 여유 1회 남김
_REAL_GAP_SEC = 0.25
_REAL_MAX_PER_SEC = 4

_lock = threading.Lock()
# apiId → 마지막 호출 시각
_lastAtByTr = {}
# 실전 합산 창(최근 1초) 호출 시각
_recentAt = deque()


def _pruneRecent(now):
    while _recentAt and now - _recentAt[0] > 1.0:
        _recentAt.popleft()


def acquireTrSlot(apiId, mode="paper"):
    """
    apiId 호출 직전에 부른다. 한도를 넘지 않을 때까지 대기 후 반환.
    반환: 실제로 기다린 초(로그·계측용).

    게이트를 잡은 채로 대기한다 — 대기 중 다른 스레드가 끼어들어
    같은 창에서 초과 호출하는 것을 막으려면 직렬화가 필요하다.
    """
    key = str(apiId or "").strip() or "unknown"
    isPaper = str(mode or "paper").lower() != "real"
    waited = 0.0
    with _lock:
        while True:
            now = time.time()
            _pruneRecent(now)
            gapNeed = _PAPER_TR_GAP_SEC if isPaper else _REAL_GAP_SEC
            waits = []

            # 1) 같은 TR 간격
            lastAt = float(_lastAtByTr.get(key) or 0)
            if lastAt:
                waits.append(gapNeed - (now - lastAt))
            # 2) 실전은 조회 TR 합산 초당 개수도 본다
            if (not isPaper) and len(_recentAt) >= _REAL_MAX_PER_SEC:
                waits.append(1.0 - (now - _recentAt[0]))

            need = max(waits) if waits else 0.0
            if need <= 0:
                _lastAtByTr[key] = now
                _recentAt.append(now)
                return waited
            time.sleep(need)
            waited += need


def peekTrLastAt(apiId):
    """마지막 호출 시각 (없으면 None). 진단용."""
    with _lock:
        at = _lastAtByTr.get(str(apiId or "").strip())
    return float(at) if at else None
