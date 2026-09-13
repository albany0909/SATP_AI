"""로그인 후 토큰 자동 갱신 스케줄러 (지정 시각 + expires_dt 만료 전)"""

import threading
import time
from datetime import datetime

from api.getToken import issueAccToken
from config.config import (
    paper_app_key,
    paper_app_secret,
    paper_host_url,
    real_app_key,
    real_app_secret,
    real_host_url,
)
from config import runtimeState
from realtime.wsHub import restartWsHub
from telegram.telSend import sendTelegramMsg
from utils.termLog import termLog

# 스케줄러 중복 기동 방지
_schedulerStarted = False
# 같은 날 시각 스케줄 중복 갱신 방지용 마지막 일자(YYYY-MM-DD)
_lastRenewDate = ""
# 미로그인 스킵 안내를 분 단위로 1회만 출력하기 위한 키
_lastSkipKey = ""


def _modeLabel(mode):
    """mode → 표시용 한글 라벨."""
    if mode == "paper":
        return "모의투자"
    if mode == "real":
        return "실투자"
    return mode or "미설정"


def _hostCredByMode(mode):
    """모드에 맞는 hostUrl, appKey, appSecret 반환. 알 수 없으면 None."""
    if mode == "paper":
        return paper_host_url, paper_app_key, paper_app_secret
    if mode == "real":
        return real_host_url, real_app_key, real_app_secret
    return None


def markRenewDoneToday():
    """오늘 시각 스케줄 갱신을 완료한 것으로 표시한다."""
    global _lastRenewDate
    _lastRenewDate = datetime.now().strftime("%Y-%m-%d")


def renewOneMode(mode):
    """
    지정 모드 토큰만 재발급한다.
    반환: (성공여부, 결과메시지)
    """
    modeLabel = _modeLabel(mode)
    cred = _hostCredByMode(mode)
    if cred is None:
        return False, f"[{modeLabel}] 알 수 없는 모드"

    if not runtimeState.getToken(mode):
        return False, f"[{modeLabel}] 미로그인 — 갱신 건너뜀"

    hostUrl, appKey, appSecret = cred
    result = issueAccToken(hostUrl, appKey, appSecret)
    if not result.get("ok"):
        return False, f"[{modeLabel}] 토큰 갱신 실패: {result.get('errMsg')}"

    token = result["token"]
    expiresDt = result.get("expiresDt", "")
    tokenType = result.get("tokenType", "bearer")
    runtimeState.setLoginState(token, mode, expiresDt=expiresDt, tokenType=tokenType)
    expiresText = runtimeState.formatExpiresText(mode)

    termLog(f"[{modeLabel}] 토큰 갱신 성공")
    termLog(f"[{modeLabel}] 토큰: {token}")
    termLog(f"[{modeLabel}] 만료: {expiresText}")
    restartWsHub()
    return True, f"[{modeLabel}] 토큰 갱신 성공 (만료: {expiresText})"


def renewAccToken(modes=None, reportTelegram=True):
    """
    지정 모드(또는 로그인된 모든 모드) 토큰을 재발급한다.
    반환: 하나라도 성공하면 True
    """
    targetModes = modes if modes is not None else runtimeState.loggedInModes()
    if not targetModes:
        skipMsg = (
            "[토큰갱신] 미로그인 상태 — 갱신을 건너뜁니다. "
            "먼저 login paper 또는 login real을 실행하세요."
        )
        termLog(skipMsg)
        return False

    reportLines = []
    anyOk = False
    for mode in targetModes:
        ok, msg = renewOneMode(mode)
        reportLines.append(msg)
        if ok:
            anyOk = True
        else:
            termLog(msg)

    if reportTelegram and reportLines:
        try:
            sendTelegramMsg("\n".join(reportLines), pushKind="tokenRenew")
        except Exception as sendErr:
            termLog(f"텔레그램 전송 실패: {sendErr}")
    return anyOk


def _schedulerLoop():
    """
    1) 매일 renewalTime(기본 08:00)에 로그인된 전체 모드 갱신
    2) 각 모드 expires_dt 기준 만료 N분 전이면 해당 모드만 갱신
    """
    global _lastRenewDate, _lastSkipKey
    while True:
        try:
            now = datetime.now()
            curHm = now.strftime("%H:%M")
            curDate = now.strftime("%Y-%m-%d")
            target = runtimeState.renewalTime

            # --- 시각 스케줄 갱신 (하루 1회, 로그인된 전 모드) ---
            if curHm == target and curDate != _lastRenewDate:
                if runtimeState.isLoggedIn():
                    _lastRenewDate = curDate
                    termLog(f"[토큰갱신] 지정 시각 {target} 도달 — 전체 갱신")
                    renewAccToken()
                else:
                    skipKey = f"{curDate} {curHm}"
                    if skipKey != _lastSkipKey:
                        _lastSkipKey = skipKey
                        termLog(
                            f"[토큰갱신] {target} 도달 — 미로그인 상태라 건너뜁니다. "
                            "login paper/real 후 같은 분이면 재시도합니다."
                        )

            # --- 만료 임박 갱신 (모드별) ---
            expiryTargets = [
                mode
                for mode in runtimeState.loggedInModes()
                if runtimeState.needsRenewByExpiry(mode)
            ]
            if expiryTargets:
                # 실제 만료 임박으로 갱신을 실행할 때만 이 로그가 출력됨
                labels = ", ".join(_modeLabel(m) for m in expiryTargets)
                termLog(f"[토큰갱신] 만료 임박으로 갱신 실행 — 대상: {labels}")
                renewAccToken(modes=expiryTargets)

        except Exception as err:
            termLog(f"[토큰갱신] 스케줄러 오류: {err}")

        time.sleep(10)


def startRenewalScheduler():
    """데몬 스레드로 토큰 갱신 스케줄러를 한 번만 기동한다."""
    global _schedulerStarted
    if _schedulerStarted:
        return
    _schedulerStarted = True
    thr = threading.Thread(target=_schedulerLoop, name="tokenRenewal", daemon=True)
    thr.start()
    termLog(
        f"[토큰갱신] 스케줄러 시작 — 갱신 정책: "
        f"매일 {runtimeState.renewalTime} 또는 "
        f"만료 {runtimeState.renewBeforeMin}분 전"
    )
