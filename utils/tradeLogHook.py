"""매매창 거래로그 훅. Qt를 쓰지 않아 utils/trading 에서 호출 가능하다."""

_hook = None
_fillHook = None
_acntHook = None


def setTradeLogHook(fn):
    """창이 뜰 때 알고리즘 이벤트 콜백 등록. 닫히면 None."""
    global _hook
    _hook = fn


def setFillLogHook(fn):
    """00 체결 한 줄."""
    global _fillHook
    _fillHook = fn


def setAcntRefreshHook(fn):
    """체결 후 계좌/보유 갱신."""
    global _acntHook
    _acntHook = fn


def emitTradeLogRow(row):
    """알고리즘 이벤트 1행을 매매창으로 보낸다. 실패해도 호출측을 막지 않는다."""
    fn = _hook
    if fn is None:
        return
    try:
        fn(row)
    except Exception:
        pass


def emitFillLog(sideLabel, stkCd, stkNm, qty, px, ordNo="", cmsn=0, tax=0, cumQty=0):
    """
    00 체결을 일별 로그에 남기고 매매창으로 보낸다.
    qty 는 **단위 체결량**(분할 체결 1건분). cumQty 는 주문 누계(참고·로그 표시용).
    """
    try:
        from utils.deskLogStore import insertDeskFill
        from utils.tradeSession import getDeskTradeMode

        mode = getDeskTradeMode()
        insertDeskFill(
            sideLabel,
            stkCd,
            stkNm,
            qty,
            px,
            ordNo,
            mode=mode,
            cmsn=cmsn,
            tax=tax,
            cumQty=cumQty,
        )
    except Exception:
        pass
    try:
        from trading.maTouchLive import onBrokerFill

        onBrokerFill(sideLabel, stkCd, qty, px, ordNo, cmsn=cmsn, tax=tax)
    except Exception:
        pass
    fn = _fillHook
    if fn is None:
        return
    try:
        fn(sideLabel, stkCd, stkNm, qty, px, ordNo)
    except Exception:
        pass
    acntFn = _acntHook
    if acntFn is None:
        return
    try:
        acntFn()
    except Exception:
        pass


def clearTradeLogHooks():
    global _hook, _fillHook, _acntHook
    _hook = None
    _fillHook = None
    _acntHook = None
