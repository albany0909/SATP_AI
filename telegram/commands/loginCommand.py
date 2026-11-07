"""login paper / login real / login renewal 명령 처리"""

import os
import re
from pathlib import Path

from api.getToken import issueAccToken
from config.config import (
    paper_host_url,
    real_host_url,
)
from config import runtimeState
from realtime.wsHub import isHubRunning, restartWsHub
from utils.termLog import termLog


def _modeAppKeys(mode: str):
    """
    모드별 앱키 — config 모듈 상수(최초 import 스냅샷) 대신
    deskApiKeys.json / .env / 환경변수 / loginLocal 순으로 읽는다.
    """
    from config.loadEnv import ensureEnvLoaded, resolveEnvFilePaths
    from utils.localApiKeyStore import loadApiKeysFromEnv, secretEnvPath

    ensureEnvLoaded(force=True)
    keys = loadApiKeysFromEnv()

    def _pick(k):
        return str((keys or {}).get(k) or "").strip()

    def _fillFromDict(data: dict):
        if not isinstance(data, dict):
            return
        for k in (
            "realAppKey",
            "realAppSecret",
            "paperAppKey",
            "paperAppSecret",
        ):
            if not _pick(k):
                val = str(data.get(k) or "").strip()
                if val:
                    keys[k] = val

    # 1) 런처가 tradeApp 에 남긴 deskApiKeys.json (최우선 폴백)
    for path in _deskApiKeyPaths():
        try:
            if not path.is_file():
                continue
            import json

            _fillFromDict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue

    # 2) 로그인 런처 loginLocal.json
    if mode == "paper":
        need = ("paperAppKey", "paperAppSecret")
    else:
        need = ("realAppKey", "realAppSecret")
    if not all(_pick(k) for k in need):
        for path in _loginLocalKeyPaths():
            try:
                if not path.is_file():
                    continue
                import json

                _fillFromDict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue

    appKey = _pick("paperAppKey" if mode == "paper" else "realAppKey")
    appSecret = _pick("paperAppSecret" if mode == "paper" else "realAppSecret")
    # 진단(시크릿 미출력)
    try:
        envPaths = [str(p) for p in resolveEnvFilePaths()]
        termLog(
            f"[login] keys mode={mode} present={bool(appKey and appSecret)} "
            f"secretEnv={secretEnvPath()} cands={envPaths}"
        )
    except Exception:
        pass
    if mode == "paper":
        return paper_host_url, appKey, appSecret, "모의투자"
    return real_host_url, appKey, appSecret, "실투자"


def _deskApiKeyPaths():
    """런처가 기록한 deskApiKeys.json 후보."""
    from utils.appPaths import projectRoot

    out = []
    seen = set()

    def _add(p: Path):
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    root = projectRoot()
    _add(root / "config" / "data" / "deskApiKeys.json")
    if root.name.lower() == "tradeapp":
        _add(root.parent / "config" / "data" / "deskApiKeys.json")
    ir = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if ir:
        p = Path(ir)
        _add(p / "config" / "data" / "deskApiKeys.json")
        if p.name.lower() == "tradeapp":
            _add(p.parent / "config" / "data" / "deskApiKeys.json")
        else:
            _add(p / "tradeApp" / "config" / "data" / "deskApiKeys.json")
    return out


def _loginLocalKeyPaths():
    """앱키 폴백용 loginLocal.json 후보."""
    from utils.appPaths import projectRoot
    from utils.localApiKeyStore import secretEnvPath

    out = []
    seen = set()

    def _add(p: Path):
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    root = projectRoot()
    _add(root / "config" / "data" / "loginLocal.json")
    if root.name.lower() == "tradeapp":
        _add(root.parent / "config" / "data" / "loginLocal.json")
    try:
        envParent = secretEnvPath().parent
        _add(envParent / "config" / "data" / "loginLocal.json")
    except Exception:
        pass
    ir = str(os.environ.get("SATP_TRADE_APP_DIR") or "").strip()
    if ir:
        p = Path(ir)
        _add(p / "config" / "data" / "loginLocal.json")
        if p.name.lower() == "tradeapp":
            _add(p.parent / "config" / "data" / "loginLocal.json")
        else:
            _add(p / "tradeApp" / "config" / "data" / "loginLocal.json")
    return out



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
    if mode not in ("paper", "real"):
        return (
            "사용법:\n"
            "login paper / login real\n"
            "login renewal {HH:MM}"
        )
    hostUrl, appKey, appSecret, modeLabel = _modeAppKeys(mode)
    already = (
        runtimeState.isPaperLoggedIn()
        if mode == "paper"
        else runtimeState.isRealLoggedIn()
    )
    if not appKey or not appSecret:
        return (
            f"[{modeLabel}] 앱키가 없습니다. "
            "로그인창에서 앱키를 저장한 뒤 다시 실행하세요."
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
