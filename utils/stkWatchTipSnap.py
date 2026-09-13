"""
관심종목 툴팁용 스냅샷.

- 업종: AI_STK_INFO.UP_NAME
- 시총·영업이익·ROE: ka10001 (mac / bus_pro / roe), 종목당 6시간 캐시
  모의 1초 1회라 백그라운드에서만 채운다 (한도는 utils.trRateLimit 공용 게이트).
"""

import threading
import time

from api.getAcntEvlt import parsePadNum
from api.getStkInfo import fetchStkInfo
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.trRateLimit import acquireTrSlot

_API_ID = "ka10001"
_CACHE_SEC = 6 * 3600
_FAIL_SEC = 10 * 60

_lock = threading.Lock()
# stkCd → {at, ok, mac, busPro, roe}
_basicCache = {}
_fetching = set()


def _parseFloat(raw):
    """부호·콤마 있는 숫자. ROE 등 소수 유지."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if not text or text == "-":
        return None
    sign = 1.0
    if text.startswith("-"):
        sign = -1.0
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    text = text.strip()
    if not text:
        return 0.0
    try:
        return sign * float(text)
    except (TypeError, ValueError):
        return None


def _parseEok(raw):
    """억원 정수. 파싱 실패 시 None."""
    n = parsePadNum(raw)
    if n is None:
        f = _parseFloat(raw)
        if f is None:
            return None
        return int(round(f))
    return int(n)


def getCachedBasic(stkCd):
    """유효 캐시만. 없으면 None."""
    cd = normStkCd(stkCd)
    if not cd:
        return None
    with _lock:
        rec = _basicCache.get(cd)
    if not rec:
        return None
    ttl = _CACHE_SEC if rec.get("ok") else _FAIL_SEC
    if (time.time() - float(rec.get("at") or 0)) >= ttl:
        return None
    # 매출액이 없는 옛 캐시는 영익률을 못 내니 다시 조회
    if rec.get("ok") and "saleAmt" not in rec:
        return None
    return dict(rec)


def listMissingBasicCds(stkCds):
    """ka10001 캐시가 없는 코드."""
    out = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if not cd or cd in seen:
            continue
        seen.add(cd)
        if getCachedBasic(cd) is None:
            out.append(cd)
    return out


def loadWatchTipMeta(stkCds):
    """
    DB만. {stkCd: {upName, listCnt, lastPx}}
    """
    cds = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            cds.append(cd)
    if not cds:
        return {}
    out = {cd: {"upName": "", "listCnt": None, "lastPx": None} for cd in cds}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(cds))
            cur.execute(
                f"""
                SELECT STK_CD, UP_NAME, LIST_CNT, LAST_PRICE
                FROM AI_STK_INFO
                WHERE STK_CD IN ({ph})
                """,
                cds,
            )
            for r in cur.fetchall() or []:
                cd = normStkCd(r.get("STK_CD"))
                if not cd or cd not in out:
                    continue
                out[cd]["upName"] = str(r.get("UP_NAME") or "").strip()
                try:
                    cnt = r.get("LIST_CNT")
                    out[cd]["listCnt"] = int(cnt) if cnt is not None else None
                except (TypeError, ValueError):
                    out[cd]["listCnt"] = None
                try:
                    px = r.get("LAST_PRICE")
                    out[cd]["lastPx"] = abs(int(px)) if px is not None else None
                except (TypeError, ValueError):
                    out[cd]["lastPx"] = None
    except Exception as err:
        termLog(f"[watchTip] 메타 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def mergeWatchTipSnap(meta, basic=None, curPx=None):
    """툴팁 한 줄용 dict."""
    meta = meta or {}
    basic = basic or {}
    mac = basic.get("mac")
    if mac is None:
        # ka10001 전이면 상장주식수×현재가(또는 전일종가)로 대략 시총
        cnt = meta.get("listCnt")
        px = curPx if curPx else meta.get("lastPx")
        try:
            if cnt and px:
                mac = int(round((int(cnt) * float(px)) / 100000000.0))
        except (TypeError, ValueError):
            mac = None
    busPro = basic.get("busPro")
    saleAmt = basic.get("saleAmt")
    busRt = None
    try:
        if busPro is not None and saleAmt not in (None, 0):
            busRt = (float(busPro) / float(saleAmt)) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        busRt = None
    return {
        "upName": meta.get("upName") or "",
        "mac": mac,
        "busPro": busPro,
        "busRt": busRt,
        "roe": basic.get("roe"),
        "basicOk": bool(basic.get("ok")),
    }


def fillBasicsFromKa10001(stkCds):
    """
    캐시 없는 종목만 ka10001. 호출 스레드에서 블로킹.
    반환: 이번에 채운 건수.
    """
    cds = listMissingBasicCds(stkCds)
    if not cds:
        return 0
    with _lock:
        todo = [c for c in cds if c not in _fetching]
        for c in todo:
            _fetching.add(c)
    if not todo:
        return 0
    filled = 0
    try:
        from utils.dayPrcLoadService import acquireKiwoomSession

        sess = acquireKiwoomSession()
        if not sess:
            termLog("[watchTip] 키움 토큰 없음 — 시총/영업이익/ROE 생략")
            now = time.time()
            with _lock:
                for cd in todo:
                    _basicCache[cd] = {"at": now, "ok": False}
            return 0
        mode, hostUrl, token, tokenType = sess
        for cd in todo:
            # 이평지지(liveOhlcSnap)도 같은 ka10001 을 쓴다 — 공용 게이트로 한도를 지킨다
            acquireTrSlot(_API_ID, mode=mode)
            got = fetchStkInfo(hostUrl, token, cd, tokenType=tokenType)
            now = time.time()
            rec = {"at": now, "ok": False, "mac": None, "busPro": None, "saleAmt": None, "roe": None}
            if got.get("ok"):
                body = got.get("body") or {}
                rec["ok"] = True
                rec["mac"] = _parseEok(body.get("mac"))
                rec["busPro"] = _parseEok(body.get("bus_pro"))
                rec["saleAmt"] = _parseEok(body.get("sale_amt"))
                rec["roe"] = _parseFloat(body.get("roe"))
                filled += 1
            with _lock:
                _basicCache[cd] = rec
    except Exception as err:
        termLog(f"[watchTip] ka10001 실패: {err}")
        now = time.time()
        with _lock:
            for cd in todo:
                if cd not in _basicCache:
                    _basicCache[cd] = {"at": now, "ok": False}
    finally:
        with _lock:
            for cd in todo:
                _fetching.discard(cd)
    if filled:
        termLog(f"[watchTip] ka10001 캐시 {filled}/{len(todo)}종")
    return filled
