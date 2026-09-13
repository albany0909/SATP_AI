"""login paper / login real / login renewal 명령 처리"""

import re

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
from realtime.wsHub import isHubRunning, restartWsHub
from utils.termLog import termLog


def _syncDeskTradeMode(mode):
    """
    로그인(신규·재사용) 후 매매창 설정과 맞춘다.
    재사용 경로에서 빠지면 재기동 시 deskTradeMode 가 어긋날 수 있다.
    """
    try:
        from config.settingsStore import setDeskTradeMode

        setDeskTradeMode(mode)
        if mode == "real":
            from utils.tradeSession import applyRealPaperOnlyOff

            applyRealPaperOnlyOff()
    except Exception as err:
        termLog(f"[login] deskTradeMode 동기화 실패: {err}")


def _reuseLoginMsg(mode, modeLabel):
    """이미 유효한 토큰이 있을 때 au10001 없이 안내만 만든다."""
    # 토큰을 안 고쳐도 설정 모드는 이 로그인 의도와 같게 둔다
    _syncDeskTradeMode(mode)
    expiresText = runtimeState.formatExpiresText(mode)
    status = runtimeState.loginStatusText()
    termLog(f"[{modeLabel}] 기존 토큰 재사용 (au10001 생략)")
    termLog(f"[{modeLabel}] 만료: {expiresText}")
    termLog(f"[세션] {status}")
    from telegram.commands.stkLoadCommand import ensureStkMasterOnLogin

    stkLine = ensureStkMasterOnLogin()
    lines = [
        f"[{modeLabel}] 로그인 유지 (기존 토큰 재사용)",
        f"만료: {expiresText}",
        f"세션: {status}",
        f"갱신 정책: 매일 {runtimeState.renewalTime} "
        f"또는 만료 {runtimeState.renewBeforeMin}분 전",
    ]
    if stkLine:
        lines.append(stkLine)
    return "\n".join(lines)


def handleLogin(mode, force=False):
    """
    실/모의 구분에 따라 키움 접근 토큰(au10001)을 발급하고 runtimeState에 보관한다.
    mode: "paper" | "real"
    force: True 이면 유효 토큰이 있어도 재발급 (기본 False — 429 방지)
    반환: 텔레그램에 보낼 결과 메시지 문자열
    """
    mode = (mode or "").strip().lower()

    # 모드별 호스트·앱키 선택 — 잘못된 값이면 즉시 안내
    if mode == "paper":
        hostUrl = paper_host_url
        appKey = paper_app_key
        appSecret = paper_app_secret
        modeLabel = "모의투자"
        already = runtimeState.isPaperLoggedIn()
    elif mode == "real":
        hostUrl = real_host_url
        appKey = real_app_key
        appSecret = real_app_secret
        modeLabel = "실투자"
        already = runtimeState.isRealLoggedIn()
    else:
        return (
            "사용법:\n"
            "login paper / login real\n"
            "login renewal {HH:MM}"
        )

    # 만료 여유 5분 이상 남으면 재발급하지 않음 (기동·중복 login 시 429 방지)
    if (
        not force
        and already
        and not runtimeState.needsRenewByExpiry(mode, beforeMin=5)
    ):
        return _reuseLoginMsg(mode, modeLabel)

    result = issueAccToken(hostUrl, appKey, appSecret)
    if not result.get("ok"):
        # 발급 실패해도 아직 쓸 수 있는 토큰이 있으면 유지 (429 등)
        if already and not runtimeState.needsRenewByExpiry(mode, beforeMin=0):
            termLog(
                f"[{modeLabel}] 토큰 재발급 실패 — 기존 토큰 유지: "
                f"{result.get('errMsg')}"
            )
            return _reuseLoginMsg(mode, modeLabel)
        failMsg = f"[{modeLabel}] 토큰 발급 실패: {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    token = result["token"]
    expiresDt = result.get("expiresDt", "")
    tokenType = result.get("tokenType", "bearer")

    # 해당 모드만 갱신 — 다른 모드 토큰·만료는 유지
    runtimeState.setLoginState(token, mode, expiresDt=expiresDt, tokenType=tokenType)
    # 매매창·주문 경로가 같은 모드를 쓰도록 설정 동기화
    _syncDeskTradeMode(mode)
    expiresText = runtimeState.formatExpiresText(mode)

    termLog(f"[{modeLabel}] 로그인 성공")
    termLog(f"[{modeLabel}] 토큰: {token}")
    termLog(f"[{modeLabel}] 만료: {expiresText}")
    status = runtimeState.loginStatusText()
    termLog(f"[세션] {status}")
    # 정책 안내 — 지금 갱신한다는 뜻이 아님
    termLog(
        f"[토큰갱신] 갱신 정책: 매일 {runtimeState.renewalTime} "
        f"또는 만료 {runtimeState.renewBeforeMin}분 전"
    )
    # 허브가 이미 떠 있을 때만 재연결 (기동 직후는 main이 startWsHub)
    if isHubRunning():
        restartWsHub()

    # 종목마스터 DB 비어 있으면 최초 1회 자동 적재 (백그라운드)
    from telegram.commands.stkLoadCommand import ensureStkMasterOnLogin

    stkLine = ensureStkMasterOnLogin()

    lines = [
        f"[{modeLabel}] 로그인 성공 (토큰 보관 완료)",
        f"만료: {expiresText}",
        f"세션: {status}",
        f"갱신 정책: 매일 {runtimeState.renewalTime} "
        f"또는 만료 {runtimeState.renewBeforeMin}분 전",
    ]
    if stkLine:
        lines.append(stkLine)
    return "\n".join(lines)


def parseRenewalTime(timeStr):
    """
    24시간제 시각 문자열을 HH:MM으로 정규화한다.
    예: '8:00', '08:00', '8:0' → '08:00'
    잘못된 형식이면 None.
    """
    raw = (timeStr or "").strip()
    matched = re.fullmatch(r"(\d{1,2}):(\d{1,2})", raw)
    if not matched:
        return None
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def handleLoginRenewal(timeStr):
    """
    토큰 자동 갱신 시각을 설정한다.
    - 현재 시각과 같으면(로그인된 경우) 즉시 갱신한다.
    - 이미 지난 시각이면 내일 해당 시각에 갱신된다고 안내한다.
    반환: 텔레그램에 보낼 결과 메시지 문자열
    """
    from datetime import datetime

    from utils.tokenRenewal import renewAccToken

    normalized = parseRenewalTime(timeStr)
    if not normalized:
        failMsg = "사용법: login renewal {HH:MM} 예) login renewal 8:00"
        termLog(failMsg)
        return failMsg

    runtimeState.setRenewalTime(normalized)
    nowHm = datetime.now().strftime("%H:%M")
    lines = [f"[토큰갱신] 갱신 시각을 {normalized}으로 설정했습니다."]

    if not runtimeState.isLoggedIn():
        lines.append("현재 미로그인 상태입니다. 먼저 login paper 또는 login real을 실행하세요.")
    elif normalized == nowHm:
        lines.append(
            f"현재 시각과 같아 즉시 갱신합니다. (대상: {runtimeState.loginStatusText()})"
        )
        termLog("\n".join(lines))
        renewAccToken()
        return "\n".join(lines)
    elif normalized < nowHm:
        lines.append(f"오늘 {normalized}은 이미 지났습니다. 내일 {normalized}에 갱신됩니다.")
        lines.append(f"갱신 대상: {runtimeState.loginStatusText()}")
        lines.append(
            f"갱신 정책: 만료 {runtimeState.renewBeforeMin}분 전에도 자동 갱신"
        )
    else:
        lines.append(f"오늘 {normalized}에 자동 갱신됩니다.")
        lines.append(f"갱신 대상: {runtimeState.loginStatusText()}")
        lines.append(
            f"갱신 정책: 만료 {runtimeState.renewBeforeMin}분 전에도 자동 갱신"
        )

    okMsg = "\n".join(lines)
    termLog(okMsg)
    return okMsg
