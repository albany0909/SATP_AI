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

from api.getAcntEvlt import parsePadNum, parsePctNum
from api.getStkInfo import fetchStkInfo
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.trRateLimit import acquireTrSlot

_API_ID = "ka10001"
# 같은 종목 재조회 억제(초). 장중 봉 모양 판정에는 이 정도 지연이면 충분
_CACHE_SEC = 8.0
# 장외·휴장 등락/기준가는 거의 안 바뀌므로 길게 캐시 (보유표 등락 보정용)
_FLU_CACHE_SEC = 6 * 3600.0

_lock = threading.Lock()
_snapCache = {}  # stkCd → {at, ohlc}


def _absNum(raw):
    """키움 부호 포함 문자열 → 절댓값 int. 저가·시가는 하락 종목에서 '-' 가 붙는다."""
    n = parsePadNum(raw)
    return abs(n) if n is not None else None


def _fluCacheSec():
    """시세 구간이면 짧게, 장외면 길게."""
    try:
        from config.settingsStore import isNotifySessionNow

        if isNotifySessionNow():
            return _CACHE_SEC
    except Exception:
        pass
    return _FLU_CACHE_SEC


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
    ka10001 로 당일 시/고/저/현재가 + 누적거래량 (+등락·기준가).
    반환: {open, high, low, close, qty, fluRt, basePrc, src} 또는 None(조회 실패).
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
    basePrc = _absNum(body.get("base_pric"))
    fluRt = parsePctNum(body.get("flu_rt"))
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
        "fluRt": float(fluRt) if fluRt is not None else None,
        "basePrc": float(basePrc) if basePrc else None,
        "src": "ka10001",
    }
    with _lock:
        _snapCache[cd] = {"at": time.time(), "ohlc": dict(ohlc)}
    return dict(ohlc)


def fetchOfficialFlu(stkCd, preferMode=None):
    """
    키움 공식 등락률(flu_rt). 없으면 기준가 대비 현재가로 계산.
    반환: float(%) 또는 None.
    장외·재시작 후 보유표가 DB 낡은 일봉으로 +8%대 잘못 나오는 것을 막는다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return None
    mode = preferMode
    if mode not in ("paper", "real"):
        try:
            from utils.tradeSession import getDeskTradeMode

            mode = getDeskTradeMode() or "real"
        except Exception:
            mode = "real"
    snap = fetchLiveOhlc(cd, cacheSec=_fluCacheSec(), preferMode=mode)
    if not snap:
        return None
    flu = snap.get("fluRt")
    if flu is not None:
        try:
            return float(flu)
        except (TypeError, ValueError):
            pass
    base = snap.get("basePrc")
    cur = snap.get("close")
    try:
        base = float(base or 0)
        cur = float(cur or 0)
    except (TypeError, ValueError):
        return None
    if base > 0 and cur > 0:
        return (cur - base) / base * 100.0
    return None
