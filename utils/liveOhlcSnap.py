"""
장중 당일 OHLC 스냅 (ka10001 주식기본정보).

당일 1분봉(AI_MIN_STK_PRC)은 장마감 배치에서 적재되므로 15:18 매수 창에는 아직
DB에 없다. ka10001 은 시가·고가·저가·현재가·거래량을 한 번에 주므로, 장중
봉 모양·거래량 판정에 그대로 쓴다 (tr_docs/ka10001.json).

호출 한도는 utils.trRateLimit 공용 게이트로 지킨다 — 같은 ka10001 을
관심툴팁(stkWatchTipSnap)도 쓰기 때문에 모듈별로 따로 재면 합쳐서 초과한다.
"""

import threading
import time

from api.getAcntEvlt import parsePadNum
from api.getStkInfo import fetchStkInfo
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.trRateLimit import acquireTrSlot

_API_ID = "ka10001"
# 같은 종목 재조회 억제(초). 장중 봉 모양 판정에는 이 정도 지연이면 충분
_CACHE_SEC = 8.0

_lock = threading.Lock()
_snapCache = {}  # stkCd → {at, ohlc}


def _absNum(raw):
    """키움 부호 포함 문자열 → 절댓값 int. 저가·시가는 하락 종목에서 '-' 가 붙는다."""
    n = parsePadNum(raw)
    return abs(n) if n is not None else None


def peekLiveOhlc(stkCd, cacheSec=_CACHE_SEC):
    """캐시에 살아있는 스냅만 반환 (TR 호출 없음)."""
    cd = normStkCd(stkCd)
    if not cd:
        return None
    with _lock:
        rec = _snapCache.get(cd)
    if not rec:
        return None
    if time.time() - float(rec.get("at") or 0) > float(cacheSec):
        return None
    ohlc = dict(rec.get("ohlc") or {})
    if not ohlc:
        return None
    # 호출측이 TR 예산을 깎지 않도록 캐시 히트를 표시한다
    ohlc["cached"] = True
    return ohlc


def fetchLiveOhlc(stkCd, cacheSec=_CACHE_SEC, preferMode="paper"):
    """
    ka10001 로 당일 시/고/저/현재가 + 누적거래량.
    반환: {open, high, low, close, qty, src} 또는 None(조회 실패).
    """
    cd = normStkCd(stkCd)
    if not cd:
        return None
    cached = peekLiveOhlc(cd, cacheSec=cacheSec)
    if cached:
        return cached

    sess = acquireKiwoomSession(preferMode=preferMode)
    if not sess or not sess[2]:
        return None
    mode, hostUrl, token, tokenType = sess
    # 같은 ka10001 을 쓰는 다른 모듈과 합쳐서 한도를 넘지 않게 공용 게이트를 통과한다
    acquireTrSlot(_API_ID, mode=mode)
    got = fetchStkInfo(hostUrl, token, cd, tokenType=tokenType)
    if not got.get("ok"):
        termLog(f"[liveOhlc] {cd} ka10001 실패: {got.get('errMsg')}")
        return None

    body = got.get("body") or {}
    openPrc = _absNum(body.get("open_pric"))
    highPrc = _absNum(body.get("high_pric"))
    lowPrc = _absNum(body.get("low_pric"))
    curPrc = _absNum(body.get("cur_prc"))
    trdeQty = _absNum(body.get("trde_qty"))
    # 시가·현재가가 없으면 봉을 만들 수 없다 (거래정지·조회이상)
    if not openPrc or not curPrc:
        return None

    highPrc = highPrc or max(openPrc, curPrc)
    lowPrc = lowPrc or min(openPrc, curPrc)
    ohlc = {
        "open": float(openPrc),
        "high": float(max(highPrc, openPrc, curPrc)),
        "low": float(min(lowPrc, openPrc, curPrc)),
        "close": float(curPrc),
        "qty": int(trdeQty or 0),
        "src": "ka10001",
    }
    with _lock:
        _snapCache[cd] = {"at": time.time(), "ohlc": dict(ohlc)}
    return dict(ohlc)
