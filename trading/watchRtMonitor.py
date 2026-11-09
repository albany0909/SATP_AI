"""
관심종목(USE_TRADE=Y) 실시간 체결(0B) 감시.
웹에서 추가해도 시세 구간이 아니면 즉시 REG 하지 않고, 08:00(NXT 장전) 또는 기동 시 구독한다.
STLS 0B(grp 1)와 겹치지 않게 grp_no=2 를 쓴다.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from config.krHoliday import isTradingDay
from config.settingsStore import getMktHours, getNotifyHours, isNotifySessionNow, isWithinMktHours
from realtime.wsHub import (
    getWsClient,
    registerLoginHandler,
    registerMsgHandler,
)
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.deskDataApi import listTradeWatch

_HANDLER_NAME = "watchRt0B"
_LOGIN_HANDLER_NAME = "watchRt0BLogin"
_GRP_NO = "2"
_MAX_STK = 180
_MAX_ITEM = 200
_CHUNK = 40
_STATE_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "watchRtState.json"
# 재시작 후에도 마지막 0B 시세를 유지 (일봉 시드보다 우선)
_PX_CACHE_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "watchLastPx.json"
_PX_FLUSH_SEC = 2.0


def _nzQuote(raw):
    """키움 FID 공백은 미수신으로 본다."""
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s else None


def _pickFlu(*vals):
    """등락률 후보 중 공백이 아닌 첫 값."""
    for val in vals:
        picked = _nzQuote(val)
        if picked is not None:
            return picked
    return None


_lock = threading.Lock()
_monActive = False
_loopStarted = False
_regCodes = []
_regItems = {}
_nxtCds = set()
_lastPx = {}
# 시세구간 외 일봉 종가 시드 (0B 들어오면 덮어씀)
_dayCloseSeeded = set()
_dayCloseSeedYmd = ""
_pxCacheLoaded = False
_pxSavePending = False
_lastPxFlushAt = 0.0


def _hasLivePx(info):
    """캐시에 표시 가능한 현재가가 있는지."""
    info = info or {}
    return bool(
        _nzQuote(info.get("curPrc"))
        or _nzQuote(info.get("krxPrc"))
        or _nzQuote(info.get("nxtPrc"))
    )


def _isLiveQuoteSrc(info):
    """0B(또는 복원된 실시간) 시세. dayClose 시드와 구분."""
    src = str((info or {}).get("src") or "")
    return src == "0B" or (src and src != "dayClose" and _hasLivePx(info))


def _loadLastPxCache():
    """
    재기동 직후 메모리 시세가 비어 일봉(낡은 종가)으로 떨어지지 않게
    직전 0B 스냅샷을 복원한다. dayClose 보다 최신 tickYmd 만 살린다.
    """
    global _pxCacheLoaded
    if _pxCacheLoaded:
        return
    _pxCacheLoaded = True
    try:
        if not _PX_CACHE_PATH.is_file():
            return
        data = json.loads(_PX_CACHE_PATH.read_text(encoding="utf-8"))
        quotes = data.get("quotes") if isinstance(data, dict) else None
        if not isinstance(quotes, dict):
            return
        restored = 0
        with _lock:
            for rawCd, rawInfo in quotes.items():
                cd = normStkCd(rawCd)
                if not cd or not isinstance(rawInfo, dict):
                    continue
                # 이미 메모리에 실시간/시드가 있으면 덮지 않음
                cur = _lastPx.get(cd) or {}
                if _hasLivePx(cur):
                    continue
                info = dict(rawInfo)
                if not _hasLivePx(info):
                    continue
                # 파일에 src 없으면 0B 복원으로 본다
                if not info.get("src"):
                    info["src"] = "0B"
                _lastPx[cd] = info
                restored += 1
        if restored:
            termLog(f"[watchRt] 시세캐시 복원 {restored}종 ({_PX_CACHE_PATH.name})")
    except Exception as err:
        termLog(f"[watchRt] 시세캐시 복원 실패: {err}")


def _markPxDirty():
    """0B 갱신 후 디스크 저장 예약."""
    global _pxSavePending
    _pxSavePending = True


def _flushLastPxCache(force=False):
    """
    _lastPx 스냅샷을 watchLastPx.json 에 기록.
    틱마다 IO 하지 않도록 기본 2초 디바운스.
    """
    global _pxSavePending, _lastPxFlushAt
    if not _pxSavePending and not force:
        return
    now = time.time()
    if not force and (now - _lastPxFlushAt) < _PX_FLUSH_SEC:
        return
    with _lock:
        snap = {}
        for cd, info in _lastPx.items():
            if not isinstance(info, dict) or not _hasLivePx(info):
                continue
            # dayClose 만 있는 종목은 파일이 일봉보다 우선해 버리면 안 되므로
            # 0B(실시간·복원) 위주로 저장한다.
            if not _isLiveQuoteSrc(info):
                continue
            row = dict(info)
            if not row.get("tickYmd"):
                row["tickYmd"] = datetime.now().strftime("%Y%m%d")
            snap[cd] = row
        pending = _pxSavePending
        _pxSavePending = False
        _lastPxFlushAt = now
    if not snap and not pending and not force:
        return
    try:
        _PX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "quotes": snap,
        }
        _PX_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as err:
        termLog(f"[watchRt] 시세캐시 저장 실패: {err}")
        with _lock:
            _pxSavePending = True


def _isMonSessionNow():
    """거래일 + 정규장(mkhr). 이평터치 매매 구간."""
    now = datetime.now()
    if not isTradingDay(now):
        return False
    return isWithinMktHours(now.strftime("%H:%M"))


def _isQuoteSessionNow():
    """
    거래일 + 시세 수신 구간(notify 08:00~20:00).
    NXT 장전·애프터를 포함해 0B 를 건다. 정규장만 보면 장전에 NXT가 안 들어온다.
    """
    return isNotifySessionNow()


def nextMktOpenLabel():
    """웹 안내용 다음 개장 시각. 장중이면 '다음 거래일' 개장."""
    openHm, _closeHm = getMktHours()
    now = datetime.now()
    hm = now.strftime("%H:%M")
    if isTradingDay(now) and hm < openHm:
        return f"{now.strftime('%Y-%m-%d')} {openHm}"
    day = now
    for _ in range(1, 12):
        day = day + timedelta(days=1)
        if isTradingDay(day):
            return f"{day.strftime('%Y-%m-%d')} {openHm}"
    return f"다음 거래일 {openHm}"


def _writeState(active, codes, reason=""):
    payload = {
        "active": bool(active),
        "reason": reason,
        "cnt": len(codes or []),
        "stkCds": list(codes or []),
        "nextOpen": nextMktOpenLabel(),
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as err:
        termLog(f"[watchRt] 상태파일 기록 실패: {err}")


def _seedDayCloseQuotes(codes):
    """
    시세 미수신 종목: AI_DAY_STK_PRC 최근 종가·전일대비 등락률로 _lastPx 채움.
    이미 0B 시세가 있으면 덮지 않는다.
    """
    global _dayCloseSeeded, _dayCloseSeedYmd
    today = datetime.now().strftime("%Y%m%d")
    if _dayCloseSeedYmd != today:
        _dayCloseSeeded = set()
        _dayCloseSeedYmd = today
    cds = []
    for raw in codes or []:
        cd = normStkCd(raw)
        if not cd or cd in cds:
            continue
        cds.append(cd)
    if not cds:
        return 0

    # client 등 로컬 DB 없으면 STP SMA/종가 API
    try:
        from utils.dbConn import isLocalDbEnabled

        if not isLocalDbEnabled():
            return _seedDayCloseFromSmaApi(cds)
    except Exception:
        pass

    filled = 0
    try:
        from utils.dbConn import openDbConn

        conn = openDbConn()
        try:
            with conn.cursor() as cur:
                for cd in cds:
                    with _lock:
                        curInfo = _lastPx.get(cd) or {}
                        # 실시간(0B·복원) 값이 있으면 유지 — 일봉은 아래에서 날짜 비교
                        if _isLiveQuoteSrc(curInfo):
                            tickYmd = str(curInfo.get("tickYmd") or "")
                            # tickYmd 없으면 최신 틱으로 보고 일봉으로 덮지 않음
                            if not tickYmd:
                                _dayCloseSeeded.add(cd)
                                continue
                        # 오늘 이미 보강 시도(데이터 없음 포함)면 재조회하지 않는다
                        if cd in _dayCloseSeeded:
                            continue
                    cur.execute(
                        """
                        SELECT TRADE_DT, CLOSE_PRC
                        FROM AI_DAY_STK_PRC
                        WHERE STK_CD=%s AND INDC_TP='0'
                          AND CLOSE_PRC IS NOT NULL
                          AND TRADE_DT<=%s
                        ORDER BY TRADE_DT DESC
                        LIMIT 2
                        """,
                        (cd, today),
                    )
                    rows = cur.fetchall() or []
                    if not rows:
                        with _lock:
                            _dayCloseSeeded.add(cd)
                        continue
                    try:
                        closePx = abs(float(rows[0]["CLOSE_PRC"]))
                    except (TypeError, ValueError):
                        with _lock:
                            _dayCloseSeeded.add(cd)
                        continue
                    if closePx <= 0:
                        with _lock:
                            _dayCloseSeeded.add(cd)
                        continue
                    fluRt = None
                    if len(rows) >= 2:
                        try:
                            prevPx = abs(float(rows[1]["CLOSE_PRC"]))
                            if prevPx > 0:
                                fluRt = (closePx - prevPx) / prevPx * 100.0
                        except (TypeError, ValueError):
                            fluRt = None
                    prcTxt = str(int(round(closePx)))
                    fluTxt = f"{fluRt:+.2f}" if fluRt is not None else ""
                    tradeDt = str(rows[0].get("TRADE_DT") or "")
                    with _lock:
                        # 시드 중에 0B 가 들어왔으면 덮지 않음
                        nowInfo = _lastPx.get(cd) or {}
                        if _isLiveQuoteSrc(nowInfo):
                            tickYmd = str(nowInfo.get("tickYmd") or "")
                            # 일봉 종가일이 마지막 틱일 이하면 낡은 시드 — 0B 유지
                            if (not tickYmd) or (tradeDt and tradeDt <= tickYmd):
                                _dayCloseSeeded.add(cd)
                                continue
                        _lastPx[cd] = {
                            "curPrc": prcTxt,
                            "fluRt": fluTxt,
                            "krxPrc": prcTxt,
                            "krxFlu": fluTxt,
                            "tm": "153000",
                            "krxTm": "153000",
                            "exch": "KRX",
                            "src": "dayClose",
                            "dayCloseDt": tradeDt,
                        }
                        _dayCloseSeeded.add(cd)
                    filled += 1
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as err:
        try:
            from utils.dbConn import DbDisabledError, DbDownError

            if isinstance(err, (DbDisabledError, DbDownError)):
                return _seedDayCloseFromSmaApi(cds)
        except Exception:
            pass
        termLog(f"[watchRt] 일봉종가 시드 실패: {err}")
        return filled
    if filled:
        termLog(f"[watchRt] 일봉종가 시드 {filled}종")
    return filled


def _seedDayCloseFromSmaApi(cds):
    """STP listDaySma 로 종가·등락 시드 (client)."""
    global _dayCloseSeeded
    try:
        from utils.deskDataApi import loadPriorSmaMap
    except Exception as err:
        termLog(f"[watchRt] SMA API import 실패: {err}")
        return 0
    need = []
    with _lock:
        for cd in cds:
            if cd in _dayCloseSeeded:
                continue
            curInfo = _lastPx.get(cd) or {}
            if _isLiveQuoteSrc(curInfo) and not str(curInfo.get("tickYmd") or ""):
                _dayCloseSeeded.add(cd)
                continue
            need.append(cd)
    if not need:
        return 0
    try:
        mp = loadPriorSmaMap(need) or {}
    except Exception as err:
        termLog(f"[watchRt] 일봉종가 시드(API) 실패: {err}")
        return 0
    filled = 0
    for cd in need:
        rec = mp.get(cd) or {}
        closePx = rec.get("lastClose")
        try:
            closePx = abs(float(closePx)) if closePx is not None else 0
        except (TypeError, ValueError):
            closePx = 0
        with _lock:
            if cd in _dayCloseSeeded and _isLiveQuoteSrc(_lastPx.get(cd) or {}):
                continue
            if closePx <= 0:
                _dayCloseSeeded.add(cd)
                continue
            fluRt = rec.get("fluRt")
            prcTxt = str(int(round(closePx)))
            # 등락은 ka10001/0B 공식값만 쓴다. SMA fluRt 는 직전 거래일 수치라
            # 장외·휴장에 +8%대 오표시가 난다 → 시드는 가격만.
            fluTxt = ""
            tradeDt = str(rec.get("lastDt") or "")[:8]
            nowInfo = _lastPx.get(cd) or {}
            if _isLiveQuoteSrc(nowInfo):
                tickYmd = str(nowInfo.get("tickYmd") or "")
                if (not tickYmd) or (tradeDt and tradeDt <= tickYmd):
                    _dayCloseSeeded.add(cd)
                    continue
            _lastPx[cd] = {
                "curPrc": prcTxt,
                "fluRt": fluTxt,
                "krxPrc": prcTxt,
                "krxFlu": fluTxt,
                "tm": "153000",
                "krxTm": "153000",
                "exch": "KRX",
                "src": "dayClose",
                "dayCloseDt": tradeDt,
            }
            _dayCloseSeeded.add(cd)
        filled += 1
    if filled:
        termLog(f"[watchRt] 일봉종가 시드(API) {filled}종")
    return filled


# 전일종가가 없어 등락 보강을 포기한 (일자, 종목)
_fluFillSkip = set()


def fillMissingFluQuotes(codes, pxByCd=None, force=False):
    """
    등락률이 비면 보강한다.
    force=True(보유 dayClose 등): 키움 ka10001 flu_rt/기준가 우선.
      DB 최신 일봉은 적재 공백 시 기준가와 달라 +8%대 오표시가 난다.
    force=False: 등락 빈 칸만 전일종가 대비로 채움.
    pxByCd 는 0B 캐시에 현재가가 없을 때 쓰는 힌트(계좌평가 현재가).
    client(로컬 DB 없음): STP SMA 전일종가 + 현재가로 계산.
    """
    hint = pxByCd or {}
    cds = []
    seen = set()
    today = datetime.now().strftime("%Y%m%d")
    for raw in codes or []:
        cd = normStkCd(raw)
        if not cd or cd in seen:
            continue
        seen.add(cd)
        if (today, cd) in _fluFillSkip:
            continue
        with _lock:
            info = _lastPx.get(cd) or {}
        if not force:
            flu = info.get("fluRt") or info.get("krxFlu") or info.get("nxtFlu")
            if flu is not None and str(flu).strip() != "":
                continue
        cds.append(cd)
    if not cds:
        return 0
    filled = 0

    def _applyFlu(cd, fluTxt):
        nonlocal filled
        with _lock:
            info = dict(_lastPx.get(cd) or {})
            if not force:
                for key in ("fluRt", "krxFlu", "nxtFlu"):
                    val = info.get(key)
                    if val is not None and str(val).strip() != "":
                        return
            info["fluRt"] = fluTxt
            info["krxFlu"] = fluTxt
            _lastPx[cd] = info
        filled += 1

    def _curPxOf(cd):
        with _lock:
            info = dict(_lastPx.get(cd) or {})
            curRaw = (
                info.get("curPrc") or info.get("krxPrc") or info.get("nxtPrc")
            )
        try:
            s = str(curRaw or "").replace(",", "").strip()
            if s[:1] in "+-":
                s = s[1:]
            curPx = abs(float(s)) if s else 0
        except (TypeError, ValueError):
            curPx = 0
        if curPx <= 0:
            try:
                curPx = abs(float(hint.get(cd) or 0))
            except (TypeError, ValueError):
                curPx = 0
        return curPx

    def _stillNeed():
        out = []
        for cd in cds:
            with _lock:
                info = _lastPx.get(cd) or {}
            flu = info.get("fluRt") or info.get("krxFlu") or info.get("nxtFlu")
            if flu is not None and str(flu).strip() != "":
                continue
            out.append(cd)
        return out

    # 키움 공식 등락(ka10001 flu_rt / 기준가) — 항상 최우선
    # force=True 뿐 아니라 빈 칸 보강도 공식값을 먼저 쓴다 (STP/DB +8%대 오표시 방지)
    try:
        from utils.liveOhlcSnap import fetchOfficialFlu

        for cd in list(cds):
            flu = fetchOfficialFlu(cd)
            if flu is None:
                continue
            _applyFlu(cd, f"{float(flu):+.2f}")
            # 공식값으로 채웠으면 dayClose 오등락 잔상을 끊는다
            with _lock:
                info = dict(_lastPx.get(cd) or {})
                if str(info.get("src") or "") == "dayClose":
                    info["fluSrc"] = "ka10001"
                    _lastPx[cd] = info
    except Exception as err:
        termLog(f"[watchRt] 공식등락 보강 실패: {err}")

    need = _stillNeed()
    if not need:
        return filled

    # force(보유 dayClose 등): 공식 실패 시 DB/STP 로 억지 채우지 않음
    if force:
        return filled

    # 로컬 DB 전일종가 (main, 비강제)
    useLocalDb = True
    try:
        from utils.dbConn import isLocalDbEnabled

        useLocalDb = bool(isLocalDbEnabled())
    except Exception:
        useLocalDb = False

    if useLocalDb:
        try:
            from utils.dbConn import openDbConn

            conn = openDbConn()
            try:
                with conn.cursor() as cur:
                    for cd in list(need):
                        cur.execute(
                            """
                            SELECT CLOSE_PRC
                            FROM AI_DAY_STK_PRC
                            WHERE STK_CD=%s AND INDC_TP='0'
                              AND CLOSE_PRC IS NOT NULL
                              AND TRADE_DT<%s
                            ORDER BY TRADE_DT DESC
                            LIMIT 1
                            """,
                            (cd, today),
                        )
                        row = cur.fetchone() or {}
                        try:
                            prevPx = abs(float(row.get("CLOSE_PRC") or 0))
                        except (TypeError, ValueError):
                            prevPx = 0
                        if prevPx <= 0:
                            continue
                        curPx = _curPxOf(cd)
                        if curPx <= 0:
                            continue
                        _applyFlu(
                            cd, f"{(curPx - prevPx) / prevPx * 100.0:+.2f}"
                        )
            finally:
                conn.close()
        except Exception as err:
            termLog(f"[watchRt] 등락률 DB 보강 실패: {err}")

    need = _stillNeed()
    if not need:
        return filled

    # client 등: STP 전일종가 대비 계산만 (fluRt 필드 직접 사용 금지)
    try:
        from utils.deskDataApi import loadPriorSmaMap

        mp = loadPriorSmaMap(need) or {}
        for cd in need:
            rec = mp.get(cd) or {}
            try:
                prevPx = abs(float(rec.get("lastClose") or 0))
            except (TypeError, ValueError):
                prevPx = 0
            curPx = _curPxOf(cd)
            if prevPx > 0 and curPx > 0:
                _applyFlu(cd, f"{(curPx - prevPx) / prevPx * 100.0:+.2f}")
            elif prevPx <= 0:
                _fluFillSkip.add((today, cd))
    except Exception as err:
        termLog(f"[watchRt] 등락률 STP 보강 실패: {err}")
    return filled


def getWatchLastPxMap():
    """매매창 그리드용. {stkCd: {curPrc, fluRt, tm, krxPrc, nxtPrc, ...}} 스냅샷."""
    # 0B 미수신(장후 KRX전용·구독한도 밖·재시작)은 일봉으로 보강. 0B 는 덮지 않음.
    # 시세구간(08~20시)에도 보강한다. 예전에는 구간 안에서 시드를 건너뛰어 빈칸이 남았다.
    try:
        codes = _monCodes()
        missPx = []
        missFlu = []
        with _lock:
            for cd in codes:
                info = _lastPx.get(cd) or {}
                hasPx = bool(
                    _nzQuote(info.get("curPrc"))
                    or _nzQuote(info.get("krxPrc"))
                    or _nzQuote(info.get("nxtPrc"))
                )
                hasFlu = bool(
                    _pickFlu(info.get("fluRt"), info.get("krxFlu"), info.get("nxtFlu"))
                )
                if not hasPx:
                    missPx.append(cd)
                elif not hasFlu:
                    missFlu.append(cd)
        if missPx:
            _seedDayCloseQuotes(missPx)
            with _lock:
                for cd in missPx:
                    info = _lastPx.get(cd) or {}
                    if not _pickFlu(
                        info.get("fluRt"), info.get("krxFlu"), info.get("nxtFlu")
                    ):
                        missFlu.append(cd)
        if missFlu:
            fillMissingFluQuotes(missFlu)
    except Exception as err:
        termLog(f"[watchRt] 시세 보강 실패: {err}")
    with _lock:
        return {cd: dict(info) for cd, info in _lastPx.items()}


def peekWatchLastPx(stkCd):
    """
    캐시만 읽는다. DB 시드·등락 보강을 하지 않아
    차트 틱이 주문/0B 수신을 막지 않게 한다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return {}
    with _lock:
        info = _lastPx.get(cd)
        return dict(info) if info else {}


def readWatchRtState():
    """웹이 읽는 감시 상태. 파일 없으면 스케줄 힌트만."""
    try:
        if _STATE_PATH.is_file():
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["nextOpen"] = nextMktOpenLabel()
                return data
    except Exception:
        pass
    return {
        "active": False,
        "reason": "",
        "cnt": 0,
        "stkCds": [],
        "nextOpen": nextMktOpenLabel(),
        "updatedAt": "",
    }


def _monCodes():
    """USE_TRADE=Y ∪ 알고리즘 관리종목 ∪ 원장 미청산."""
    codes = []
    seen = set()
    # watchRt.subscribeScope: "realAcnt"=실계좌(RAM01)·전략탭·미청산 원장만, "all"=관심(USE_TRADE=Y) 포함
    scope = "all"
    try:
        from config.settingsStore import loadSettings

        scope = str(((loadSettings() or {}).get("watchRt") or {}).get("subscribeScope") or "all").strip()
    except Exception:
        scope = "all"
    rows = [] if scope == "realAcnt" else listTradeWatch(limit=_MAX_STK)
    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    # client 매매창 탭(ALG) 종목도 0B 구독·시드 대상
    try:
        from trading.deskLiveAlgs import listDeskLiveAlgCds
        from utils.deskDataApi import listAlgStk

        algCds = list(listDeskLiveAlgCds(enabledOnly=False) or [])
        if "RAM01" not in algCds:
            # 실계좌 관심은 전략 탭과 별도로 시세를 받는다
            algCds.append("RAM01")
        for alg in algCds:
            if len(codes) >= _MAX_STK:
                break
            for row in listAlgStk(alg) or []:
                cd = normStkCd(row.get("stkCd") or row.get("STK_CD"))
                if cd and cd not in seen and len(codes) < _MAX_STK:
                    seen.add(cd)
                    codes.append(cd)
    except Exception as err:
        termLog(f"[watchRt] 관리종목 병합 실패: {err}")
    try:
        from trading.surgeTradeInputs import srgQuoteCodes

        for cd in srgQuoteCodes():
            if cd and cd not in seen and len(codes) < _MAX_STK:
                seen.add(cd)
                codes.append(cd)
    except Exception:
        pass
    try:
        from utils.stratOrdLedgerStore import listOpenLedgerStkCds

        for cd in listOpenLedgerStkCds():
            if cd and cd not in seen and len(codes) < _MAX_STK:
                seen.add(cd)
                codes.append(cd)
    except Exception as err:
        termLog(f"[watchRt] 원장 종목 병합 실패: {err}")
    return codes


def _splitRtItem(raw):
    """0B item → (6자리코드, KRX|NXT)."""
    cd = normStkCd(raw)
    up = cd.upper()
    if up.endswith("_NX"):
        return cd[:-3], "NXT"
    if up.endswith("_AL"):
        return cd[:-3], "SOR"
    return cd, "KRX"


def _isNxtStk(stkCd):
    try:
        from utils.stkInfoStore import isNxtEnabled

        return isNxtEnabled(stkCd)
    except Exception:
        return False


def _rtItemsOf(stkCd):
    """
    구독 코드. KRX 6자리는 항상, NXT가능이면 code_NX 도 같이.
    매매방식이 KRX여도 NXT 시세는 받는다.
    """
    cd = normStkCd(stkCd)
    if not cd:
        return []
    items = [cd]
    if _isNxtStk(cd):
        items.append(f"{cd}_NX")
    return items


def _buildRegMap(codes):
    """종목 → 구독 item 목록. 세션 200 한도, NXT가능은 KRX+NXT."""
    out = {}
    used = 0
    for raw in codes or []:
        cd = normStkCd(raw)
        if not cd or cd in out:
            continue
        items = _rtItemsOf(cd)
        if not items:
            continue
        if used + len(items) > _MAX_ITEM:
            items = items[: max(0, _MAX_ITEM - used)]
        if not items:
            break
        out[cd] = items
        used += len(items)
    return out


def _flatItems(regMap, codes=None):
    items = []
    keys = codes if codes is not None else list((regMap or {}).keys())
    for cd in keys:
        raw = (regMap or {}).get(cd, [cd])
        if isinstance(raw, list):
            items.extend([x for x in raw if x])
        elif raw:
            items.append(raw)
    return items


def reregWatchForExch(reason="거래소변경"):
    """매매 거래소 변경 시 시세 구간이면 0B 를 다시 등록한다."""
    if not _isQuoteSessionNow():
        termLog(f"[watchRt] {reason} — 시세구간 외, 다음 구독에 반영")
        return
    ws = getWsClient()
    _unregWatch(ws, reason)
    if ws is not None and getattr(ws, "isLoggedIn", False):
        _regWatch(ws, _monCodes(), reason)
    else:
        termLog(f"[watchRt] {reason} — WS 미로그인, LOGIN 후 구독")


def _prepareBeforeOpen(reason):
    """장 시작 전 SMA·원장 로드 + 시세구간 외 일봉 종가 시드. 0B REG 는 개장 때."""
    codes = _monCodes()
    try:
        from trading.maTouchLive import prepareMaTouchSession

        prepareMaTouchSession(codes)
    except Exception as err:
        termLog(f"[watchRt] 장시작전 준비 실패: {err}")
        return
    try:
        _seedDayCloseQuotes(codes)
    except Exception as err:
        termLog(f"[watchRt] 일봉종가 시드 실패: {err}")
    _writeState(False, codes, reason)
    termLog(f"[watchRt] {reason} — 원장/관심 {len(codes)}종 준비 (REG는 개장 시)")


def _regWatch(ws, codes, reason):
    global _monActive, _regCodes, _regItems, _nxtCds
    if not codes:
        termLog(f"[watchRt] 구독 종목 없음 ({reason})")
        _writeState(False, [], reason)
        return
    itemMap = _buildRegMap(codes)
    cds = list(itemMap.keys())
    allItems = _flatItems(itemMap, cds)
    for i in range(0, len(allItems), _CHUNK):
        chunk = allItems[i : i + _CHUNK]
        ws.sendReg(["0B"], chunk, grpNo=_GRP_NO, refresh="1")
    nxtSet = {cd for cd, its in itemMap.items() if any(str(x).upper().endswith("_NX") for x in its)}
    with _lock:
        _monActive = True
        _regCodes = list(cds)
        _regItems = dict(itemMap)
        _nxtCds = nxtSet
    _writeState(True, cds, reason)
    nxtCnt = len(nxtSet)
    termLog(
        f"[watchRt] 0B 구독 {len(cds)}종 · item {len(allItems)} "
        f"(NXT시세 {nxtCnt}) ({reason}) grp={_GRP_NO}"
    )
    # 이평선터치 모의 — 구독 종목의 전일 SMA를 장 시작에 고정
    try:
        from trading.maTouchLive import prepareMaTouchSession

        prepareMaTouchSession(codes)
    except Exception as err:
        termLog(f"[watchRt] maTouch 준비 실패: {err}")


def addWatchRtCodes(codes, reason="장중편입"):
    """
    이평터치 15분 순위에서 새로 들어온 종목만 0B 추가 등록.
    웹 수동 추가와 달리 모의 장중 편입은 당일 매매를 위해 즉시 REG 한다.
    """
    global _monActive
    cds = []
    seen = set()
    with _lock:
        have = set(_regCodes)
        usedItems = sum(len(_regItems.get(cd) or [cd]) for cd in _regCodes)
    for raw in codes or []:
        cd = normStkCd(raw)
        if not cd or cd in seen or cd in have:
            continue
        seen.add(cd)
        cds.append(cd)
    if not cds:
        return 0
    leftStk = _MAX_STK - len(have)
    if leftStk <= 0 or usedItems >= _MAX_ITEM:
        termLog(f"[watchRt] 0B 한도 종목{_MAX_STK}/item{_MAX_ITEM} — 편입 생략")
        return 0
    keep = []
    for cd in cds[:leftStk]:
        its = _rtItemsOf(cd)
        if usedItems + len(its) > _MAX_ITEM:
            break
        keep.append(cd)
        usedItems += len(its)
    cds = keep
    if not cds:
        termLog(f"[watchRt] 0B item 한도 {_MAX_ITEM} — 편입 생략")
        return 0
    ws = getWsClient()
    if ws is None or not getattr(ws, "isLoggedIn", False):
        termLog(f"[watchRt] WS 미로그인 — 편입 REG 보류 {len(cds)}종")
        return 0
    for i in range(0, len(cds), _CHUNK):
        chunk = cds[i : i + _CHUNK]
        items = _flatItems({cd: _rtItemsOf(cd) for cd in chunk}, chunk)
        ws.sendReg(["0B"], items, grpNo=_GRP_NO, refresh="1")
    with _lock:
        _regCodes.extend(cds)
        for cd in cds:
            its = _rtItemsOf(cd)
            _regItems[cd] = its
            if any(str(x).upper().endswith("_NX") for x in its):
                _nxtCds.add(cd)
        _monActive = True
        allCds = list(_regCodes)
    _writeState(True, allCds, reason)
    try:
        from trading.maTouchLive import prepareMaTouchSession

        prepareMaTouchSession(allCds)
    except Exception as err:
        termLog(f"[watchRt] 편입 SMA 준비 실패: {err}")
    termLog(f"[watchRt] 0B 편입 {len(cds)}종 ({reason}) grp={_GRP_NO}")
    return len(cds)


def unregWatchStk(stkCd, reason=""):
    """전량 청산 등 — 한 종목만 0B 해지하고 구독 목록에서 뺀다."""
    global _monActive
    cd = normStkCd(stkCd)
    if not cd:
        return
    with _lock:
        if cd not in _regCodes:
            return
        item = _regItems.pop(cd, [cd])
        _nxtCds.discard(cd)
        left = [x for x in _regCodes if x != cd]
        _regCodes[:] = left
        _lastPx.pop(cd, None)
        codes = list(_regCodes)
        active = bool(codes)
        _monActive = active
    ws = getWsClient()
    dropItems = item if isinstance(item, list) else [item]
    if ws is not None:
        try:
            ws.sendRemove(["0B"], dropItems, grpNo=_GRP_NO)
        except Exception as err:
            termLog(f"[watchRt] 단건 해지 실패 {cd}: {err}")
    _writeState(active, codes, reason or f"단건해지 {cd}")
    termLog(f"[watchRt] 0B 해지 1종 {cd} ({reason})")


def _unregWatch(ws, reason):
    global _monActive, _regCodes, _regItems, _nxtCds
    with _lock:
        codes = list(_regCodes)
        items = _flatItems(_regItems, codes)
        _monActive = False
        _regCodes = []
        _regItems = {}
        _nxtCds = set()
    if ws is not None and items:
        for i in range(0, len(items), _CHUNK):
            chunk = items[i : i + _CHUNK]
            try:
                ws.sendRemove(["0B"], chunk, grpNo=_GRP_NO)
            except Exception:
                pass
    _writeState(False, [], reason)
    # 시세 종료 시 마지막 0B 를 디스크에 남긴다 (재시작·장외 평가용)
    try:
        _flushLastPxCache(force=True)
    except Exception as err:
        termLog(f"[watchRt] 시세캐시 강제저장 실패: {err}")
    termLog(f"[watchRt] 0B 해지 ({reason}) {len(codes)}종")


def _onLogin(ws):
    """WS LOGIN 후, 시세 구간이면 0B 재등록 (장전 NXT 포함)."""
    if not _isQuoteSessionNow():
        return
    codes = _monCodes()
    _regWatch(ws, codes, "LOGIN(시세구간)")


def _onWsMessage(msg):
    """0B 현재가 캐시 후 이평선터치 모의 신호로 넘긴다."""
    if not isinstance(msg, dict) or msg.get("trnm") != "REAL":
        return
    for row in msg.get("data") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "") != "0B":
            continue
        stkCd, venue = _splitRtItem(row.get("item") or (row.get("values") or {}).get("9001"))
        if not stkCd:
            continue
        vals = row.get("values") or {}
        fireTick = False
        with _lock:
            if stkCd not in _regCodes:
                continue
            info = dict(_lastPx.get(stkCd) or {})
            # 0B 는 변동 FID 만 올 수 있다. 가격만 온 틱이 등락률을 지우지 않게 한다.
            prcRaw = _nzQuote(vals.get("10"))
            fluRaw = _nzQuote(vals.get("12"))
            tmRaw = _nzQuote(vals.get("20"))
            if venue == "NXT":
                if prcRaw is not None:
                    info["nxtPrc"] = prcRaw
                if fluRaw is not None:
                    info["nxtFlu"] = fluRaw
                if tmRaw is not None:
                    info["nxtTm"] = tmRaw
            else:
                if prcRaw is not None:
                    info["krxPrc"] = prcRaw
                if fluRaw is not None:
                    info["krxFlu"] = fluRaw
                if tmRaw is not None:
                    info["krxTm"] = tmRaw
            tradeNxt = False
            try:
                from config.settingsStore import getDeskTradeExch

                tradeNxt = getDeskTradeExch() == "NXT" and stkCd in _nxtCds
            except Exception:
                tradeNxt = False
            if tradeNxt and info.get("nxtPrc"):
                info["curPrc"] = info.get("nxtPrc")
                info["fluRt"] = _pickFlu(
                    info.get("nxtFlu"), info.get("krxFlu"), info.get("fluRt")
                )
                info["tm"] = info.get("nxtTm")
                info["exch"] = "NXT"
            else:
                info["curPrc"] = info.get("krxPrc") or info.get("nxtPrc")
                info["fluRt"] = _pickFlu(
                    info.get("krxFlu"), info.get("nxtFlu"), info.get("fluRt")
                )
                info["tm"] = info.get("krxTm") or info.get("nxtTm")
                info["exch"] = "KRX"
            info["src"] = "0B"
            info["tickYmd"] = datetime.now().strftime("%Y%m%d")
            info.pop("dayCloseDt", None)
            _lastPx[stkCd] = info
            _dayCloseSeeded.discard(stkCd)
            # 매매 판단은 선택한 거래소 틱만. NXT 시세는 KRX 매매여도 캐시한다.
            fireTick = (tradeNxt and venue == "NXT") or (
                (not tradeNxt) and venue == "KRX"
            )
        _markPxDirty()
        # 디바운스 저장 (락 밖)
        try:
            _flushLastPxCache(force=False)
        except Exception:
            pass
        if not fireTick:
            continue
        # 락 밖에서 이평터치 판단 (주문 대기 시 0B 수신을 막지 않음)
        try:
            from trading.maTouchLive import onMaTouchTick

            onMaTouchTick(
                stkCd, vals.get("10"), vals.get("20"), fluRtRaw=vals.get("12")
            )
        except Exception as err:
            termLog(f"[watchRt] maTouch 틱 오류 {stkCd}: {err}")


def _mktLoop():
    wasOpen = None
    while True:
        try:
            nowOpen = _isQuoteSessionNow()
            if wasOpen is None:
                if nowOpen:
                    ws = getWsClient()
                    if ws is not None and ws.isLoggedIn:
                        _regWatch(ws, _monCodes(), "기동(시세구간)")
                    else:
                        termLog("[watchRt] 기동 시세구간 — WS LOGIN 후 구독")
                else:
                    _prepareBeforeOpen("기동(시세구간 외)")
                    termLog(
                        f"[watchRt] 시세구간 외 — 다음 개장 {nextMktOpenLabel()} 에 0B 구독"
                    )
            elif nowOpen and not wasOpen:
                ws = getWsClient()
                if ws is not None and ws.isLoggedIn:
                    _regWatch(ws, _monCodes(), "시세시작")
                else:
                    termLog("[watchRt] 시세시작 — WS 미연결, LOGIN 훅에서 구독")
            elif (not nowOpen) and wasOpen:
                _unregWatch(getWsClient(), "시세종료")
            wasOpen = nowOpen
        except Exception as err:
            termLog(f"[watchRt] 장시간 감시 오류: {err}")
        try:
            _flushLastPxCache(force=False)
        except Exception:
            pass
        time.sleep(1)


def startWatchRtMonitor():
    """관심종목 0B 감시 루프 기동. main.py 에서 WS 기동 전에 호출."""
    global _loopStarted
    # 기동 직후 일봉 시드보다 먼저 직전 0B 를 복원해 평가손익이 뒤집히지 않게 한다
    _loadLastPxCache()
    registerMsgHandler(_HANDLER_NAME, _onWsMessage)
    registerLoginHandler(_LOGIN_HANDLER_NAME, _onLogin)
    if not _loopStarted:
        _loopStarted = True
        openHm, closeHm = getNotifyHours()
        threading.Thread(
            target=_mktLoop, name="watchRtMkt", daemon=True
        ).start()
        try:
            from config.settingsStore import isDeskMain

            if not isDeskMain():
                termLog("[watchRt] client — 자동매매(이평터치·이평지지) 기동 생략")
            else:
                try:
                    from trading.maTouchLive import startMaTouchLive

                    startMaTouchLive()
                except Exception as err:
                    termLog(f"[watchRt] maTouch 기동 실패: {err}")
                try:
                    from trading.maSupportEodLive import startMaSupportEodLive

                    startMaSupportEodLive()
                except Exception as err:
                    termLog(f"[watchRt] maSupportEod 기동 실패: {err}")
                try:
                    from trading.surgeTradeLive import startSurgeRankCollector

                    startSurgeRankCollector()
                except Exception as err:
                    termLog(f"[watchRt] 급등주 조회 수집 실패: {err}")
                try:
                    from trading.surgeTradeLive import startSurgeTradeLive

                    startSurgeTradeLive()
                except Exception as err:
                    termLog(f"[watchRt] SRG01 기동 실패: {err}")
        except Exception as err:
            termLog(f"[watchRt] deskRole 확인 실패 — 전략 기동 스킵: {err}")
        termLog(
            f"[watchRt] 감시 루프 시작 — 거래일 시세 {openHm}~{closeHm} "
            f"(NXT 장전·애프터 포함) USE_TRADE=Y · 다음 개장 {nextMktOpenLabel()}"
        )
