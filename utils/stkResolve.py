"""종목코드/종목명 공통 해석"""

import re

from utils.stkCdUtil import normStkCd
from utils.stkInfoStore import findStkByCd, findStkByName

# 순수 코드 형태: 6자리 숫자 + 선택적 거래소 접미사
_CODE_RE = re.compile(r"^[A-Za-z]?\d{6}(_NX|_AL|_nx|_al)?$")


def looksLikeStkCd(raw):
    """입력이 종목코드 형태로 보이면 True."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return False
    return bool(_CODE_RE.match(text))


def parseExchSuffix(stkCd):
    """
    코드에서 거래소·순수코드 분리.
    반환: (dmstStexTp, cleanCd)  — KRX|NXT|SOR
    """
    code = normStkCd(stkCd)
    upper = code.upper()
    if upper.endswith("_NX"):
        return "NXT", code[:-3]
    if upper.endswith("_AL"):
        return "SOR", code[:-3]
    return "KRX", code


def resolveStkRef(raw):
    """
    종목코드 또는 종목명 → 정규 코드.
    반환:
      ok=True:  stkCd, stkNm, dmstStexTp, fromName
      ok=False: errMsg, matches?(다건)
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {"ok": False, "errMsg": "종목코드/종목명이 비어 있습니다."}

    # "005930 삼성전자" 처럼 코드+이름이 붙으면 앞 토큰만 코드로 본다
    firstTok = text.split()[0]
    if looksLikeStkCd(firstTok):
        text = firstTok

    # 코드 형태면 정규화 후 반환 (캐시에 이름 있으면 보강)
    if looksLikeStkCd(text):
        dmst, clean = parseExchSuffix(text)
        row = findStkByCd(clean)
        return {
            "ok": True,
            "stkCd": clean,
            "stkNm": (row or {}).get("STK_NM") or "",
            "dmstStexTp": dmst,
            "fromName": False,
            "exchSuffix": text[len(clean) :] if len(text) > len(clean) else "",
        }

    # 종목명 검색 — 거래소 접미사가 이름에 붙은 경우는 드묾
    name = text
    exchSuffix = ""
    upper = text.upper()
    if upper.endswith("_NX") or upper.endswith("_AL"):
        exchSuffix = text[-3:]
        name = text[:-3].strip()

    matches = findStkByName(name, limit=15)
    if not matches:
        return {
            "ok": False,
            "errMsg": (
                f"종목을 찾을 수 없습니다: {text}\n"
                "종목마스터가 없습니다. 로그인 후 자동 적재를 기다리거나\n"
                "stkload 로 강제 적재하세요.\n"
                "예) buy 005930 1 / buy 삼성전자 1"
            ),
            "matches": [],
        }

    exact = [m for m in matches if str(m.get("STK_NM") or "").strip() == name]
    if len(exact) == 1:
        pick = exact[0]
    elif len(exact) > 1:
        return _multiMsg(text, exact)
    elif len(matches) == 1:
        pick = matches[0]
    else:
        return _multiMsg(text, matches)

    clean = normStkCd(pick["STK_CD"])
    dmst = "KRX"
    if exchSuffix.upper() == "_NX":
        dmst = "NXT"
    elif exchSuffix.upper() == "_AL":
        dmst = "SOR"
    return {
        "ok": True,
        "stkCd": clean,
        "stkNm": pick.get("STK_NM") or name,
        "dmstStexTp": dmst,
        "fromName": True,
    }


def _multiMsg(text, matches):
    lines = [
        f"[종목모호] '{text}' 검색 결과가 {len(matches)}건입니다. 코드를 지정하세요.",
    ]
    for m in matches[:12]:
        mkt = m.get("MARKET_NAME") or ""
        mid = f" / {mkt}" if mkt else ""
        lines.append(f"  {m.get('STK_CD')} {m.get('STK_NM')}{mid}")
    if len(matches) > 12:
        lines.append(f"  … 외 {len(matches) - 12}건")
    return {"ok": False, "errMsg": "\n".join(lines), "matches": matches}


def formatStkLabel(stkCd, stkNm=""):
    """표시용 '종목명(코드)'."""
    code = normStkCd(stkCd)
    nm = (stkNm or "").strip()
    if nm:
        return f"{nm}({code})"
    return code
