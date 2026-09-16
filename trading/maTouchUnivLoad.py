"""
이평선터치(MTS01) 매매대상 준비 — 거래일 16:00 실시간조회순위(ka00198 당일누적).

**조회순위 상위 20종 ∪ 거래대금(ka10032) 상위 30종** (중복 제거),
**코스피/코스닥만** 다음 거래일 관리종목(AI_ALG_STK)·관심 매매ON(AI_WATCH_STK USE_TRADE=Y)
으로 등록한다.

같은 16:00 배치에서 이평지지(MSE01) 관심은 **ka10016 52주 신고가**
(코스피 001 + 코스닥 101, 고저기준 dt=250)로 **편입**한다.
이미 있는 종목은 **현재가 > SMA20** 이면 유지하고,
**이틀 연속 음봉(종가<시가)이면서 종가 ≤ SMA20** 이면 뺀다.
하루만 SMA20 아래이거나 양봉이면 관심에 남긴다. 원장 미청산은 예외로 남긴다.

설계 이유
- 장중 15분 순위 편입을 없애고 하루를 전날 16시에 확정된 목록으로 시작한다.
  (장중에 대상이 늘어나면 진입 시점·검증 기준이 매번 달라져 복기가 어렵다)
- **16시 배치 직전** 기존 관심(USE_TRADE=Y)을 SMA20 기준으로 정리한다.
  현재가 > 전일까지 확정 SMA20 이면 다음 장까지 유지.
  이탈은 **이틀 연속 음봉 + 종가≤SMA20** 일 때만 관심·관리에서 제외한다
  (하루 이탈·양봉 이탈은 유예).
- 신규 편입(순위∪대금)도 **현재가 > SMA20** 일 때만 등록한다.
- 원장 미청산 종목은 SMA20·교체와 무관하게 유지한다.

실시간(0B) 구독은 여기서 하지 않는다 — 다음 장 시작 시 watchRtMonitor 가
USE_TRADE=Y 를 읽어 등록한다.
"""

import threading
import time
from datetime import datetime

from api.getAcntEvlt import parsePadNum
from api.getDayStkPrc import INDC_TP_QTY
from api.getInqRank import QRY_TP_DAY, fetchInqRank
from api.getNtlPric import DT_250, HL_HIGH_LOW, NTL_HIGH, fetchNtlPric
from api.getTrdePrcaUpper import (
    MRKT_ALL,
    MRKT_KOSDAQ,
    MRKT_KOSPI,
    fetchTrdePrcaUpper,
)
from config.krHoliday import isTradingDay
from config.opsSettings import isBatchSchedEnabled
from telegram.telSend import sendTelegramMsg
from utils.algStkStore import (
    deactivateAlgStkExcept,
    delAlgManaged,
    listAlgStk,
    upsertAlgStkRows,
)
from utils.dayInqRankLoad import rowToRecord
from utils.dayInqRankStore import upsertDayInqRankRows
from utils.dayPrcEnsureSma import ensureDayPrcForSma
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dbConn import openDbConn
from utils.rankToWatchLoad import WATCH_GRP_RANK, _isKospiKosdaq
from utils.stkCdUtil import normStkCd
from utils.stratOrdLedgerStore import listOpenLedgerStkCds
from utils.termLog import termLog
from utils.watchStkStore import (
    deactivateWatchByGrp,
    deactivateWatchStk,
    listTradeWatch,
    upsertWatchStkRows,
)

_JOB_ID = "maTouchUniv"
_ALG_CD = "MTS01"
_MSE01_ALG = "MSE01"
_STRAT_ID = "maTouchScale_v1"
_SRC = "rank16h"

# 16:00 부터 시도. 장마감 일봉 적재(15:30~18:00)가 끝난 뒤라 종가가 DB에 있다.
_RUN_START_HM = "16:00"
_RUN_DEADLINE_HM = "18:30"

# 매매 시점에 쓰는 이평선 — 등록 조건이 아니고 참고 표기용
_SMA_SLOW = 20
# 다음날 매매에서 SMA 를 바로 쓸 수 있게 확보할 일봉 수 (SMA20 + 휴장 여유)
_MIN_BARS = 25
# 실시간조회순위(ka00198) 상위 N종 (코스피/코스닥)
_RANK_MAX = 20
# 거래대금(ka10032 전체) 상위 N종 중 코스피/코스닥
_TRDE_PRCA_MAX = 30
# 합집합 상한 (20+30, 중복·필터 후)
_MAX_UNIV = 50
# MSE01 52주 신고가 — ka10016 dt=250 (고저기준). DB 일봉 판정은 검증 스크립트용.
_LOOKBACK_52W = 250
# ka10016 코스피+코스닥 합 상한 (AI_ALG_STK 조회·스캔 여유)
_MSE01_MAX_STK = 500
# 매차 금액으로 이 수량을 못 채우면 제외.
# 실매매는 계산수량 0이면 1주로 올려 사기 때문에, 고가주는 매차 금액을 넘겨
# 종목당 투입금 통제가 깨진다. 대상 단계에서 걸러 낸다.
_MIN_QTY_PER_STEP = 1
# 모의 TR 1초 1회. ka00198 다음 ka10032 를 이 간격으로 둔다.
_PAPER_WAIT_SEC = 1.05

_schedStarted = False
_doneYmd = ""
_lastAttemptKey = ""
_running = False


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="batch")
    except Exception as err:
        termLog(f"[maTouchUniv] 텔레그램 전송 실패: {err}")


def _stratEnabled():
    """전략 자체가 OFF 면 대상도 만들지 않는다."""
    try:
        from config.settingsStore import getMaTouchLive

        return bool((getMaTouchLive() or {}).get("enabled", True))
    except Exception:
        return True


def _mse01LiveEnabled():
    """이평지지(MSE01) stratLive.enabled. 없으면 ON."""
    try:
        from config.settingsStore import loadSettings

        live = (loadSettings() or {}).get("stratLive") or {}
        row = live.get(_MSE01_ALG) if isinstance(live, dict) else {}
        if isinstance(row, dict) and "enabled" in row:
            return bool(row.get("enabled"))
    except Exception:
        pass
    return True


def _pruneMse01ToOpenOnly(ymd):
    """
    MSE01 사용중지 시: 원장 미청산만 남기고 관리종목·brokeOut 정리.
    16:00 ka10016 편입을 막기 위해 호출한다.
    """
    mseOpen = sorted(_mse01OpenCds())
    dropGot = deactivateAlgStkExcept(_MSE01_ALG, mseOpen)
    dropCnt = int(dropGot.get("deactCnt") or 0)
    if not dropGot.get("ok"):
        termLog(
            f"[maTouchUniv] MSE01 원장유지 해제 실패: {dropGot.get('errMsg')}"
        )
    try:
        from trading.maSupportEodLive import pruneMse01BrokeOut

        pruneMse01BrokeOut(mseOpen)
    except Exception as err:
        termLog(f"[maTouchUniv] MSE01 brokeOut 정리 실패: {err}")
    # 원장만 다시 활성 표기(없으면 UPSERT)
    algRows = []
    noteOpen = f"원장미청산 {ymd}"
    for cd in mseOpen:
        algRows.append(
            {
                "algCd": _MSE01_ALG,
                "stkCd": cd,
                "stkNm": "",
                "src": "ledger",
                "note": noteOpen,
            }
        )
    saved = {"upsertCnt": 0}
    if algRows:
        try:
            saved = upsertAlgStkRows(algRows) or saved
        except Exception as err:
            termLog(f"[maTouchUniv] MSE01 원장유지 UPSERT 실패: {err}")
    termLog(
        f"[maTouchUniv] MSE01 사용중지 — 원장 {len(mseOpen)}종만 유지 "
        f"(해제 {dropCnt})"
    )
    return {
        "ok": True,
        "mseSkip": False,
        "msePaused": True,
        "mseNewCnt": 0,
        "mseKeepCnt": len(mseOpen),
        "mseDropCnt": dropCnt,
        "mseSmaKeepCnt": 0,
        "mseUnionCnt": 0,
        "mseAlgCnt": int(saved.get("upsertCnt") or 0),
        "mseOpenCnt": len(mseOpen),
        "mseCds": mseOpen,
        "kospiCnt": 0,
        "kosdaqCnt": 0,
        "skipMkt": 0,
    }


def _absPrc(raw):
    """순위 응답의 부호·콤마 붙은 가격 → 절대값 정수."""
    num = parsePadNum(raw)
    if num is None:
        try:
            num = int(str(raw).replace(",", "").replace("+", "").replace("-", ""))
        except (TypeError, ValueError):
            return None
    return abs(int(num)) if num is not None else None


def _minStepAmt():
    """매차 금액 중 가장 작은 금액 (설정 → 스펙 순서로 반영된 값)."""
    try:
        from config.settingsStore import getMaTouchLive

        live = getMaTouchLive() or {}
        amts = [
            int(live.get("buyAmt5") or 0),
            int(live.get("buyAmt10") or 0),
            int(live.get("buyAmt15") or 0),
        ]
        got = [a for a in amts if a > 0]
        if got:
            return min(got)
    except Exception as err:
        termLog(f"[maTouchUniv] 매차 금액 조회 실패: {err}")
    return 3_000_000


def _waitTrGap():
    """모의 조회 TR 간격."""
    time.sleep(_PAPER_WAIT_SEC)


def _fetchKa10016Mse01(hostUrl, token, tokenType, isPaper=True):
    """
    ka10016 52주 신고가 — 코스피(001) + 코스닥(101).
    고저기준(dt=250, ntl_tp=1). 최초진입 필터는 쓰지 않는다.
    ETF·코넥스는 AI_STK_INFO 로 한 번 더 걸러 낸다.
    """
    items = []
    seen = set()
    kospiCnt = 0
    kosdaqCnt = 0
    for mrktTp, label in ((MRKT_KOSPI, "코스피"), (MRKT_KOSDAQ, "코스닥")):
        _waitTrGap()
        got = fetchNtlPric(
            hostUrl,
            token,
            mrktTp=mrktTp,
            ntlTp=NTL_HIGH,
            highLowCloseTp=HL_HIGH_LOW,
            stkCnd="0",
            trdeQtyTp="00000",
            crdCnd="0",
            updownIncls="0",
            dt=DT_250,
            stexTp="1",
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not got.get("ok"):
            return {
                "ok": False,
                "errMsg": f"ka10016 {label} 실패: {got.get('errMsg')}",
            }
        rows = got.get("rows") or []
        termLog(f"[maTouchUniv] ka10016 {label} 52주신고 {len(rows)}종")
        for rec in rows:
            cd = normStkCd(rec.get("stkCd"))
            if not cd or cd in seen:
                continue
            seen.add(cd)
            items.append(
                {
                    "stkCd": cd,
                    "stkNm": rec.get("stkNm") or cd,
                    "curPrc": rec.get("curPrc"),
                    "mrktTp": mrktTp,
                    "srcTag": "ka10016",
                }
            )
            if mrktTp == MRKT_KOSPI:
                kospiCnt += 1
            else:
                kosdaqCnt += 1

    mktMap = _stkMktMap([it["stkCd"] for it in items])
    filtered = []
    skipMkt = 0
    for it in items:
        meta = mktMap.get(it["stkCd"]) or {}
        # 마스터에 시장이 있으면 ETF·코넥스만 제외. 없으면 ka10016 001/101 을 신뢰
        if meta.get("mrktTp") or meta.get("marketName"):
            if not _isKospiKosdaq(meta.get("mrktTp"), meta.get("marketName")):
                skipMkt += 1
                continue
            if meta.get("stkNm"):
                it["stkNm"] = meta.get("stkNm")
            it["mrktTp"] = meta.get("mrktTp") or it.get("mrktTp")
            it["marketName"] = meta.get("marketName")
        filtered.append(it)

    if len(filtered) > _MSE01_MAX_STK:
        termLog(
            f"[maTouchUniv] ka10016 코스피/코스닥 {len(filtered)}종 → "
            f"상한 {_MSE01_MAX_STK}종으로 자름"
        )
        filtered = sorted(filtered, key=lambda x: x["stkCd"])[:_MSE01_MAX_STK]

    termLog(
        f"[maTouchUniv] ka10016 52주신고 합 {kospiCnt}+{kosdaqCnt}종 → "
        f"코스피/코스닥 {len(filtered)}종 (시장제외 {skipMkt})"
    )
    return {
        "ok": True,
        "items": filtered,
        "kospiCnt": kospiCnt,
        "kosdaqCnt": kosdaqCnt,
        "skipMkt": skipMkt,
    }


def _tryReplaceMse01FromKa10016(ymd, sess):
    """세션이 있으면 ka10016 조회 후 MSE01 전량 교체. 실패 시 기존 목록 유지."""
    # 사용중지: 신규 편입 없이 원장 미청산만 유지
    if not _mse01LiveEnabled():
        return _pruneMse01ToOpenOnly(ymd)
    if not sess:
        return {"ok": False, "mseSkip": True, "errMsg": "키움 토큰 없음"}
    mode, hostUrl, token, tokenType = sess
    ntlGot = _fetchKa10016Mse01(
        hostUrl, token, tokenType, isPaper=(str(mode) == "paper")
    )
    if not ntlGot.get("ok"):
        termLog(f"[maTouchUniv] MSE01 ka10016 생략 — {ntlGot.get('errMsg')}")
        return {
            "ok": False,
            "mseSkip": True,
            "errMsg": ntlGot.get("errMsg"),
            "mseNewCnt": 0,
            "mseKeepCnt": 0,
            "mseDropCnt": 0,
            "mseUnionCnt": 0,
        }
    mseGot = _replaceMse01Managed(ntlGot.get("items") or [], ymd)
    mseGot["ok"] = True
    mseGot["mseSkip"] = False
    mseGot["kospiCnt"] = int(ntlGot.get("kospiCnt") or 0)
    mseGot["kosdaqCnt"] = int(ntlGot.get("kosdaqCnt") or 0)
    mseGot["skipMkt"] = int(ntlGot.get("skipMkt") or 0)
    _loadMse01DayPrcBg(mseGot.get("mseCds") or [], ymd)
    return mseGot


def loadMse01UnivFromKa10016(tradeDt=None):
    """MSE01 관심만 ka10016 으로 즉시 교체 (MTS01 순위 배치와 분리)."""
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    if not _mse01LiveEnabled():
        return _pruneMse01ToOpenOnly(ymd)
    sess = acquireKiwoomSession()
    if not sess:
        return {"ok": False, "errMsg": "키움 토큰 없음 — ka10016 생략"}
    return _tryReplaceMse01FromKa10016(ymd, sess)


def _loadMse01DayPrcBg(stkCds, ymd):
    """관심 편입 종목 1년 일봉 — 순위 TR 을 막지 않게 백그라운드."""
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return

    def _work():
        try:
            from utils.rankDayPrcLoad import loadDayPrcForRankStks

            got = loadDayPrcForRankStks(
                codes, months=12, endDt=ymd, skipIfEnough=True
            )
            termLog(
                f"[maTouchUniv] MSE01 일봉 적재 {got.get('loadedCnt', 0)}종 · "
                f"스킵 {got.get('skipCnt', 0)} · 실패 {got.get('failCnt', 0)}"
            )
        except Exception as err:
            termLog(f"[maTouchUniv] MSE01 일봉 적재 실패: {err}")

    threading.Thread(target=_work, name="mse01DayPrc", daemon=True).start()


def _fetchTrdePrcaTopCds(hostUrl, token, tokenType):
    """
    ka10032 전체(000) 순위 → 코스피/코스닥만 상위 N종.
    전체 목록에 ETF 등이 끼면 순위를 더 읽어 채운다.
    """
    _waitTrGap()
    # ETF·외국주 등을 건너뛰며 N종을 채우기 위해 여유 순위를 읽는다
    fetchCap = max(_TRDE_PRCA_MAX * 3, 80)
    got = fetchTrdePrcaUpper(
        hostUrl,
        token,
        mrktTp=MRKT_ALL,
        maxRank=fetchCap,
        tokenType=tokenType,
    )
    if not got.get("ok"):
        # 전체 실패 시 시장별 백업 (코스피·코스닥 각 N/2 근사)
        return _fetchTrdePrcaTopCdsByMkt(hostUrl, token, tokenType)

    mktMap = _stkMktMap([normStkCd(r.get("stk_cd")) for r in (got.get("rows") or [])])
    cds = []
    seen = set()
    for row in got.get("rows") or []:
        cd = normStkCd((row or {}).get("stk_cd"))
        if not cd or cd in seen:
            continue
        meta = mktMap.get(cd) or {}
        if not _isKospiKosdaq(meta.get("mrktTp"), meta.get("marketName")):
            continue
        seen.add(cd)
        cds.append(cd)
        if len(cds) >= _TRDE_PRCA_MAX:
            break
    termLog(
        f"[maTouchUniv] 거래대금상위 전체→코스피/코스닥 "
        f"{len(cds)}종 (목표 ≤{_TRDE_PRCA_MAX})"
    )
    if len(cds) < max(5, _TRDE_PRCA_MAX // 2):
        # 너무 적으면 시장별 합으로 보강
        fb = _fetchTrdePrcaTopCdsByMkt(hostUrl, token, tokenType)
        if fb.get("ok"):
            for c in fb.get("cds") or []:
                if c not in seen:
                    seen.add(c)
                    cds.append(c)
                if len(cds) >= _TRDE_PRCA_MAX:
                    break
    return {"ok": True, "cds": set(cds[:_TRDE_PRCA_MAX])}


def _fetchTrdePrcaTopCdsByMkt(hostUrl, token, tokenType):
    """백업: 코스피·코스닥 각각 상위 N위 합집합 후 N종만."""
    cds = []
    seen = set()
    per = max(1, (_TRDE_PRCA_MAX + 1) // 2)
    for mrktTp, label in ((MRKT_KOSPI, "코스피"), (MRKT_KOSDAQ, "코스닥")):
        _waitTrGap()
        got = fetchTrdePrcaUpper(
            hostUrl,
            token,
            mrktTp=mrktTp,
            maxRank=per,
            tokenType=tokenType,
        )
        if not got.get("ok"):
            return {
                "ok": False,
                "errMsg": f"ka10032 {label} 실패: {got.get('errMsg')}",
            }
        chunk = list(got.get("cds") or [])
        for cd in chunk:
            c = normStkCd(cd)
            if not c or c in seen:
                continue
            seen.add(c)
            cds.append(c)
            if len(cds) >= _TRDE_PRCA_MAX:
                break
        termLog(
            f"[maTouchUniv] 거래대금상위 {label} "
            f"{len(chunk)}종 (≤{per}위)"
        )
        if len(cds) >= _TRDE_PRCA_MAX:
            break
    return {"ok": True, "cds": set(cds[:_TRDE_PRCA_MAX])}


def _stkMktMap(stkCds):
    """종목코드 → {mrktTp, marketName, stkNm}."""
    cds = [normStkCd(c) for c in (stkCds or []) if normStkCd(c)]
    if not cds:
        return {}
    out = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 청크 조회
            for i in range(0, len(cds), 200):
                chunk = cds[i : i + 200]
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"""
                    SELECT STK_CD, STK_NM, MRKT_TP, MARKET_NAME
                    FROM AI_STK_INFO
                    WHERE STK_CD IN ({ph})
                    """,
                    chunk,
                )
                for r in cur.fetchall() or []:
                    cd = normStkCd(r.get("STK_CD"))
                    if not cd:
                        continue
                    out[cd] = {
                        "stkNm": (r.get("STK_NM") or cd).strip(),
                        "mrktTp": (str(r.get("MRKT_TP") or "").strip() or None),
                        "marketName": (str(r.get("MARKET_NAME") or "").strip() or None),
                    }
    except Exception as err:
        termLog(f"[maTouchUniv] 종목마스터 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _makeUnivItem(cd, ymd, stepAmt, rankNo=None, stkNm="", mrktTp=None, marketName=None, curPrc=None, indGrp=None, srcTag=""):
    """선정 후보 1건 + 수량/SMA20 참고값."""
    info = _lastBarInfo(cd, ymd, rankCurPx=curPrc)
    item = {
        "stkCd": cd,
        "stkNm": (stkNm or cd).strip(),
        "rankNo": rankNo,
        "indGrp": (str(indGrp or "").strip() or None),
        "mrktTp": (str(mrktTp or "").strip() or None),
        "marketName": (str(marketName or "").strip() or None),
        "curPrc": curPrc if curPrc is not None else None,
        "baseDt": info.get("baseDt"),
        "openPrc": info.get("openPrc"),
        "closePrc": info.get("closePrc"),
        "sma20": info.get("sma20"),
        "aboveSma": bool(info.get("aboveSma")),
        "barCnt": info.get("barCnt") or 0,
        "srcTag": srcTag or "",
        "reason": "",
    }
    basePx = item.get("closePrc") or item.get("curPrc")
    stepQty = int(stepAmt // int(basePx)) if basePx else 0
    item["stepQty"] = stepQty
    return item


def _copyUnionItem(item):
    """과거 검증 합집합용 얕은 복사. 실전 MSE01 은 ka10016."""
    return {
        "stkCd": item.get("stkCd"),
        "stkNm": item.get("stkNm"),
        "rankNo": item.get("rankNo"),
        "indGrp": item.get("indGrp"),
        "mrktTp": item.get("mrktTp"),
        "marketName": item.get("marketName"),
        "curPrc": item.get("curPrc"),
        "closePrc": item.get("closePrc"),
        "srcTag": item.get("srcTag") or "",
    }


def _tagUnionTrde(unionByCd, cd):
    """합집합에 이미 있으면 거래대금 출처만 덧붙인다."""
    prev = unionByCd.get(cd)
    if not prev:
        return
    src = prev.get("srcTag") or ""
    if "trde" not in src:
        prev["srcTag"] = (src + "+trde").strip("+")


def screenUnivFromDb(ymd, maxCnt=_MAX_UNIV, trdePrcaCds=None, rankMax=_RANK_MAX):
    """
    DB 스냅샷 + (선택) 거래대금 코드로 대상 선별.
    조건: 코스피/코스닥 · 조회순위 상위 rankMax ∪ 거래대금 상위 집합 · 매차 최소수량
    · **현재가 > 전일까지 SMA20** (이평터치 MTS01).
    trdePrcaCds 가 None 이면 조회순위만 사용 (과거 BT·미리보기).
    반환: {ok, tradeDt, rankCnt, picks, skips, unionItems, ...}
    unionItems: 과거 검증용 순위20∪대금30 (실전 MSE01 은 ka10016 전량 교체).
    """
    conn = None
    rows = []
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.STK_CD, r.RANK_NO, r.STK_NM, r.IND_GRP, r.CUR_PRC,
                       i.MRKT_TP, i.MARKET_NAME
                FROM AI_DAY_INQ_RANK r
                LEFT JOIN AI_STK_INFO i ON i.STK_CD = r.STK_CD
                WHERE r.TRADE_DT=%s AND r.QRY_TP=%s
                ORDER BY r.RANK_NO ASC
                """,
                (str(ymd)[:8], QRY_TP_DAY),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        return {"ok": False, "errMsg": f"순위 스냅샷 조회 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not rows:
        return {"ok": False, "errMsg": f"순위 스냅샷 없음: {ymd}"}

    stepAmt = _minStepAmt()
    rankCap = max(1, int(rankMax or _RANK_MAX))
    univCap = max(1, int(maxCnt or _MAX_UNIV))
    byCd = {}
    skips = []
    rankPickCnt = 0
    # MSE01 원천: 수량·SMA20 없이 순위20 ∪ 대금30, 코스피/코스닥, 최대 50
    unionByCd = {}
    unionItems = []
    rankUnionCnt = 0

    # 1) 실시간조회순위 → 코스피/코스닥 상위 rankCap
    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if not cd:
            continue
        marketName = str(r.get("MARKET_NAME") or "").strip()
        mrktTp = str(r.get("MRKT_TP") or "").strip() or None
        item = _makeUnivItem(
            cd,
            ymd,
            stepAmt,
            rankNo=r.get("RANK_NO"),
            stkNm=(r.get("STK_NM") or cd).strip(),
            mrktTp=mrktTp,
            marketName=marketName or None,
            curPrc=_absPrc(r.get("CUR_PRC")),
            indGrp=r.get("IND_GRP"),
            srcTag="rank",
        )
        if not _isKospiKosdaq(item.get("mrktTp"), marketName):
            item["reason"] = "notMkt"
            skips.append(item)
            continue
        # 이평지지 합집합 — 매차수량·SMA20 과 무관하게 순위 상위 20
        if rankUnionCnt < rankCap and cd not in unionByCd:
            unionByCd[cd] = _copyUnionItem(item)
            unionItems.append(unionByCd[cd])
            rankUnionCnt += 1
        if item.get("stepQty", 0) < _MIN_QTY_PER_STEP:
            item["reason"] = "qtyShort"
            skips.append(item)
            continue
        if not item.get("aboveSma"):
            item["reason"] = "belowSma20"
            skips.append(item)
            continue
        if rankPickCnt >= rankCap:
            item["reason"] = "rankOver"
            skips.append(item)
            continue
        byCd[cd] = item
        rankPickCnt += 1

    # 2) 거래대금 상위 ∪ (중복은 순위쪽 유지)
    trdePickCnt = 0
    trdeSet = set()
    if trdePrcaCds is not None:
        trdeList = [normStkCd(c) for c in trdePrcaCds if normStkCd(c)]
        needMeta = [c for c in trdeList if c not in byCd]
        mktMap = _stkMktMap(needMeta)
        # 순위 스냅샷에 있는 이름·시장도 보강
        rankByCd = {normStkCd(r.get("STK_CD")): r for r in rows}
        for cd in trdeList:
            if not cd:
                continue
            trdeSet.add(cd)
            if cd in byCd:
                # 이미 순위 편입 — 출처만 합침
                prev = byCd[cd].get("srcTag") or ""
                if "trde" not in prev:
                    byCd[cd]["srcTag"] = (prev + "+trde").strip("+")
                if cd in unionByCd:
                    _tagUnionTrde(unionByCd, cd)
                elif len(unionItems) < univCap:
                    # MTS01 순위 편입(필터 후 20)이 원천 순위20 밖이어도
                    # 대금 상위면 이평지지 합집합에 넣는다
                    u = _copyUnionItem(byCd[cd])
                    u["srcTag"] = "trde"
                    unionByCd[cd] = u
                    unionItems.append(u)
                continue
            rr = rankByCd.get(cd) or {}
            meta = mktMap.get(cd) or {}
            marketName = (
                str(rr.get("MARKET_NAME") or meta.get("marketName") or "").strip()
            )
            mrktTp = (
                str(rr.get("MRKT_TP") or meta.get("mrktTp") or "").strip() or None
            )
            stkNm = (
                (rr.get("STK_NM") or meta.get("stkNm") or cd)
            ).strip()
            item = _makeUnivItem(
                cd,
                ymd,
                stepAmt,
                rankNo=rr.get("RANK_NO"),
                stkNm=stkNm,
                mrktTp=mrktTp,
                marketName=marketName or None,
                curPrc=_absPrc(rr.get("CUR_PRC")),
                indGrp=rr.get("IND_GRP"),
                srcTag="trde",
            )
            if not _isKospiKosdaq(item.get("mrktTp"), marketName):
                item["reason"] = "notMkt"
                skips.append(item)
                continue
            # 이평지지 합집합 — 수량·SMA20 과 무관하게 대금 상위 추가
            if cd in unionByCd:
                _tagUnionTrde(unionByCd, cd)
            elif len(unionItems) < univCap:
                unionByCd[cd] = _copyUnionItem(item)
                unionItems.append(unionByCd[cd])
            if item.get("stepQty", 0) < _MIN_QTY_PER_STEP:
                item["reason"] = "qtyShort"
                skips.append(item)
                continue
            if not item.get("aboveSma"):
                item["reason"] = "belowSma20"
                skips.append(item)
                continue
            byCd[cd] = item
            trdePickCnt += 1

    # 순위 편입 순 → 거래대금만 추가된 순
    picks = []
    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if cd and cd in byCd and byCd[cd].get("srcTag", "").startswith("rank"):
            if byCd[cd] not in picks:
                picks.append(byCd[cd])
    for cd, item in byCd.items():
        if item not in picks:
            picks.append(item)
    if len(picks) > univCap:
        picks = picks[:univCap]

    return {
        "ok": True,
        "tradeDt": str(ymd)[:8],
        "rankCnt": len(rows),
        "stepAmt": stepAmt,
        "picks": picks,
        "skips": skips,
        "rankPickCnt": rankPickCnt,
        "trdePickCnt": trdePickCnt,
        "trdeInCnt": len(trdeSet),
        "unionItems": unionItems,
        "rankUnionCnt": rankUnionCnt,
        "unionCnt": len(unionItems),
    }


def _dayBarsAsOf(stkCd, ymd, limit=_MIN_BARS + 5):
    """기준일 포함 이전 일봉 (오래된→최신). [{dt, openPrc, closePrc}, ...]"""
    code = normStkCd(stkCd)
    if not code:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, OPEN_PRC, CLOSE_PRC
                FROM (
                  SELECT TRADE_DT, OPEN_PRC, CLOSE_PRC
                  FROM AI_DAY_STK_PRC
                  WHERE STK_CD=%s AND TRADE_DT<=%s
                    AND INDC_TP=%s AND CLOSE_PRC IS NOT NULL
                  ORDER BY TRADE_DT DESC
                  LIMIT %s
                ) t
                ORDER BY TRADE_DT ASC
                """,
                (code, str(ymd)[:8], INDC_TP_QTY, int(limit)),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[maTouchUniv] 일봉 조회 실패 {code}: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    bars = []
    for r in rows:
        close = r.get("CLOSE_PRC")
        if close is None:
            continue
        bars.append(
            {
                "dt": str(r.get("TRADE_DT") or "")[:8],
                "openPrc": abs(float(r["OPEN_PRC"])) if r.get("OPEN_PRC") else None,
                "closePrc": abs(float(close)),
            }
        )
    return bars


def _sma20Prior(stkCd, ymd):
    """전일까지 확정 종가 기준 SMA20 — live maTouch 와 동일."""
    code = normStkCd(stkCd)
    ref = str(ymd)[:8]
    if not code:
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CLOSE_PRC
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP=%s AND TRADE_DT < %s
                  AND CLOSE_PRC IS NOT NULL
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (code, INDC_TP_QTY, ref, _SMA_SLOW),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[maTouchUniv] SMA20 조회 실패 {code}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    closes = [abs(float(r["CLOSE_PRC"])) for r in reversed(rows)]
    if len(closes) < _SMA_SLOW:
        return None
    return sum(closes[-_SMA_SLOW:]) / float(_SMA_SLOW)


def _resolveCurPx(stkCd, ymd, rankCurPx=None):
    """SMA20 비교용 현재가 — 순위 16시 현재가 우선, 없으면 당일·최근 종가."""
    px = _absPrc(rankCurPx)
    if px and px > 0:
        return int(px)
    bars = _dayBarsAsOf(stkCd, ymd, limit=3)
    if not bars:
        return None
    ref = str(ymd)[:8]
    for bar in reversed(bars):
        if str(bar.get("dt") or "")[:8] == ref:
            return int(bar["closePrc"])
    return int(bars[-1]["closePrc"])


def _isAboveSma20(stkCd, ymd, rankCurPx=None):
    """현재가 > 전일까지 SMA20. 반환: (above, sma20, curPx)."""
    sma20 = _sma20Prior(stkCd, ymd)
    curPx = _resolveCurPx(stkCd, ymd, rankCurPx=rankCurPx)
    if sma20 is None or curPx is None:
        return False, sma20, curPx
    return curPx > float(sma20), sma20, curPx


def _lastBarInfo(stkCd, ymd, rankCurPx=None):
    """
    기준일 OHLC·SMA20(전일까지) · 현재가>SMA20 여부.
    반환: {baseDt, openPrc, closePrc, sma20, barCnt, aboveSma, curPx}
    """
    bars = _dayBarsAsOf(stkCd, ymd)
    if not bars:
        return {"barCnt": 0}

    last = bars[-1]
    sma20 = _sma20Prior(stkCd, ymd)
    curPx = _resolveCurPx(stkCd, ymd, rankCurPx=rankCurPx)
    refClose = curPx if curPx is not None else last.get("closePrc")
    return {
        "baseDt": last["dt"],
        "openPrc": last.get("openPrc"),
        "closePrc": refClose,
        "curPx": curPx,
        "sma20": sma20,
        "barCnt": len(bars),
        "aboveSma": bool(
            sma20 is not None and refClose is not None and float(refClose) > float(sma20)
        ),
    }


def _dayBarsHiClose(stkCd, ymd, limit=_LOOKBACK_52W + 8):
    """기준일 포함 일봉 고가·종가 (오래된→최신). 52주 신고가 판정용."""
    code = normStkCd(stkCd)
    if not code:
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, HIGH_PRC, CLOSE_PRC
                FROM (
                  SELECT TRADE_DT, HIGH_PRC, CLOSE_PRC
                  FROM AI_DAY_STK_PRC
                  WHERE STK_CD=%s AND TRADE_DT<=%s
                    AND INDC_TP=%s AND CLOSE_PRC IS NOT NULL
                  ORDER BY TRADE_DT DESC
                  LIMIT %s
                ) t
                ORDER BY TRADE_DT ASC
                """,
                (code, str(ymd)[:8], INDC_TP_QTY, int(limit)),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[maTouchUniv] 52주 일봉 조회 실패 {code}: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    bars = []
    for r in rows:
        close = r.get("CLOSE_PRC")
        if close is None:
            continue
        highRaw = r.get("HIGH_PRC")
        try:
            highPric = abs(float(highRaw)) if highRaw not in (None, "") else None
        except (TypeError, ValueError):
            highPric = None
        bars.append(
            {
                "dt": str(r.get("TRADE_DT") or "")[:8],
                "highPric": highPric,
                "closePrc": abs(float(close)),
            }
        )
    return bars


def _barHigh(bar):
    """고가 우선, 없으면 종가. 키움 부호 붙은 값은 절대값."""
    if not bar:
        return 0.0
    try:
        highPric = float(bar.get("highPric") or 0)
    except (TypeError, ValueError):
        highPric = 0.0
    if highPric > 0:
        return abs(highPric)
    try:
        return abs(float(bar.get("closePrc") or 0))
    except (TypeError, ValueError):
        return 0.0


def _todayClose(stkCd, ymd):
    """기준일 종가. 당일 봉이 없으면 None — 미확정으로 제외하지 않을 때 씀."""
    ref = str(ymd)[:8]
    bars = _dayBarsAsOf(stkCd, ymd, limit=5)
    for bar in reversed(bars or []):
        if str(bar.get("dt") or "")[:8] == ref:
            try:
                return abs(float(bar.get("closePrc") or 0)) or None
            except (TypeError, ValueError):
                return None
    return None


def _closeAboveSma20(stkCd, ymd):
    """
    종가 > 전일까지 확정 SMA20.
    반환: (above|None, sma20, closePx) — 종가/이평 없으면 above=None (유지 쪽은 빼지 않음).
    """
    sma20 = _sma20Prior(stkCd, ymd)
    closePx = _todayClose(stkCd, ymd)
    if sma20 is None or closePx is None:
        return None, sma20, closePx
    return float(closePx) > float(sma20), sma20, closePx


def _is52wHigh(stkCd, ymd):
    """
    당일 고가(없으면 종가) >= 직전 250거래일 고가 최댓값.
    당일 봉·과거 250봉이 부족하면 False (추정 편입 금지).
    """
    bars = _dayBarsHiClose(stkCd, ymd)
    if not bars:
        return False
    ref = str(ymd)[:8]
    idx = None
    for i, bar in enumerate(bars):
        if str(bar.get("dt") or "")[:8] == ref:
            idx = i
    if idx is None or idx < _LOOKBACK_52W:
        return False
    prev = bars[idx - _LOOKBACK_52W : idx]
    if len(prev) < _LOOKBACK_52W:
        return False
    prevMax = max(_barHigh(b) for b in prev)
    curHigh = _barHigh(bars[idx])
    if prevMax <= 0 or curHigh <= 0:
        return False
    return curHigh >= prevMax


def _mse01UnivOk(stkCd, ymd):
    """
    MSE01 신규 편입(과거 DB 검증): 당일 52주 신고가이고 종가가 SMA20 위.
    실전 16시 관심은 ka10016 편입 + 종가≤SMA20 제외 (_replaceMse01Managed).
    """
    if not _is52wHigh(stkCd, ymd):
        return False
    above, _, _ = _closeAboveSma20(stkCd, ymd)
    return above is True


def _mse01OpenCds():
    cds = set()
    try:
        for cd in listOpenLedgerStkCds("maSupportEod_v1") or []:
            n = normStkCd(cd)
            if n:
                cds.add(n)
    except Exception:
        pass
    return cds


def _openLedgerCds():
    """MTS01·MSE01 미청산 종목."""
    openCds = set()
    try:
        for sid in (_STRAT_ID, "maSupportEod_v1"):
            for cd in listOpenLedgerStkCds(sid) or []:
                n = normStkCd(cd)
                if n:
                    openCds.add(n)
    except Exception as err:
        termLog(f"[maTouchUniv] 미청산 조회 실패: {err}")
    return openCds


def _dropAlgBoth(cd, note=""):
    """MTS01·MSE01 관리종목 해제."""
    for alg in (_ALG_CD, "MSE01"):
        try:
            delAlgManaged(alg, cd)
        except Exception as err:
            termLog(f"[maTouchUniv] 관리종목 해제 실패 {alg} {cd}: {err}")



def _isBearBar(bar):
    """음봉: 종가 < 시가. 시가 없거나 동일가면 False."""
    if not bar:
        return False
    o = bar.get("openPrc")
    c = bar.get("closePrc")
    if o is None or c is None:
        return False
    try:
        return float(c) < float(o)
    except (TypeError, ValueError):
        return False


def _belowSma20TwoBearDays(stkCd, ymd):
    """
    기준일 포함 최근 2거래일 연속:
    음봉(종가<시가) 이고 종가 ≤ 그 날 전일확정 SMA20.
    반환: (shouldDrop, sma20_today, close_today)
    일봉 부족·당일봉 미확정이면 False (제외하지 않음).
    """
    ref = str(ymd)[:8]
    bars = _dayBarsAsOf(stkCd, ymd, limit=_SMA_SLOW + 5)
    if not bars or len(bars) < 2:
        return False, None, None
    d1 = bars[-1]
    d0 = bars[-2]
    if str(d1.get("dt") or "")[:8] != ref:
        return False, None, None
    last_sma = None
    last_close = None
    for bar in (d0, d1):
        if not _isBearBar(bar):
            return False, None, None
        sma = _sma20Prior(stkCd, bar.get("dt"))
        close_px = bar.get("closePrc")
        if sma is None or close_px is None:
            return False, None, None
        try:
            if float(close_px) > float(sma):
                return False, None, None
        except (TypeError, ValueError):
            return False, None, None
        last_sma = sma
        last_close = close_px
    return True, last_sma, last_close


def _purgeWatchBelowSma20(ymd, openCds, rankPxMap=None):
    """
    적재 배치 직전 — 기존 USE_TRADE 관심 정리.
    · 현재가 > SMA20 → 유지
    · 이틀 연속 음봉 + 종가≤SMA20 → 제외
    · 그 외(하루 이탈·양봉 등) → 유예 유지
    """
    rankPxMap = rankPxMap or {}
    openCds = openCds or set()
    dropped = []
    retain = []
    noteDrop = f"sma20_2음봉이탈 {ymd}"
    noteKeep = f"sma20유지 {ymd}"
    noteHold = f"sma20유예(2음봉대기) {ymd}"

    try:
        curWatch = listTradeWatch(limit=200) or []
    except Exception as err:
        termLog(f"[maTouchUniv] 관심 조회 실패: {err}")
        return {"retain": [], "dropped": [], "dropCnt": 0, "retainCnt": 0}

    for row in curWatch:
        cd = normStkCd(row.get("STK_CD"))
        if not cd or cd in openCds:
            continue
        above, sma20, curPx = _isAboveSma20(cd, ymd, rankPxMap.get(cd))
        stkNm = (row.get("STK_NM") or cd).strip()
        if not above:
            should_drop, drop_sma, drop_close = _belowSma20TwoBearDays(cd, ymd)
            if should_drop:
                got = deactivateWatchStk(cd, note=noteDrop)
                if got.get("ok"):
                    # SMA20 정리는 이평터치 관심(USE_TRADE)만. 이평지지는 AI_ALG_STK(MSE01)에서 따로 종가 기준으로 정리
                    try:
                        delAlgManaged(_ALG_CD, cd)
                    except Exception as err:
                        termLog(f"[maTouchUniv] 관리종목 해제 실패 {_ALG_CD} {cd}: {err}")
                    dropped.append(
                        {
                            "stkCd": cd,
                            "stkNm": stkNm,
                            "closePrc": drop_close if drop_close is not None else curPx,
                            "sma20": drop_sma if drop_sma is not None else sma20,
                            "note": noteDrop,
                        }
                    )
                continue
            # 하루만 이탈 등 — 관심 유지(유예)
            retain.append(
                {
                    "stkCd": cd,
                    "stkNm": stkNm,
                    "rankNo": row.get("RANK_NO"),
                    "indGrp": row.get("IND_GRP"),
                    "marketName": row.get("MARKET_NAME"),
                    "curPrc": curPx,
                    "closePrc": curPx,
                    "openPrc": row.get("OPEN_PRC"),
                    "sma20": sma20,
                    "aboveSma": False,
                    "srcTag": "sma20keep",
                    "note": noteHold,
                }
            )
            continue
        retain.append(
            {
                "stkCd": cd,
                "stkNm": stkNm,
                "rankNo": row.get("RANK_NO"),
                "indGrp": row.get("IND_GRP"),
                "marketName": row.get("MARKET_NAME"),
                "curPrc": curPx,
                "closePrc": curPx,
                "openPrc": row.get("OPEN_PRC"),
                "sma20": sma20,
                "aboveSma": True,
                "srcTag": "sma20keep",
                "note": noteKeep,
            }
        )

    if dropped:
        _writeEvts(dropped, "WATCH_OUT", "sma20_2bear", ymd)
        termLog(f"[maTouchUniv] SMA20 이틀음봉 이탈 제외 {len(dropped)}종")
    hold_n = len([r for r in retain if not r.get("aboveSma")])
    keep_n = len([r for r in retain if r.get("aboveSma")])
    if keep_n:
        termLog(f"[maTouchUniv] SMA20 위 유지 {keep_n}종")
    if hold_n:
        termLog(f"[maTouchUniv] SMA20 유예유지(2음봉대기) {hold_n}종")
    return {
        "retain": retain,
        "dropped": dropped,
        "dropCnt": len(dropped),
        "retainCnt": len(retain),
    }


def _mergeRetainPicks(picks, retainItems, pickCds):
    """신규 picks 에 없는 SMA20 유지 종목을 합친다."""
    merged = list(picks or [])
    seen = set(pickCds or set())
    extra = 0
    for item in retainItems or []:
        cd = normStkCd(item.get("stkCd"))
        if not cd or cd in seen:
            continue
        merged.append(item)
        seen.add(cd)
        extra += 1
    return merged, extra


def _writeEvts(items, evtTp, reason, ymd):
    """복기용 알고리즘 이벤트 (매매창 거래로그에도 뜬다)."""
    try:
        from utils.algEvtStore import insertAlgEvt

        nowTm = datetime.now().strftime("%H%M%S")
        for it in items:
            insertAlgEvt(
                {
                    "algCd": _ALG_CD,
                    "evtTp": evtTp,
                    "reason": reason,
                    "stkCd": it.get("stkCd"),
                    "stkNm": it.get("stkNm") or "",
                    "mode": "paper",
                    "tradeDt": ymd,
                    "evtTm": nowTm,
                    "px": (
                        int(it["closePrc"]) if it.get("closePrc") else None
                    ),
                    "note": it.get("note") or "",
                }
            )
    except Exception as err:
        termLog(f"[maTouchUniv] 복기이벤트 실패: {err}")


def _replaceMse01Managed(ntlItems, ymd):
    """
    이평지지(MSE01) 관심.
    - 편입: ka10016 52주 신고가 (코스피+코스닥, 고저기준 dt=250)
    - 유지: 기존 관심은 종가 > 전일확정 SMA20 이면 남김 (당일 신고가 목록에 없어도)
    - 제외: 종가 ≤ SMA20. 종가/이평 없으면 빼지 않음
    - 원장 미청산은 항상 유지 (매도 감시)
    """
    mseOpen = _mse01OpenCds()
    ntlCds = []
    seenNtl = set()
    nmByCd = {}
    for it in ntlItems or []:
        cd = normStkCd(it.get("stkCd"))
        if not cd or cd in seenNtl:
            continue
        seenNtl.add(cd)
        ntlCds.append(cd)
        nmByCd[cd] = (it.get("stkNm") or cd).strip()

    try:
        mseRows = listAlgStk(_MSE01_ALG, limit=2000) or []
    except Exception as err:
        termLog(f"[maTouchUniv] 관리종목 조회 실패 {_MSE01_ALG}: {err}")
        mseRows = []
    existCds = set()
    for row in mseRows:
        cd = normStkCd(row.get("stkCd"))
        if not cd:
            continue
        existCds.add(cd)
        if cd not in nmByCd:
            nmByCd[cd] = (row.get("stkNm") or cd).strip()

    # 후보 = 기존 ∪ 당일 52주. 종가≤SMA20 만 탈락 (미청산 제외)
    candCds = existCds | seenNtl
    smaKeep = set()
    smaDrop = set()
    for cd in candCds:
        if cd in mseOpen:
            smaKeep.add(cd)
            continue
        above, _, _ = _closeAboveSma20(cd, ymd)
        if above is False:
            smaDrop.add(cd)
            continue
        smaKeep.add(cd)

    mseKeep = smaKeep | mseOpen
    dropGot = deactivateAlgStkExcept(_MSE01_ALG, mseKeep)
    dropCnt = int(dropGot.get("deactCnt") or 0)
    if not dropGot.get("ok"):
        termLog(
            f"[maTouchUniv] MSE01 일괄 해제 실패: {dropGot.get('errMsg')}"
        )

    ntlAdd = seenNtl - existCds
    smaKeepExist = (existCds & smaKeep) - seenNtl
    algRows = []
    note52 = f"ka10016 52w {ymd}"
    noteKeep = f"sma20유지 {ymd}"
    noteOpen = f"원장미청산 {ymd}"
    for cd in sorted(mseKeep):
        if cd in seenNtl:
            src = "ka10016"
            note = note52
        elif cd in mseOpen and cd not in existCds:
            src = "ledger"
            note = noteOpen
        else:
            src = "sma20keep"
            note = noteKeep if cd not in mseOpen else noteOpen
        algRows.append(
            {
                "algCd": _MSE01_ALG,
                "stkCd": cd,
                "stkNm": nmByCd.get(cd) or cd,
                "src": src,
                "note": note[:80],
            }
        )
    saved = upsertAlgStkRows(algRows) if algRows else {"upsertCnt": 0}
    try:
        from trading.maSupportEodLive import pruneMse01BrokeOut

        pruneMse01BrokeOut(mseKeep)
    except Exception as err:
        termLog(f"[maTouchUniv] MSE01 brokeOut 정리 실패: {err}")
    termLog(
        f"[maTouchUniv] MSE01 ka10016 {len(ntlCds)}종 → "
        f"신규 {len(ntlAdd)} · SMA20유지 {len(smaKeepExist)} · "
        f"관심 {len(mseKeep)} · 20선이탈 {len(smaDrop)} "
        f"(해제 {dropCnt}, 미청산 {len(mseOpen)})"
    )
    return {
        "mseNewCnt": len(ntlAdd),
        "mseKeepCnt": len(mseKeep),
        "mseDropCnt": len(smaDrop),
        "mseSmaKeepCnt": len(smaKeepExist),
        "mseUnionCnt": len(ntlCds),
        "mseAlgCnt": int(saved.get("upsertCnt") or 0),
        "mseOpenCnt": len(mseOpen),
        "mseCds": sorted(mseKeep),
    }


def _replaceManaged(picks, ymd):
    """
    이평터치(MTS01) 관리·관심 반영.
    picks: 신규 순위 + SMA20 유지 종목 합집합.
    미청산(원장 보유) 종목은 목록에 없어도 감시·청산을 이어간다.
    MSE01 은 같은 배치에서 ka10016 편입 + 종가≤SMA20 제외.
    """
    pickCds = {p["stkCd"] for p in picks}
    openCds = _openLedgerCds()
    keepCds = sorted(pickCds | openCds)

    removed = []
    try:
        curRows = listAlgStk(_ALG_CD) or []
    except Exception as err:
        termLog(f"[maTouchUniv] 관리종목 조회 실패 {_ALG_CD}: {err}")
        curRows = []
    for row in curRows:
        cd = normStkCd(row.get("stkCd"))
        if not cd or cd in pickCds or cd in openCds:
            continue
        got = delAlgManaged(_ALG_CD, cd)
        if got.get("ok"):
            removed.append(
                {"stkCd": cd, "stkNm": row.get("stkNm") or "", "note": f"univ교체 {ymd}"}
            )
        elif not got.get("ok"):
            termLog(f"[maTouchUniv] 관리종목 해제 실패 {_ALG_CD} {cd}: {got.get('errMsg')}")

    note = f"rank16h {ymd} 순위{_RANK_MAX}∪대금{_TRDE_PRCA_MAX} 코스피/코스닥"
    algRows = []
    for p in picks:
        pNote = p.get("note") or note
        src = _SRC if p.get("srcTag") != "sma20keep" else "sma20keep"
        algRows.append(
            {
                "algCd": _ALG_CD,
                "stkCd": p["stkCd"],
                "stkNm": p.get("stkNm") or "",
                "src": src,
                "note": pNote,
            }
        )
    savedAlg = upsertAlgStkRows(algRows) if algRows else {"upsertCnt": 0}

    watchRows = []
    for p in picks:
        closePx = p.get("closePrc") or p.get("curPrc")
        rankNo = p.get("rankNo")
        try:
            rankNo = int(rankNo) if rankNo is not None else None
        except (TypeError, ValueError):
            rankNo = None
        rowNote = p.get("note") or note
        watchRows.append(
            {
                "STK_CD": p["stkCd"],
                "MKT_TP": "KR",
                "STK_NM": p.get("stkNm") or p["stkCd"],
                "MARKET_NAME": p.get("marketName"),
                "IND_GRP": p.get("indGrp"),
                "GRP_CD": WATCH_GRP_RANK,
                "STATUS": "Y",
                "USE_BT": "Y",
                "USE_TRADE": "Y",
                "PRIORITY": rankNo if rankNo is not None else 90,
                "RANK_NO": rankNo,
                "REF_TRADE_DT": ymd,
                "LOAD_DT": ymd,
                "OPEN_PRC": int(p["openPrc"]) if p.get("openPrc") else None,
                "CLOSE_PRC": int(closePx) if closePx else None,
                "SRC": _SRC if p.get("srcTag") != "sma20keep" else "sma20keep",
                "NOTE": rowNote,
            }
        )
    deact = deactivateWatchByGrp(
        WATCH_GRP_RANK, exceptStkCds=keepCds, mktTp="KR"
    )
    savedWatch = upsertWatchStkRows(watchRows) if watchRows else {"upsertCnt": 0}

    if picks:
        _writeEvts(
            [dict(p, note=note) for p in picks], "RANK_IN", "rank_16h", ymd
        )
    if removed:
        _writeEvts(removed, "WATCH_OUT", "univ_replace", ymd)

    return {
        "algCnt": savedAlg.get("upsertCnt") or 0,
        "watchCnt": savedWatch.get("upsertCnt") or 0,
        "removeCnt": len(removed),
        "keepOpenCnt": len(openCds - pickCds),
        "watchDeactCnt": int(deact.get("deactCnt") or 0),
        "removedNms": [r.get("stkNm") or r["stkCd"] for r in removed],
    }


def _rankPxMapFromRecords(records):
    """순위 스냅샷 STK_CD → CUR_PRC."""
    out = {}
    for r in records or []:
        cd = normStkCd(r.get("STK_CD"))
        if not cd:
            continue
        px = _absPrc(r.get("CUR_PRC"))
        if px:
            out[cd] = px
    return out


def _rankPxMapFromDb(ymd):
    """DB 순위 스냅샷 CUR_PRC."""
    out = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, CUR_PRC
                FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT=%s AND QRY_TP=%s
                """,
                (str(ymd)[:8], QRY_TP_DAY),
            )
            for r in cur.fetchall() or []:
                cd = normStkCd(r.get("STK_CD"))
                px = _absPrc(r.get("CUR_PRC"))
                if cd and px:
                    out[cd] = px
    except Exception as err:
        termLog(f"[maTouchUniv] 순위가격 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _mse01SummaryLine(mseGot):
    """16시 텔레그램용 이평지지 한 줄."""
    got = mseGot or {}
    if got.get("msePaused"):
        openCnt = int(got.get("mseOpenCnt") or 0)
        return (
            f"[이평지지] 사용중지 — 신규편입 없음 · "
            f"원장미청산 {openCnt}종만 유지 "
            f"(해제 {got.get('mseDropCnt', 0)})"
        )
    if got.get("mseSkip"):
        err = got.get("errMsg") or "생략"
        return f"[이평지지] ka10016 미반영 ({err})"
    kospi = got.get("kospiCnt")
    kosdaq = got.get("kosdaqCnt")
    mkt = ""
    if kospi is not None or kosdaq is not None:
        mkt = f" 코스피 {int(kospi or 0)} · 코스닥 {int(kosdaq or 0)} →"
    openCnt = int(got.get("mseOpenCnt") or 0)
    return (
        f"[이평지지] ka10016 52주신고가{mkt} "
        f"신규 {got.get('mseNewCnt', 0)} · "
        f"SMA20유지 {got.get('mseSmaKeepCnt', 0)} · "
        f"관심 {got.get('mseKeepCnt', 0)} · "
        f"20선이탈제외 {got.get('mseDropCnt', 0)}"
        + (f" · 미청산 {openCnt}" if openCnt else "")
    )


def loadMaTouchUniv(tradeDt=None, useTr=True):
    """
    16시 순위 → 다음 거래일 관리종목 등록.
    useTr=False 면 이미 적재된 순위 스냅샷을 쓰고, 거래대금(ka10032)은 세션이 있으면 조회한다.
    반환: {ok, tradeDt, rankCnt, pickCnt, summary, ...}
    """
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    rankPxMap = {}
    records = []
    smaDropCnt = 0
    smaRetainExtra = 0
    openCds = _openLedgerCds()

    mseGot = {"mseSkip": True}
    if useTr:
        sess = acquireKiwoomSession()
        if not sess:
            return {"ok": False, "errMsg": "키움 토큰 없음 — 16시 순위 조회 생략"}
        # 순위 TR 실패와 무관하게 이평지지 관심은 ka10016 편입 + SMA20 정리
        mseGot = _tryReplaceMse01FromKa10016(ymd, sess)
        mode, hostUrl, token, tokenType = sess
        fetched = fetchInqRank(
            hostUrl, token, qryTp=QRY_TP_DAY, tokenType=tokenType
        )
        if not fetched.get("ok"):
            err = f"ka00198 실패: {fetched.get('errMsg')}"
            _notify(_mse01SummaryLine(mseGot) + "\n[이평터치대상실패] " + err)
            return {"ok": False, "errMsg": err, **(mseGot or {})}
        for row in fetched.get("rows") or []:
            rec = rowToRecord(row, QRY_TP_DAY, ymd)
            if rec is not None:
                records.append(rec)
        if not records:
            err = f"16시 순위 응답 없음 {ymd}"
            _notify(_mse01SummaryLine(mseGot) + "\n[이평터치대상실패] " + err)
            return {"ok": False, "errMsg": err, **(mseGot or {})}
        upsertDayInqRankRows(records)
        rankPxMap = _rankPxMapFromRecords(records)
        # 이평 계산용 일봉 — 신규 순위 + 기존 관심
        try:
            cds = [normStkCd(r.get("STK_CD")) for r in records]
            for w in listTradeWatch(limit=200) or []:
                cd = normStkCd(w.get("STK_CD"))
                if cd:
                    cds.append(cd)
            ensureDayPrcForSma(
                [c for c in cds if c], minBars=_MIN_BARS, endDt=ymd
            )
        except Exception as err:
            termLog(f"[maTouchUniv] 일봉 보강 실패: {err}")
        # 적재 전 — SMA20 이탈 관심 제거 · 위 종목 유지 후보
        try:
            smaPrep = _purgeWatchBelowSma20(ymd, openCds, rankPxMap)
            smaDropCnt = int(smaPrep.get("dropCnt") or 0)
            smaRetainPool = smaPrep.get("retain") or []
        except Exception as err:
            termLog(f"[maTouchUniv] SMA20 관심 정리 실패: {err}")
            smaRetainPool = []
    else:
        rankPxMap = _rankPxMapFromDb(ymd)
        smaRetainPool = []

    trdePrcaCds = None
    sess = acquireKiwoomSession()
    if sess:
        _mode, hostUrl, token, tokenType = sess
        # useTr 경로에서 이미 ka00198 을 썼으면 간격을 지킨다
        prca = _fetchTrdePrcaTopCds(hostUrl, token, tokenType)
        if not prca.get("ok"):
            return {"ok": False, "errMsg": prca.get("errMsg")}
        trdePrcaCds = prca.get("cds") or set()
        if not trdePrcaCds:
            return {"ok": False, "errMsg": "ka10032 거래대금상위 0종"}
    elif useTr:
        return {"ok": False, "errMsg": "키움 토큰 없음 — 거래대금상위 조회 생략 불가"}

    screened = screenUnivFromDb(ymd, trdePrcaCds=trdePrcaCds)
    if not screened.get("ok"):
        return {"ok": False, "errMsg": screened.get("errMsg")}

    picks = screened.get("picks") or []
    skips = screened.get("skips") or []
    if useTr and smaRetainPool:
        pickCds = {p["stkCd"] for p in picks}
        picks, smaRetainExtra = _mergeRetainPicks(picks, smaRetainPool, pickCds)

    if not picks:
        summary = (
            f"[이평터치대상] {ymd} 16시 순위 {screened.get('rankCnt')}종 — "
            f"코스피/코스닥·SMA20 통과 0종"
            + (f" (SMA20 이탈 제외 {smaDropCnt}종)" if smaDropCnt else "")
            + ". 이평터치 관리종목을 바꾸지 않습니다.\n"
            + _mse01SummaryLine(mseGot)
        )
        _notify(summary)
        return {
            "ok": False,
            "tradeDt": ymd,
            "rankCnt": screened.get("rankCnt"),
            "pickCnt": 0,
            "errMsg": summary,
            "summary": summary,
            **(mseGot or {}),
        }

    applied = _replaceManaged(picks, ymd)

    mktCnt = len([s for s in skips if s.get("reason") == "notMkt"])
    qtyCnt = len([s for s in skips if s.get("reason") == "qtyShort"])
    smaSkipCnt = len([s for s in skips if s.get("reason") == "belowSma20"])
    aboveCnt = len([p for p in picks if p.get("aboveSma")])
    keepCnt = len([p for p in picks if p.get("srcTag") == "sma20keep"])
    barCnt = len([p for p in picks if (p.get("barCnt") or 0) < _SMA_SLOW])
    stepAmt = int(screened.get("stepAmt") or 0)
    rankPickCnt = int(screened.get("rankPickCnt") or 0)
    trdePickCnt = int(screened.get("trdePickCnt") or 0)
    trdeInCnt = int(screened.get("trdeInCnt") or 0)
    names = ", ".join(
        f"{p.get('stkNm')}({'20선위' if p.get('aboveSma') else '20선아래'})"
        for p in picks[:12]
    )
    extra = f" 외 {len(picks)-12}종" if len(picks) > 12 else ""
    mktNms = ", ".join(
        (s.get("stkNm") or s.get("stkCd"))
        for s in skips
        if s.get("reason") == "notMkt"
    )
    summary = (
        f"[이평터치대상] {ymd} 조회순위 {screened.get('rankCnt')}종 → "
        f"다음 거래일 관리 {len(picks)}종\n"
        f"조건: 코스피/코스닥 · 조회순위 상위 {_RANK_MAX} ∪ "
        f"거래대금 상위 {_TRDE_PRCA_MAX} (중복제거) · 현재가>{_SMA_SLOW}선\n"
        f"편입: 순위 {rankPickCnt} · 대금만추가 {trdePickCnt} "
        f"(대금원천 {trdeInCnt})"
        + (f" · SMA20유지 {keepCnt}" if keepCnt else "")
        + "\n"
        f"매차 {stepAmt // 10000:,}만원으로 {_MIN_QTY_PER_STEP}주 이상\n"
        f"제외: 시장제외 {mktCnt}{f'({mktNms})' if mktNms else ''} · "
        f"수량부족 {qtyCnt} · SMA20이탈 {smaSkipCnt}"
        + (f" · 기존관심2음봉이탈 {smaDropCnt}" if smaDropCnt else "")
        + "\n"
        f"참고: {_SMA_SLOW}선 위 {aboveCnt}종"
        + (f" · 일봉부족 {barCnt}종" if barCnt else "")
        + "\n"
        f"교체해제 {applied.get('removeCnt')}종 · "
        f"보유유지 {applied.get('keepOpenCnt')}종 · "
        f"관심비활성 {applied.get('watchDeactCnt', 0)}종\n"
        f"{names}{extra}\n"
        + _mse01SummaryLine(mseGot)
    )
    _notify(summary)
    return {
        "ok": True,
        "tradeDt": ymd,
        "rankCnt": screened.get("rankCnt"),
        "pickCnt": len(picks),
        "removeCnt": applied.get("removeCnt"),
        "keepOpenCnt": applied.get("keepOpenCnt"),
        "summary": summary,
        **(mseGot or {}),
    }


def runMaTouchUnivNow(tradeDt=None):
    """수동 실행 — 시간창·ON/OFF 무시. 배치 화면/텔레그램에서 호출."""
    global _running, _doneYmd

    if _running:
        return {"ok": False, "errMsg": "이미 대상 선정이 진행 중입니다."}
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d"))[:8]
    _running = True
    try:
        result = loadMaTouchUniv(tradeDt=ymd)
    finally:
        _running = False
    if result.get("ok"):
        _doneYmd = ymd
    else:
        _notify(f"[이평터치대상실패] {result.get('errMsg')}")
    return result


def _tryLoad(now=None):
    global _doneYmd, _lastAttemptKey, _running

    dt = now or datetime.now()
    ymd = dt.strftime("%Y%m%d")
    curHm = dt.strftime("%H:%M")

    if _doneYmd == ymd or _running:
        return False
    if not isBatchSchedEnabled(_JOB_ID) or not _stratEnabled():
        return False
    if not isTradingDay(dt):
        return False
    if curHm < _RUN_START_HM or curHm > _RUN_DEADLINE_HM:
        return False

    # 같은 분에 재시도하지 않는다 (실패 시 다음 분에 다시)
    attemptKey = f"{ymd}|{curHm}"
    if attemptKey == _lastAttemptKey:
        return False
    _lastAttemptKey = attemptKey

    termLog(f"[maTouchUniv] 16시 순위 대상 선정 시도 now={curHm}")
    _running = True
    try:
        result = loadMaTouchUniv(tradeDt=ymd)
    finally:
        _running = False

    if result.get("ok"):
        _doneYmd = ymd
        return True
    # 통과 0종은 재시도해도 같으므로 하루 1회로 끝낸다
    if result.get("pickCnt") == 0 and result.get("rankCnt"):
        _doneYmd = ymd
        _notify(result.get("summary") or "[이평터치대상] 통과 0종")
        return True
    _notify(
        f"[이평터치대상실패] {result.get('errMsg')}\n"
        f"({_RUN_DEADLINE_HM} 까지 재시도)"
    )
    return True


def _loop():
    while True:
        try:
            _tryLoad()
        except Exception as err:
            termLog(f"[maTouchUniv] 루프 오류: {err}")
        time.sleep(30)


def startMaTouchUnivSched():
    """거래일 16:00 이평터치 매매대상 선정 스케줄러 기동."""
    global _schedStarted
    if _schedStarted:
        return
    _schedStarted = True
    threading.Thread(target=_loop, name="maTouchUnivSched", daemon=True).start()
    termLog(
        f"[maTouchUniv] 기동 — 거래일 {_RUN_START_HM}~{_RUN_DEADLINE_HM} "
        f"ka00198 상위{_RANK_MAX} ∪ ka10032 대금상위{_TRDE_PRCA_MAX} "
        f"코스피/코스닥(중복제거) 다음 거래일 관리종목(MTS01). "
        f"MSE01 은 ka10016 52주신고가 편입 · 종가≤SMA20 제외. "
        f"{_SMA_SLOW}선 조건은 당일 매매에서 판정"
    )
