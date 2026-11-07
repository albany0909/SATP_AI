"""
네이버 증권 테마 ↔ 종목 매핑.
- 구성종목(종목→테마)은 하루 1회 백그라운드로 모아 config/data/naverThemeMap_YYYYMMDD.json 에 캐시.
- 테마 등락률은 5분마다 목록 API(3회 호출)로 갱신.
- 화면 스레드는 캐시만 읽는다(naverThemesFor 는 절대 네트워크를 기다리지 않음).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from utils.termLog import termLog

_BASE = "https://m.stock.naver.com/api/stocks/theme"
_HDR = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 8
_GAP = 0.15  # 요청 간격 (레이트리밋 배려)
_RATE_TTL = 300
_DIR = Path(__file__).resolve().parents[1] / "config" / "data"

_LOCK = threading.Lock()
_STATE = {"ymd": "", "byCd": {}, "names": {}, "rates": {}, "rateAt": 0.0, "busy": False}


def _get(url):
    r = requests.get(url, headers=_HDR, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _fetchThemeList():
    """{no: (name, changeRate)}"""
    out = {}
    page = 1
    while page <= 10:
        d = _get(f"{_BASE}?page={page}&pageSize=100")
        for g in d.get("groups") or []:
            try:
                rate = float(str(g.get("changeRate") or "0").replace(",", ""))
            except ValueError:
                rate = 0.0
            out[int(g["no"])] = (str(g.get("name") or ""), rate)
        total = int(d.get("totalCount") or 0)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(_GAP)
    return out


def _fetchMembers(no):
    cds = []
    page = 1
    while page <= 5:
        d = _get(f"{_BASE}/{no}?page={page}&pageSize=100")
        for s in d.get("stocks") or []:
            cd = str(s.get("itemCode") or "").strip()
            if len(cd) == 6:
                cds.append(cd)
        total = int(d.get("totalCount") or 0)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(_GAP)
    return cds


def _cachePath(ymd):
    return _DIR / f"naverThemeMap_{ymd}.json"


def _loadCache(ymd):
    p = _cachePath(ymd)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {k: list(v) for k, v in (d.get("byCd") or {}).items()}, {
            int(k): v for k, v in (d.get("names") or {}).items()
        }
    except Exception:
        return None


def _buildWorker(ymd):
    try:
        got = _loadCache(ymd)
        themes = _fetchThemeList()
        rates = {no: r for no, (_n, r) in themes.items()}
        if got is None:
            byCd = {}
            names = {no: n for no, (n, _r) in themes.items()}
            for no in themes:
                try:
                    for cd in _fetchMembers(no):
                        byCd.setdefault(cd, []).append(no)
                except Exception as err:
                    termLog(f"[naverTheme] 테마 {no} 구성 조회 실패: {err}")
                time.sleep(_GAP)
            try:
                _DIR.mkdir(parents=True, exist_ok=True)
                _cachePath(ymd).write_text(
                    json.dumps({"byCd": byCd, "names": names}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as err:
                termLog(f"[naverTheme] 캐시 저장 실패: {err}")
            termLog(f"[naverTheme] 테마 {len(names)}개 · 종목 {len(byCd)} 매핑 완료")
        else:
            byCd, names = got
            for no, (n, _r) in themes.items():
                names.setdefault(no, n)
        with _LOCK:
            _STATE.update(ymd=ymd, byCd=byCd, names=names, rates=rates, rateAt=time.time())
    except Exception as err:
        termLog(f"[naverTheme] 매핑 실패 (키움 테마로 대체): {err}")
    finally:
        with _LOCK:
            _STATE["busy"] = False


def _rateWorker():
    try:
        themes = _fetchThemeList()
        with _LOCK:
            _STATE["rates"] = {no: r for no, (_n, r) in themes.items()}
            _STATE["rateAt"] = time.time()
    except Exception as err:
        termLog(f"[naverTheme] 등락률 갱신 실패: {err}")
        with _LOCK:
            _STATE["rateAt"] = time.time()  # 실패해도 5분 뒤 재시도
    finally:
        with _LOCK:
            _STATE["busy"] = False


def _kick():
    ymd = datetime.now().strftime("%Y%m%d")
    with _LOCK:
        if _STATE["busy"]:
            return
        if _STATE["ymd"] != ymd:
            _STATE["busy"] = True
            target, args = _buildWorker, (ymd,)
        elif time.time() - _STATE["rateAt"] > _RATE_TTL:
            _STATE["busy"] = True
            target, args = _rateWorker, ()
        else:
            return
    threading.Thread(target=target, args=args, daemon=True, name="naverTheme").start()


def naverThemesFor(stkCd, n=2):
    """종목의 네이버 테마명 (당일 테마 등락률 높은 순) 최대 n개. 캐시 없으면 []."""
    _kick()
    cd = str(stkCd or "").strip().upper().lstrip("A")[:6]
    with _LOCK:
        nos = list(_STATE["byCd"].get(cd) or [])
        rates = _STATE["rates"]
        names = _STATE["names"]
        nos.sort(key=lambda x: -float(rates.get(x, -999.0)))
        return [names[x] for x in nos[:n] if names.get(x)]


def naverThemeReady():
    with _LOCK:
        return bool(_STATE["byCd"])


def naverThemesWithRate(stkCd, n=2):
    """[(테마명, 당일 테마 등락률 or None)] 강한 순 최대 n개. 캐시 없으면 []."""
    _kick()
    cd = str(stkCd or "").strip().upper().lstrip("A")[:6]
    with _LOCK:
        nos = list(_STATE["byCd"].get(cd) or [])
        rates = _STATE["rates"]
        names = _STATE["names"]
        nos.sort(key=lambda x: -float(rates.get(x, -999.0)))
        out = []
        for x in nos[:n]:
            if names.get(x):
                r = rates.get(x)
                out.append((names[x], float(r) if r is not None else None))
        return out
