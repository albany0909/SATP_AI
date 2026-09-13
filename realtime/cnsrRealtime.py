"""
국내주식 조건검색 실시간 (ka10173 / CNSRREQ, 해제 ka10174 / CNSRCLR).
목록(CNSRLST) 선행 후 seq 선택 → 초기 종목 + REAL type=02 편입/이탈 알림.
세션당 국내 실시간 조건검색 최대 10건.
"""

import threading
import time

from realtime.cnsrList import (
    fetchCnsrList,
    findCnsrItemBySeq,
    getLastCnsrItems,
    waitWsLoggedIn,
)
from realtime.wsHub import (
    getWsClient,
    registerMsgHandler,
    unregisterMsgHandler,
)
from telegram.telSend import sendTelegramMsg
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

TRNM_CNSRREQ = "CNSRREQ"
TRNM_CNSRCLR = "CNSRCLR"
REAL_TYPE_CNSR = "02"

# 세션당 국내 실시간 조건검색 한도
MAX_ACTIVE_CNSR = 10
# 초기 결과 텔레그램에 나열할 최대 종목 수
_MAX_LIST_DISP = 30

_stateLock = threading.RLock()
_reqLock = threading.Lock()
# seq → {name, stkCodes:set, stexTp}
_activeMap = {}
_HANDLER_NAME = "cnsrRealtime"


def _notify(msg):
    termLog(msg)
    try:
        sendTelegramMsg(msg, pushKind="cnsr")
    except Exception as err:
        termLog(f"[CNSR] 텔레그램 전송 실패: {err}")


def _ensureListLoaded():
    """조건식 캐시가 없으면 CNSRLST 선행 조회."""
    if getLastCnsrItems():
        return True, ""
    result = fetchCnsrList(timeoutSec=15)
    if not result.get("ok"):
        return False, result.get("errMsg") or "조건식 목록 조회 실패"
    return True, ""


def _parseJmCodes(data):
    """CNSRREQ 초기 data → 정규화 종목코드 리스트 (중복 제거, 최대 100)."""
    codes = []
    seen = set()
    if not isinstance(data, list):
        return codes
    for row in data:
        raw = ""
        if isinstance(row, dict):
            raw = row.get("jmcode") or row.get("9001") or ""
        elif isinstance(row, (list, tuple)) and row:
            raw = row[0]
        else:
            continue
        stkCd = normStkCd(raw)
        if not stkCd or stkCd in seen:
            continue
        seen.add(stkCd)
        codes.append(stkCd)
        if len(codes) >= 100:
            break
    return codes


def _formatCodePreview(codes):
    if not codes:
        return "(종목 없음)"
    show = codes[:_MAX_LIST_DISP]
    lines = [f"  {c}" for c in show]
    if len(codes) > _MAX_LIST_DISP:
        lines.append(f"  … 외 {len(codes) - _MAX_LIST_DISP}종목")
    return "\n".join(lines)


def _ensureRealtimeHandler():
    registerMsgHandler(_HANDLER_NAME, _onWsMessage)


def _maybeRemoveRealtimeHandler():
    with _stateLock:
        empty = len(_activeMap) == 0
    if empty:
        unregisterMsgHandler(_HANDLER_NAME)


def _onWsMessage(msg):
    """REAL type=02 조건검색 편입/이탈 처리."""
    if not isinstance(msg, dict):
        return
    if str(msg.get("trnm") or "") != "REAL":
        return

    dataList = msg.get("data") or []
    if not isinstance(dataList, list):
        return

    for row in dataList:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "") != REAL_TYPE_CNSR:
            continue

        values = row.get("values") or {}
        if not isinstance(values, dict):
            values = {}

        seq = str(values.get("841") or "").strip()
        stkCd = normStkCd(values.get("9001") or row.get("item"))
        action = str(values.get("843") or "").strip().upper()
        if not seq or not stkCd or action not in ("I", "D"):
            continue

        with _stateLock:
            info = _activeMap.get(seq)
            if info is None:
                continue
            name = info.get("name") or seq
            stkSet = info["stkCodes"]
            if action == "I":
                stkSet.add(stkCd)
                tag = "편입"
            else:
                stkSet.discard(stkCd)
                tag = "이탈"
            cnt = len(stkSet)

        _notify(
            f"[조건실시간] {tag}\n"
            f"조건: {seq}. {name}\n"
            f"종목: {stkCd}\n"
            f"현재편입: {cnt}종목"
        )


def getCnsrRealtimeStatus():
    """활성 실시간 조건검색 요약 텍스트."""
    with _stateLock:
        if not _activeMap:
            return "실시간 조건검색: 없음"
        lines = [f"실시간 조건검색: {len(_activeMap)}건 (한도 {MAX_ACTIVE_CNSR})"]
        for seq in sorted(_activeMap.keys(), key=lambda x: int(x) if str(x).isdigit() else x):
            info = _activeMap[seq]
            lines.append(
                f"  {seq}. {info.get('name') or '-'} "
                f"— {len(info.get('stkCodes') or [])}종목"
            )
        return "\n".join(lines)


def startCnsrRealtime(seq, stexTp="K", searchType="1", timeoutSec=20):
    """
    조건식 seq에 대해 CNSRREQ(search_type=1) 등록.
    반환: (성공여부, 메시지)
    """
    seq = str(seq or "").strip()
    if not seq:
        return False, "조건식 일련번호(seq)가 필요합니다. 예) cnsr start 114"

    okList, errList = _ensureListLoaded()
    if not okList:
        return False, f"조건식 목록 선행 조회 실패: {errList}"

    item = findCnsrItemBySeq(seq)
    if item is None:
        return (
            False,
            f"seq={seq} 조건식을 목록에서 찾지 못했습니다. "
            f"먼저 cnsr 로 목록을 확인하세요.",
        )
    cnsrName = item.get("name") or seq

    with _stateLock:
        if seq in _activeMap:
            return False, f"이미 실시간 감시 중: {seq}. {cnsrName}"
        if len(_activeMap) >= MAX_ACTIVE_CNSR:
            return (
                False,
                f"실시간 조건검색 한도 초과 (최대 {MAX_ACTIVE_CNSR}건). "
                f"cnsr stop 으로 해제한 뒤 다시 시도하세요.",
            )

    if not _reqLock.acquire(blocking=False):
        return False, "조건검색 요청이 이미 진행 중입니다."

    doneEvent = threading.Event()
    resultBox = {"msg": None}
    waitName = f"cnsrReqWait_{seq}"

    def _onReqMsg(msg):
        if not isinstance(msg, dict):
            return
        if str(msg.get("trnm") or "") != TRNM_CNSRREQ:
            return
        # 응답 seq가 있으면 일치 확인
        respSeq = str(msg.get("seq") or "").strip()
        if respSeq and respSeq != seq:
            return
        resultBox["msg"] = msg
        doneEvent.set()

    try:
        registerMsgHandler(waitName, _onReqMsg)
        _ensureRealtimeHandler()

        ws = waitWsLoggedIn(min(10, timeoutSec))
        if ws is None:
            return False, "웹소켓 LOGIN 대기 시간 초과"

        payload = {
            "trnm": TRNM_CNSRREQ,
            "seq": seq,
            "search_type": str(searchType),
            "stex_tp": str(stexTp),
        }
        if not ws.sendJson(payload):
            return False, "CNSRREQ 전송 실패"

        termLog(f"[CNSRREQ] 실시간 요청 seq={seq} name={cnsrName}")
        if not doneEvent.wait(timeout=float(timeoutSec)):
            return False, f"CNSRREQ 응답 대기 시간 초과 ({timeoutSec}초)"

        msg = resultBox["msg"] or {}
        try:
            codeOk = int(msg.get("return_code", 1)) == 0
        except (TypeError, ValueError):
            codeOk = isinstance(msg.get("data"), list)

        if not codeOk:
            err = msg.get("return_msg") or "조건검색 실시간 등록 실패"
            return False, f"[{msg.get('return_code')}] {err}"

        codes = _parseJmCodes(msg.get("data"))
        with _stateLock:
            _activeMap[seq] = {
                "name": cnsrName,
                "stkCodes": set(codes),
                "stexTp": stexTp,
            }

        preview = _formatCodePreview(codes)
        okMsg = (
            f"[조건실시간] 시작\n"
            f"조건: {seq}. {cnsrName}\n"
            f"거래소: {stexTp} / 초기편입: {len(codes)}종목\n"
            f"{preview}\n"
            f"(편입·이탈 시 실시간 알림)"
        )
        termLog(okMsg)
        return True, okMsg
    finally:
        unregisterMsgHandler(waitName)
        _reqLock.release()
        _maybeRemoveRealtimeHandler()


def stopCnsrRealtime(seq=None, timeoutSec=10):
    """
    실시간 조건검색 해제(CNSRCLR).
    seq=None 또는 'all' 이면 활성 전부 해제.
    반환: (성공여부, 메시지)
    """
    with _stateLock:
        if not _activeMap:
            return False, "실행 중인 실시간 조건검색이 없습니다."

        if seq is None or str(seq).strip().lower() in ("", "all", "전부"):
            targets = list(_activeMap.keys())
        else:
            targets = [str(seq).strip()]
            if targets[0] not in _activeMap:
                return False, f"seq={targets[0]} 은 실시간 감시 중이 아닙니다."

    ws = waitWsLoggedIn(min(10, timeoutSec))
    if ws is None:
        return False, "웹소켓 LOGIN 대기 시간 초과"

    lines = []
    anyOk = False
    for s in targets:
        with _stateLock:
            info = _activeMap.get(s)
            name = (info or {}).get("name") or s
        sent = ws.sendJson({"trnm": TRNM_CNSRCLR, "seq": s})
        # 로컬 상태는 전송 시도 후 정리 (서버 해제 실패해도 재시도 가능하도록 유지할 수도 있으나
        # 사용자 중지 의도면 로컬도 해제)
        with _stateLock:
            _activeMap.pop(s, None)
        if sent:
            anyOk = True
            lines.append(f"해제: {s}. {name}")
            termLog(f"[CNSRCLR] seq={s}")
        else:
            lines.append(f"해제전송실패: {s}. {name}")

    _maybeRemoveRealtimeHandler()
    msg = "[조건실시간] 중지\n" + "\n".join(lines)
    return anyOk, msg
