"""
val — 전략 검증 (정적 + 관심종목 일괄 백테스트 게이트)

val list
val help
val status / val stop
val yes paper|real / val no
val {번호|전략ID} watch {시작} {종료} [투자금만원]
val static {번호|전략ID}
"""

import threading
from datetime import datetime, timedelta

from backtest.btSession import (
    beginBtRun,
    endBtRun,
    formatBtStatusMsg,
    isBtBusy,
    isPendingLogin,
    requestBtAbort,
    setBtPhase,
    setPendingLogin,
    shouldAbortBt,
)
from backtest.engine import runBacktest
from config import runtimeState
from config.config import paper_host_url, real_host_url
from strategies.specLoader import loadStratSpec
from telegram.commands.loginCommand import handleLogin
from telegram.telSend import sendTelegramMsg
from utils.btEnsureMinData import ensureMinBarsForBt
from utils.btRunStore import saveBtRun
from utils.highPullUniverse import listHighPullUniverse
from utils.stratValidate import (
    evaluateBtGates,
    formatStaticReport,
    formatValidateReport,
    listRegisteredStratsLine,
    resolveValidateStrat,
    runStaticChecks,
)
from utils.termLog import termLog
from utils.watchStkStore import listActiveWatch

_DEFAULT_MANWON = 10_000.0
_MANWON_TO_WON = 10_000.0
_DEFAULT_CASH = _DEFAULT_MANWON * _MANWON_TO_WON
_TG_MAX = 3500

# 로그인 대기 확장 메타 (watch 검증)
_valPendingMeta = None


def _normYmd(raw):
    text = str(raw or "").strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _parseCash(raw):
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip().replace(",", "").replace("원", "").replace("만", "")
    try:
        manWon = float(text)
    except ValueError:
        return None
    if manWon <= 0:
        return None
    return manWon * _MANWON_TO_WON


def _fmtCash(won):
    wonVal = float(won or 0)
    return f"{wonVal / _MANWON_TO_WON:,.0f}만원 ({wonVal:,.0f}원)"


def _notify(msg):
    text = str(msg or "")
    if len(text) > _TG_MAX:
        text = text[:_TG_MAX] + "\n…(생략)"
    termLog(text)
    try:
        sendTelegramMsg(text, pushKind="btVal")
    except Exception as err:
        termLog(f"[VAL] 텔레그램 실패: {err}")


def _usage():
    return (
        "사용법:\n"
        "val list — 전략 목록\n"
        "val static {번호|전략ID} — 정적 검증만\n"
        "val {번호|전략ID} watch {시작일} {종료일} [투자금만원]\n"
        "  예) val 1 watch 20260101 20260331 10000\n"
        "  예) val 2 watch 20260701 20260807 1000\n"
        "  · highPullback: 유니버스=ntl first 최초52주(+10%·시장) 선별 후 BT\n"
        "  · 그 외: 관심종목 STATUS=Y · USE_BT=Y\n"
        "val live [전략ID] — 모의 실전 청산 수익률 검증\n"
        "  예) val live / val live maTouchScale_v1\n"
        "val mfe [전략ID] — 1차 익절용 최대수익률(MFE) 통계 (val live 와 동일 리포트)\n"
        "val status / val stop\n"
        "val yes paper|real / val no — 미로그인 시\n"
        f"등록: {listRegisteredStratsLine()}\n"
        "※ 투자금 만원 단위 (기본 10000=1억)\n"
        "※ bt 와 동시 실행 불가"
    )


def _isHighPullStrat(stratId):
    return str(stratId or "").startswith("highPullback")


def _pickQuerySession():
    """모의 우선, 없으면 실전."""
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



def _handleList():
    from strategies.specLoader import listStratSpecs

    specs = listStratSpecs()
    if not specs:
        return "[VAL] 등록 전략 없음"
    lines = ["[VAL 전략목록]"]
    for s in specs:
        lines.append(
            f"{s['seq']}. {s.get('name') or '-'} [{s.get('status')}]\n"
            f"   코드: {s.get('algCd') or '-'}"
        )
    lines.append("")
    lines.append("검증: val {번호} watch {시작} {종료} [투자금]")
    return "\n".join(lines)


def _handleStatic(parts):
    if len(parts) < 3:
        return "사용법: val static {번호|전략ID}\n" + _usage()
    got = resolveValidateStrat(parts[2])
    if not got.get("ok"):
        return f"[VAL] {got.get('errMsg')}"
    staticRes = runStaticChecks(got["stratId"])
    return formatStaticReport(got["stratId"], staticRes)


def _runOneStkBt(stratId, stkCd, stkNm, startDt, endDt, initCash):
    """1종목: 분봉 보장 + 백테스트. 반환 결과 dict."""
    # 이평터치는 일봉 SMA 를 쓰므로 분봉 워밍업은 시뮬 기간이면 충분
    if str(stratId).startswith("maTouchScale"):
        warmDt = startDt
    else:
        warmDt = (
            datetime.strptime(startDt, "%Y%m%d") - timedelta(days=7)
        ).strftime("%Y%m%d")

    if shouldAbortBt():
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": "사용자 중단",
        }

    ensured = ensureMinBarsForBt(
        stkCd,
        startDt,
        endDt,
        warmupStartDt=warmDt,
        ticScope="1",
        shouldAbort=shouldAbortBt,
        onProgress=None,
    )
    if not ensured.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": ensured.get("errMsg") or "분봉 준비 실패",
        }

    if shouldAbortBt():
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": "사용자 중단",
        }

    result = runBacktest(
        stratId,
        stkCd,
        startDt,
        endDt,
        warmupStartDt=warmDt,
        ticScope="1",
        initCash=initCash,
        barSource="min",
    )
    result.setdefault("stratId", stratId)
    result.setdefault("stkCd", stkCd)
    result.setdefault("startDt", startDt)
    result.setdefault("endDt", endDt)
    result.setdefault("warmupStartDt", warmDt)

    # 이력 저장 (실패해도 검증은 계속)
    try:
        saveBtRun(result, initCash=initCash)
    except Exception as err:
        termLog(f"[VAL] 이력 저장 실패 {stkCd}: {err}")

    if not result.get("ok"):
        return {
            "ok": False,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "errMsg": result.get("errMsg") or "BT 실패",
        }

    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "stats": result.get("stats") or {},
        "barCnt": result.get("barCnt"),
        "trades": result.get("trades") or [],
        "rounds": result.get("rounds") or [],
        "scalePnl": result.get("scalePnl") or {},
    }


def _valWorker(stratId, stratName, startDt, endDt, initCash, watchRows, staticRes, universeLabel):
    """유니버스 순차 BT + 게이트 리포트. watchRows 가 None 이면 선별부터."""
    try:
        # highPullback: 배치 시작 시 유니버스 실시간 선별
        if watchRows is None and _isHighPullStrat(stratId):
            setBtPhase("universe", "ntl first 선별")
            session = _pickQuerySession()
            if not session:
                _notify("[VAL] 로그인 세션 없음 — highPull 유니버스 선별 불가")
                return
            mode, hostUrl, token, tokenType = session
            _notify(
                f"[VAL] highPull 유니버스 선별 중…\n"
                f"ntl first · 최초52주 · 등락≥10% · 코스피/코스닥\n"
                f"({mode})"
            )
            # 스펙 params 반영
            minFlu, volMa, volMult = 10.0, 20, 1.5
            spec = loadStratSpec(stratId) or {}
            params = spec.get("params") or {}
            try:
                minFlu = float(params.get("setupMinFluRt", minFlu))
                volMa = int(params.get("volMaPeriod", volMa))
                volMult = float(params.get("volMult", volMult))
            except (TypeError, ValueError):
                pass

            uni = listHighPullUniverse(
                hostUrl,
                token,
                tokenType=tokenType,
                isPaper=(mode == "paper"),
                minFluRt=minFlu,
                volMaPeriod=volMa,
                volMult=volMult,
                requireVol=True,
                requireSup=True,
                softIfNoDay=True,
                maxCand=100,
                maxOut=40,
                baseDt=endDt,
                shouldAbort=shouldAbortBt,
            )
            if not uni.get("ok"):
                _notify(f"[VAL] 유니버스 실패: {uni.get('errMsg')}")
                return
            watchRows = uni.get("rows") or []
            universeLabel = uni.get("label") or universeLabel
            universeLabel = (
                f"{universeLabel} · 선정 {len(watchRows)}종 "
                f"(first후보 {uni.get('firstCnt')})"
            )
            if watchRows:
                names = ", ".join(
                    f"{w.get('STK_NM')}({w.get('STK_CD')})" for w in watchRows[:15]
                )
                more = f" 외{len(watchRows)-15}" if len(watchRows) > 15 else ""
                _notify(
                    f"[VAL] 선정 완료 {len(watchRows)}종\n{names}{more}\n"
                    f"→ BT {startDt}~{endDt}"
                )
            else:
                _notify(
                    "[VAL] 선정 종목 0건 — 최초 52주+10% 코스피/코스닥 없음\n"
                    + (formatStaticReport(stratId, staticRes) if staticRes else "")
                )
                report = formatValidateReport(
                    stratId,
                    stratName,
                    staticRes,
                    evaluateBtGates([]),
                    startDt,
                    endDt,
                    initCash,
                    universeLabel=universeLabel + " · 0종",
                    stkResults=[],
                )
                _notify(formatStaticReport(stratId, staticRes) + "\n\n" + report)
                return

        if not watchRows:
            _notify("[VAL] 대상 종목 없음")
            return

        n = len(watchRows)
        _notify(
            f"[VAL] 배치 시작 {stratId}\n"
            f"종목 {n} · {startDt}~{endDt}\n"
            f"투자금 {_fmtCash(initCash)}\n"
            f"Universe: {universeLabel}"
        )
        setBtPhase("val_batch", f"0/{n}")

        stkResults = []
        for i, w in enumerate(watchRows):
            if shouldAbortBt():
                _notify("[VAL] 중단됨 (val stop / bt stop)")
                break
            stkCd = w.get("STK_CD") or ""
            stkNm = w.get("STK_NM") or stkCd
            setBtPhase("val_batch", f"{i + 1}/{n} {stkCd}")
            termLog(f"[VAL] ({i + 1}/{n}) {stkCd} 시뮬…")
            one = _runOneStkBt(
                stratId, stkCd, stkNm, startDt, endDt, initCash
            )
            one["marketName"] = w.get("MARKET_NAME") or ""
            one["mrktTp"] = w.get("MRKT_TP") or ""
            if not one.get("stkNm"):
                one["stkNm"] = stkNm
            # 포착일을 유니버스 선별일로 보강 (라운드 catchDt 가 매수일과 같을 때)
            refCatch = w.get("REF_TRADE_DT") or w.get("LOAD_DT")
            if refCatch and one.get("ok"):
                rounds = one.get("rounds") or []
                for rd in rounds:
                    # 셋업 포착일 우선 표시
                    if not rd.get("catchDt") or rd.get("catchDt") == rd.get("buyDt"):
                        rd["catchDt"] = refCatch
                one["rounds"] = rounds
            stkResults.append(one)

        gateRes = evaluateBtGates(stkResults)
        report = formatValidateReport(
            stratId,
            stratName,
            staticRes,
            gateRes,
            startDt,
            endDt,
            initCash,
            universeLabel=universeLabel,
            stkResults=stkResults,
        )
        staticBrief = formatStaticReport(stratId, staticRes)
        msg = staticBrief + "\n\n" + report
        setBtPhase("done", gateRes.get("verdict") or "")
        _notify(msg)
    except Exception as err:
        _notify(f"[VAL 예외] {err}")
    finally:
        endBtRun()


def _startValWatch(stratId, stratName, startDt, endDt, initCash, staticRes):
    """세션 lock 후 워커 기동."""
    highPull = _isHighPullStrat(stratId)

    if highPull:
        # 유니버스는 워커에서 선별 (로그인·API 필요)
        watchRows = None
        universeLabel = "ntl first 최초52주+등락≥10%·코스피/닥"
        nHint = "?"
        tag = "HIGH_PULL"
    else:
        watchRows = listActiveWatch(useBtOnly=True, mktTp="KR")
        if not watchRows:
            return (
                "[VAL] 관심종목(USE_BT=Y · 활성)이 없습니다.\n"
                "장마감 rank load 로 적재하거나 관심종목을 확인하세요."
            )
        universeLabel = f"watch {len(watchRows)}종 (USE_BT=Y)"
        nHint = str(len(watchRows))
        tag = f"WATCH:{len(watchRows)}"

    if not beginBtRun(stratId, tag, startDt, endDt, initCash):
        return "[VAL] 이미 bt/val 실행 중. val status / val stop"

    th = threading.Thread(
        target=_valWorker,
        args=(
            stratId,
            stratName,
            startDt,
            endDt,
            initCash,
            watchRows,
            staticRes,
            universeLabel,
        ),
        name="valWatchWorker",
        daemon=True,
    )
    th.start()
    if highPull:
        return (
            f"[VAL 접수·실행]\n"
            f"전략: {stratId} ({stratName})\n"
            f"유니버스: ntl first로 최초 52주 신고 선별 후 BT\n"
            f"  (등락≥10% · 코스피/코스닥 · 거래량/수급 가능 시 필터)\n"
            f"기간: {startDt} ~ {endDt}\n"
            f"투자금: {_fmtCash(initCash)}\n"
            f"Static: {staticRes.get('grade')}\n"
            "1) 선별 → 2) 종목별 시뮬 → 완료 시 Verdict\n"
            "상태: val status / 중단: val stop"
        )
    return (
        f"[VAL 접수·실행]\n"
        f"전략: {stratId} ({stratName})\n"
        f"유니버스: watch {nHint}종\n"
        f"기간: {startDt} ~ {endDt}\n"
        f"투자금: {_fmtCash(initCash)}\n"
        f"Static: {staticRes.get('grade')}\n"
        "완료 시 종합 Verdict 전송\n"
        "상태: val status / 중단: val stop"
    )


def _askLoginMsg(stratId, startDt, endDt, initCash, nWatch):
    uniHint = (
        "highPull 유니버스(ntl first)"
        if _isHighPullStrat(stratId)
        else f"watch {nWatch}종"
    )
    return (
        "[VAL] 로그인이 필요합니다 (분봉·유니버스 API).\n"
        f"전략: {stratId}\n"
        f"{uniHint} · {startDt}~{endDt}\n"
        f"투자금: {_fmtCash(initCash)}\n"
        "\n"
        "val yes paper — 모의 로그인 후 실행\n"
        "val yes real — 실투자 로그인 후 실행\n"
        "val no — 취소"
    )


def _handleYes(parts):
    global _valPendingMeta
    if not isPendingLogin() or not _valPendingMeta:
        return "[VAL] 로그인 대기 작업이 없습니다."

    mode = parts[2].strip().lower() if len(parts) >= 3 else ""
    if mode not in ("paper", "real"):
        return "사용법: val yes paper | val yes real"

    meta = dict(_valPendingMeta)
    endBtRun()
    _valPendingMeta = None

    loginMsg = handleLogin(mode)
    if not runtimeState.isLoggedIn():
        # 대기 복구
        setPendingLogin(
            meta["stratId"],
            "WATCH",
            meta["startDt"],
            meta["endDt"],
            meta["initCash"],
        )
        _valPendingMeta = meta
        return f"{loginMsg}\n\n[VAL] 로그인 실패 — 대기 유지"

    runMsg = _startValWatch(
        meta["stratId"],
        meta["stratName"],
        meta["startDt"],
        meta["endDt"],
        meta["initCash"],
        meta["staticRes"],
    )
    return f"{loginMsg}\n\n{runMsg}"


def _handleNo():
    global _valPendingMeta
    if not isPendingLogin() and not _valPendingMeta:
        return "[VAL] 취소할 대기 없음"
    endBtRun()
    _valPendingMeta = None
    return "[VAL] 취소되었습니다."


def _handleWatchRun(stratRef, startRaw, endRaw, cashRaw):
    global _valPendingMeta

    got = resolveValidateStrat(stratRef)
    if not got.get("ok"):
        return f"[VAL] {got.get('errMsg')}\n{_usage()}"

    startDt = _normYmd(startRaw)
    endDt = _normYmd(endRaw)
    if not startDt or not endDt:
        return f"날짜 형식 오류 (YYYYMMDD)\n{_usage()}"
    if startDt > endDt:
        return "시작일이 종료일보다 클 수 없습니다."

    initCash = _DEFAULT_CASH
    if cashRaw is not None:
        parsed = _parseCash(cashRaw)
        if parsed is None:
            return f"투자금 오류: {cashRaw}\n{_usage()}"
        initCash = parsed

    if isBtBusy():
        if isPendingLogin():
            return (
                "[VAL] 로그인 대기 중\n"
                "val yes paper | val yes real | val no"
            )
        return "[VAL] 이미 실행 중 (bt/val). val status / val stop"

    staticRes = runStaticChecks(got["stratId"])
    if staticRes.get("grade") == "FAIL":
        termLog(f"[VAL] 정적 FAIL — 배치 계속: {staticRes.get('miss')}")

    highPull = _isHighPullStrat(got["stratId"])

    if not highPull:
        watchRows = listActiveWatch(useBtOnly=True, mktTp="KR")
        if not watchRows:
            return (
                formatStaticReport(got["stratId"], staticRes)
                + "\n\n[VAL] 관심종목 없음 — watch 배치 스킵"
            )
        nWatch = len(watchRows)
    else:
        # highPull: 관심종목 불필요, API 선별
        nWatch = 0

    if not runtimeState.isLoggedIn():
        meta = {
            "stratId": got["stratId"],
            "stratName": got["name"],
            "startDt": startDt,
            "endDt": endDt,
            "initCash": initCash,
            "staticRes": staticRes,
        }
        if not setPendingLogin(
            got["stratId"], "WATCH", startDt, endDt, initCash
        ):
            return "[VAL] 세션 busy"
        _valPendingMeta = meta
        return _askLoginMsg(
            got["stratId"], startDt, endDt, initCash, nWatch
        )

    return _startValWatch(
        got["stratId"],
        got["name"],
        startDt,
        endDt,
        initCash,
        staticRes,
    )


def handleVal(rawParts=None):
    """
    val list | static | watch 검증 | status | stop | yes | no
    """
    global _valPendingMeta
    parts = list(rawParts or [])
    if len(parts) < 2:
        return _usage()

    sub = str(parts[1]).strip().lower()

    if sub in ("help", "?"):
        return _usage()
    if sub == "list":
        return _handleList()
    if sub == "static":
        return _handleStatic(parts)
    if sub in ("live", "mfe"):
        stratId = "MTS01"
        if len(parts) >= 3:
            got = resolveValidateStrat(parts[2])
            if got.get("ok"):
                stratId = got["stratId"]
            else:
                stratId = str(parts[2]).strip()
        from utils.stratLiveTradeStore import formatLiveTradeReport

        return formatLiveTradeReport(stratId=stratId)
    if sub == "status":
        return "[VAL/BT 상태]\n" + formatBtStatusMsg()
    if sub == "stop":
        if isPendingLogin():
            requestBtAbort()
            _valPendingMeta = None
            return "[VAL] 로그인 대기 취소"
        if requestBtAbort():
            return "[VAL] 중단 요청 — 현재 종목 종료 후 멈춤"
        return "[VAL] 실행 중 작업 없음"
    if sub == "yes":
        return _handleYes(parts)
    if sub == "no":
        return _handleNo()

    # val {strat} watch {start} {end} [cash]
    # parts: val, strat, watch, start, end, [cash]
    if len(parts) >= 5 and str(parts[2]).strip().lower() == "watch":
        cashRaw = parts[5] if len(parts) >= 6 else None
        return _handleWatchRun(parts[1], parts[3], parts[4], cashRaw)

    # val static 없이 짧은 static: val {strat} static — 이미 static 처리
    return _usage()
