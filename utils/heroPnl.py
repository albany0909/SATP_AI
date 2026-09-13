"""
영웅문식 실현손익 (수수료·세금 반영).

00 체결 FID:
  938 = 당일매매수수료 (해당 체결분)
  939 = 당일매매세금 (매도 시 증권거래세 등)

실현손익 = (매도가−평단)×수량 − 매수수수료배분 − 매도수수료 − 매도세금
수익률 = 실현손익 / (평단×수량) × 100
"""

from __future__ import annotations


def parseFeeField(raw):
    """FID/문자열 금액을 정수 원으로."""
    if raw is None:
        return 0
    text = str(raw).strip().replace(",", "")
    if not text or text in ("-", "--"):
        return 0
    try:
        return abs(int(float(text)))
    except (TypeError, ValueError):
        return 0


def parseFillFees(values):
    """
    00 values 에서 수수료·세금.
    반환: (cmsn, tax)
    """
    if not isinstance(values, dict):
        return 0, 0
    cmsn = parseFeeField(
        values.get("938")
        or values.get("tdy_trde_cmsn")
        or values.get("cmsn")
    )
    tax = parseFeeField(
        values.get("939")
        or values.get("tdy_trde_tax")
        or values.get("tax")
    )
    return cmsn, tax


def estimateSellTax(sellAmt):
    """
    FID 없을 때 폴백 — 국내주식 거래세 약 0.20% (원 단위 반올림).
    실제 요율·반올림은 키움과 1~수원 차이 날 수 있다.
    """
    try:
        amt = float(sellAmt or 0)
    except (TypeError, ValueError):
        return 0
    if amt <= 0:
        return 0
    return int(round(amt * 0.002))


def estimateCmsn(trdAmt, mode="real"):
    """
    FID 없을 때 폴백 수수료.
    실전 온라인 대략 0.015% (최소 없음 가정). 모의도 동일 추정 가능.
    """
    try:
        amt = float(trdAmt or 0)
    except (TypeError, ValueError):
        return 0
    if amt <= 0:
        return 0
    # 아이티센 사례: 매수 300/1988600≈0.01508%, 매도 290/1945900≈0.0149%
    return int(round(amt * 0.00015))


def heroEvalRt(buyPx, sellPx, qty=1, purAmt=0):
    """
    평가식 수익률(%) — 매도 수수료·세금 전.
    purAmt(매입금액, 매수수수료 포함)가 있으면 분모로 쓰고,
    없으면 평단×수량.
    """
    try:
        bpx = abs(float(buyPx or 0))
        spx = abs(float(sellPx or 0))
        q = int(qty or 0)
        pur = abs(float(purAmt or 0))
    except (TypeError, ValueError):
        return 0.0
    if q <= 0:
        return 0.0
    cost = pur if pur > 0 else (bpx * q if bpx > 0 else 0.0)
    if cost <= 0 or spx <= 0:
        return 0.0
    return (spx * q - cost) / cost * 100.0


def heroRealizedPnl(buyPx, sellPx, qty, buyCmsn=0, sellCmsn=0, sellTax=0):
    """
    영웅문식 실현손익·수익률.
    반환: {grossPnl, netPnl, pnlRt, evalRt, buyAmt, sellAmt, feeTot}
    - evalRt: 수수료·세금 전(평가식)
    - pnlRt: 비용 차감 후(실현)
    """
    try:
        bpx = abs(float(buyPx or 0))
        spx = abs(float(sellPx or 0))
        q = int(qty or 0)
    except (TypeError, ValueError):
        return {
            "grossPnl": 0.0,
            "netPnl": 0.0,
            "pnlRt": 0.0,
            "evalRt": 0.0,
            "buyAmt": 0.0,
            "sellAmt": 0.0,
            "feeTot": 0.0,
        }
    if q <= 0 or bpx <= 0:
        return {
            "grossPnl": 0.0,
            "netPnl": 0.0,
            "pnlRt": 0.0,
            "evalRt": 0.0,
            "buyAmt": 0.0,
            "sellAmt": 0.0,
            "feeTot": 0.0,
        }
    buyAmt = bpx * q
    sellAmt = spx * q
    gross = sellAmt - buyAmt
    bc = abs(int(buyCmsn or 0))
    sc = abs(int(sellCmsn or 0))
    st = abs(int(sellTax or 0))
    feeTot = bc + sc + st
    net = gross - feeTot
    evalRt = (gross / buyAmt * 100.0) if buyAmt > 0 else 0.0
    pnlRt = (net / buyAmt * 100.0) if buyAmt > 0 else 0.0
    return {
        "grossPnl": float(gross),
        "netPnl": float(net),
        "pnlRt": float(pnlRt),
        "evalRt": float(evalRt),
        "buyAmt": float(buyAmt),
        "sellAmt": float(sellAmt),
        "feeTot": float(feeTot),
        "buyCmsn": bc,
        "sellCmsn": sc,
        "sellTax": st,
    }


def allocBuyCmsn(buyCmsnTot, buyQtyTot, sellQty):
    """매도 수량 비율로 매수 수수료 배분."""
    try:
        bq = int(buyQtyTot or 0)
        sq = int(sellQty or 0)
        tot = abs(int(buyCmsnTot or 0))
    except (TypeError, ValueError):
        return 0
    if bq <= 0 or sq <= 0 or tot <= 0:
        return 0
    if sq >= bq:
        return tot
    return int(round(tot * (sq / float(bq))))


def loadDeskFeeBuckets(tradeDt, mode=None):
    """
    일자·모드별 종목 수수료 버킷.
    {stkCd: {buyCmsn, buyQtyLeft, sells: deque[(qty, cmsn, tax)]}}
    """
    from collections import deque

    from utils.deskLogStore import listDeskLog
    from utils.stkCdUtil import normStkCd

    buckets = {}
    for row in listDeskLog(tradeDt, src="FILL", limit=2000, mode=mode) or []:
        cd = normStkCd(row.get("STK_CD"))
        if not cd:
            continue
        kind = str(row.get("KIND") or "").upper()
        try:
            qty = int(float(row.get("QTY") or 0))
        except (TypeError, ValueError):
            qty = 0
        cmsn = parseFeeField(row.get("CMSN"))
        tax = parseFeeField(row.get("TAX"))
        b = buckets.setdefault(
            cd,
            {"buyCmsn": 0, "buyQtyLeft": 0, "sells": deque()},
        )
        if kind == "BUY" and qty > 0:
            b["buyCmsn"] += cmsn
            b["buyQtyLeft"] += qty
        elif kind == "SELL" and qty > 0:
            b["sells"].append((qty, cmsn, tax))
    return buckets


def takeSellFees(bucket, sellQty, sellPx=None, buyPx=None, mode="real"):
    """
    버킷에서 매도 1건분 수수료를 꺼낸다.
    반환: (buyCmsnAlloc, sellCmsn, sellTax)
    """
    sq = int(sellQty or 0)
    if not bucket or sq <= 0:
        return 0, 0, 0
    buyCmsn = allocBuyCmsn(bucket.get("buyCmsn"), bucket.get("buyQtyLeft"), sq)
    # 매수 잔량·수수료 풀 감소
    bq = int(bucket.get("buyQtyLeft") or 0)
    if bq > 0 and buyCmsn > 0:
        bucket["buyCmsn"] = max(0, int(bucket.get("buyCmsn") or 0) - buyCmsn)
        bucket["buyQtyLeft"] = max(0, bq - sq)
    sellCmsn = 0
    sellTax = 0
    sells = bucket.get("sells")
    if sells:
        q0, c0, t0 = sells.popleft()
        sellCmsn, sellTax = int(c0 or 0), int(t0 or 0)
        # 수량 불일치 시 비율 보정은 생략(동일 체결 가정)
        if q0 and sq and q0 != sq and (c0 or t0):
            ratio = sq / float(q0)
            sellCmsn = int(round(sellCmsn * ratio))
            sellTax = int(round(sellTax * ratio))
    try:
        spx = abs(float(sellPx or 0))
        bpx = abs(float(buyPx or 0))
    except (TypeError, ValueError):
        spx, bpx = 0.0, 0.0
    if buyCmsn <= 0 and bpx > 0:
        buyCmsn = estimateCmsn(bpx * sq, mode=mode)
    if sellCmsn <= 0 and spx > 0:
        sellCmsn = estimateCmsn(spx * sq, mode=mode)
    if sellTax <= 0 and spx > 0:
        sellTax = estimateSellTax(spx * sq)
    return buyCmsn, sellCmsn, sellTax
