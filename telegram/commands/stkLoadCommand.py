"""stkload — ka10099 종목마스터 적재 / 종목명 검색 보조"""

import threading

from api.getStkList import DEFAULT_MRKT_TPS, MRKT_TP_LABEL, fetchStkListMarkets
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.stkInfoStore import countStkInfo, findStkByName, upsertStkInfoRows
from utils.stkResolve import resolveStkRef
from utils.termLog import termLog

# 자동 적재 중복 방지 (로그인 연속 호출 등)
_autoLoadLock = threading.Lock()
_autoLoadRunning = False


def _pickSession():
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


def _usage():
    return (
        "사용법:\n"
        "stkload — 코스피+코스닥 종목마스터 강제 재적재 (ka10099)\n"
        "stkload kospi|kosdaq|etf|all — 시장 지정 적재\n"
        "stkfind {종목명} — 캐시에서 종목 검색\n"
        "※ 로그인 시 DB가 비어 있으면 자동 1회 적재됨"
    )


def _normMrktArgs(rawParts):
    """명령 인자 → mrkt_tp 목록."""
    if len(rawParts) < 2:
        return list(DEFAULT_MRKT_TPS)

    key = str(rawParts[1]).strip().lower()
    if key in ("", "default", "기본"):
        return list(DEFAULT_MRKT_TPS)
    if key in ("all", "전체"):
        return ["0", "10", "8", "50"]
    if key in ("0", "kospi", "코스피"):
        return ["0"]
    if key in ("10", "kosdaq", "코스닥"):
        return ["10"]
    if key in ("8", "etf"):
        return ["8"]
    if key in ("50", "konex", "코넥스"):
        return ["50"]
    tps = []
    for p in rawParts[1:]:
        if str(p).isdigit():
            tps.append(str(p))
    return tps if tps else list(DEFAULT_MRKT_TPS)


def runStkLoad(mrktTps=None):
    """
    종목마스터 동기 적재.
    반환: 결과 메시지 문자열
    """
    session = _pickSession()
    if session is None:
        return "종목마스터 적재 전 login paper 또는 login real이 필요합니다."

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    tps = list(mrktTps) if mrktTps else list(DEFAULT_MRKT_TPS)

    labels = ", ".join(MRKT_TP_LABEL.get(t, t) for t in tps)
    termLog(f"[stkload] ({modeLabel}) 시장={labels} 조회 시작")

    fetched = fetchStkListMarkets(
        hostUrl,
        token,
        mrktTps=tps,
        tokenType=tokenType,
        isPaper=(mode == "paper"),
    )
    if not fetched.get("ok"):
        fail = f"[종목마스터실패] {fetched.get('errMsg')}"
        termLog(fail)
        return fail

    rows = fetched.get("rows") or []
    saved = upsertStkInfoRows(rows)
    if not saved.get("ok"):
        fail = f"[종목마스터실패] {saved.get('errMsg')}"
        termLog(fail)
        return fail

    byMrkt = fetched.get("byMrkt") or {}
    detail = ", ".join(
        f"{MRKT_TP_LABEL.get(k, k)}={v}" for k, v in byMrkt.items()
    )
    total = countStkInfo()
    msg = (
        f"[종목마스터] ({modeLabel}) 적재 완료\n"
        f"시장: {labels}\n"
        f"금회: {saved.get('upsertCnt')}건 ({detail})\n"
        f"캐시 총건수: {total}\n"
        "이후 buy/srch/bt 등에 종목명 사용 가능"
    )
    termLog(msg)
    return msg


def _autoLoadWorker():
    """로그인 후 백그라운드 최초 적재."""
    global _autoLoadRunning
    try:
        msg = runStkLoad()
        try:
            from telegram.telSend import sendTelegramMsg

            sendTelegramMsg(msg, pushKind="boot")
        except Exception as err:
            termLog(f"[stkload] 자동적재 결과 전송 실패: {err}")
    finally:
        with _autoLoadLock:
            _autoLoadRunning = False


def ensureStkMasterOnLogin():
    """
    로그인 성공 직후 호출.
    AI_STK_INFO가 비어 있으면 백그라운드로 최초 1회 적재.
    반환: 로그인 메시지에 붙일 한 줄 안내(없으면 빈 문자열)
    """
    global _autoLoadRunning

    try:
        cnt = countStkInfo()
    except Exception as err:
        termLog(f"[stkload] 캐시 건수 조회 실패: {err}")
        return f"종목마스터 확인 실패: {err}"

    if cnt > 0:
        termLog(f"[stkload] 캐시 이미 있음 ({cnt}건) — 자동 적재 생략")
        return f"종목마스터: 캐시 {cnt}건 유지 (자동 적재 생략)"

    with _autoLoadLock:
        if _autoLoadRunning:
            return "종목마스터: 자동 적재 진행 중…"
        _autoLoadRunning = True

    th = threading.Thread(
        target=_autoLoadWorker, name="stkAutoLoad", daemon=True
    )
    th.start()
    termLog("[stkload] DB 비어 있음 — 백그라운드 자동 적재 시작")
    return "종목마스터: 최초 적재 시작(백그라운드) — 완료 시 알림"


def handleStkLoad(rawParts=None):
    parts = list(rawParts or [])
    if len(parts) >= 2 and str(parts[1]).lower() in ("help", "?"):
        return _usage()

    if parts and str(parts[0]).lower() == "stkfind":
        return handleStkFind(parts)

    return runStkLoad(mrktTps=_normMrktArgs(parts))


def handleStkFind(rawParts=None):
    """stkfind {종목명} — DB 캐시 검색."""
    parts = list(rawParts or [])
    name = " ".join(parts[1:]).strip() if len(parts) >= 2 else ""
    if not name:
        return "사용법: stkfind {종목명} 예) stkfind 삼성"

    rows = findStkByName(name, limit=20)
    if not rows:
        got = resolveStkRef(name)
        return got.get("errMsg") or f"검색 결과 없음: {name}"

    lines = [f"[종목검색] '{name}' {len(rows)}건"]
    for r in rows:
        mkt = r.get("MARKET_NAME") or ""
        mid = f" / {mkt}" if mkt else ""
        lines.append(f"  {r.get('STK_CD')} {r.get('STK_NM')}{mid}")
    return "\n".join(lines)
