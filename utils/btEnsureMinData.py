"""백테스트용 1분봉 부족 시 ka10080 적재"""

from datetime import datetime, timedelta

from api.getMinStkPrc import TIC_SCOPE_1, fetchMinStkPrcRange
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.dbConn import openDbConn
from utils.minStkPrcStore import upsertMinStkPrcRows
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 단일 거래일 1분봉이 이보다 적으면 불완전으로 보고 재적재
_MIN_BARS_PER_DAY = 50


def _pickTradeSession():
    """모의 우선, 없으면 실투자."""
    if runtimeState.isPaperLoggedIn():
        return (
            "paper",
            paper_host_url,
            runtimeState.getToken("paper"),
            runtimeState.paperTokenType or "bearer",
        )
    if runtimeState.isRealLoggedIn():
        return (
            "real",
            real_host_url,
            runtimeState.getToken("real"),
            runtimeState.realTokenType or "bearer",
        )
    return None


def _weekdayYmds(startDt, endDt):
    """기간 내 월~금 YYYYMMDD 목록 (공휴일은 알 수 없어 평일만)."""
    cur = datetime.strptime(startDt, "%Y%m%d")
    end = datetime.strptime(endDt, "%Y%m%d")
    out = []
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def countMinBarsByDay(stkCd, ticScope, startDt, endDt):
    """
    TRADE_DT별 분봉 건수.
    반환: {ymd: cnt}
    """
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, COUNT(*) AS cnt
                FROM AI_MIN_STK_PRC
                WHERE STK_CD=%s AND TIC_SCOPE=%s
                  AND TRADE_DT BETWEEN %s AND %s
                GROUP BY TRADE_DT
                """,
                (str(stkCd), str(ticScope), str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return {r["TRADE_DT"]: int(r["cnt"] or 0) for r in rows}


def findMissingMinDays(stkCd, ticScope, startDt, endDt):
    """
    평일 기준 봉이 없거나 너무 적은 일자 목록.
    """
    byDay = countMinBarsByDay(stkCd, ticScope, startDt, endDt)
    missing = []
    for ymd in _weekdayYmds(startDt, endDt):
        cnt = byDay.get(ymd, 0)
        if cnt < _MIN_BARS_PER_DAY:
            missing.append(ymd)
    return missing


def ensureMinBarsForBt(
    stkCd,
    startDt,
    endDt,
    warmupStartDt=None,
    ticScope=TIC_SCOPE_1,
    shouldAbort=None,
    onProgress=None,
):
    """
    워밍업~종료 구간 1분봉이 부족하면 API 적재 후 재확인.
    shouldAbort: callable → True면 중단
    onProgress: callable(msg) 진행 알림
    반환: {ok, loaded, missingBefore, barCnt, errMsg?}
    """
    code = normStkCd(stkCd)
    warm = warmupStartDt or startDt
    scope = str(ticScope or TIC_SCOPE_1)

    def _prog(msg):
        # onProgress가 있으면 그쪽에서 termLog/텔레그램 처리
        if onProgress:
            try:
                onProgress(msg)
            except Exception:
                pass
        else:
            termLog(msg)

    if shouldAbort and shouldAbort():
        return {"ok": False, "errMsg": "사용자 중단(bt stop)", "loaded": False}

    missing = findMissingMinDays(code, scope, warm, endDt)
    simMissing = findMissingMinDays(code, scope, startDt, endDt)

    if not missing:
        byDay = countMinBarsByDay(code, scope, warm, endDt)
        barCnt = sum(byDay.values())
        _prog(f"[BT] 분봉 OK — {code} {warm}~{endDt} {barCnt}봉")
        return {
            "ok": True,
            "loaded": False,
            "missingBefore": [],
            "barCnt": barCnt,
        }

    # 시뮬 구간은 충분하고 워밍업만 부족 + 로그인 없음 → 경고 후 진행
    if not simMissing:
        session = _pickTradeSession()
        if session is None:
            byDay = countMinBarsByDay(code, scope, warm, endDt)
            barCnt = sum(byDay.values())
            _prog(
                f"[BT] 워밍업 분봉 일부 부족({len(missing)}일) — "
                f"로그인 없어 기존 데이터로 진행 (봉수={barCnt})"
            )
            return {
                "ok": True,
                "loaded": False,
                "missingBefore": missing,
                "barCnt": barCnt,
                "warmPartial": True,
            }

    _prog(
        f"[BT] 분봉 부족 {len(missing)}일 — 적재 시작 "
        f"({missing[0]}~{missing[-1]})"
    )

    session = _pickTradeSession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "분봉 적재 전 login paper 또는 login real이 필요합니다.",
            "loaded": False,
            "missingBefore": missing,
        }

    if shouldAbort and shouldAbort():
        return {"ok": False, "errMsg": "사용자 중단(bt stop)", "loaded": False}

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    _prog(f"[BT] ({modeLabel}) ka10080 분봉 조회·적재 중…")

    fetched = fetchMinStkPrcRange(
        hostUrl,
        token,
        code,
        warm,
        endDt,
        ticScope=scope,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    if not fetched.get("ok"):
        return {
            "ok": False,
            "errMsg": f"분봉 조회 실패: {fetched.get('errMsg')}",
            "loaded": False,
            "missingBefore": missing,
        }

    rows = fetched.get("rows") or []
    if rows:
        saved = upsertMinStkPrcRows(rows)
        if not saved.get("ok"):
            return {
                "ok": False,
                "errMsg": f"분봉 DB 적재 실패: {saved.get('errMsg')}",
                "loaded": False,
                "missingBefore": missing,
            }
        _prog(f"[BT] 분봉 적재 {saved.get('upsertCnt')}건 완료")
    else:
        _prog("[BT] API 조회 건수 0 — 기존 DB로 계속 시도")

    if shouldAbort and shouldAbort():
        return {"ok": False, "errMsg": "사용자 중단(bt stop)", "loaded": True}

    still = findMissingMinDays(code, scope, startDt, endDt)
    byDay = countMinBarsByDay(code, scope, warm, endDt)
    barCnt = sum(byDay.values())
    # 시뮬 구간(start~end)에 봉이 전혀 없으면 실패
    simBars = sum(
        countMinBarsByDay(code, scope, startDt, endDt).values()
    )
    if simBars <= 0:
        return {
            "ok": False,
            "errMsg": (
                f"시뮬 기간 분봉 없음: {code} {startDt}~{endDt} "
                f"(부족일 예: {', '.join(still[:5])})"
            ),
            "loaded": True,
            "missingBefore": missing,
            "barCnt": barCnt,
        }

    if still:
        _prog(
            f"[BT] 주의: 평일 기준 부족 {len(still)}일 남음 "
            f"(공휴일 가능) — 시뮬 계속"
        )

    return {
        "ok": True,
        "loaded": True,
        "missingBefore": missing,
        "stillMissing": still,
        "barCnt": barCnt,
    }
