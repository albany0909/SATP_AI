"""
bt — 전략 백테스트 (한 줄 입력 즉시 실행)

bt list
bt hist [N]
bt status
bt stop
bt {전략ID} {종목} {시작일} {종료일} {투자금}
"""

import threading
from datetime import datetime, timedelta

from backtest.btSession import (
    beginBtRun,
    endBtRun,
    formatBtStatusMsg,
    getPendingJob,
    isBtBusy,
    isPendingLogin,
    requestBtAbort,
    setBtPhase,
    setPendingLogin,
    shouldAbortBt,
)
from backtest.engine import formatBtResult, runBacktest
from config import runtimeState
from strategies.specLoader import listStratSpecs, loadStratSpec, resolveStratRef
from telegram.commands.loginCommand import handleLogin
from telegram.telSend import sendTelegramMsg
from utils.btEnsureMinData import ensureMinBarsForBt
from utils.btRunStore import listBtHist, saveBtRun
from utils.stkArgParse import splitBtArgs
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog

_DEFAULT_MANWON = 10_000.0  # 기본 투자금 1억 = 10,000만원
_MANWON_TO_WON = 10_000.0
_DEFAULT_CASH = _DEFAULT_MANWON * _MANWON_TO_WON
_TG_MAX = 3500


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
    """
    투자금(만원 단위) 파싱 → 원(float).
    예) 10000 → 100,000,000원 / 실패 시 None
    """
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
    """원 금액을 'N만원 (원)' 표시."""
    wonVal = float(won or 0)
    manWon = wonVal / _MANWON_TO_WON
    return f"{manWon:,.0f}만원 ({wonVal:,.0f}원)"


def _usage():
    specs = listStratSpecs()
    if specs:
        ids = ", ".join(
            f"{s['seq']}:{s.get('algCd') or s['stratId']}" for s in specs
        )
    else:
        ids = "(스펙 없음)"
    return (
        "사용법:\n"
        "bt list — 전략 목록 (번호=시퀀스)\n"
        "bt hist [N] — 최근 시뮬 이력 (기본 5)\n"
        "bt status — 현재 작업 상태\n"
        "bt stop — 실행 중 중단 / 로그인 대기 취소\n"
        "bt yes paper|real — 미로그인 시 로그인 후 이어서 실행\n"
        "bt no — 로그인 대기 취소\n"
        "bt {번호|알고리즘코드} {종목코드|종목명} {시작일} {종료일} {투자금(만원)}\n"
        "예) bt 1 삼성전자 20260601 20260601 10000  ← 1억(10000만원)\n"
        "예) bt MHP01 005930 20260601 20260601 5000\n"
        f"등록 전략: {ids}\n"
        "※ 투자금은 만원 단위 (생략 시 10000만원=1억)\n"
        "※ 미로그인이면 로그인 여부를 물은 뒤 실행\n"
        "※ 분봉 없으면 자동 적재 후 시뮬"
    )


def _notify(msg):
    """진행/결과 텔레그램 전송 (실패해도 시뮬은 계속)."""
    text = str(msg or "")
    if len(text) > _TG_MAX:
        text = text[:_TG_MAX] + "\n…(생략)"
    termLog(text)
    try:
        sendTelegramMsg(text, pushKind="btVal")
    except Exception as err:
        termLog(f"[BT] 텔레그램 전송 실패: {err}")


def _handleList():
    specs = listStratSpecs()
    if not specs:
        return "[BT 전략목록] 등록된 스펙이 없습니다. strategies/specs/ 확인"
    lines = ["[BT 전략목록]"]
    for s in specs:
        lines.append(
            f"{s['seq']}. {s.get('name') or '-'} [{s.get('status') or '-'}]\n"
            f"   코드: {s.get('algCd') or '-'}"
        )
    lines.append("")
    lines.append("실행: bt {번호} {종목} {시작} {종료} {투자금(만원)}")
    lines.append("예) bt 1 삼성전자 20260601 20260601 10000")
    return "\n".join(lines)


def _handleHist(parts):
    lim = 5
    if len(parts) >= 3 and str(parts[2]).isdigit():
        lim = int(parts[2])
    got = listBtHist(lim)
    if not got.get("ok"):
        return f"[BT 이력실패] {got.get('errMsg')}"
    rows = got.get("rows") or []
    if not rows:
        return "[BT 이력] 저장된 실행이 없습니다."
    lines = [f"[BT 이력] 최근 {len(rows)}건"]
    for r in rows:
        created = r.get("CREATED_AT")
        createdStr = str(created)[:19] if created else "-"
        if r.get("STATUS") == "OK":
            lines.append(
                f"#{r['BT_RUN_ID']} {createdStr} OK "
                f"{r.get('ALG_CD') or r.get('STRAT_ID')} {r.get('STK_CD')} "
                f"{r.get('START_DT')}~{r.get('END_DT')} "
                f"거래{r.get('TRADE_CNT') or 0} "
                f"복리{float(r.get('FINAL_RET_PCT') or 0):+.2f}% "
                f"MDD{float(r.get('MAX_DD_PCT') or 0):.2f}%"
            )
        else:
            err = (r.get("ERR_MSG") or "")[:60]
            lines.append(
                f"#{r['BT_RUN_ID']} {createdStr} FAIL "
                f"{r.get('ALG_CD') or r.get('STRAT_ID')} {r.get('STK_CD')} — {err}"
            )
    return "\n".join(lines)


def _saveAndReport(result, initCash, prefixExtra=""):
    """결과/실패 저장 + 메시지 생성."""
    # 종목명·시장(코스피/코스닥) 보강 — 거래상세 선두 표시용
    if result.get("ok") and result.get("stkCd"):
        try:
            from utils.stratValidate import enrichStkMktMeta

            meta = enrichStkMktMeta(
                result.get("stkCd"),
                stkNm=result.get("stkNm"),
                marketName=result.get("marketName"),
            )
            result["stkNm"] = meta.get("stkNm")
            result["marketName"] = meta.get("mktLabel")
        except Exception:
            pass
    msg = formatBtResult(result)
    if prefixExtra:
        msg = prefixExtra + "\n" + msg
    saved = saveBtRun(result, resultMsg=msg, initCash=initCash)
    if saved.get("ok"):
        msg = f"{msg}\n\n이력ID: #{saved.get('btRunId')}"
    else:
        msg = f"{msg}\n\n[이력저장실패] {saved.get('errMsg')}"
    return msg


def _runBtWorker(stratId, stkCd, startDt, endDt, initCash):
    """백그라운드: 데이터 보장 → 시뮬 → 이력 → 텔레그램 리포트."""
    try:
        if str(stratId).startswith("maTouchScale"):
            warmDt = startDt
        else:
            warmDt = (
                datetime.strptime(startDt, "%Y%m%d") - timedelta(days=7)
            ).strftime("%Y%m%d")

        setBtPhase("ensure_data")
        ensured = ensureMinBarsForBt(
            stkCd,
            startDt,
            endDt,
            warmupStartDt=warmDt,
            ticScope="1",
            shouldAbort=shouldAbortBt,
            onProgress=lambda m: (setBtPhase("ensure_data", m), _notify(m)),
        )
        if not ensured.get("ok"):
            fail = {
                "ok": False,
                "errMsg": ensured.get("errMsg") or "데이터 준비 실패",
                "stratId": stratId,
                "stkCd": stkCd,
                "startDt": startDt,
                "endDt": endDt,
                "warmupStartDt": warmDt,
                "ticScope": "1",
                "barSource": "min",
            }
            _notify(_saveAndReport(fail, initCash))
            return

        if shouldAbortBt():
            fail = {
                "ok": False,
                "errMsg": "사용자 중단(bt stop)",
                "stratId": stratId,
                "stkCd": stkCd,
                "startDt": startDt,
                "endDt": endDt,
                "warmupStartDt": warmDt,
                "ticScope": "1",
                "barSource": "min",
            }
            _notify(_saveAndReport(fail, initCash))
            return

        setBtPhase("simulate", "[BT] 시뮬레이션 수행 중…")
        _notify("[BT] 시뮬레이션 수행 중…")

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

        setBtPhase("save")
        extra = ""
        if ensured.get("loaded"):
            extra = f"[BT] 분봉 자동적재 후 실행 (봉수≈{ensured.get('barCnt')})"
        msg = _saveAndReport(result, initCash, prefixExtra=extra)
        setBtPhase("done", msg)
        _notify(msg)
    except Exception as err:
        fail = {
            "ok": False,
            "errMsg": f"예외: {err}",
            "stratId": stratId,
            "stkCd": stkCd,
            "startDt": startDt,
            "endDt": endDt,
            "ticScope": "1",
            "barSource": "min",
        }
        _notify(_saveAndReport(fail, initCash))
    finally:
        endBtRun()


def _startBtThread(stratId, stkCd, startDt, endDt, initCash):
    """세션 running 전환 후 워커 스레드 시작. 반환: 접수 메시지."""
    if not beginBtRun(stratId, stkCd, startDt, endDt, initCash):
        return "[BT] 이미 실행 중입니다. bt status / bt stop"

    warmDt = (
        datetime.strptime(startDt, "%Y%m%d") - timedelta(days=7)
    ).strftime("%Y%m%d")
    ack = (
        "[BT 접수·실행]\n"
        f"전략: {stratId}\n"
        f"종목: {stkCd}\n"
        f"기간: {startDt} ~ {endDt}\n"
        f"워밍업: {warmDt}~\n"
        f"투자금: {_fmtCash(initCash)}\n"
        "진행: 데이터점검 → 시뮬 → 결과/이력\n"
        "상태: bt status / 중단: bt stop"
    )
    setBtPhase("queued", ack)

    th = threading.Thread(
        target=_runBtWorker,
        args=(stratId, stkCd, startDt, endDt, initCash),
        name="btWorker",
        daemon=True,
    )
    th.start()
    return ack


def _askLoginMsg(stratId, stkCd, startDt, endDt, initCash):
    return (
        "[BT] 로그인되어 있지 않습니다.\n"
        "분봉 자동적재가 필요할 수 있어 로그인이 필요합니다.\n"
        "\n"
        f"전략: {stratId}\n"
        f"종목: {stkCd}\n"
        f"기간: {startDt} ~ {endDt}\n"
        f"투자금: {_fmtCash(initCash)}\n"
        "\n"
        "로그인 후 이어서 실행할까요?\n"
        "bt yes paper — 모의투자 로그인 후 실행\n"
        "bt yes real — 실투자 로그인 후 실행\n"
        "bt no — 취소"
    )


def _handleYes(parts):
    """bt yes paper|real — 대기 중 작업 로그인 후 실행."""
    if not isPendingLogin():
        return "[BT] 로그인 확인 대기 중인 작업이 없습니다."

    mode = parts[2].strip().lower() if len(parts) >= 3 else ""
    if mode not in ("paper", "real"):
        return (
            "사용법: bt yes paper 또는 bt yes real\n"
            "취소: bt no"
        )

    job = getPendingJob()
    if not job:
        endBtRun()
        return "[BT] 대기 작업이 없어졌습니다. 다시 bt 명령으로 요청하세요."

    # pending 해제 후 로그인 — 실패 시 pending 복구
    endBtRun()
    loginMsg = handleLogin(mode)
    if not runtimeState.isLoggedIn():
        setPendingLogin(
            job["stratId"],
            job["stkCd"],
            job["startDt"],
            job["endDt"],
            job["initCash"],
        )
        return (
            f"{loginMsg}\n\n"
            "[BT] 로그인 실패 — 대기 유지 중\n"
            "bt yes paper | bt yes real | bt no"
        )

    runMsg = _startBtThread(
        job["stratId"],
        job["stkCd"],
        job["startDt"],
        job["endDt"],
        job["initCash"],
    )
    return f"{loginMsg}\n\n{runMsg}"


def _handleNo():
    """bt no — 로그인 대기 취소."""
    if not isPendingLogin():
        return "[BT] 취소할 대기 작업이 없습니다."
    job = getPendingJob() or {}
    endBtRun()
    return (
        "[BT] 취소되었습니다.\n"
        f"(대기 중이던 {job.get('stratId') or '-'} "
        f"{job.get('stkCd') or '-'} "
        f"{job.get('startDt') or '-'}~{job.get('endDt') or '-'})"
    )


def _runBt(stratId, stkCd, startDt, endDt, initCash):
    """미로그인이면 확인 대기, 아니면 즉시 백그라운드 실행."""
    if isBtBusy():
        if isPendingLogin():
            return (
                "[BT] 로그인 확인 대기 중입니다.\n"
                "bt yes paper | bt yes real | bt no\n"
                "또는 bt status"
            )
        return "[BT] 이미 실행 중입니다. bt status / bt stop"

    # 로그인 안 되어 있으면 진행 여부 확인
    if not runtimeState.isLoggedIn():
        if not setPendingLogin(stratId, stkCd, startDt, endDt, initCash):
            return "[BT] 다른 작업이 진행 중입니다."
        return _askLoginMsg(stratId, stkCd, startDt, endDt, initCash)

    return _startBtThread(stratId, stkCd, startDt, endDt, initCash)


def handleBt(rawParts=None):
    parts = list(rawParts or [])
    if len(parts) < 2:
        return _usage()

    sub = str(parts[1]).strip().lower()

    if sub == "list":
        return _handleList()
    if sub == "hist":
        return _handleHist(parts)
    if sub == "status":
        return formatBtStatusMsg()
    if sub == "stop":
        if isPendingLogin():
            requestBtAbort()
            return "[BT] 로그인 대기 취소됨"
        if requestBtAbort():
            return "[BT] 중단 요청됨 — 현재 단계 종료 후 멈춥니다."
        return "[BT] 실행 중인 작업이 없습니다."
    if sub == "yes":
        return _handleYes(parts)
    if sub == "no":
        return _handleNo()
    if sub == "help":
        return _usage()

    # bt {번호|전략ID} {종목코드|종목명...} {시작} {종료} {투자금}
    stratRef, stkRef, startRaw, endRaw, cashRaw = splitBtArgs(parts[1:])
    if not stratRef:
        return _usage()

    startDt = _normYmd(startRaw)
    endDt = _normYmd(endRaw)
    initCash = _DEFAULT_CASH
    if cashRaw is not None:
        parsed = _parseCash(cashRaw)
        if parsed is None:
            return f"투자금이 올바르지 않습니다: {cashRaw}\n{_usage()}"
        initCash = parsed

    if not stkRef or not startDt or not endDt:
        return _usage()
    if startDt > endDt:
        return "시작일이 종료일보다 클 수 없습니다."

    stratGot = resolveStratRef(stratRef)
    if not stratGot.get("ok"):
        return f"{stratGot.get('errMsg')}\n{_usage()}"
    stratId = stratGot["stratId"]
    if loadStratSpec(stratId) is None:
        return f"알 수 없는 전략ID: {stratId}\n{_usage()}"

    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        return resolved.get("errMsg")
    stkCd = resolved["stkCd"]
    stkLabel = formatStkLabel(stkCd, resolved.get("stkNm"))
    if resolved.get("fromName"):
        termLog(f"[bt] 종목명 해석: {stkRef} → {stkLabel}")
    if str(stratRef).isdigit():
        termLog(
            f"[bt] 전략번호 {stratRef} → {stratId} "
            f"({stratGot.get('name') or '-'})"
        )

    return _runBt(stratId, stkCd, startDt, endDt, initCash)
