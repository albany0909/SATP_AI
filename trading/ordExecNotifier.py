"""
웹소켓 00(주문체결) 수신 시 콘솔에 남긴다.
계좌 단위 — 종목코드 REG 없이 type=00 + item='' 등록.
장시간(notify) 내에서만 구독. 장마감 시 REMOVE 후 종료, 장 시작 시 재등록.
매매·구독은 텔레그램 푸시하지 않는다.
"""

import json
import threading
import time

from api.getAcntEvlt import parsePadNum
from config.settingsStore import getNotifyHours, isNotifySessionNow
from config import runtimeState
from realtime.wsHub import (
    getWsClient,
    registerLoginHandler,
    registerMsgHandler,
    unregisterLoginHandler,
    unregisterMsgHandler,
)
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_HANDLER_NAME = "ordExec00"
_LOGIN_HANDLER_NAME = "ordExec00Login"
# 동일 체결번호 중복 알림 방지 (최근 N건 유지)
_seenFillKeys = []
_seenFillMax = 200

# 장중 구독·알림 활성 여부
_isNotifyEnabled = False
_mktWatchStarted = False
_stateLock = threading.Lock()


def _modeLabel():
    if runtimeState.isPaperLoggedIn():
        return "모의투자"
    if runtimeState.isRealLoggedIn():
        return "실투자"
    return "미로그인"


def _isMktSessionNow():
    """
    체결알림 가동 구간: 거래일(주말·공휴일 제외) + notify 08:00~20:00.
    정규 mkhr(STLS용)과 별개.
    """
    return isNotifySessionNow()


def _firstNonEmpty(values, keys):
    for key in keys:
        raw = values.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _parseIntField(values, keys):
    raw = _firstNonEmpty(values, keys)
    if not raw:
        return None
    num = parsePadNum(raw)
    if num is None:
        try:
            num = int(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    return int(num)


def _parsePrice(values, keys):
    num = _parseIntField(values, keys)
    if num is None:
        return None
    return abs(int(num))


def _isBuySide(values):
    """905(+매수) 또는 907(2=매수) 기준."""
    side905 = _firstNonEmpty(values, ["905"])
    if side905.startswith("+") or "매수" in side905:
        return True
    if "매도" in side905 or side905.startswith("-"):
        return False
    side907 = _firstNonEmpty(values, ["907"])
    if side907 == "2":
        return True
    if side907 == "1":
        return False
    return None


def _rememberFill(key):
    if not key:
        return False
    if key in _seenFillKeys:
        return False
    _seenFillKeys.append(key)
    if len(_seenFillKeys) > _seenFillMax:
        del _seenFillKeys[: len(_seenFillKeys) - _seenFillMax]
    return True


def _formatOrdExecMsg(stkCd, stkNm, sideLabel, fillQty, fillPrc, ordNo, ordSts, unfilledQty):
    doneTag = "완료" if unfilledQty is not None and unfilledQty <= 0 else "체결"
    title = f"[{sideLabel}{doneTag}]"
    lines = [
        f"{title} ({_modeLabel()})",
        f"종목: {stkCd}" + (f" ({stkNm})" if stkNm else ""),
        f"체결: {fillQty:,}주 @ {fillPrc:,}원",
    ]
    if ordNo:
        lines.append(f"주문번호: {ordNo}")
    if ordSts:
        lines.append(f"상태: {ordSts}")
    if unfilledQty is not None and unfilledQty > 0:
        lines.append(f"미체결: {unfilledQty:,}주")
    return "\n".join(lines)


def _regOrdExec(ws):
    """
    주문체결(00) 실시간 등록.
    종목코드는 불필요 — 04 잔고와 같이 item 빈 문자열로 계좌 단위 구독.
    """
    if ws is None:
        return False
    ok = ws.sendReg(["00"], [""], grpNo="1", refresh="1")
    if ok:
        termLog("[체결알림] 00 주문체결 REG 전송 (계좌 단위)")
    else:
        termLog("[체결알림] 00 REG 전송 실패")
    return ok


def _unregOrdExec(ws):
    """장마감 등 — 00 구독 해제."""
    if ws is None:
        return False
    ok = ws.sendRemove(["00"], [""], grpNo="1")
    if ok:
        termLog("[체결알림] 00 주문체결 REMOVE (장마감/비활성)")
    return ok


def _enableNotify(reason="장시작"):
    """장중 알림·00 구독 활성."""
    global _isNotifyEnabled
    with _stateLock:
        if _isNotifyEnabled:
            return
        _isNotifyEnabled = True
    ws = getWsClient()
    if ws is not None and ws.isLoggedIn:
        _regOrdExec(ws)
    openHm, closeHm = getNotifyHours()
    termLog(f"[체결알림] {reason} — 수신 시작 ({openHm}~{closeHm})")


def _disableNotify(reason="장마감"):
    """장마감 — 알림 중지 및 00 구독 해제."""
    global _isNotifyEnabled
    with _stateLock:
        if not _isNotifyEnabled:
            return
        _isNotifyEnabled = False
    ws = getWsClient()
    _unregOrdExec(ws)
    termLog(f"[체결알림] {reason} — 수신 종료")


def _onLogin(ws):
    # 장중에만 00 REG — 장외 LOGIN 시 구독하지 않음
    if _isNotifyEnabled and _isMktSessionNow():
        _regOrdExec(ws)


def _onWsMessage(msg):
    trnm = str(msg.get("trnm") or "")

    # REG 응답 확인 로그
    if trnm == "REG":
        termLog(f"[체결알림] REG 응답: {msg.get('return_code')} {msg.get('return_msg') or ''}")
        return

    # 장외·비활성 — 체결 알림 생략 (WS 허브/다른 핸들러는 유지)
    if not _isNotifyEnabled or not _isMktSessionNow():
        return

    if trnm != "REAL":
        return

    dataList = msg.get("data") or []
    if not isinstance(dataList, list):
        return

    for row in dataList:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "") != "00":
            continue

        values = row.get("values") or {}
        if not isinstance(values, dict):
            values = {}

        # 수신 확인용 — FID 키가 명세와 다를 수 있어 원문 키를 남긴다
        try:
            preview = json.dumps(values, ensure_ascii=False)
        except Exception:
            preview = str(values)
        termLog(f"[체결알림] 00 수신 values={preview}")

        # 주식 주문만 (선물·잔고 통보 제외). 빈 값이면 통과
        bizTp = _firstNonEmpty(values, ["912"])
        if bizTp and bizTp not in ("JJ", ""):
            continue

        fillQty = _parseIntField(values, ["911", "915", "921"])
        ordSts = _firstNonEmpty(values, ["913"])

        # 체결량 없거나 0이면 접수/확인만 — 알림 생략
        if fillQty is None or fillQty <= 0:
            continue
        # 상태가 있으면 체결만 (접수·확인 스킵). 상태 필드를 못 받으면 체결량 기준으로 알림
        if ordSts and ("체결" not in ordSts):
            continue

        stkCd = normStkCd(_firstNonEmpty(values, ["9001"]) or row.get("item"))
        stkNm = _firstNonEmpty(values, ["302"])
        fillPrc = _parsePrice(values, ["910", "914", "920"])
        ordNo = _firstNonEmpty(values, ["9203", "904"])
        fillNo = _firstNonEmpty(values, ["909"])
        unfilledQty = _parseIntField(values, ["902"])

        if fillPrc is None:
            fillPrc = 0

        dedupeKey = f"{ordNo}|{fillNo}|{stkCd}|{fillQty}|{fillPrc}"
        if not _rememberFill(dedupeKey):
            continue

        buySide = _isBuySide(values)
        if buySide is True:
            sideLabel = "매수"
        elif buySide is False:
            sideLabel = "매도"
        else:
            sideLabel = "주문"

        # 영웅문 실현손익용 — 00 FID 938 수수료 / 939 세금
        try:
            from utils.heroPnl import parseFillFees

            fillCmsn, fillTax = parseFillFees(values)
        except Exception:
            fillCmsn, fillTax = 0, 0

        text = _formatOrdExecMsg(
            stkCd, stkNm, sideLabel, fillQty, fillPrc, ordNo, ordSts, unfilledQty
        )
        termLog(text)
        try:
            from utils.tradeLogHook import emitFillLog

            emitFillLog(
                sideLabel,
                stkCd,
                stkNm,
                fillQty,
                fillPrc,
                ordNo,
                cmsn=fillCmsn,
                tax=fillTax,
            )
        except Exception:
            pass


def _mktWatchLoop():
    """
    장 시작/마감 경계를 감시해 체결알림을 켜고 끈다.
    1초 간격 폴링 — mkhr 변경도 다음 틱에 반영.
    """
    wasOpen = None
    while True:
        try:
            nowOpen = _isMktSessionNow()
            if wasOpen is None:
                # 기동 직후 현재 구간에 맞춤
                if nowOpen:
                    _enableNotify("기동(장중)")
                else:
                    _disableNotify("기동(장외)")
            elif nowOpen and not wasOpen:
                _enableNotify("장시작")
            elif (not nowOpen) and wasOpen:
                _disableNotify("장마감")
            wasOpen = nowOpen
        except Exception as err:
            termLog(f"[체결알림] 장시간 감시 오류: {err}")
        time.sleep(1)


def startOrdExecNotifier():
    """00 체결 알림 핸들러·LOGIN 훅·장시간 감시 기동."""
    global _mktWatchStarted

    registerMsgHandler(_HANDLER_NAME, _onWsMessage)
    registerLoginHandler(_LOGIN_HANDLER_NAME, _onLogin)

    if not _mktWatchStarted:
        _mktWatchStarted = True
        thr = threading.Thread(
            target=_mktWatchLoop, name="ordExecMktWatch", daemon=True
        )
        thr.start()
        openHm, closeHm = getNotifyHours()
        termLog(
            f"[체결알림] 알림 감시 시작 — 거래일 {openHm}~{closeHm} "
            f"(주말·공휴일 제외)"
        )

    # 현재가 장중이면 즉시 활성
    if _isMktSessionNow():
        _enableNotify("기동(장중)")
    else:
        termLog("[체결알림] 현재 장외 — 장 시작 시 자동 등록")


def stopOrdExecNotifier():
    """체결알림 완전 중지 (프로그램 종료 등)."""
    _disableNotify("수동중지")
    unregisterMsgHandler(_HANDLER_NAME)
    unregisterLoginHandler(_LOGIN_HANDLER_NAME)
