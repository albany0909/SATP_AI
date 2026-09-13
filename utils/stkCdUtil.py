"""종목코드 정규화 유틸"""


def normStkCd(raw):
    """
    종목코드를 정규화한다.
    길이가 7자리 이상이고 맨 앞 글자가 A(대소문자)이면 A를 제거한다.
    예: A005930 → 005930, a005930 → 005930, 005930 → 005930
    None/빈값이면 빈 문자열 반환.
    """
    code = "" if raw is None else str(raw).strip()
    if len(code) >= 7 and code[0] in ("A", "a"):
        return code[1:]
    return code


def padStkCd(raw, width=6):
    """원장·거래코드용 고정 자리 종목코드. 숫자는 0 패딩 (005930)."""
    code = normStkCd(raw)
    if not code:
        return ""
    w = int(width)
    if code.isdigit():
        return code.zfill(w)[-w:]
    return (code + (" " * w))[:w]
