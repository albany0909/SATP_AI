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
# ka00198 base_comp_chgr: 기준가 대비 등락율(%). '+10.50' 은 +10.50, '--' 는 값 없음.
# 탭은 5% 이상. SRG01 매수 후보는 10% 이상. 둘을 같은 값으로 두지 않는다.
TAB_MIN_DAILY_PCT = 5.0
ENTRY_MIN_DAILY_PCT = 10.0
MIN_DAILY_PCT = ENTRY_MIN_DAILY_PCT
DAILY_BASIS_KA00198 = "ka00198_base_comp_chgr"
FIRST_SURGE_FILE = "firstSurge.json"
TAB_COLUMNS = (
    ("code", "코드"),
    ("name", "종목명"),
    ("price", "현재가"),
    ("dailyPct", "일간%"),
    ("rsi14", "RSI14"),
    ("turnoverEok", "거래대금(억)"),
    ("issues", "이슈"),
    ("frgnNetMillionKrw", "외국인"),
    ("orgnNetMillionKrw", "기관"),
    ("penfndNetMillionKrw", "연기금"),
    ("yearRisePct", "최근1년"),
    ("dataState", "상태"),
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
    거래일 정규장 15분, 15:18 판단 캡처, 16:00 정각 1분.
    거래일이 아니면 None. 16:00 을 놓친 보정은 rankCollectSlot.
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


def rankCollectSlot(now, isTrading, hasFinalCollected=False):
    """
    조회 수집 슬롯.
    16:00 실측이 있으면 그날은 더 수집하지 않는다.
    16:00:00~16:10:59 에 아직 1600 이 없으면, 실제 시각으로 1600 을 받는다.
    거래일이 아니면 None.
    """
    if not isTrading or hasFinalCollected:
        return None
    hms = now.strftime("%H%M%S")
    if "160000" <= hms < "161100":
        return "1600"
    if hms >= "160000":
        return None
    return dueSlot(now, True)


def _hmsOf(value):
    text = str(value or "").replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    return text[8:14] if len(text) >= 14 else ""


def _collected(snap):
    return isinstance(snap, dict) and snap.get("state") == "COLLECTED" and snap.get("observedAt")


def sessionSnapshot(snap, tradeYmd):
    """거래일 09:00 이후 관측. 장 시작 전 0% 조회는 여기 들어가지 않는다."""
    if not _collected(snap):
        return False
    if _ymdOf(snap.get("observedAt")) != str(tradeYmd):
        return False
    hms = _hmsOf(snap.get("observedAt"))
    return bool(hms) and hms >= "090000"


def dayFinalSnapshot(snaps):
    """그 날 16:00 이 있으면 그것을, 없으면 가장 늦은 수집분을 쓴다."""
    collected = [s for s in snaps or [] if _collected(s)]
    finals = [s for s in collected if str(s.get("slot")) == "1600"]
    if finals:
        return latestDisplaySnapshot(finals)
    return latestDisplaySnapshot(collected)


def queryStampLabel(observedAt, fallback):
    """상단 조회일시. 전일 목록이면 뒤에 전일 기준을 붙인다."""
    text = str(observedAt or "")
    stamp = ""
    if "T" in text and len(text) >= 16:
        day, rest = text.split("T", 1)
        stamp = f"{day} {rest[:5]}"
    elif len(text) >= 16 and text[10] == " ":
        stamp = text[:16]
    if not stamp:
        return "조회일시 -"
    if fallback:
        return f"조회일시 {stamp} (전일 기준)"
    return f"조회일시 {stamp}"


def selectTabDisplay(store, now):
    """
    탭에 그릴 스냅샷만 고른다.
    장중 관측이 생기기 전에는 직전 거래일 최종 목록을 보여 준다.
    주문·첫급등 파일을 쓰지 않는다.
    """
    from config.krHoliday import isTradingDay, prevTradingDay

    ymd = now.strftime("%Y%m%d")
    chosen = None
    fallback = True
    if isTradingDay(now):
        collected = [s for s in store.snaps(ymd) if _collected(s)]
        inSession = [s for s in collected if sessionSnapshot(s, ymd)]
        if inSession:
            chosen = latestDisplaySnapshot(inSession)
            fallback = False
        else:
            named = [
                s
                for s in collected
                if tabRankRows(s.get("rows") or []).get("keptCount", 0) > 0
            ]
            if named:
                chosen = latestDisplaySnapshot(named)
                fallback = False
    if chosen is None:
        cursor = ymd
        for _ in range(40):
            prev = prevTradingDay(cursor)
            if not prev:
                break
            final = dayFinalSnapshot(store.snaps(prev))
            if final is not None:
                chosen = final
                fallback = True
                break
            cursor = prev
    return {
        "snap": chosen,
        "fallback": bool(chosen) and fallback,
        "asof": str((chosen or {}).get("tradeYmd") or ymd)[:8],
        "queryLabel": queryStampLabel((chosen or {}).get("observedAt"), bool(chosen) and fallback),
        "orders": 0,
    }


def latestDisplaySnapshot(snaps):
    """
    화면에 쓸 그날 스냅샷.
    관측 시각이 가장 늦은 수집분을 쓴다.
    같은 시각이면 1600 을 남긴다. 더 늦은 수동(ADHOC)이면 수동을 보여 준다.
    """
    rows = [s for s in snaps or [] if isinstance(s, dict)]
    collected = [
        s for s in rows if s.get("state") == "COLLECTED" and s.get("observedAt")
    ]
    if collected:
        return max(
            collected,
            key=lambda s: (
                str(s.get("observedAt")),
                1 if str(s.get("slot")) == "1600" else 0,
            ),
        )
    missed = [
        s for s in rows if str(s.get("slot")) == "1600" and s.get("state") == "NOT_COLLECTED"
    ]
    return missed[-1] if missed else None


def _num(raw):
    """
    키움 부호 문자열.
    '+10.5' → 10.5, '-3.20' → -3.2, '--10.50' → -10.5.
    빈 값·'-'·'--' 는 None. 등락이 없는 칸을 0 으로 만들지 않는다.
    """
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
                "dailyBasis": DAILY_BASIS_KA00198,
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
        try:
            rememberFirstSurge(self.dir, snap)
        except Exception:
            pass
        return snap

    def snaps(self, tradeYmd):
        return list(self._load(tradeYmd).get("snaps") or [])

    def slot(self, tradeYmd, slot):
        match = [
            snap
            for snap in self.snaps(tradeYmd)
            if str(snap.get("slot")) == str(slot)
        ]
        for snap in match:
            if snap.get("state") == "COLLECTED" and snap.get("observedAt"):
                return snap
        return match[-1] if match else None

    def latestForDay(self, tradeYmd):
        return latestDisplaySnapshot(self.snaps(tradeYmd))

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


def ka00198ChangePct(row):
    """행의 base_comp_chgr. 이미 숫자면 그대로, 아니면 '+10.5' / '--' 규칙을 쓴다."""
    if not isinstance(row, dict):
        return None
    raw = row.get("baseCompChgr")
    if raw is None:
        raw = row.get("base_comp_chgr")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return _num(raw)


def snapComplete(snap):
    """다시 받지 않아도 되는 수집분. 잘리거나 실패한 파일은 불완전이다."""
    if not isinstance(snap, dict):
        return False
    if snap.get("state") != "COLLECTED" or not snap.get("observedAt"):
        return False
    if snap.get("ok") is False or snap.get("truncated") is True:
        return False
    return isinstance(snap.get("rows"), list)


def filterRankRows(rows, minPct=ENTRY_MIN_DAILY_PCT):
    """
    원천 순위를 유지하고, ka00198 base_comp_chgr 가 minPct 이상인 행만 남긴다.
    기본값은 SRG01 매수 기준 10%. 탭은 tabRankRows 로 5% 를 쓴다.
    명부·전일종가 계산은 쓰지 않는다. 등락이 없으면 버린다.
    """
    kept = []
    floor = float(minPct)
    for row in rows or []:
        daily = ka00198ChangePct(row)
        if daily is None or daily + 1e-9 < floor:
            continue
        item = dict(row)
        item["dailyPct"] = daily
        item["dailyBasis"] = DAILY_BASIS_KA00198
        item["index"] = ""
        kept.append(item)
    kept.sort(key=lambda r: int(r.get("originalRank") or 0))
    return {
        "rows": kept,
        "sourceCount": len(rows or []),
        "keptCount": len(kept),
        "exhaustive": False,
        "note": SOURCE_CAP_NOTE + f" 필터는 base_comp_chgr >= {floor:g}.",
    }


def tabRankRows(rows):
    """급등주 탭. base_comp_chgr >= 5. 매수 후보가 아니다."""
    return filterRankRows(rows, TAB_MIN_DAILY_PCT)


def entryUniverseRows(rows):
    """SRG01 후보. base_comp_chgr >= 10. 탭의 5% 목록과 분리한다."""
    return filterRankRows(rows, ENTRY_MIN_DAILY_PCT)


def _priceSource(slot):
    return "snapshot_1600" if str(slot) == "1600" else "snapshot_intraday"


def _offerFirstSurge(book, code, ymd, price, source):
    """더 이른 첫 급등일을 유지한다. 같은 날이면 16:00 종가, 그 다음 일봉, 그 다음 장중 최신가."""
    try:
        px = float(price)
    except (TypeError, ValueError):
        return
    if not code or len(str(ymd)) != 8 or px <= 0:
        return
    rank = {"snapshot_intraday": 1, "daily_close": 2, "snapshot_1600": 3}
    cur = book.get(code)
    if cur is None or str(ymd) < str(cur.get("ymd") or "99999999"):
        book[code] = {"ymd": str(ymd), "price": px, "source": source}
        return
    if str(ymd) != str(cur.get("ymd")):
        return
    old = rank.get(cur.get("source"), 0)
    new = rank.get(source, 0)
    if new > old or (new == old == 1):
        book[code] = {"ymd": str(ymd), "price": px, "source": source}


def absorbFirstSurge(book, snap):
    """등락이 10% 이상인 스냅만 첫 급등일로 본다. 입력 dict 는 바꾸지 않는다."""
    out = {}
    for code, row in (book or {}).items():
        if isinstance(row, dict) and row.get("ymd") and row.get("price"):
            out[str(code)] = {
                "ymd": str(row.get("ymd"))[:8],
                "price": float(row.get("price")),
                "source": str(row.get("source") or "snapshot_intraday"),
            }
    if not isinstance(snap, dict) or snap.get("state") == "NOT_COLLECTED":
        return out
    ymd = str(snap.get("tradeYmd") or "")[:8]
    if len(ymd) != 8:
        return out
    source = _priceSource(snap.get("slot"))
    for row in snap.get("rows") or []:
        if not isinstance(row, dict):
            continue
        change = ka00198ChangePct(row)
        if change is None or change + 1e-9 < ENTRY_MIN_DAILY_PCT:
            continue
        _offerFirstSurge(out, str(row.get("stkCd") or ""), ymd, row.get("price"), source)
    return out


def applyDailyClose(book, code, ymd, price):
    """첫 급등일의 16:00 스냅이 없을 때만 일봉 종가로 기준가를 바꾼다."""
    out = absorbFirstSurge(book, None)
    cur = out.get(str(code))
    if not cur or str(cur.get("ymd")) != str(ymd)[:8]:
        return out
    if cur.get("source") == "snapshot_1600":
        return out
    _offerFirstSurge(out, str(code), str(ymd)[:8], price, "daily_close")
    return out


def firstSurgePath(directory):
    return Path(directory) / FIRST_SURGE_FILE


def loadFirstSurgeBook(directory):
    path = firstSurgePath(directory)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return absorbFirstSurge(data if isinstance(data, dict) else {}, None)


def saveFirstSurgeBook(directory, book):
    path = firstSurgePath(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    return book


def rememberFirstSurge(directory, snap):
    book = absorbFirstSurge(loadFirstSurgeBook(directory), snap)
    saveFirstSurgeBook(directory, book)
    return book


def refreshFirstSurgeBook(directory):
    """저장된 일자 파일을 다시 읽어 첫 급등 기준을 맞춘다."""
    book = loadFirstSurgeBook(directory)
    root = Path(directory)
    if not root.is_dir():
        return book
    for path in sorted(root.glob("*.json")):
        if path.name == FIRST_SURGE_FILE:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for snap in (data or {}).get("snaps") or []:
            book = absorbFirstSurge(book, snap)
    saveFirstSurgeBook(directory, book)
    return book


def formatFirstSurgeVs(current, refPrice):
    """(현재가 / 첫급등 기준가 - 1) × 100. 기준이 없으면 '-'."""
    try:
        px = float(current)
        ref = float(refPrice)
    except (TypeError, ValueError):
        return "-", ""
    if px <= 0 or ref <= 0:
        return "-", ""
    pct = (px / ref - 1.0) * 100.0
    if pct > 0:
        return f"+{pct:.2f}", "up"
    if pct < 0:
        return f"{pct:.2f}", "down"
    return "+0.00", ""


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
    pen = _num(row.get("penfnd_etc")) if "penfnd_etc" in row else None
    if orgn is None and frgn is None and pen is None:
        return {"state": "N/A", "reason": "순매수 필드 없음", "asof": asof}
    turnover, note = turnoverEok(row.get("acc_trde_prica"), FLOW_UNIT_MILLION_KRW)
    return {
        "state": "OK",
        "asof": asof,
        "unit": FLOW_UNIT_MILLION_KRW,
        "unitNote": "ka10059 금액(amt_qty_tp=1)·순매수(trde_tp=0). 명세 단위 백만원.",
        "orgnNetMillionKrw": orgn,
        "frgnNetMillionKrw": frgn,
        "penfndNetMillionKrw": pen,
        "pensionDefinition": "penfnd_etc 연기금등. 기관계(orgn)에서 빼지 않는다.",
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


def recentYearLow(bars, asof="", lookback=250):
    """
    최근 1년 일봉 저가의 최저.
    창은 오늘 봉이 있으면 그 저가와 직전 lookback 거래일이다.
    상장이 그보다 짧으면 있는 저가만 쓴다. 종가로 저가를 만들지 않는다.
    """
    day = str(asof or "")[:8]
    points = []
    for bar in bars or []:
        if isinstance(bar, dict):
            dt = str(bar.get("dt") or "")[:8]
            raw = bar.get("low", None)
            if raw in (None, ""):
                raw = bar.get("lowPric", None)
        elif isinstance(bar, (list, tuple)) and len(bar) >= 2:
            dt = str(bar[0] or "")[:8]
            raw = bar[1]
        else:
            continue
        if len(dt) != 8:
            continue
        if day and dt > day:
            continue
        try:
            low = float(raw)
        except (TypeError, ValueError):
            continue
        if low <= 0:
            continue
        points.append((dt, low))
    if not points:
        return None
    points.sort(key=lambda item: item[0])
    collapsed = []
    for dt, low in points:
        if collapsed and collapsed[-1][0] == dt:
            collapsed[-1] = (dt, min(collapsed[-1][1], low))
        else:
            collapsed.append((dt, low))
    if not day:
        day = collapsed[-1][0]
    today = [low for dt, low in collapsed if dt == day]
    prior = [low for dt, low in collapsed if dt < day]
    window = prior[-int(lookback) :] if len(prior) > int(lookback) else list(prior)
    window.extend(today)
    if not window:
        return None
    return min(window)


def yearRisePct(currentPx, yearLow):
    """(현재가 / 일봉 저가 최저 - 1) × 100. 저가가 없으면 None."""
    try:
        px = float(currentPx)
        low = float(yearLow)
    except (TypeError, ValueError):
        return None
    if px <= 0 or low <= 0:
        return None
    return (px / low - 1.0) * 100.0


def formatYearRisePct(pct):
    """소수 1자리 %. 플러스 빨강, 마이너스 파랑에 쓸 부호."""
    if pct in (None, ""):
        return "-", ""
    try:
        shown = round(float(pct) + 1e-9, 1)
    except (TypeError, ValueError):
        return "-", ""
    if shown > 0:
        return f"+{shown:.1f}%", "up"
    if shown < 0:
        return f"{shown:.1f}%", "down"
    return "+0.0%", ""


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


# 이유·테마 단어를 앞에 두고, 서로 떨어진 파스텔을 고정한다.
# RSI #D6E8FF/#FFD6D6, 배지 #D4F5D4 와 겹치지 않는다.
_ISSUE_PALETTE = (
    "#ACDBEC", "#F08989", "#89F089", "#F4F4A4", "#F089F0", "#8989F0", "#FBDBFB", "#CFAABE",
    "#B9ECAC", "#BFA4F4", "#89F0C7", "#F0C089", "#F7C7BF", "#89B9F0", "#F089B9", "#F0FBDB",
    "#AAB9CF", "#D6CBEB", "#D5F089", "#89F0F0", "#ECACDB", "#D6DABE", "#BFF7F7", "#F4AAA4",
    "#B2F089", "#ACECCE", "#AB89F0", "#9FDB9F", "#DBCF9F", "#CE89F0", "#AACFB9", "#9F9FDB",
    "#F0BFF7", "#CF9FDB", "#F7E5BF", "#F0E389", "#ACBDEC", "#DFA4F4", "#BECDDA", "#DABECF",
    "#89F0A5", "#DB9F9F", "#F4A4BF", "#A4F4F4", "#D4F4A4", "#94D0E6", "#E694D0", "#F0A589",
    "#F4D9A4", "#C0CFAA", "#E3B8B5", "#A4A4F4", "#BFCBF7", "#C7B5E3", "#BEDAC5", "#94E6DB",
    "#B79FDB", "#A4F4A4", "#DBB79F", "#E8F7BF", "#F0E5E9", "#BFF7BF", "#AACFCF", "#C5E694",
    "#F4A4EE", "#E094E6", "#94E6B4", "#F4BFA4", "#A4F4B9", "#899EF0", "#F0899E", "#EBCBE7",
    "#A4F4DF", "#C3AACF", "#B3DB9F", "#9EF089", "#E3CEB5", "#CFAAAA", "#D1DDE5", "#B5E3DD",
    "#DDB5E3", "#CFC3AA", "#9FDBC3", "#89DCF0", "#9FC3DB", "#F089DC", "#94AAE6", "#E694AA",
    "#C7E3B5", "#E3ECAC", "#CEBFF7", "#E0E694", "#DB9FC7", "#DB9FB3", "#E69494", "#94E694",
    "#9494E6", "#E6AF94", "#BF94E6", "#B1ACEC", "#EBE7CB", "#F7BFE5", "#9FD7DB", "#B2AACF",
    "#CFDB9F", "#AAE694", "#DAC3BE", "#D2ACEC", "#E6D594", "#A4CFF4", "#B5E3BB", "#94BFE6",
    "#E694BF", "#9FDBAF", "#9FB3DB", "#A494E6", "#F4A4CF", "#AFCFAA", "#C4F4A4", "#E6C594",
    "#ECACB1", "#E3B5C4", "#94E6CA", "#CFBEDA", "#E3DDB5", "#F7F7BF", "#BFF7E8", "#BEBEDA",
    "#DDBFF7", "#D094E6",
)


def _knownIssueTags():
    from trading.leaderIssueWords import LEADER_EVENT_MAP, LEADER_ISSUE_MAP, REASON_MAP

    tags = []
    for canon, _words in REASON_MAP:
        if canon not in tags:
            tags.append(canon)
    for _key, lab in list(LEADER_ISSUE_MAP) + list(LEADER_EVENT_MAP):
        if lab not in tags:
            tags.append(lab)
    return tags


_KNOWN_ISSUE_TAGS = _knownIssueTags()
_ISSUE_TAG_COLORS = {
    tag: _ISSUE_PALETTE[i] for i, tag in enumerate(_KNOWN_ISSUE_TAGS)
}


def _truncInt(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def formatGroupedInt(value):
    """현재가·거래대금. 소수점 버리고 천 단위 콤마."""
    num = _truncInt(value)
    if num is None:
        return "-"
    return f"{num:,}"


def formatNetEok(millionKrw):
    """
    ka10059 금액(amt_qty_tp=1)·순매수(trde_tp=0)의 orgn·frgnr_invsr·penfnd_etc.
    명세 단위는 백만원. 100백만원 = 1억. 소수점 이하는 버린다.
    반환: (표시문자열, up|down|"")
    """
    if millionKrw in (None, ""):
        return "-", ""
    try:
        eok = float(millionKrw) / 100.0
    except (TypeError, ValueError):
        return "-", ""
    num = int(eok)
    if num > 0:
        return f"+{num:,}억", "up"
    if num < 0:
        return f"{num:,}억", "down"
    return "0억", ""


def netFlowColor(sign):
    if sign == "up":
        return "#D32F2F"
    if sign == "down":
        return "#1565C0"
    return ""


def nameBadges(newHigh, newSurge):
    """배지 글자. 셀의 종목명 문자열에는 넣지 않는다."""
    marks = ""
    if newHigh:
        marks += "신"
    if newSurge:
        marks += "뉴"
    return marks


def badgeFillColor(mark):
    """신·뉴 네모 안 바탕. 종목명 칸 전체에는 쓰지 않는다."""
    if mark == "신":
        return "#FFD6D6"
    if mark == "뉴":
        return "#D4F5D4"
    return ""


DAILY_PCT_BG_20 = "#FFD6D6"
DAILY_PCT_BG_10 = "#FFF3C4"


def dailyPctCellColor(value):
    """일간% 배경만. 20% 이상은 연빨강, 10% 이상 20% 미만은 연노랑. 글자색은 바꾸지 않는다."""
    if value in (None, ""):
        return ""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return ""
    if pct >= 20:
        return DAILY_PCT_BG_20
    if pct >= 10:
        return DAILY_PCT_BG_10
    return ""


def rsiCellColor(value):
    """RSI14 배경. 30 이하는 옅은 파랑, 70 이상은 옅은 빨강. 그 사이와 빈 값은 색 없음."""
    if value in (None, ""):
        return ""
    try:
        rsi = float(value)
    except (TypeError, ValueError):
        return ""
    if rsi <= 30:
        return "#D6E8FF"
    if rsi >= 70:
        return "#FFD6D6"
    return ""


def _freshPastel(used):
    """팔레트가 바닥나면 화면에서 아직 안 쓴 색을 하나 고른다."""
    banned = {"#D6E8FF", "#FFD6D6", "#D4F5D4"}
    n = 0
    while n < 720:
        hue = (n * 47 + 19) % 360
        n += 1
        sat = 0.45 + (n % 3) * 0.12
        light = 0.78 + (n % 4) * 0.04
        c = (1 - abs(2 * light - 1)) * sat
        x = c * (1 - abs((hue / 60.0) % 2 - 1))
        m = light - c / 2
        if hue < 60:
            r, g, b = c, x, 0
        elif hue < 120:
            r, g, b = x, c, 0
        elif hue < 180:
            r, g, b = 0, c, x
        elif hue < 240:
            r, g, b = 0, x, c
        elif hue < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        color = "#{:02X}{:02X}{:02X}".format(
            int(round((r + m) * 255)),
            int(round((g + m) * 255)),
            int(round((b + m) * 255)),
        )
        if color in used or color in banned or color in _ISSUE_TAG_COLORS.values():
            continue
        return color
    return "#F3E6D0"


def issueTagColor(tag):
    """목록에 있는 이슈는 항상 같은 색. 없으면 빈 문자열."""
    text = str(tag or "").strip()
    if not text:
        return ""
    return _ISSUE_TAG_COLORS.get(text, "")


def assignIssueColors(tags):
    """한 화면에서 서로 다른 이슈는 다른 색. 같은 이슈는 같은 색."""
    chosen = {}
    used = set()
    spare = [color for color in _ISSUE_PALETTE if color not in _ISSUE_TAG_COLORS.values()]
    spare_i = 0
    out = []
    for raw in tags or []:
        text = str(raw or "").strip()
        if not text:
            out.append("")
            continue
        if text not in chosen:
            fixed = _ISSUE_TAG_COLORS.get(text)
            if fixed and fixed not in used:
                chosen[text] = fixed
            else:
                while spare_i < len(spare) and spare[spare_i] in used:
                    spare_i += 1
                if spare_i < len(spare):
                    chosen[text] = spare[spare_i]
                    spare_i += 1
                else:
                    chosen[text] = _freshPastel(used)
            used.add(chosen[text])
        out.append(chosen[text])
    return out


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


def buildTabRows(snap, asof, extras=None):
    """탭에 그릴 행. 없는 지표는 None / N/A. 명부는 보지 않는다."""
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
    filtered = tabRankRows(snap.get("rows") or [])
    book = extras.get("firstSurge") or {}
    closes = extras.get("dailyClose") or {}
    for code, bar in closes.items():
        if isinstance(bar, dict):
            book = applyDailyClose(book, code, bar.get("ymd"), bar.get("price"))
    view = []
    for row in filtered["rows"]:
        code = row["stkCd"]
        extra = (extras.get("byCode") or {}).get(code) or {}
        issue = extra.get("issue") or {}
        flow = extra.get("flow") or {}
        daily = row.get("dailyPct")
        first = str(issue.get("firstSurgeDate") or "")
        tags = issue.get("tag") or ""
        ref = book.get(code) if isinstance(book, dict) else None
        vs, sign = ("-", "")
        if isinstance(ref, dict):
            vs, sign = formatFirstSurgeVs(row.get("price"), ref.get("price"))
        rise = yearRisePct(row.get("price"), extra.get("yearLow"))
        item = {
            "code": code,
            "name": row.get("name") or "",
            "newHigh": extra.get("newHigh") is True,
            "newSurge": extra.get("newSurge") is True,
            "index": extra.get("market") or row.get("index") or "",
            "price": row.get("price"),
            "dailyPct": daily,
            "turnoverEok": flow.get("turnoverEok"),
            "turnoverNote": flow.get("turnoverNote") or "N/A",
            "issues": tags if issue.get("confirmed") else "",
            "issueColor": "",
            "issueDetails": issue.get("details") or ([] if not issue else [issue]),
            "orgnNetMillionKrw": flow.get("orgnNetMillionKrw"),
            "frgnNetMillionKrw": flow.get("frgnNetMillionKrw"),
            "penfndNetMillionKrw": flow.get("penfndNetMillionKrw"),
            "flowUnit": flow.get("unit") or "",
            "flowAsOf": flow.get("asof") or "",
            "flowStatus": flow.get("flowStatus") or "N/A",
            "rsi14": extra.get("rsi14"),
            "yearRisePct": rise,
            "yearRiseSign": "up" if isinstance(rise, float) and rise > 0 else ("down" if isinstance(rise, float) and rise < 0 else ""),
            "firstSurgeVs": vs,
            "firstSurgeSign": sign,
            "dataState": extra.get("dataState") or "PARTIAL",
            "yellowName": yellowName(issue.get("confirmed"), daily, asof, first),
            "buyEligible": False,
        }
        view.append(item)
    colors = assignIssueColors([row.get("issues") for row in view])
    for row, color in zip(view, colors):
        row["issueColor"] = color
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
