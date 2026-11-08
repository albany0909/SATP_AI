"""SRG01 최종 주문 경계. 모의 호스트·모드·계정이 모두 확인될 때만 통과."""

from __future__ import annotations

from strategies.specLoader import normAlgCd

ALG_CD = "SRG01"
# TPX01 명세의 손실 매도 확인은 유지한다. SRG01 만 그 대기를 하지 않는다.
LOSS_APPROVAL_ALGS = {"TPX01": True, "SRG01": False}


def lossSellRequiresTelegramApproval(algCd):
    """
    True: 텔레그램 승인 전 매도 보류 (TPX01 명세).
    False: SRG01 은 설정된 청산을 승인 대기 없이 실행.
    None: 공통 관문이 없는 알고리즘. 새 관문을 만들지 않는다.
    """
    code = normAlgCd(algCd)
    if code not in LOSS_APPROVAL_ALGS:
        return None
    return bool(LOSS_APPROVAL_ALGS[code])


def _hosts():
    from config.config import paper_host_url, real_host_url

    return str(paper_host_url or ""), str(real_host_url or "")


def _paperChecks(session):
    if not isinstance(session, dict):
        return "SESSION_MISSING"
    mode = str(session.get("mode") or "").strip().lower()
    if mode != "paper":
        return "REAL_OR_UNVERIFIED_MODE"
    host = str(session.get("host") or "").strip()
    paperHost, realHost = _hosts()
    if not host or not paperHost or host != paperHost or host == realHost:
        return "HOST_NOT_PAPER"
    if "mockapi.kiwoom.com" not in host:
        return "HOST_NOT_PAPER"
    if session.get("accountEnv") != "paper" or session.get("accountVerified") is not True:
        return "ACCOUNT_UNVERIFIED"
    if session.get("tokenPresent") is not True:
        return "TOKEN_MISSING"
    return ""


def evaluateOrderSession(session):
    """주문 직전. 실패하면 실호스트로 넘기지 않는다."""
    if isinstance(session, dict) and session.get("purpose") == "market_read":
        return {"ok": False, "reason": "MARKET_READ_CANNOT_ORDER"}
    reason = _paperChecks(session)
    if reason:
        return {"ok": False, "reason": reason}
    return {"ok": True, "reason": "PAPER", "ordersAllowed": True}


def evaluateMarketRead(session):
    """시세 조회도 모의 호스트만. 이 세션으로는 주문할 수 없다."""
    if isinstance(session, dict) and session.get("purpose") not in (None, "market_read"):
        return {"ok": False, "reason": "PURPOSE", "ordersAllowed": False}
    reason = _paperChecks(session)
    if reason:
        return {"ok": False, "reason": reason, "ordersAllowed": False}
    return {
        "ok": True,
        "reason": "PAPER_READ",
        "ordersAllowed": False,
        "purpose": "market_read",
    }


def submitSurgeOrder(action, session, broker):
    """
    브로커 호출 직전 게이트. 텔레그램을 보내지 않는다.
    broker 는 테스트 주입 또는 모의 주문 함수. 게이트 실패 시 호출하지 않는다.
    """
    action = action or {}
    if action.get("algCd") != ALG_CD:
        return {"ok": False, "sent": False, "reason": "ALG_MISMATCH"}
    side = action.get("side")
    try:
        qty = int(action.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if side not in ("BUY", "SELL") or qty < 1:
        return {"ok": False, "sent": False, "reason": "NO_ORDER"}
    if side == "SELL" and lossSellRequiresTelegramApproval(ALG_CD) is not False:
        return {"ok": False, "sent": False, "reason": "APPROVAL_POLICY"}
    if action.get("exemptLossApproval") is not True:
        return {"ok": False, "sent": False, "reason": "EXEMPT_FLAG_MISSING"}
    gate = evaluateOrderSession(session)
    if not gate.get("ok"):
        return {"ok": False, "sent": False, "reason": gate.get("reason")}
    if broker is None:
        return {"ok": False, "sent": False, "reason": "NO_BROKER"}
    try:
        result = broker(action, session)
    except Exception as err:
        return {"ok": False, "sent": False, "reason": "BROKER_ERROR", "errMsg": str(err)}
    if not isinstance(result, dict):
        return {"ok": False, "sent": True, "reason": "BROKER_BAD_RESULT"}
    result = dict(result)
    result["sent"] = bool(result.get("sent", result.get("ok")))
    result.setdefault("reason", "PAPER_ORDER" if result.get("ok") else "BROKER_REJECT")
    return result
