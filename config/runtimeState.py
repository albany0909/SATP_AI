"""
프로그램 실행 중 유지하는 세션 상태.
모의·실투자 토큰은 서로 덮어쓰지 않고 각각 보관한다.
재시작 시 초기화되며 파일로 저장하지 않는다.
"""

from datetime import datetime

# 모의투자 / 실투자 접근 토큰 (미로그인 시 빈 문자열)
paperToken = ""
realToken = ""

# 토큰 만료 시각 원문(YYYYMMDDHHmmss) — au10001 expires_dt
paperExpiresDt = ""
realExpiresDt = ""

# 토큰 타입 (보통 bearer)
paperTokenType = ""
realTokenType = ""

# 매일 토큰 갱신 시각 (24시간제 HH:MM). 기본: 장 시작 1시간 전 08:00
renewalTime = "08:00"

# 만료 전 자동 갱신 여유(분) — expires_dt 기준
renewBeforeMin = 30


def setLoginState(token, mode, expiresDt="", tokenType="bearer"):
    """
    해당 모드의 토큰·만료·타입만 갱신한다. 다른 모드 값은 유지한다.
    mode: "paper" | "real"
    """
    global paperToken, realToken
    global paperExpiresDt, realExpiresDt
    global paperTokenType, realTokenType

    if mode == "paper":
        paperToken = token
        paperExpiresDt = expiresDt or ""
        paperTokenType = tokenType or "bearer"
    elif mode == "real":
        realToken = token
        realExpiresDt = expiresDt or ""
        realTokenType = tokenType or "bearer"
    else:
        raise ValueError(f"알 수 없는 모드: {mode}")


def getToken(mode):
    """모드에 해당하는 보관 토큰을 반환한다."""
    if mode == "paper":
        return paperToken
    if mode == "real":
        return realToken
    return ""


def getExpiresDt(mode):
    """모드별 expires_dt 원문(YYYYMMDDHHmmss) 반환."""
    if mode == "paper":
        return paperExpiresDt
    if mode == "real":
        return realExpiresDt
    return ""


def getExpiresDatetime(mode):
    """모드별 만료 datetime. 없거나 파싱 실패 시 None."""
    raw = getExpiresDt(mode)
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def needsRenewByExpiry(mode, beforeMin=None):
    """
    만료 시각 기준으로 지금 갱신이 필요한지.
    만료 정보가 없으면 False (시각 스케줄 갱신에 맡김).
    """
    margin = renewBeforeMin if beforeMin is None else beforeMin
    expAt = getExpiresDatetime(mode)
    if expAt is None:
        return False
    remainSec = (expAt - datetime.now()).total_seconds()
    return remainSec <= margin * 60


def clearLoginState(mode=None):
    """
    로그아웃. mode가 None이면 둘 다, "paper"/"real"이면 해당만 비운다.
    """
    global paperToken, realToken
    global paperExpiresDt, realExpiresDt
    global paperTokenType, realTokenType

    if mode is None or mode == "paper":
        paperToken = ""
        paperExpiresDt = ""
        paperTokenType = ""
    if mode is None or mode == "real":
        realToken = ""
        realExpiresDt = ""
        realTokenType = ""


def setRenewalTime(timeStr):
    """토큰 자동 갱신 시각(HH:MM)을 저장한다."""
    global renewalTime
    renewalTime = timeStr


def isPaperLoggedIn():
    """모의투자 토큰 보유 여부."""
    return bool(paperToken)


def isRealLoggedIn():
    """실투자 토큰 보유 여부."""
    return bool(realToken)


def isLoggedIn():
    """모의·실투자 중 하나라도 로그인되어 있는지."""
    return isPaperLoggedIn() or isRealLoggedIn()


def loggedInModes():
    """현재 로그인된 모드 목록 예: ['paper', 'real']"""
    modes = []
    if isPaperLoggedIn():
        modes.append("paper")
    if isRealLoggedIn():
        modes.append("real")
    return modes


def loginStatusText():
    """보관 중인 로그인 상태를 짧은 안내 문자열로 반환."""
    parts = []
    if isPaperLoggedIn():
        parts.append("모의")
    if isRealLoggedIn():
        parts.append("실투자")
    if not parts:
        return "미로그인"
    return "+".join(parts) + " 로그인 유지"


def formatExpiresText(mode):
    """만료 시각을 로그/응답용 문자열로. 없으면 '만료정보없음'."""
    expAt = getExpiresDatetime(mode)
    if expAt is None:
        return "만료정보없음"
    return expAt.strftime("%Y-%m-%d %H:%M:%S")
