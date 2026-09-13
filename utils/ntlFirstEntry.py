"""
52주 신고가 '최초 진입' 판별.

ka10016 은 '현재 52주 신고가 영역에 있는 종목' 목록만 주므로,
일봉으로 전일 대비 처음으로 52주 고점을 뚫은 종목만 남긴다.

정의 (고저 기준, lookback=250):
  - 당일 고가 >= 직전 250거래일 고가 최댓값 → 당일 52주 신고
  - 전일은 동일 정의로 52주 신고가 아님 → 신고 구간 시작일(최초 진입)
"""

from datetime import datetime, timedelta

from api.getDayChart import fetchDayBars
from api.getNtlPric import (
    DT_250,
    MRKT_ALL,
    NTL_HIGH,
    fetchNtlPric,
)
from utils.termLog import termLog

LOOKBACK = 250


def _sortBarsAsc(bars):
    """dt 오름차순 복사."""
    rows = [b for b in (bars or []) if b.get("dt") and b.get("highPric")]
    rows.sort(key=lambda b: str(b["dt"]))
    return rows


def is52wHighOnIdx(barsAsc, idx, lookback=LOOKBACK, useClose=False):
    """
    barsAsc[idx] 가 lookback 구간 대비 신고가인지.
    useClose=False → 고가 기준 / True → 종가 기준.
    """
    if idx < 1 or idx >= len(barsAsc):
        return False
    start = max(0, idx - lookback)
    # 당일 제외 과거 lookback
    prev = barsAsc[start:idx]
    if not prev:
        return False
    key = "curPrc" if useClose else "highPric"
    try:
        prevMax = max(float(b.get(key) or 0) for b in prev)
        cur = float(barsAsc[idx].get(key) or 0)
    except (TypeError, ValueError):
        return False
    if prevMax <= 0 or cur <= 0:
        return False
    return cur >= prevMax


def calcVolStats(barsAsc, idx=None, volMaPeriod=20):
    """
    거래량·20일 평균·증가율(%).
    volIncRt = (당일거래량 / 평균 - 1) * 100  (평균 대비 몇 % 증가)
    """
    rows = _sortBarsAsc(barsAsc)
    if not rows:
        return {"trdeQty": None, "avgVol20": None, "volIncRt": None}
    i = len(rows) - 1 if idx is None else int(idx)
    if i < 0 or i >= len(rows):
        return {"trdeQty": None, "avgVol20": None, "volIncRt": None}

    try:
        trdeQty = int(float(rows[i].get("trdeQty") or 0))
    except (TypeError, ValueError):
        trdeQty = 0
    if trdeQty <= 0:
        trdeQty = None

    period = max(1, int(volMaPeriod or 20))
    prev = rows[max(0, i - period) : i]
    qtyList = []
    for b in prev:
        try:
            q = float(b.get("trdeQty") or 0)
        except (TypeError, ValueError):
            q = 0
        if q > 0:
            qtyList.append(q)

    avgVol = None
    volIncRt = None
    if qtyList:
        avgVol = sum(qtyList) / len(qtyList)
        if avgVol > 0 and trdeQty is not None:
            # 평균 대비 증가율 — +100%이면 평균의 2배
            volIncRt = (float(trdeQty) / avgVol - 1.0) * 100.0

    return {
        "trdeQty": trdeQty,
        "avgVol20": int(round(avgVol)) if avgVol is not None else None,
        "volIncRt": round(volIncRt, 2) if volIncRt is not None else None,
        "volMaN": len(qtyList),
        "volMaPeriod": period,
    }


def isFirst52wEntry(barsAsc, idx=None, lookback=LOOKBACK, useClose=False, volMaPeriod=20):
    """
    최신 봉(또는 idx)이 52주 신고가 구간의 시작일인지.
    반환: {ok, firstEntry, isTodayHigh, isYdayHigh, tradeDt, high, prevMax, vol…}
    """
    rows = _sortBarsAsc(barsAsc)
    if len(rows) < lookback + 2:
        return {
            "ok": False,
            "firstEntry": False,
            "errMsg": f"일봉 부족({len(rows)}<{lookback + 2})",
        }
    i = len(rows) - 1 if idx is None else int(idx)
    if i < 1:
        return {"ok": False, "firstEntry": False, "errMsg": "인덱스 오류"}

    key = "curPrc" if useClose else "highPric"
    start = max(0, i - lookback)
    prev = rows[start:i]
    prevMax = max(float(b.get(key) or 0) for b in prev)
    cur = float(rows[i].get(key) or 0)
    todayHigh = cur >= prevMax and prevMax > 0

    ydayHigh = is52wHighOnIdx(rows, i - 1, lookback=lookback, useClose=useClose)
    first = bool(todayHigh and not ydayHigh)

    flu = None
    openP = float(rows[i].get("openPric") or 0)
    closeP = float(rows[i].get("curPrc") or 0)
    if i >= 1:
        prevClose = float(rows[i - 1].get("curPrc") or 0)
        if prevClose > 0:
            flu = (closeP / prevClose - 1.0) * 100.0

    vol = calcVolStats(rows, i, volMaPeriod=volMaPeriod)

    return {
        "ok": True,
        "firstEntry": first,
        "isTodayHigh": todayHigh,
        "isYdayHigh": ydayHigh,
        "tradeDt": rows[i].get("dt"),
        "high": cur if not useClose else None,
        "close": closeP,
        "open": openP,
        "prevMax": prevMax,
        "fluRt": flu,
        "barCnt": len(rows),
        "useClose": useClose,
        "trdeQty": vol.get("trdeQty"),
        "avgVol20": vol.get("avgVol20"),
        "volIncRt": vol.get("volIncRt"),
    }


def fetchFirst52wHighList(
    hostUrl,
    token,
    tokenType="bearer",
    isPaper=True,
    mrktTp=MRKT_ALL,
    minFluRt=None,
    useClose=False,
    maxCand=80,
    maxOut=40,
    baseDt=None,
):
    """
    1) ka10016 으로 52주 신고 후보 수집
    2) 각 종목 일봉으로 최초 진입만 필터
    """
    import time

    from utils.stkCdUtil import normStkCd

    if not baseDt:
        baseDt = datetime.now().strftime("%Y%m%d")

    waitSec = 1.15 if isPaper else 0.35

    got = fetchNtlPric(
        hostUrl,
        token,
        mrktTp=mrktTp,
        ntlTp=NTL_HIGH,
        highLowCloseTp="2" if useClose else "1",
        dt=DT_250,
        tokenType=tokenType,
        isPaper=isPaper,
        minFluRt=minFluRt,
        bullOnly=False,
        maxRows=maxCand,
    )
    if not got.get("ok"):
        return {
            "ok": False,
            "errMsg": got.get("errMsg") or "ka10016 실패",
            "rows": [],
        }

    candidates = got.get("rows") or []
    termLog(
        f"[first52w] 후보 {len(candidates)}건 → 일봉 최초진입 검사 "
        f"(maxCand={maxCand})"
    )

    firstRows = []
    stillHigh = 0
    failCnt = 0
    checked = 0

    for rec in candidates:
        if len(firstRows) >= maxOut:
            break
        rawCd = rec.get("stkCd") or ""
        # API 접미사(_AL 등) 제거 후 정규 종목코드
        stkCd = normStkCd(str(rawCd).split("_")[0])
        if not stkCd:
            continue
        checked += 1
        # 모의: 종목 간 1초 이상 간격 (ka10081 유량)
        if checked > 1:
            time.sleep(waitSec)

        day = None
        for attempt in range(3):
            day = fetchDayBars(
                hostUrl,
                token,
                stkCd,
                baseDt,
                minBars=LOOKBACK + 5,
                tokenType=tokenType,
                isPaper=isPaper,
            )
            if day.get("ok"):
                break
            err = str(day.get("errMsg") or "")
            if "429" in err or "유량" in err or "초과" in err:
                time.sleep(waitSec * (attempt + 2))
                continue
            break

        if not day or not day.get("ok"):
            failCnt += 1
            termLog(f"[first52w] 일봉실패 {stkCd}: {(day or {}).get('errMsg')}")
            continue

        judge = isFirst52wEntry(
            day.get("bars") or [], lookback=LOOKBACK, useClose=useClose
        )
        if not judge.get("ok"):
            failCnt += 1
            continue
        if judge.get("isTodayHigh"):
            stillHigh += 1
        if not judge.get("firstEntry"):
            continue

        firstRows.append(
            {
                "stkCd": stkCd,
                "stkNm": rec.get("stkNm") or stkCd,
                "tradeDt": judge.get("tradeDt"),
                "curPrc": rec.get("curPrc") or judge.get("close"),
                "high": judge.get("high"),
                "prevMax": judge.get("prevMax"),
                "fluRt": judge.get("fluRt")
                if judge.get("fluRt") is not None
                else rec.get("fluRt"),
                "apiFluRt": rec.get("fluRt"),
                # 거래량(카10081) — 20일 평균 대비 증가율 적재용
                "trdeQty": judge.get("trdeQty"),
                "avgVol20": judge.get("avgVol20"),
                "volIncRt": judge.get("volIncRt"),
            }
        )
        termLog(
            f"[first52w] 최초진입 {stkCd} {rec.get('stkNm')} "
            f"{judge.get('tradeDt')} flu={firstRows[-1].get('fluRt')}"
        )

    firstRows.sort(
        key=lambda r: (
            r.get("fluRt") is None,
            -(r.get("fluRt") or 0),
        )
    )

    return {
        "ok": True,
        "rows": firstRows,
        "rowCnt": len(firstRows),
        "candCnt": len(candidates),
        "checked": checked,
        "stillHighCnt": stillHigh,
        "failCnt": failCnt,
        "baseDt": baseDt,
        "mrktLabel": got.get("mrktLabel"),
        "minFluRt": minFluRt,
        "useClose": useClose,
    }


def formatFirst52wReport(got, modeLabel=""):
    """텔레그램/로그용 텍스트."""
    if not got.get("ok"):
        return f"[최초52주신고 실패] {got.get('errMsg')}"

    lines = [
        f"[최초 52주 신고가 진입] {got.get('baseDt')}"
        + (f" · {modeLabel}" if modeLabel else ""),
        f"시장 {got.get('mrktLabel') or '-'} · "
        f"기준={'종가' if got.get('useClose') else '고가'} · lookback {LOOKBACK}",
        f"ka10016 후보 {got.get('candCnt')} → 검사 {got.get('checked')} "
        f"→ 최초진입 {got.get('rowCnt')} "
        f"(연속신고제외 중·일봉실패 {got.get('failCnt')})",
        "",
    ]
    rows = got.get("rows") or []
    if not rows:
        lines.append("당일 최초 진입 종목 없음")
        lines.append(
            "(이미 여러 날 신고 중이거나, 후보 전원 미진입/일봉 부족)"
        )
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        flu = r.get("fluRt")
        fluTxt = f"{flu:+.2f}%" if flu is not None else "-"
        prev = r.get("prevMax")
        prevTxt = f"{int(prev):,}" if prev else "-"
        cur = r.get("curPrc")
        curTxt = f"{int(cur):,}" if cur else "-"
        lines.append(
            f"{i}. {r.get('stkNm')}({r.get('stkCd')}) "
            f"{curTxt} {fluTxt}  ·전고 {prevTxt}  ·{r.get('tradeDt')}"
        )
    return "\n".join(lines)
