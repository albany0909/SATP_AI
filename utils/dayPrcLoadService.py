"""
일별주가(ka10086) API 조회 → AI_DAY_STK_PRC 적재.

텔레그램 dayprc / 웹 차트 공용.
웹 서버 프로세스에서도 .env 앱키로 토큰 발급 가능.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
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
from utils.dayStkPrcStore import upsertDayStkPrcRows
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog

# 프로세스 내 발급 토큰 캐시 (웹 서버용)
_sessCache = {
    "mode": "",
    "token": "",
    "tokenType": "bearer",
    "hostUrl": "",
    "expiresDt": "",
}


def _tokenStillValid(expiresDt, marginMin=5):
    if not expiresDt:
        return True  # 만료 정보 없으면 사용 시도
    try:
        exp = datetime.strptime(str(expiresDt).strip(), "%Y%m%d%H%M%S")
    except ValueError:
        return True
    return (exp - datetime.now()).total_seconds() > marginMin * 60


def acquireKiwoomSession(preferMode=None):
    """
    키움 REST 세션 (mode, hostUrl, token, tokenType).
    1) runtime 로그인 상태  2) 모듈 캐시  3) .env 앱키로 발급
    preferMode: paper|real|None
    None 이면 settings deskTradeMode 우선 (매매창·기동 모드와 맞춤).
    """
    order = []
    wantMode = None
    if preferMode in ("paper", "real"):
        wantMode = preferMode
        order = [preferMode]
        order.append("real" if preferMode == "paper" else "paper")
    else:
        # 명시 prefer 없으면 매매 모드를 먼저 — 예전처럼 무조건 모의 발급하지 않음
        deskMode = "paper"
        try:
            from config.settingsStore import getDeskTradeMode

            deskMode = getDeskTradeMode()
        except Exception:
            deskMode = "paper"
        if deskMode not in ("paper", "real"):
            deskMode = "paper"
        wantMode = deskMode
        order = [deskMode]
        order.append("real" if deskMode == "paper" else "paper")

    for mode in order:
        if mode == "paper" and runtimeState.isPaperLoggedIn():
            return (
                "paper",
                paper_host_url,
                runtimeState.getToken("paper"),
                runtimeState.paperTokenType or "bearer",
            )
        if mode == "real" and runtimeState.isRealLoggedIn():
            return (
                "real",
                real_host_url,
                runtimeState.getToken("real"),
                runtimeState.realTokenType or "bearer",
            )

    # 캐시 — 원하는 모드와 다르면 버리고 재발급
    if _sessCache["token"] and _tokenStillValid(_sessCache.get("expiresDt")):
        if wantMode and _sessCache["mode"] != wantMode:
            pass
        else:
            return (
                _sessCache["mode"],
                _sessCache["hostUrl"],
                _sessCache["token"],
                _sessCache["tokenType"],
            )

    # .env 발급
    for mode in order:
        if mode == "paper":
            appKey, appSec, host = paper_app_key, paper_app_secret, paper_host_url
        else:
            appKey, appSec, host = real_app_key, real_app_secret, real_host_url
        if not appKey or not appSec:
            continue
        issued = issueAccToken(host, appKey, appSec)
        if not issued.get("ok"):
            termLog(f"[dayPrcLoad] {mode} 토큰 실패: {issued.get('errMsg')}")
            continue
        tok = issued["token"]
        tokType = issued.get("tokenType") or "bearer"
        expDt = issued.get("expiresDt") or ""
        _sessCache["mode"] = mode
        _sessCache["token"] = tok
        _sessCache["tokenType"] = tokType
        _sessCache["hostUrl"] = host
        _sessCache["expiresDt"] = expDt
        # runtime 에도 넣어 이후 handleLogin/감시가 au10001 을 또 치지 않게 함
        try:
            runtimeState.setLoginState(tok, mode, expiresDt=expDt, tokenType=tokType)
        except Exception as err:
            termLog(f"[dayPrcLoad] runtime 로그인 반영 실패: {err}")
        termLog(f"[dayPrcLoad] {mode} 토큰 발급 OK")
        return (
            mode,
            host,
            _sessCache["token"],
            _sessCache["tokenType"],
        )

    return None


def loadDayStkPrc(
    stkRef,
    startDt,
    endDt,
    preferMode=None,
    indcTp=INDC_TP_QTY,
):
    """
    종목코드|이름 + 기간 → API 조회 후 DB 적재.
    반환: ok, stkCd, stkNm, upsertCnt, firstDt, lastDt, mode, errMsg?, ...
    """
    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        # 다건이면 matches 포함
        return {
            "ok": False,
            "errMsg": resolved.get("errMsg") or "종목 해석 실패",
            "matches": resolved.get("matches") or [],
        }

    stkCd = resolved["stkCd"]
    stkNm = resolved.get("stkNm") or ""
    stkLabel = formatStkLabel(stkCd, stkNm)

    session = acquireKiwoomSession(preferMode=preferMode)
    if session is None:
        return {
            "ok": False,
            "errMsg": (
                "키움 API 세션 없음. "
                "main 로그인 상태이거나 .env 앱키(모의/실전)가 필요합니다."
            ),
            "stkCd": stkCd,
            "stkNm": stkNm,
        }

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의" if mode == "paper" else "실전"
    isPaper = mode == "paper"

    termLog(f"[dayPrcLoad] ({modeLabel}) {stkLabel} {startDt}~{endDt}")

    fetched = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCd,
        startDt,
        endDt,
        indcTp=indcTp,
        tokenType=tokenType,
        isPaper=isPaper,
    )
    if not fetched.get("ok"):
        return {
            "ok": False,
            "errMsg": fetched.get("errMsg") or "일별주가 조회 실패",
            "stkCd": stkCd,
            "stkNm": stkNm,
            "mode": mode,
        }

    rows = fetched.get("rows") or []
    if not rows:
        return {
            "ok": True,
            "stkCd": stkCd,
            "stkNm": stkNm,
            "mode": mode,
            "upsertCnt": 0,
            "rowCnt": 0,
            "firstDt": None,
            "lastDt": None,
            "msg": "API 조회 0건 — 적재 없음",
        }

    saved = upsertDayStkPrcRows(rows)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg") or "DB 적재 실패",
            "stkCd": stkCd,
            "stkNm": stkNm,
            "mode": mode,
        }

    firstDt = rows[0]["TRADE_DT"]
    lastDt = rows[-1]["TRADE_DT"]
    return {
        "ok": True,
        "stkCd": stkCd,
        "stkNm": stkNm,
        "mode": mode,
        "upsertCnt": saved.get("upsertCnt") or len(rows),
        "rowCnt": len(rows),
        "firstDt": firstDt,
        "lastDt": lastDt,
        "startDt": fetched.get("startDt"),
        "endDt": fetched.get("endDt"),
        "pageUsed": fetched.get("pageUsed"),
        "msg": f"{stkLabel} {len(rows)}건 적재 ({firstDt}~{lastDt})",
    }


def defaultLoadRange(months=12):
    """오늘 기준 과거 N개월 ~ 오늘."""
    end = datetime.now()
    start = end - timedelta(days=int(months) * 30 + 5)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
