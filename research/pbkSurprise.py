"""정기보고서 실적 서프라이즈 S1.

분기 영업이익 YoY ≥ +50%, 매출 YoY ≥ +20%, 매출 YoY 가 직전 분기보다 커야 한다.
분기 단독 값은 누적값에서 직전 누적값을 뺀다. 작년 같은 분기가 0 이하면 제외한다.
인증키는 반환·로그에 넣지 않는다.
"""

from __future__ import annotations

import re
import time

from utils.dartSecret import maskDartSecret

# Open DART 일반 일일 한도. 목록 조회와 재무 조회가 이 한도를 같이 쓴다.
DART_DAILY_LIMIT = 10000
# 요청 한 번에 응답·간격을 더해 잡은 대략의 초.
CALL_SEC = 0.4
OP_YOY_MIN = 0.50
SALES_YOY_MIN = 0.20

_NAME = re.compile(r"(사업보고서|반기보고서|분기보고서)")
_YM = re.compile(r"(20\d{2})\s*[.\-/]\s*(\d{2})")
_DROP = re.compile(r"정정|취소|철회|첨부|감사보고서|소액공모")

# 분기 → (보고서코드, 빼야 하는 직전 누적 보고서코드)
_REPRT = {
    1: ("11013", None),
    2: ("11012", "11013"),
    3: ("11014", "11012"),
    4: ("11011", "11014"),
}


def estimateSurpriseCalls(nStocks=2400, seasons=2):
    """120일 창에 실적 시즌이 seasons 개 있을 때의 호출 수.

    반기와 3분기가 같이 있으면 종목당 재무 조회는 1분기·반기·3분기 3회다.
    3분기 가속 판정에 1분기 누적이 필요해서 1분기를 창 밖에서라도 한 번 본다.
    목록은 100건 페이지로 잡고, 시장을 나누는 여유를 더한다.
    """
    stocks = max(0, int(nStocks))
    seasonN = max(0, int(seasons))
    filings = stocks * seasonN
    listCalls = (filings + 99) // 100 + 8
    finCalls = stocks * 3 if seasonN >= 2 else stocks * (2 if seasonN == 1 else 0)
    total = listCalls + finCalls
    return {
        "listCalls": listCalls,
        "finCalls": finCalls,
        "total": total,
        "dailyLimit": DART_DAILY_LIMIT,
        "seconds": int(total * CALL_SEC),
        "withinLimit": total <= DART_DAILY_LIMIT,
    }


def parsePeriodic(title, rceptDt=""):
    """분기·반기·사업보고서만. 정정은 버린다."""
    text = str(title or "")
    if _DROP.search(text):
        return None
    kind = _NAME.search(text)
    if not kind:
        return None
    ym = _YM.search(text)
    year = int(ym.group(1)) if ym else 0
    month = int(ym.group(2)) if ym else 0
    name = kind.group(1)
    if name == "사업보고서":
        quarter = 4
    elif name == "반기보고서":
        quarter = 2
    elif month in (1, 2, 3):
        quarter = 1
    elif month in (7, 8, 9):
        quarter = 3
    else:
        return None
    if not year and len(str(rceptDt)) >= 4:
        year = int(str(rceptDt)[:4])
        if quarter == 4:
            year -= 1
    if year < 2000:
        return None
    reprt, prior = _REPRT[quarter]
    return {"year": year, "quarter": quarter, "reprt": reprt, "prior": prior}


def parseAmount(raw):
    text = str(raw or "").strip().replace(",", "").replace(" ", "")
    if not text or text in ("-", "nan", "None"):
        return None
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        val = float(text)
    except (TypeError, ValueError):
        return None
    return -val if neg else val


def _pickFs(items):
    """연결에 매출·영업이익이 있으면 연결. 없으면 별도."""

    def grab(fsDiv):
        pool = [row for row in items or [] if str(row.get("fsDiv") or "") == fsDiv]
        income = [row for row in pool if str(row.get("sjDiv") or "") in ("IS", "CIS")]
        use = income or pool
        sales = salesLy = op = opLy = None
        for row in use:
            name = str(row.get("accountNm") or "").replace(" ", "")
            if name in ("매출액", "수익(매출액)", "매출액(수익)") and sales is None:
                sales = parseAmount(row.get("thstrmAmount"))
                salesLy = parseAmount(row.get("frmtrmAmount"))
            elif name == "영업이익" and op is None:
                op = parseAmount(row.get("thstrmAmount"))
                opLy = parseAmount(row.get("frmtrmAmount"))
        if sales is None or op is None:
            return None
        return {"fsDiv": fsDiv, "sales": sales, "salesLy": salesLy, "op": op, "opLy": opLy}

    return grab("CFS") or grab("OFS")


def standalone(current, prior):
    """누적에서 직전 누적을 뺀 분기 값. 직전이 없으면 그 값 자체(1분기)."""
    if current is None:
        return None
    if prior is None:
        return current
    return current - prior


def yoy(current, lastYear):
    """작년 같은 분기가 0 이하면 None."""
    if current is None or lastYear is None or lastYear <= 0:
        return None
    return round((current / lastYear) - 1.0, 6)


def quarterFigures(cumulative, quarter):
    """cumulative: 분기번호 → {sales, salesLy, op, opLy} 누적."""
    cur = (cumulative or {}).get(int(quarter))
    if not cur:
        return None
    priorQ = {1: None, 2: 1, 3: 2, 4: 3}[int(quarter)]
    prior = (cumulative or {}).get(priorQ) if priorQ else None
    if priorQ and not prior:
        return None
    return {
        "sales": standalone(cur.get("sales"), None if not prior else prior.get("sales")),
        "salesLy": standalone(cur.get("salesLy"), None if not prior else prior.get("salesLy")),
        "op": standalone(cur.get("op"), None if not prior else prior.get("op")),
        "opLy": standalone(cur.get("opLy"), None if not prior else prior.get("opLy")),
    }


def passesS1(cumulative, quarter):
    """이번 분기와 직전 분기 매출 YoY 를 비교한다. 재료가 없으면 탈락."""
    now = quarterFigures(cumulative, quarter)
    if not now:
        return {"pass": False, "reason": "NO_QUARTER"}
    prevQ = {1: 4, 2: 1, 3: 2, 4: 3}[int(quarter)]
    prevSrc = cumulative
    if int(quarter) == 1:
        prevSrc = (cumulative or {}).get("prevYear") or {}
    prev = quarterFigures(prevSrc if int(quarter) != 1 else prevSrc, prevQ)
    salesYoy = yoy(now["sales"], now["salesLy"])
    opYoy = yoy(now["op"], now["opLy"])
    prevSalesYoy = None if not prev else yoy(prev["sales"], prev["salesLy"])
    ok = (
        salesYoy is not None
        and opYoy is not None
        and prevSalesYoy is not None
        and opYoy + 1e-9 >= OP_YOY_MIN
        and salesYoy + 1e-9 >= SALES_YOY_MIN
        and salesYoy > prevSalesYoy
    )
    return {
        "pass": bool(ok),
        "sales": now["sales"],
        "salesLy": now["salesLy"],
        "op": now["op"],
        "opLy": now["opLy"],
        "salesYoy": salesYoy,
        "opYoy": opYoy,
        "prevSalesYoy": prevSalesYoy,
        "reason": "S1" if ok else "MISS",
    }


def accountsNeeded(spec):
    """이 보고서를 판정하려면 받아야 하는 (사업연도, 보고서코드)."""
    if not spec:
        return []
    year = int(spec["year"])
    quarter = int(spec["quarter"])
    keys = [(year, spec["reprt"])]
    if spec.get("prior"):
        keys.append((year, spec["prior"]))
    if quarter == 3:
        keys.append((year, "11013"))
    if quarter == 4:
        keys.append((year, "11012"))
    if quarter == 1:
        keys.append((year - 1, "11011"))
        keys.append((year - 1, "11014"))
    out = []
    seen = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def loadStatement(corpCode, year, reprt, getter=None, key=""):
    """fnlttSinglAcnt 한 번. 실패 문구에서 키를 지운다."""
    if getter is not None:
        try:
            got = getter(corpCode, year, reprt) or {}
        except Exception as err:
            return {"ok": False, "errMsg": maskDartSecret(str(err), key), "calls": 1}
    else:
        from research.dartFinFetch import fetchFnlttSinglAcnt

        got = fetchFnlttSinglAcnt(corpCode, year, reprt) or {}
        if got.get("errMsg"):
            got = dict(got)
            got["errMsg"] = maskDartSecret(got.get("errMsg"), key)
    got = dict(got)
    got["calls"] = int(got.get("calls") or 1)
    if not got.get("ok"):
        return got
    picked = _pickFs(got.get("items") or [])
    if not picked:
        return {"ok": False, "errMsg": "매출·영업이익 없음", "calls": got["calls"]}
    picked["ok"] = True
    picked["calls"] = got["calls"]
    return picked


def cumulativeFromCache(cache, year, quarter):
    """캐시의 보고서 코드를 분기 누적으로 옮긴다."""
    code = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}[int(quarter)]
    row = (cache or {}).get((int(year), code))
    if not row:
        return None
    return {"sales": row.get("sales"), "salesLy": row.get("salesLy"), "op": row.get("op"), "opLy": row.get("opLy")}


def _figureQuarters(quarter):
    """이번 분기, 누적을 뺄 직전 분기, 가속 비교에 필요한 그 전 분기."""
    quarter = int(quarter)
    prior = {1: None, 2: 1, 3: 2, 4: 3}[quarter]
    prevQ = {1: 4, 2: 1, 3: 2, 4: 3}[quarter]
    need = {quarter}
    if prior:
        need.add(prior)
    if quarter != 1:
        need.add(prevQ)
        prevPrior = {1: None, 2: 1, 3: 2, 4: 3}[prevQ]
        if prevPrior:
            need.add(prevPrior)
    return need


def judgeCached(cache, spec):
    """캐시에 있는 누적만으로 S1 을 판정한다. 빠지면 None."""
    year = int(spec["year"])
    quarter = int(spec["quarter"])
    cumulative = {}
    for q in _figureQuarters(quarter):
        fig = cumulativeFromCache(cache, year, q)
        if fig is None:
            return None
        cumulative[q] = fig
    if quarter == 1:
        prev = {}
        for q in (3, 4):
            fig = cumulativeFromCache(cache, year - 1, q)
            if fig is None:
                return None
            prev[q] = fig
        cumulative["prevYear"] = prev
    return passesS1(cumulative, quarter)


def evaluateReports(reports, seen, fetchOne, callsLeft, pauseSec=0.0):
    """접수일 순으로 판정한다. 한도에 닿으면 나머지는 pending 으로 돌려준다.

    fetchOne(corpCode, year, reprt) → loadStatement 결과.
    seen 에 있는 접수번호는 다시 부르지 않는다.
    """
    ordered = sorted(
        [row for row in (reports or []) if isinstance(row, dict)],
        key=lambda row: (str(row.get("rceptDt") or ""), str(row.get("rceptNo") or "")),
    )
    saved = []
    pending = []
    calls = 0
    cache = {}
    stopped = False
    for report in ordered:
        rcept = str(report.get("rceptNo") or "")
        if rcept and rcept in seen:
            continue
        if stopped:
            pending.append(report)
            continue
        spec = parsePeriodic(report.get("reportNm") or report.get("title") or "", report.get("rceptDt") or "")
        corp = str(report.get("corpCode") or "")
        code = str(report.get("stkCd") or "")
        if not spec or not corp or len(code) != 6:
            saved.append({
                "rceptNo": rcept,
                "stkCd": code,
                "rceptDt": str(report.get("rceptDt") or "")[:8],
                "reportNm": str(report.get("reportNm") or ""),
                "pass": False,
                "reason": "SKIP",
            })
            continue
        stockCache = cache.setdefault(code, {})
        missing = [key for key in accountsNeeded(spec) if key not in stockCache]
        if calls + len(missing) > int(callsLeft):
            stopped = True
            pending.append(report)
            continue
        failed = False
        for year, reprt in missing:
            got = fetchOne(corp, year, reprt) or {}
            calls += int(got.get("calls") or 1)
            if pauseSec:
                time.sleep(pauseSec)
            if not got.get("ok"):
                failed = True
                break
            stockCache[(int(year), str(reprt))] = got
        if failed:
            continue
        judged = judgeCached(stockCache, spec)
        if not judged:
            continue
        saved.append({
            "rceptNo": rcept,
            "stkCd": code,
            "corpCode": corp,
            "rceptDt": str(report.get("rceptDt") or "")[:8],
            "reportNm": str(report.get("reportNm") or ""),
            "bsnsYear": str(spec["year"]),
            "reprtCode": spec["reprt"],
            "quarter": spec["quarter"],
            "fsDiv": (stockCache.get((spec["year"], spec["reprt"])) or {}).get("fsDiv") or "",
            "sales": judged.get("sales"),
            "salesLy": judged.get("salesLy"),
            "op": judged.get("op"),
            "opLy": judged.get("opLy"),
            "salesYoy": judged.get("salesYoy"),
            "opYoy": judged.get("opYoy"),
            "prevSalesYoy": judged.get("prevSalesYoy"),
            "pass": bool(judged.get("pass")),
            "reason": judged.get("reason") or "",
        })
    return {"saved": saved, "pending": pending, "calls": calls, "stopped": stopped}
