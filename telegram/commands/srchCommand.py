"""srch {종목코드|종목명} — ka10001 주식기본정보 조회 명령"""

from api.getStkInfo import fetchStkInfo
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.stkCdUtil import normStkCd
from utils.stkResolve import resolveStkRef
from utils.termLog import termLog


def _pickQuerySession():
    if runtimeState.isPaperLoggedIn():
        return (
            "paper",
            paper_host_url,
            runtimeState.getToken("paper"),
            runtimeState.paperTokenType or "bearer",
        )
    if runtimeState.isRealLoggedIn():
        return (
            "real",
            real_host_url,
            runtimeState.getToken("real"),
            runtimeState.realTokenType or "bearer",
        )
    return None


def _val(body, key, default="-"):
    v = body.get(key)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip()


def formatStkInfoMsg(body, modeLabel):
    stkCdDisp = normStkCd(_val(body, "stk_cd")) or "-"
    lines = [
        f"[종목정보] ({modeLabel})",
        f"종목: {_val(body, 'stk_nm')} ({stkCdDisp})",
        f"현재가: {_val(body, 'cur_prc')} / 등락율: {_val(body, 'flu_rt')}%",
        f"전일대비: {_val(body, 'pred_pre')}",
        f"시가: {_val(body, 'open_pric')} / 고가: {_val(body, 'high_pric')} / 저가: {_val(body, 'low_pric')}",
        f"상한가: {_val(body, 'upl_pric')} / 하한가: {_val(body, 'Ist_pric')}",
        f"거래량: {_val(body, 'trde_qty')}",
        f"시가총액: {_val(body, 'mac')}억원",
        f"PER: {_val(body, 'per')} / PBR: {_val(body, 'pbr')} / ROE: {_val(body, 'roe')}",
        f"EPS: {_val(body, 'eps')} / BPS: {_val(body, 'bps')}",
        f"연중최고: {_val(body, 'oyr_hgst')} / 연중최저: {_val(body, 'oyr_lwst')}",
        f"외인소진률: {_val(body, 'for_exh_rt')}%",
    ]
    return "\n".join(lines)


def handleSrch(stkRef):
    """종목코드 또는 종목명으로 ka10001 조회."""
    raw = (stkRef or "").strip()
    if not raw:
        return "사용법: srch {종목코드|종목명} 예) srch 005930 / srch 삼성전자"

    resolved = resolveStkRef(raw)
    if not resolved.get("ok"):
        return resolved.get("errMsg")

    code = resolved["stkCd"]

    session = _pickQuerySession()
    if session is None:
        failMsg = "종목 조회 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"

    result = fetchStkInfo(hostUrl, token, code, tokenType=tokenType)
    if not result.get("ok"):
        failMsg = f"[종목정보] 조회 실패 ({code}): {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    msg = formatStkInfoMsg(result.get("body") or {}, modeLabel)
    termLog(msg)
    return msg
