"""
장마감 순위 종목 + 장중 15분 편입 종목 → 당일 1분봉(AI_MIN_STK_PRC) 적재.

ka10080 tic_scope=1. 모의 1초 1회. 당일 봉이 충분하면 스킵.
"""

import time
from datetime import datetime

from api.getInqRank import QRY_TP_DAY
from api.getMinStkPrc import TIC_SCOPE_1, fetchMinStkPrcRange
from utils.btEnsureMinData import countMinBarsByDay
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dbConn import openDbConn
from utils.minStkPrcStore import upsertMinStkPrcRows
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 과거 장중 15분 편입 출처 (지금은 전날 16시 선정으로 대체 — 과거분 복기용)
RANK_15M_SRC = "rank_15m"
# 정규장 1분봉이 이보다 적으면 불완전으로 보고 재조회
_MIN_BARS_PER_DAY = 50


def _addCd(codes, seen, raw):
    cd = normStkCd(raw)
    if cd and cd not in seen:
        seen.add(cd)
        codes.append(cd)


def collectCloseMinStkCds(tradeDt, rankStkCds=None):
    """
    당일 1분봉 대상: 당일누적 순위 ∪ 관리종목 ∪ 전 거래일 순위 ∪ 장중 15분 편입.
    전 거래일 순위가 당일 매매 유니버스(rank16h)이므로 검증에 필요하고,
    15분 편입은 장마감 관심 교체로 SRC 가 지워져도
    NOTE·복기이벤트·관리종목으로 당일 추가분을 살린다.
    """
    ymd = str(tradeDt or "").replace("-", "").strip()[:8]
    codes = []
    seen = set()
    extra15m = []

    for raw in rankStkCds or []:
        _addCd(codes, seen, raw)
    rankCnt = len(codes)

    if len(ymd) != 8 or not ymd.isdigit():
        return {"stkCds": codes, "rankCnt": rankCnt, "extra15mCnt": 0}

    noteLike = f"rank15m양봉 {ymd}%"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 1) 당일 누적 순위 (장마감 스냅샷)
            cur.execute(
                """
                SELECT DISTINCT STK_CD
                FROM AI_DAY_INQ_RANK
                WHERE TRADE_DT=%s AND QRY_TP=%s
                """,
                (ymd, QRY_TP_DAY),
            )
            for r in cur.fetchall() or []:
                _addCd(codes, seen, r.get("STK_CD"))
            rankCnt = len(codes)

            # 2) 관심: 15분 편입 SRC 또는 당일 NOTE
            try:
                cur.execute(
                    """
                    SELECT STK_CD
                    FROM AI_WATCH_STK
                    WHERE MKT_TP='KR' AND STATUS='Y'
                      AND (
                        (SRC=%s AND (LOAD_DT=%s OR REF_TRADE_DT=%s))
                        OR NOTE LIKE %s
                      )
                    """,
                    (RANK_15M_SRC, ymd, ymd, noteLike),
                )
                for r in cur.fetchall() or []:
                    before = len(seen)
                    _addCd(codes, seen, r.get("STK_CD"))
                    if len(seen) > before:
                        extra15m.append(normStkCd(r.get("STK_CD")))
            except Exception as err:
                termLog(f"[rankMinPrc] 관심 15분편입 조회 스킵: {err}")

            # 3) 관리종목 NOTE (장마감 관심 SRC 덮어쓰기와 무관)
            try:
                cur.execute(
                    """
                    SELECT STK_CD
                    FROM AI_ALG_STK
                    WHERE STATUS='Y' AND SRC=%s AND NOTE LIKE %s
                    """,
                    (RANK_15M_SRC, noteLike),
                )
                for r in cur.fetchall() or []:
                    before = len(seen)
                    _addCd(codes, seen, r.get("STK_CD"))
                    if len(seen) > before:
                        extra15m.append(normStkCd(r.get("STK_CD")))
            except Exception as err:
                termLog(f"[rankMinPrc] 관리종목 15분편입 조회 스킵: {err}")

            # 4) 당일 매매한 관리종목 (전날 16시 선정분 — 순위에서 빠졌어도 필요)
            try:
                cur.execute(
                    """
                    SELECT STK_CD
                    FROM AI_ALG_STK
                    WHERE STATUS='Y'
                    """
                )
                for r in cur.fetchall() or []:
                    before = len(seen)
                    _addCd(codes, seen, r.get("STK_CD"))
                    if len(seen) > before:
                        extra15m.append(normStkCd(r.get("STK_CD")))
            except Exception as err:
                termLog(f"[rankMinPrc] 관리종목 조회 스킵: {err}")

            # 5) 전 거래일 순위 — 그게 당일 매매 유니버스라 검증에 반드시 필요.
            #    관리종목 목록은 매일 교체되므로 소급 적재 시 이 경로로 살린다.
            try:
                from utils.dayPrcRefixLoad import prevTradingDay

                prevDt = prevTradingDay(ymd)
                if prevDt:
                    cur.execute(
                        """
                        SELECT DISTINCT STK_CD
                        FROM AI_DAY_INQ_RANK
                        WHERE TRADE_DT=%s AND QRY_TP=%s
                        """,
                        (prevDt, QRY_TP_DAY),
                    )
                    for r in cur.fetchall() or []:
                        before = len(seen)
                        _addCd(codes, seen, r.get("STK_CD"))
                        if len(seen) > before:
                            extra15m.append(normStkCd(r.get("STK_CD")))
            except Exception as err:
                termLog(f"[rankMinPrc] 전거래일 순위 조회 스킵: {err}")

            # 6) 복기 RANK_IN (재시작 후 훅이 쌓인 경우)
            try:
                cur.execute(
                    """
                    SELECT DISTINCT STK_CD
                    FROM AI_ALG_EVT
                    WHERE EVT_TP='RANK_IN' AND TRADE_DT=%s
                    """,
                    (ymd,),
                )
                for r in cur.fetchall() or []:
                    before = len(seen)
                    _addCd(codes, seen, r.get("STK_CD"))
                    if len(seen) > before:
                        extra15m.append(normStkCd(r.get("STK_CD")))
            except Exception as err:
                termLog(f"[rankMinPrc] 복기 RANK_IN 조회 스킵: {err}")
    except Exception as err:
        termLog(f"[rankMinPrc] 대상 수집 실패: {err}")
        rankCnt = len(codes)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    extraCds = []
    extraSeen = set()
    for cd in extra15m:
        if cd and cd not in extraSeen:
            extraSeen.add(cd)
            extraCds.append(cd)

    return {
        "stkCds": codes,
        "rankCnt": rankCnt,
        "extra15mCnt": len(extraCds),
        "extra15mCds": extraCds,
        "tradeDt": ymd,
    }


def loadMinPrcForRankStks(stkCds, tradeDt, skipIfEnough=True, onProgress=None):
    """
    종목 목록의 당일 1분봉 적재.
    반환: {ok, stkCnt, loadedCnt, skipCnt, failCnt, upsertCnt, summary}
    """
    ymd = str(tradeDt or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    codes = []
    seen = set()
    for raw in stkCds or []:
        _addCd(codes, seen, raw)

    if not codes:
        return {
            "ok": False,
            "errMsg": "1분봉 적재 대상 종목이 없습니다.",
            "stkCnt": 0,
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
        }

    session = acquireKiwoomSession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "키움 토큰 없음 — login paper|real 또는 .env 앱키 확인",
            "stkCnt": len(codes),
            "loadedCnt": 0,
            "skipCnt": 0,
            "failCnt": 0,
            "upsertCnt": 0,
        }

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    isPaper = mode == "paper"
    waitSec = 1.05 if isPaper else 0.35

    termLog(
        f"[rankMinPrc] ({modeLabel}) {len(codes)}종목 {ymd} 1분봉 적재 시작"
    )
    if onProgress:
        onProgress(
            f"[순위1분봉] ({modeLabel}) {len(codes)}종목 {ymd} 적재 시작"
        )

    loadedCnt = 0
    skipCnt = 0
    failCnt = 0
    upsertCnt = 0
    fails = []
    called = 0

    for stkCd in codes:
        if skipIfEnough:
            byDay = countMinBarsByDay(stkCd, TIC_SCOPE_1, ymd, ymd)
            if int(byDay.get(ymd, 0) or 0) >= _MIN_BARS_PER_DAY:
                skipCnt += 1
                termLog(
                    f"[rankMinPrc] skip {stkCd} (당일 {byDay.get(ymd)}봉)"
                )
                continue

        if called > 0:
            time.sleep(waitSec)
        called += 1

        fetched = fetchMinStkPrcRange(
            hostUrl,
            token,
            stkCd,
            ymd,
            ymd,
            ticScope=TIC_SCOPE_1,
            tokenType=tokenType,
            isPaper=isPaper,
        )
        if not fetched.get("ok"):
            failCnt += 1
            err = fetched.get("errMsg") or "조회실패"
            fails.append(f"{stkCd}:{err}")
            termLog(f"[rankMinPrc] fail {stkCd}: {err}")
            continue

        rows = fetched.get("rows") or []
        if not rows:
            skipCnt += 1
            termLog(f"[rankMinPrc] empty {stkCd}")
            continue

        saved = upsertMinStkPrcRows(rows)
        if not saved.get("ok"):
            failCnt += 1
            err = saved.get("errMsg") or "DB실패"
            fails.append(f"{stkCd}:{err}")
            termLog(f"[rankMinPrc] db fail {stkCd}: {err}")
            continue

        loadedCnt += 1
        upsertCnt += int(saved.get("upsertCnt") or 0)
        termLog(
            f"[rankMinPrc] ok {stkCd} {saved.get('upsertCnt')}건 "
            f"({called}/{len(codes)} 호출)"
        )

    summary = (
        f"[순위1분봉] ({modeLabel}) 완료\n"
        f"일자: {ymd}\n"
        f"대상: {len(codes)}종목 / 적재 {loadedCnt} / "
        f"스킵 {skipCnt} / 실패 {failCnt}\n"
        f"1분봉UPSERT: {upsertCnt}건"
    )
    if fails:
        summary += "\n실패예: " + "; ".join(fails[:5])
        if len(fails) > 5:
            summary += f" …외 {len(fails) - 5}건"

    termLog(summary)
    if onProgress:
        onProgress(summary)

    return {
        "ok": failCnt == 0 or loadedCnt > 0 or skipCnt > 0,
        "stkCnt": len(codes),
        "loadedCnt": loadedCnt,
        "skipCnt": skipCnt,
        "failCnt": failCnt,
        "upsertCnt": upsertCnt,
        "tradeDt": ymd,
        "fails": fails,
        "modeLabel": modeLabel,
        "summary": summary,
    }
