"""
관심종목 상승 순위.
금일·주간·월간: 상승률 백분위 50% + 거래대금 백분위 50%, 상위 20.
업종: 키움 업종 등락률 내림차순.
전업종 조회라 백그라운드에서 모으고, 화면은 캐시만 읽는다.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from utils.termLog import termLog

_TOP_N = 20
_LOCK = threading.Lock()
_STARTED = False
_STATE = {
    "status": "idle",
    "msg": "",
    "updatedAt": "",
    "day": [],
    "week": [],
    "month": [],
    "inds": [],
}

# 규모·지수 버킷은 업종명으로 쓰지 않는다
_INDEX_HINTS = (
    "KOSDAQ",
    "KOSPI",
    "코스피",
    "고배당",
    "변동성",
    "벤처",
    "우량",
    "신성장",
    "중견",
    "SMALL",
    "MID",
    "글로벌지수",
    "인버스",
)


def _withNaverTheme(rows):
    """테마 칸을 네이버 증권 테마(당일 강한 순 2개)로 교체. 실패·미적재면 키움 테마 유지."""
    try:
        from utils.naverThemeMap import naverThemesFor
    except Exception:
        return list(rows)
    out = []
    for r in rows:
        try:
            names = naverThemesFor(r.get("stkCd"), 2)
        except Exception:
            names = []
        if names:
            r2 = dict(r)
            r2["kiwoomInds"] = r.get("indsNm")
            r2["indsNm"] = "·".join(names)
            r2["themeSrc"] = "naver"
            out.append(r2)
        else:
            out.append(r)
    return out


def riseRankSnapshot():
    """화면용 복사본."""
    with _LOCK:
        return {
            "status": _STATE["status"],
            "msg": _STATE["msg"],
            "updatedAt": _STATE["updatedAt"],
            "day": _withNaverTheme(_STATE["day"]),
            "week": _withNaverTheme(_STATE["week"]),
            "month": _withNaverTheme(_STATE["month"]),
            "inds": list(_STATE["inds"]),
        }


def startRiseRankBoard(force=False):
    """금일 순위를 먼저 채운 뒤 주·월을 이어서 채운다. force=True면 재스캔."""
    global _STARTED
    with _LOCK:
        if _STARTED and not force:
            return
        if _RUNNING:
            return
        _STARTED = True
        globals()["_RUNNING"] = True
        _STATE["status"] = "scan"
        _STATE["msg"] = "종목군 조회 중"
    threading.Thread(target=_run, name="riseRankBoard", daemon=True).start()
    _startSched()


def restartRiseRankBoard():
    """주도주 탭 재조회."""
    global _STARTED
    with _LOCK:
        _STARTED = False
        _STATE["status"] = "scan"
        _STATE["msg"] = "재조회 중"
        _STATE["updatedAt"] = ""
        _STATE["day"] = []
        _STATE["week"] = []
        _STATE["month"] = []
        _STATE["inds"] = []
    startRiseRankBoard(force=True)


def _set(**kwargs):
    with _LOCK:
        _STATE.update(kwargs)


def _isIndexName(name):
    text = str(name or "").upper()
    return any(h.upper() in text for h in _INDEX_HINTS)


def _pctRank(values):
    """값이 클수록 1에 가깝다. None 은 최하위."""
    n = len(values)
    if n <= 1:
        return [1.0] * n
    order = sorted(
        range(n),
        key=lambda i: (values[i] is None, float(values[i] or 0)),
    )
    out = [0.0] * n
    for rank, i in enumerate(order):
        out[i] = rank / (n - 1)
    return out


def _topScored(rows, fluKey, amtKey):
    """상승률·거래대금 백분위 5:5 상위 20."""
    if not rows:
        return []
    fluR = _pctRank([r.get(fluKey) for r in rows])
    amtR = _pctRank([r.get(amtKey) for r in rows])
    scored = []
    for i, row in enumerate(rows):
        score = 0.5 * fluR[i] + 0.5 * amtR[i]
        item = dict(row)
        item["score"] = score
        scored.append(item)
    scored.sort(key=lambda r: (-float(r.get("score") or 0), -(float(r.get(fluKey) or 0))))
    return scored[:_TOP_N]


def _run():
    global _RUNNING
    _RUNNING = True
    try:
        _runInner()
    finally:
        _RUNNING = False


def _runInner():
    try:
        from api.getAllIndsIdex import (
            AGG_INDS_CDS,
            INDS_CD_KOSDAQ,
            INDS_CD_KOSPI,
            MRKT_TP_KOSDAQ,
            MRKT_TP_KOSPI,
            fetchAllIndsIdex,
        )
        from api.getDayStkPrc import fetchDayStkPrcRange
        from api.getIndsStkPrc import fetchIndsStkPrc
        from utils.dayPrcLoadService import acquireKiwoomSession

        session = acquireKiwoomSession(preferMode="paper") or acquireKiwoomSession()
        if not session:
            _set(status="err", msg="키움 세션 없음")
            termLog("[riseRank] 세션 없음")
            return
        mode, host, token, tt = session
        isPaper = mode == "paper"
        wait = 1.05 if isPaper else 0.25
        _set(status="scan", msg="테마 구분 조회 중")
        themeByCd, themeRows, themeStks = _loadThemeGroups(host, token, tt, wait)

        # 테마 구성종목만으로 우선 금일 순위 공개 (업종 전수 스캔 대기 없이 주도주 탭 표시)
        byCd = {}
        for cd, rec in (themeStks or {}).items():
            row = dict(rec)
            labels = themeByCd.get(cd) or []
            if labels:
                row["indsNm"] = "·".join(labels)
            byCd[cd] = row
        indsRows = list(themeRows or [])
        indsRows.sort(
            key=lambda r: (
                r.get("fluRt") is None,
                -(float(r["fluRt"]) if r.get("fluRt") is not None else 0),
            )
        )
        dayTop = _topScored(list(byCd.values()), "fluRt", "amt")
        stamp = datetime.now().strftime("%H:%M")
        _set(
            status="daybar",
            msg="테마 기준 순위 · 업종 보강 중",
            updatedAt=stamp,
            day=dayTop,
            inds=indsRows,
        )
        termLog(
            f"[riseRank] 테마선공개 금일 {len(dayTop)} / 테마 {len(indsRows)} / 종목 {len(byCd)}"
        )

        markets = [
            ("코스피", INDS_CD_KOSPI, MRKT_TP_KOSPI),
            ("코스닥", INDS_CD_KOSDAQ, MRKT_TP_KOSDAQ),
        ]
        # 업종 스캔은 보강용. 실패·지연해도 테마 선공개 유지
        try:
            for i, (mkt, root, mrktTp) in enumerate(markets):
                if i:
                    time.sleep(wait)
                got = fetchAllIndsIdex(
                    host, token, indsCd=root, tokenType=tt, sleepSec=wait
                )
                if not got.get("ok"):
                    termLog(f"[riseRank] {mkt} 업종 실패: {got.get('errMsg')}")
                    continue
                groups = [
                    r
                    for r in (got.get("rows") or [])
                    if r.get("indsCd") not in AGG_INDS_CDS
                ]
                # 등락 상위 업종만 보강 (전체 스캔은 모의·휴장에 너무 김)
                ranked = sorted(
                    groups,
                    key=lambda r: (
                        r.get("fluRt") is None,
                        -(float(r["fluRt"]) if r.get("fluRt") is not None else 0),
                    ),
                )[:25]
                for inds in ranked:
                    name = inds.get("indsNm") or inds.get("indsCd")
                    if _isIndexName(name):
                        continue
                    time.sleep(wait)
                    stkGot = fetchIndsStkPrc(
                        host,
                        token,
                        mrktTp=mrktTp,
                        indsCd=inds["indsCd"],
                        tokenType=tt,
                        sleepSec=wait,
                    )
                    if not stkGot.get("ok"):
                        continue
                    for stk in stkGot.get("rows") or []:
                        cd = stk.get("stkCd")
                        if not cd:
                            continue
                        px = int(stk.get("curPrc") or 0)
                        qty = int(stk.get("nowTrdeQty") or 0)
                        amt = px * qty
                        fluStk = stk.get("fluRt")
                        labels = themeByCd.get(cd) or []
                        rec = {
                            "stkCd": cd,
                            "stkNm": stk.get("stkNm") or cd,
                            "mkt": mkt,
                            "indsNm": "·".join(labels) if labels else name,
                            "fluRt": float(fluStk) if fluStk is not None else None,
                            "amt": amt,
                            "curPrc": px,
                        }
                        prev = byCd.get(cd)
                        if prev is None:
                            byCd[cd] = rec
                        else:
                            # 대금·등락이 더 확실한 업종 시세로 보강
                            if amt > float(prev.get("amt") or 0):
                                prev["amt"] = amt
                            if rec.get("fluRt") is not None:
                                prev["fluRt"] = rec["fluRt"]
                            if px > 0:
                                prev["curPrc"] = px
                            if not prev.get("stkNm"):
                                prev["stkNm"] = rec["stkNm"]
            dayTop = _topScored(list(byCd.values()), "fluRt", "amt")
            _set(
                status="daybar",
                msg="주·월 수익률 계산 중",
                updatedAt=datetime.now().strftime("%H:%M"),
                day=dayTop,
                inds=indsRows,
            )
            termLog(
                f"[riseRank] 금일 {len(dayTop)} / 업종보강 / 종목 {len(byCd)}"
            )
        except Exception as err:
            termLog(f"[riseRank] 업종 보강 건너뜀: {err}")

        endDt = datetime.now().strftime("%Y%m%d")
        startDt = (datetime.now() - timedelta(days=70)).strftime("%Y%m%d")
        weekRows = []
        monthRows = []
        r20Cur = []
        r20Prv = []
        cds = list(byCd.keys())
        for n, cd in enumerate(cds, start=1):
            time.sleep(wait)
            day = fetchDayStkPrcRange(
                host, token, cd, startDt, endDt, tokenType=tt, isPaper=isPaper
            )
            base = byCd[cd]
            if not day.get("ok"):
                continue
            bars = sorted(day.get("rows") or [], key=lambda r: r["TRADE_DT"])
            w = _periodRow(base, bars, 5)
            m = _periodRow(base, bars, 20)
            try:
                r20Cur.append(_r20Item(base, bars, 0))
                r20Prv.append(_r20Item(base, bars, 1))
            except Exception:
                pass
            if w:
                weekRows.append(w)
            if m:
                monthRows.append(m)
            if n % 40 == 0:
                _set(
                    week=_topScored(weekRows, "fluRt", "amt"),
                    month=_topScored(monthRows, "fluRt", "amt"),
                    msg=f"주·월 계산 {n}/{len(cds)}",
                    updatedAt=datetime.now().strftime("%H:%M"),
                )
        _set(
            status="ready",
            msg="",
            updatedAt=datetime.now().strftime("%H:%M"),
            week=_topScored(weekRows, "fluRt", "amt"),
            month=_topScored(monthRows, "fluRt", "amt"),
        )
        termLog("[riseRank] 주·월 순위 완료")
        try:
            _r20Finish(r20Cur, r20Prv)
        except Exception as err:
            termLog(f"[riseRank20] 순위 계산 실패: {err}")
    except Exception as err:
        _set(status="err", msg=str(err))
        termLog(f"[riseRank] 실패: {err}")


def _loadThemeGroups(host, token, tt, wait):
    """키움 테마명·구성종목 시세. (labels, themeRows, stockMap) 반환."""
    from api.getThemaCompStk import fetchThemaCompStk
    from api.getThemaGrp import FLU_PL_AMT_TP_TOP_FLU, QRY_TP_ALL, fetchThemaGrp

    themes = []
    try:
        got = fetchThemaGrp(
            host,
            token,
            qryTp=QRY_TP_ALL,
            themaNm="",
            fluPlAmtTp=FLU_PL_AMT_TP_TOP_FLU,
            tokenType=tt,
            sleepSec=wait,
        )
        if not got.get("ok"):
            termLog(f"[riseRank] 테마목록 실패: {got.get('errMsg')}")
            return {}, [], {}
        for row in got.get("rows") or []:
            code = str(row.get("themaCd") or "").strip()
            name = str(row.get("themaNm") or "").strip()
            if not code or not name:
                continue
            flu = row.get("fluRt")
            themes.append(
                {
                    "code": code,
                    "name": name,
                    "flu": float(flu) if flu is not None else None,
                    "n": int(row.get("stkNum") or 0),
                }
            )
    except Exception as err:
        termLog(f"[riseRank] 테마목록 실패: {err}")
        return {}, [], {}

    groupRows = [
        {"mkt": "테마", "indsNm": t["name"], "fluRt": t["flu"]}
        for t in themes
    ]
    pairs = {}
    stockMap = {}
    for t in themes:
        if int(t.get("n") or 0) <= 0:
            continue
        time.sleep(wait)
        try:
            comp = fetchThemaCompStk(
                host, token, themaGrpCd=t["code"], tokenType=tt, sleepSec=wait
            )
        except Exception as err:
            termLog(f"[riseRank] 테마 {t['name']} 실패: {err}")
            continue
        if not comp.get("ok"):
            continue
        flu = t["flu"] if t["flu"] is not None else -999.0
        for stk in comp.get("rows") or []:
            cd = stk.get("stkCd")
            if not cd:
                continue
            pairs.setdefault(cd, []).append((flu, t["name"]))
            px = int(stk.get("curPrc") or 0)
            qty = int(stk.get("nowTrdeQty") or stk.get("trdeQty") or 0)
            fluStk = stk.get("fluRt")
            amt = px * qty if px and qty else abs(float(stk.get("trdeAmt") or 0) or 0)
            prev = stockMap.get(cd)
            rec = {
                "stkCd": cd,
                "stkNm": stk.get("stkNm") or cd,
                "mkt": "테마",
                "indsNm": t["name"],
                "fluRt": float(fluStk) if fluStk is not None else None,
                "amt": amt,
                "curPrc": px,
            }
            if prev is None:
                stockMap[cd] = rec
            else:
                if amt > float(prev.get("amt") or 0):
                    prev["amt"] = amt
                if rec.get("fluRt") is not None and (
                    prev.get("fluRt") is None
                    or abs(float(rec["fluRt"])) >= abs(float(prev.get("fluRt") or 0))
                ):
                    prev["fluRt"] = rec["fluRt"]
                if px > 0:
                    prev["curPrc"] = px
                if stk.get("stkNm"):
                    prev["stkNm"] = stk.get("stkNm")

    byStk = {}
    for cd, items in pairs.items():
        items.sort(key=lambda x: -float(x[0]))
        names = []
        for _flu, name in items:
            if name not in names:
                names.append(name)
            if len(names) >= 2:
                break
        byStk[cd] = names
        if cd in stockMap and names:
            stockMap[cd]["indsNm"] = "·".join(names)
    termLog(f"[riseRank] 키움테마 {len(groupRows)}개 · 종목 {len(byStk)}")
    return byStk, groupRows, stockMap


def _periodRow(base, bars, nDays):
    """최근 n거래일 수익률·거래대금 합. 봉이 부족하면 None."""
    if len(bars) < nDays + 1:
        return None
    window = bars[-(nDays + 1) :]
    prevClose = int(window[0].get("CLOSE_PRC") or 0)
    lastClose = int(window[-1].get("CLOSE_PRC") or 0)
    if prevClose <= 0 or lastClose <= 0:
        return None
    amt = 0
    for bar in window[1:]:
        px = int(bar.get("CLOSE_PRC") or 0)
        qty = int(bar.get("TRDE_QTY") or 0)
        amt += px * qty
    return {
        "stkCd": base["stkCd"],
        "stkNm": base["stkNm"],
        "mkt": base["mkt"],
        "indsNm": base["indsNm"],
        "fluRt": (lastClose / prevClose - 1.0) * 100.0,
        "amt": amt,
        "curPrc": lastClose,
    }



# ── 주도주 이슈용 뉴스 헤드라인 캐시 (네이버 종목뉴스, 백그라운드) ──
_NEWS_CACHE = {}
_NEWS_PEND = set()
_NEWS_LOCK = threading.Lock()
_NEWS_TTL = 1800


def _newsWorker(cd):
    """네이버 모바일 종목뉴스 API에서 최신 헤드라인 1건 (이슈성 우선)."""
    title = ""
    try:
        import json
        from html import unescape
        from urllib.request import Request, urlopen

        url = f"https://m.stock.naver.com/api/news/stock/{cd}?pageSize=8&page=1"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
        with urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        titles = []
        for grp in data or []:
            for it in (grp or {}).get("items") or []:
                t = unescape(str(it.get("titleFull") or it.get("title") or "")).strip()
                if t:
                    titles.append(t)
        try:
            from research.naverStkNews import isIssueTitle
        except Exception:
            isIssueTitle = lambda _t: False
        title = next((t for t in titles if isIssueTitle(t)), titles[0] if titles else "")
    except Exception as err:
        termLog(f"[riseNews] {cd} 실패: {err}")
    with _NEWS_LOCK:
        _NEWS_CACHE[cd] = (time.time(), title)
        _NEWS_PEND.discard(cd)


def riseNewsTitle(cd):
    """캐시된 최신 이슈 헤드라인. 없으면 백그라운드 조회 시작 후 '' 반환."""
    cd = str(cd or "").strip()
    if not cd:
        return ""
    with _NEWS_LOCK:
        hit = _NEWS_CACHE.get(cd)
        if hit and time.time() - hit[0] < _NEWS_TTL:
            return hit[1]
        if cd in _NEWS_PEND:
            return hit[1] if hit else ""
        _NEWS_PEND.add(cd)
    threading.Thread(target=_newsWorker, args=(cd,), name=f"riseNews{cd}", daemon=True).start()
    return hit[1] if hit else ""


# ── 20영업일 누적 등락률 순위 (매일 16:00 스냅샷, config/data/riseRank20d_YYYYMMDD.json) ──
import json as _json
from pathlib import Path as _Path

_R20_TOP_N = 30
_R20_MIN_AMT = 10_000_000_000  # 20일 거래대금 합 100억 미만(일평균 5억 미만) 제외
_R20_HOUR = 16
_R20_DIR = _Path(__file__).resolve().parents[1] / "config" / "data"
_R20 = {"date": "", "prevDate": "", "savedAt": "", "live": False, "rows": [], "themes": []}
_RUNNING = False
_R20_TRIED = ""
_SCHED_STARTED = False


def _r20Path(ymd):
    return _R20_DIR / f"riseRank20d_{ymd}.json"


def _r20Load(ymd):
    try:
        with open(_r20Path(ymd), encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def _r20Dates():
    out = []
    try:
        for p in _R20_DIR.glob("riseRank20d_*.json"):
            d = p.stem.split("_")[-1]
            if len(d) == 8 and d.isdigit():
                out.append(d)
    except Exception:
        pass
    return sorted(out)


def _r20Prev(ymd):
    ds = [d for d in _r20Dates() if d < ymd]
    return _r20Load(ds[-1]) if ds else None


def _r20Item(base, bars, off):
    """off=0 최근봉 기준, off=1 직전 거래일 기준(백필). 21봉 미만이면 None."""
    b = bars[: len(bars) - off] if off else bars
    if len(b) < 21:
        return None
    w = b[-21:]
    p0 = int(w[0].get("CLOSE_PRC") or 0)
    p1 = int(w[-2].get("CLOSE_PRC") or 0)
    last = int(w[-1].get("CLOSE_PRC") or 0)
    if p0 <= 0 or last <= 0:
        return None
    amt = 0
    for bar in w[1:]:
        amt += int(bar.get("CLOSE_PRC") or 0) * int(bar.get("TRDE_QTY") or 0)
    return {
        "stkCd": base["stkCd"],
        "stkNm": base["stkNm"],
        "mkt": base.get("mkt"),
        "indsNm": base.get("indsNm"),
        "ret20": round((last / p0 - 1.0) * 100.0, 2),
        "dayFlu": round((last / p1 - 1.0) * 100.0, 2) if p1 > 0 else None,
        "amt": amt,
        "curPrc": last,
        "date": str(w[-1].get("TRADE_DT") or ""),
    }


def _r20Rank(items):
    pool = [
        r for r in items
        if r and r.get("ret20") is not None and float(r.get("amt") or 0) >= _R20_MIN_AMT
    ]
    pool.sort(key=lambda r: -float(r["ret20"]))
    out = []
    for i, r in enumerate(pool[:_R20_TOP_N], start=1):
        r = dict(r)
        r["rank"] = i
        out.append(r)
    return out


def _r20Diff(rows, prev):
    """직전 스냅샷 대비: 신규 NEW, 상승 ▲n, 하락 ▼n, 동일 '-'."""
    prevRank = {
        str(r.get("stkCd")): int(r.get("rank") or 0)
        for r in ((prev or {}).get("rows") or [])
    }
    for r in rows:
        p = prevRank.get(str(r.get("stkCd")))
        if p is None:
            r["chg"], r["chgN"] = "NEW", None
        else:
            d = p - int(r["rank"])
            r["chgN"] = d
            r["chg"] = f"▲{d}" if d > 0 else (f"▼{-d}" if d < 0 else "-")
    return rows


def _themeOf(cd, fallback):
    try:
        from utils.naverThemeMap import naverThemesWithRate

        got = naverThemesWithRate(cd, 2)
    except Exception:
        got = []
    if got:
        return got
    txt = str(fallback or "")
    for sep in ("·", "/", "|"):
        txt = txt.replace(sep, ",")
    names = [x.strip() for x in txt.split(",") if x.strip()]
    return [(names[0], None)] if names else []


def _r20Themes(rows):
    """상위 리스트 종목을 테마별로 묶어 점수화 (2종목 이상 테마, 상위 3).
    점수 = 0.5×평균 20일누적% + 0.3×평균 당일% + 0.2×네이버 테마 당일% + 1.0×(종목수-1)
    """
    grp = {}
    for r in rows:
        for name, rate in _themeOf(r.get("stkCd"), r.get("indsNm")):
            g = grp.setdefault(name, {"rate": None, "stks": []})
            if rate is not None:
                g["rate"] = rate
            g["stks"].append(r)
    out = []
    for name, g in grp.items():
        st = g["stks"]
        if len(st) < 2:
            continue
        a20 = sum(float(s["ret20"]) for s in st) / len(st)
        aDay = sum(float(s.get("dayFlu") or 0) for s in st) / len(st)
        rt = float(g["rate"] or 0)
        score = 0.5 * a20 + 0.3 * aDay + 0.2 * rt + 1.0 * (len(st) - 1)
        lead = sorted(st, key=lambda s: -float(s["ret20"]))[:3]
        out.append({
            "theme": name,
            "score": round(score, 1),
            "cnt": len(st),
            "avg20": round(a20, 2),
            "avgDay": round(aDay, 2),
            "themeRt": g["rate"],
            "leaders": [
                {"stkCd": s["stkCd"], "stkNm": s["stkNm"], "ret20": s["ret20"]} for s in lead
            ],
        })
    out.sort(key=lambda x: -x["score"])
    return out[:3]


def _r20Save(ymd, rows, themes, backfill=False):
    data = {
        "date": ymd,
        "savedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "topN": _R20_TOP_N,
        "minAmt20": _R20_MIN_AMT,
        "backfill": backfill,
        "rows": rows,
        "themes": themes,
    }
    try:
        _R20_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _r20Path(ymd).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(_r20Path(ymd))
        termLog(f"[riseRank20] 스냅샷 저장 {ymd} ({len(rows)}종{' 백필' if backfill else ''})")
    except Exception as err:
        termLog(f"[riseRank20] 저장 실패 {ymd}: {err}")
    return data


def _r20Finish(cur, prv):
    cur = [x for x in cur if x]
    prv = [x for x in prv if x]
    if not cur:
        return
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    lastDt = max(str(x.get("date")) for x in cur)
    # 백필: 직전 거래일 스냅샷이 없으면 봉에서 재구성 (첫 실행 전부 NEW 방지)
    prevDt = max((str(x.get("date")) for x in prv), default="")
    if prevDt and prevDt < lastDt and not _r20Path(prevDt).exists():
        pRows = _r20Diff(_r20Rank([x for x in prv if x.get("date") == prevDt]), _r20Prev(prevDt))
        _r20Save(prevDt, pRows, _r20Themes(pRows), backfill=True)
    rows = _r20Rank([x for x in cur if x.get("date") == lastDt])
    cmp = _r20Prev(lastDt)
    _r20Diff(rows, cmp)
    themes = _r20Themes(rows)
    final = (lastDt < today and not _r20Path(lastDt).exists()) or (
        lastDt == today and now.hour >= _R20_HOUR
    )
    savedAt = ""
    if final:
        savedAt = _r20Save(lastDt, rows, themes)["savedAt"]
    elif _r20Path(lastDt).exists():
        old = _r20Load(lastDt) or {}
        rows, themes, savedAt = old.get("rows") or rows, old.get("themes") or themes, old.get("savedAt", "")
        final = True
    with _LOCK:
        _R20.update(
            date=lastDt,
            prevDate=(cmp or {}).get("date", ""),
            savedAt=savedAt,
            live=not final,
            rows=rows,
            themes=themes,
        )
    termLog(f"[riseRank20] {lastDt} {'확정' if final else '장중잠정'} {len(rows)}종 / 추천테마 {[t['theme'] for t in themes]}")


def _r20LoadLatest():
    with _LOCK:
        if _R20["rows"]:
            return
    ds = _r20Dates()
    if not ds:
        return
    d = _r20Load(ds[-1]) or {}
    prev = _r20Prev(ds[-1]) or {}
    with _LOCK:
        if not _R20["rows"]:
            _R20.update(
                date=d.get("date", ds[-1]),
                prevDate=prev.get("date", ""),
                savedAt=d.get("savedAt", ""),
                live=False,
                rows=d.get("rows") or [],
                themes=d.get("themes") or [],
            )


def riseRank20Snapshot():
    """화면용: 20일 누적 순위 + 추천 테마."""
    _r20LoadLatest()
    with _LOCK:
        rows = [dict(r, fluRt=r.get("ret20")) for r in _R20["rows"]]
        meta = {k: _R20[k] for k in ("date", "prevDate", "savedAt", "live")}
        meta["themes"] = list(_R20["themes"])
    return {"rows": _withNaverTheme(rows), "meta": meta}


def _schedLoop():
    """평일 16:00 이후 당일 스냅샷이 없으면 1회 재조회(종가 기준)."""
    global _R20_TRIED
    while True:
        try:
            now = datetime.now()
            ymd = now.strftime("%Y%m%d")
            if (
                now.weekday() < 5
                and now.hour >= _R20_HOUR
                and _R20_TRIED != ymd
                and not _RUNNING
                and not _r20Path(ymd).exists()
            ):
                _R20_TRIED = ymd
                termLog("[riseRank20] 16시 순위 갱신 시작")
                startRiseRankBoard(force=True)
        except Exception as err:
            termLog(f"[riseRank20] 스케줄 오류: {err}")
        time.sleep(60)


def _startSched():
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return
    _SCHED_STARTED = True
    threading.Thread(target=_schedLoop, name="riseRank20Sched", daemon=True).start()
