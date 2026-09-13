"""텔레그램 인자에서 종목참조·날짜·수량 분리"""

import re

_YMD_RE = re.compile(r"^[\d.\-/]{8,10}$")


def looksLikeYmd(raw):
    text = str(raw or "").strip().replace("-", "").replace(".", "").replace("/", "")
    return len(text) == 8 and text.isdigit()


def splitBuySellArgs(partsAfterCmd):
    """
    buy/sell 인자: {종목참조...} {수량} [지정가]
    반환: (stkRef, qty, limitPrice|None) 또는 (None,None,None)
    """
    parts = [str(p) for p in (partsAfterCmd or []) if str(p).strip() != ""]
    if len(parts) < 2:
        return None, None, None

    def _isPosInt(t):
        s = str(t).strip().replace(",", "")
        return s.isdigit() and int(s) > 0

    if len(parts) >= 3 and _isPosInt(parts[-1]) and _isPosInt(parts[-2]):
        return " ".join(parts[:-2]), parts[-2], parts[-1]
    if _isPosInt(parts[-1]):
        return " ".join(parts[:-1]), parts[-1], None
    return None, None, None


def splitStkRefBeforeYmds(partsAfterCmd, ymdCnt=2):
    """
    dayprc 등: {종목참조...} {시작일} {종료일}
    ymdCnt개 날짜가 끝에 온다고 보고 앞을 종목참조로 합친다.
    반환: (stkRef, ymdList) 또는 (None, [])
    """
    parts = [str(p) for p in (partsAfterCmd or []) if str(p).strip() != ""]
    if len(parts) < ymdCnt + 1:
        return None, []

    ymds = parts[-ymdCnt:]
    if not all(looksLikeYmd(y) for y in ymds):
        return None, []
    stkRef = " ".join(parts[:-ymdCnt]).strip()
    if not stkRef:
        return None, []
    return stkRef, ymds


def splitMinTicArgs(partsAfterCmd):
    """
    minprc/ticprc: {종목참조...} {틱범위} {시작일} [종료일]
    틱범위는 숫자(분/틱).
    반환: (stkRef, ticScope, startDt, endDt|None)
    """
    parts = [str(p) for p in (partsAfterCmd or []) if str(p).strip() != ""]
    if len(parts) < 3:
        return None, None, None, None

    # 끝에서 날짜 1~2개
    if len(parts) >= 4 and looksLikeYmd(parts[-1]) and looksLikeYmd(parts[-2]):
        endDt = parts[-1]
        startDt = parts[-2]
        rest = parts[:-2]
    elif looksLikeYmd(parts[-1]):
        endDt = None
        startDt = parts[-1]
        rest = parts[:-1]
    else:
        return None, None, None, None

    if len(rest) < 2:
        return None, None, None, None

    ticScope = rest[-1]
    # 틱범위는 숫자 위주
    scopeClean = str(ticScope).strip().lower().replace("분", "").replace("틱", "")
    if not scopeClean.isdigit():
        return None, None, None, None

    stkRef = " ".join(rest[:-1]).strip()
    if not stkRef:
        return None, None, None, None
    return stkRef, ticScope, startDt, endDt


def splitBtArgs(partsAfterCmd):
    """
    bt 실행인자(list 등 제외): {전략ID} {종목참조...} {시작} {종료} [{투자금}]
    반환: (stratId, stkRef, startDt, endDt, cashRaw|None)
    """
    parts = [str(p) for p in (partsAfterCmd or []) if str(p).strip() != ""]
    if len(parts) < 4:
        return None, None, None, None, None

    stratId = parts[0]
    rest = parts[1:]

    cashRaw = None
    # 끝이 투자금(숫자, 8자리 날짜가 아님)이면 분리
    last = rest[-1].replace(",", "").replace("원", "")
    if last.replace(".", "", 1).isdigit() and not looksLikeYmd(rest[-1]):
        cashRaw = rest[-1]
        rest = rest[:-1]

    if len(rest) < 3:
        return None, None, None, None, None
    if not looksLikeYmd(rest[-1]) or not looksLikeYmd(rest[-2]):
        return None, None, None, None, None

    endDt = rest[-1]
    startDt = rest[-2]
    stkRef = " ".join(rest[:-2]).strip()
    if not stkRef:
        return None, None, None, None, None
    return stratId, stkRef, startDt, endDt, cashRaw
