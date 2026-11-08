"""급등주 조회 순위·보조지표·이슈 증거.

실측이 없는 값은 만들지 않는다. 0 으로 빈 칸을 채우지 않는다.
ka00198 은 당일누적(qry_tp=4) 조회 순위이며 ka10027 급등 순위가 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from strategies.impl.surgeTrade import ALG_CD
from strategies.indicators import calcRsiSeries

QRY_TP_DAY = "4"
SOURCE = "ka00198"
# 명세 예시·로컬 연구 모두 최대 순위 건수를 확정하지 못했다.
SOURCE_CAP_NOTE = (
    "ka00198 응답 건수가 곧 시장 전체 급등 목록은 아니다. "
    "페이지가 더 있거나 원천이 상한을 두면 sourceTruncated 로만 표시한다."
)
FLOW_UNIT_MILLION_KRW = "백만원"
FLOW_STATUS_PROVISIONAL = "provisional_not_krx_final"
KRW_PER_MILLION = 1_000_000
EOK_KRW = 100_000_000


def fifteenMinuteSlots():
    """정규장 15분 슬롯 09:00~15:15. 15:30 은 넣지 않는다."""
    slots = []
    minutes = 9 * 60
    end = 15 * 60 + 15
    while minutes <= end:
        slots.append(f"{minutes // 60:02d}{minutes % 60:02d}")
        minutes += 15
    return slots


def dueSlot(now, isTradingDay):
    """
    거래일 정규장 15분, 15:18 판단 캡처, 16:00 최종 캡처.
    거래일이 아니면 None.
    """
    if not isTradingDay:
        return None
    hms = now.strftime("%H%M%S")
    hhmm = hms[:4]
    if hhmm in fifteenMinuteSlots() and hms < "153000":
        return hhmm
    if "151800" <= hms < "151900":
        return "1518"
    if "160000" <= hms < "160100":
        return "1600"
    return None


def _num(raw):
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in ("", "-", "--"):
        return None
    while text.startswith("--"):
        text = "-" + text[2:]
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _absInt(raw):
    num = _num(raw)
    if num is None:
        return None
    return abs(int(num))


def parseKa00198Body(body, observedAt, slot, provenance, exchange="KRX"):
    """
    ka00198 본문 → 스냅샷. 순위·코드가 없는 행은 버린다.
    명세 예시와 합성 픽스처를 provenance 로 구분한다.
    """
    rowsIn = (body or {}).get("item_inq_rank") if isinstance(body, dict) else None
    if not isinstance(rowsIn, list):
        return {"ok": False, "errMsg": "item_inq_rank 없음", "rows": []}
    rows = []
    for raw in rowsIn:
        if not isinstance(raw, dict):
            continue
        rank = _absInt(raw.get("bigd_rank"))
        code = str(raw.get("stk_cd") or "").strip()
        if rank is None or not code:
            continue
        price = _absInt(raw.get("past_curr_prc"))
        chg = _num(raw.get("base_comp_chgr"))
        rows.append(
            {
                "originalRank": int(rank),
                "stkCd": code,
                "name": str(raw.get("stk_nm") or "").strip(),
                "price": price,
                "baseCompChgr": chg,
                "baseCompSign": str(raw.get("base_comp_sign") or "").strip(),
                "sourceDt": str(raw.get("dt") or "").strip(),
                "sourceTm": str(raw.get("tm") or "").strip(),
                "dailyBasis": "ka00198_base_comp_unverified",
            }
        )
    cont = str((body or {}).get("cont-yn") or (body or {}).get("contYn") or "").upper()
    return {
        "ok": True,
        "tradeYmd": _ymdOf(observedAt),
        "slot": str(slot),
        "intervalMin": 15 if str(slot) not in ("1518", "1600", "ADHOC") else None,
        "qryTp": QRY_TP_DAY,
        "qryLabel": "당일누적",
        "exchange": exchange,
        "source": SOURCE,
        "provenance": provenance,
        "observedAt": observedAt,
        "state": "COLLECTED",
        "rows": rows,
        "sourceCount": len(rows),
        "truncated": cont == "Y",
        "exhaustive": False,
        "note": SOURCE_CAP_NOTE,
    }


def notCollectedSnapshot(tradeYmd, slot, notedAt):
    """16:00 에 앱이 꺼져 있었으면 그 시각의 순위를 나중에 채우지 않는다."""
    return {
        "ok": True,
        "tradeYmd": str(tradeYmd),
        "slot": str(slot),
        "intervalMin": None,
        "qryTp": QRY_TP_DAY,
        "qryLabel": "당일누적",
        "exchange": "KRX",
        "source": SOURCE,
        "provenance": "not_collected",
        "observedAt": None,
        "notedAt": notedAt,
        "state": "NOT_COLLECTED",
        "rows": [],
        "sourceCount": 0,
        "truncated": None,
        "exhaustive": False,
        "note": "미수집. 이후 조회 결과를 이 슬롯의 역사 순위로 넣지 않는다.",
    }


class SnapStore:
    """관측 스냅샷 JSON. 테스트는 임시 디렉터리를 넘긴다."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, tradeYmd):
        return self.dir / f"{tradeYmd}.json"

    def _load(self, tradeYmd):
        path = self._path(tradeYmd)
        if not path.is_file():
            return {"tradeYmd": tradeYmd, "snaps": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"tradeYmd": tradeYmd, "snaps": []}
        if not isinstance(data, dict):
            return {"tradeYmd": tradeYmd, "snaps": []}
        data.setdefault("snaps", [])
        return data

    def save(self, snap):
        ymd = str(snap.get("tradeYmd") or "")
        data = self._load(ymd)
        snaps = [s for s in data["snaps"] if not _sameSnap(s, snap)]
        snaps.append(snap)
        data["snaps"] = snaps
        self._path(ymd).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return snap

    def slot(self, tradeYmd, slot):
        for snap in self._load(tradeYmd).get("snaps") or []:
            if str(snap.get("slot")) == str(slot):
                return snap
        return None

    def recordLate(self, tradeYmd, observedAt, rows, provenance):
        """16:00 슬롯이 없으면 NOT_COLLECTED 로 남기고, 실제 시각은 ADHOC 에만 저장."""
        if self.slot(tradeYmd, "1600") is None:
            self.save(notCollectedSnapshot(tradeYmd, "1600", observedAt))
        adhoc = {
            "ok": True,
            "tradeYmd": str(tradeYmd),
            "slot": "ADHOC",
            "intervalMin": None,
            "qryTp": QRY_TP_DAY,
            "qryLabel": "당일누적",
            "exchange": "KRX",
            "source": SOURCE,
            "provenance": provenance,
            "observedAt": observedAt,
            "state": "COLLECTED",
            "rows": list(rows or []),
            "sourceCount": len(rows or []),
            "truncated": None,
            "exhaustive": False,
            "note": "16:00 부재 이후의 실제 관측. 16:00 순위의 대체가 아니다.",
        }
        return self.save(adhoc)


def _sameSnap(old, new):
    return str(old.get("slot")) == str(new.get("slot")) and str(old.get("observedAt")) == str(
        new.get("observedAt")
    )


def _ymdOf(value):
    text = str(value or "").replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    return text[:8]


def membershipOk(code, asof, book):
    """날짜가 유효한 KOSPI200·KOSDAQ150 구성만 True. 명부가 없으면 False."""
    row = (book or {}).get(str(code))
    if not isinstance(row, dict):
        return False, ""
    index = str(row.get("index") or "")
    if index not in ("KOSPI200", "KOSDAQ150"):
        return False, ""
    start = str(row.get("effectiveFrom") or "")
    end = str(row.get("effectiveTo") or "99991231")
    day = str(asof or "")
    if not start or day < start or day > end:
        return False, ""
    return True, index


def verifiedDailyPct(px, prevRegularClose):
    """전일 정규 종가 대비. 둘 중 하나라도 없으면 계산하지 않는다."""
    try:
        price = float(px)
        prev = float(prevRegularClose)
    except (TypeError, ValueError):
        return None
    if price <= 0 or prev <= 0:
        return None
    return (price / prev - 1.0) * 100.0


def filterRankRows(rows, asof, membership, prevCloseByCode, priceByCode=None):
    """원천 순위를 유지한 채 구성·양봉만 남긴다. 350개로 늘리지 않는다."""
    kept = []
    for row in rows or []:
        code = str(row.get("stkCd") or "")
        ok, index = membershipOk(code, asof, membership)
        if not ok:
            continue
        px = (priceByCode or {}).get(code, row.get("price"))
        prev = (prevCloseByCode or {}).get(code)
        daily = verifiedDailyPct(px, prev)
        if daily is None or daily <= 0:
            continue
        item = dict(row)
        item["index"] = index
        item["price"] = px
        item["dailyPct"] = daily
        item["dailyBasis"] = "prev_regular_close"
        item["prevRegularClose"] = prev
        kept.append(item)
    kept.sort(key=lambda r: int(r.get("originalRank") or 0))
    return {
        "rows": kept,
        "sourceCount": len(rows or []),
        "keptCount": len(kept),
        "exhaustive": False,
        "note": SOURCE_CAP_NOTE,
    }


def turnoverEok(amount, unit):
    """
    원 단위로 확인된 거래대금만 억원으로 나눈다.
    백만원은 명세(ka10059 acc_trde_prica)일 때만 원으로 바꾼 뒤 / 1e8.
    단위가 없으면 None.
    """
    num = _num(amount)
    if num is None:
        return None, "단위 확인 불가"
    label = str(unit or "")
    if label == "KRW":
        krw = num
    elif label == FLOW_UNIT_MILLION_KRW:
        krw = num * KRW_PER_MILLION
    else:
        return None, f"미확인 단위:{label or '-'}"
    return krw / EOK_KRW, "KRW/1e8"


def parseKa10059Flow(row, requested):
    """
    기관은 orgn(기관계), 외국인은 frgnr_invsr(외국인투자자)만.
    natfor(내외국인)는 외국인 합산이 아니므로 더하지 않는다.
    금액·순매수 요청이 아니면 단위를 단정하지 않고 비운다.
    16:00 값도 KRX 확정(20:00)으로 표시하지 않는다.
    """
    req = requested or {}
    if str(req.get("amtQtyTp")) != "1" or str(req.get("trdeTp")) != "0":
        return {"state": "N/A", "reason": "금액·순매수 요청이 아님"}
    if not isinstance(row, dict):
        return {"state": "N/A", "reason": "행 없음"}
    asof = str(row.get("dt") or "")
    orgn = _num(row.get("orgn"))
    frgn = _num(row.get("frgnr_invsr"))
    if orgn is None and frgn is None:
        return {"state": "N/A", "reason": "순매수 필드 없음", "asof": asof}
    turnover, note = turnoverEok(row.get("acc_trde_prica"), FLOW_UNIT_MILLION_KRW)
    return {
        "state": "OK",
        "asof": asof,
        "unit": FLOW_UNIT_MILLION_KRW,
        "unitNote": "ka10059 금액(amt_qty_tp=1)·순매수(trde_tp=0). 명세 단위 백만원.",
        "orgnNetMillionKrw": orgn,
        "frgnNetMillionKrw": frgn,
        "foreignDefinition": "frgnr_invsr 외국인투자자. natfor(내외국인) 합산 아님.",
        "flowStatus": FLOW_STATUS_PROVISIONAL,
        "flowStatusNote": "KRX 정규 데이터는 15:45 예정, 시간외 반영 최종은 20:00. 키움 응답을 확정으로 보지 않는다.",
        "turnoverEok": turnover,
        "turnoverNote": note,
        "turnoverSource": "ka10059.acc_trde_prica",
    }


def rsi14(closes, adjusted=True):
    """Wilder RSI14. 수정주가 15개(기간+1) 미만·누락·미수정이면 None."""
    if adjusted is not True:
        return None
    vals = []
    for raw in closes or []:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if val <= 0:
            return None
        vals.append(val)
    if len(vals) < 15:
        return None
    series = calcRsiSeries(vals, 14)
    if not series or series[-1] is None:
        return None
    return float(series[-1])


def yearLowRisePct(currentPx, bars, asof, adjusted=True):
    """
    (현재가 / 직전 1년 최저가 - 1) * 100.
    최저가는 수정저가. 1년(달력 365일)을 덮지 못하거나 결측·거래정지가 표시되면 None.
    현재가가 장중이면 그 가격, 16:00 실측 종가면 그 종가. 없는 16:00 을 만들지 않는다.
    """
    if adjusted is not True:
        return None, "UNADJUSTED"
    try:
        cur = float(currentPx)
        day = datetime.strptime(str(asof)[:8], "%Y%m%d")
    except (TypeError, ValueError):
        return None, "BAD_INPUT"
    if cur <= 0:
        return None, "BAD_PRICE"
    start = (day - timedelta(days=365)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")
    lows = []
    oldest = ""
    for bar in bars or []:
        if not isinstance(bar, dict):
            return None, "BAD_BAR"
        if bar.get("missing") or bar.get("suspended"):
            return None, "MISSING_OR_SUSPENDED"
        dt = str(bar.get("dt") or "")
        if dt < start or dt > end:
            continue
        try:
            low = float(bar.get("low"))
        except (TypeError, ValueError):
            return None, "BAD_LOW"
        if low <= 0:
            return None, "BAD_LOW"
        lows.append(low)
        if not oldest or dt < oldest:
            oldest = dt
    if not lows:
        return None, "NO_BARS"
    if oldest > start:
        return None, "SHORT_HISTORY"
    low = min(lows)
    return (cur / low - 1.0) * 100.0, "OK"


def parseDartList(body, stkCd):
    """
    Open DART list.json 형태. 해당 종목 공시만 증거로 남긴다.
    제목으로 사유 태그를 만들지 않으며 confirmed 는 False.
    """
    if not isinstance(body, dict):
        return []
    if str(body.get("status") or "") != "000":
        return []
    want = str(stkCd or "").strip()
    out = []
    for row in body.get("list") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "").strip()
        if code != want:
            continue
        rcept = str(row.get("rcept_no") or "").strip()
        if not rcept:
            continue
        out.append(
            {
                "stkCd": code,
                "eventId": "",
                "tag": "",
                "confirmed": False,
                "title": str(row.get("report_nm") or "").strip(),
                "publishedAt": str(row.get("rcept_dt") or "").strip(),
                "sourceUrl": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
                "rceptNo": rcept,
                "provenance": "opendart_list",
                "eligible": False,
            }
        )
    return out


def validateEventAudit(form, todayYmd, now=None):
    """사람이 근거를 넣어 확인하는 경로. 필수값이 빠지면 매수 자격이 생기지 않는다."""
    form = form or {}
    errors = []
    eventId = str(form.get("eventId") or "").strip()
    stkCd = str(form.get("stkCd") or "").strip()
    eventDate = str(form.get("eventDate") or "").strip()
    tag = str(form.get("tag") or "").strip()
    url = str(form.get("sourceUrl") or "").strip()
    published = str(form.get("publishedAt") or "").strip()
    if not eventId:
        errors.append("EVENT_ID")
    if len(stkCd) != 6 or not stkCd.isdigit():
        errors.append("STK_CD")
    if len(eventDate) != 8 or not eventDate.isdigit() or eventDate > str(todayYmd):
        errors.append("EVENT_DATE")
    if not tag or len(tag) > 12 or tag == eventId:
        errors.append("TAG")
    if not (url.startswith("https://") or url.startswith("http://")):
        errors.append("URL")
    pubDay = published.replace("-", "")[:8]
    if len(pubDay) != 8 or not pubDay.isdigit() or pubDay > str(todayYmd):
        errors.append("PUBLISHED_AT")
    if now is not None and published:
        try:
            seen = datetime.strptime(published[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            if seen > now:
                errors.append("PUBLISHED_FUTURE")
        except ValueError:
            if "PUBLISHED_AT" not in errors and len(published) > 8:
                errors.append("PUBLISHED_AT")
    if errors:
        return {"ok": False, "errors": errors, "eligible": False}
    return {
        "ok": True,
        "errors": [],
        "eligible": False,
        "record": {
            "algCd": ALG_CD,
            "stkCd": stkCd,
            "eventId": eventId,
            "eventDate": eventDate,
            "tag": tag,
            "sourceUrl": url,
            "publishedAt": published,
            "confirmed": True,
            "firstSurgeDate": eventDate,
            "note": str(form.get("note") or "")[:200],
            "provenance": "explicit_audit",
        },
    }


def yellowName(confirmed, dailyPct, tradeYmd, firstSurgeYmd):
    """같은 이벤트의 최초 급등일에, 확인된 이슈와 일간 +10% 이상일 때만."""
    try:
        daily = float(dailyPct)
    except (TypeError, ValueError):
        return False
    return bool(
        confirmed is True
        and daily + 1e-9 >= 10.0
        and str(tradeYmd) == str(firstSurgeYmd)
        and str(firstSurgeYmd)
    )


def buildTabRows(snap, asof, membership, prevCloses, extras=None):
    """탭에 그릴 행. 없는 지표는 None / N/A."""
    extras = extras or {}
    if not snap or snap.get("state") == "NOT_COLLECTED":
        return {
            "state": "NOT_COLLECTED",
            "lastUpdated": (snap or {}).get("notedAt") or "",
            "rows": [],
            "sourceCount": 0,
            "exhaustive": False,
            "note": (snap or {}).get("note") or "미수집",
        }
    prices = extras.get("prices") or {}
    filtered = filterRankRows(
        snap.get("rows") or [],
        asof,
        membership,
        prevCloses,
        prices,
    )
    view = []
    for row in filtered["rows"]:
        code = row["stkCd"]
        extra = (extras.get("byCode") or {}).get(code) or {}
        issue = extra.get("issue") or {}
        flow = extra.get("flow") or {}
        daily = row.get("dailyPct")
        first = str(issue.get("firstSurgeDate") or "")
        tags = issue.get("tag") or ""
        item = {
            "originalRank": row.get("originalRank"),
            "code": code,
            "name": row.get("name") or "",
            "index": row.get("index") or "",
            "price": row.get("price"),
            "dailyPct": daily,
            "turnoverEok": flow.get("turnoverEok"),
            "turnoverNote": flow.get("turnoverNote") or "N/A",
            "issues": tags if issue.get("confirmed") else "",
            "issueDetails": issue.get("details") or ([] if not issue else [issue]),
            "orgnNetMillionKrw": flow.get("orgnNetMillionKrw"),
            "frgnNetMillionKrw": flow.get("frgnNetMillionKrw"),
            "flowUnit": flow.get("unit") or "",
            "flowAsOf": flow.get("asof") or "",
            "flowStatus": flow.get("flowStatus") or "N/A",
            "rsi14": extra.get("rsi14"),
            "yearLowRisePct": extra.get("yearLowRisePct"),
            "dataState": extra.get("dataState") or "PARTIAL",
            "yellowName": yellowName(issue.get("confirmed"), daily, asof, first),
            "buyEligible": False,
        }
        view.append(item)
    observed = snap.get("observedAt") or ""
    return {
        "state": "READY" if view else "EMPTY",
        "lastUpdated": observed,
        "rows": view,
        "sourceCount": snap.get("sourceCount") or filtered["sourceCount"],
        "keptCount": filtered["keptCount"],
        "exhaustive": False,
        "truncated": snap.get("truncated"),
        "note": SOURCE_CAP_NOTE,
        "provenance": snap.get("provenance") or "",
    }


def nextRetryDelaySec(attempt):
    """네트워크 재시도 간격. UI 스레드에서 sleep 하지 않는다."""
    n = max(0, int(attempt or 0))
    if n >= 3:
        return None
    return min(30, 2 ** n)


def retryOrBlock(attempt, lastError):
    delay = nextRetryDelaySec(attempt)
    if delay is None:
        return {"state": "ERROR", "retry": False, "errMsg": lastError or "조회 실패", "rows": []}
    return {"state": "RETRY", "retry": True, "delaySec": delay, "errMsg": lastError or ""}
