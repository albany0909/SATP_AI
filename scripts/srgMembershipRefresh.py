# -*- coding: utf-8 -*-
"""KOSPI200(ka20002, 모의만)과 KOSDAQ150(KRX 지수명 조회) 명부.

실호스트로 넘어가지 않는다. 주문을 내지 않는다.
python scripts/srgMembershipRefresh.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEMBERSHIP_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "indexMembership.json"


def _paperPage(session, contYn, nextKey):
    from api.getIndsStkPrc import _hdr, _parseBody, getIndsStkPrc, rowToStkRecord

    try:
        res = getIndsStkPrc(
            session["host"],
            session["_token"],
            mrktTp="2",
            indsCd="201",
            stexTp="1",
            contYn=contYn,
            nextKey=nextKey,
            tokenType=session.get("tokenType") or "bearer",
        )
    except Exception as err:
        return {"ok": False, "errMsg": str(err)}
    ok, parsed = _parseBody(res)
    if not ok:
        return {"ok": False, "errMsg": parsed.get("errMsg") or "KA20002_FAIL"}
    rows = []
    for item in parsed.get("inds_stkpc") or []:
        rec = rowToStkRecord(item)
        if rec:
            rows.append({"stk_cd": rec["stkCd"]})
    return {
        "ok": True,
        "rows": rows,
        "contYn": _hdr(res, "cont-yn"),
        "nextKey": _hdr(res, "next-key"),
    }


def fetchKospiPaperPages():
    """모의 세션만. 없거나 실패하면 실호스트를 쓰지 않는다."""
    from trading.surgeTradeInputs import fetchKospi200Pages
    from trading.surgeTradeLive import loadPaperOrderSession
    from config import runtimeState

    session = loadPaperOrderSession()
    if not session:
        return {"ok": False, "reason": "NO_PAPER_SESSION", "pages": []}
    token = runtimeState.getToken("paper")
    if not token:
        return {"ok": False, "reason": "NO_PAPER_SESSION", "pages": []}
    sess = dict(session)
    sess["_token"] = token
    return fetchKospi200Pages(lambda cont, key: _paperPage(sess, cont, key))


def _krxPost(bld, payload):
    import requests

    data = dict(payload or {})
    data["bld"] = bld
    res = requests.post(
        "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd",
        },
        timeout=20,
    )
    res.raise_for_status()
    body = res.json()
    return body if isinstance(body, dict) else {}


def fetchKosdaq150Krx(tradeYmd):
    """지수명 '코스닥 150' 이 finder 에 있을 때만 구성종목을 읽는다."""
    from trading.surgeTradeInputs import KRX_CONSTITUENT_BLD, KRX_FINDER_BLD, indexCodesFromFinder

    found = _krxPost(KRX_FINDER_BLD, {"mktsel": "1"})
    block = found.get("block1") or []
    codes = indexCodesFromFinder(block, "코스닥 150")
    if not codes:
        return {"ok": False, "reason": "KRX_NAME_NOT_FOUND", "output": [], "indexCodes": None}
    body = _krxPost(
        KRX_CONSTITUENT_BLD,
        {"indIdx": codes["indIdx"], "indIdx2": codes["indIdx2"], "trdDd": tradeYmd},
    )
    output = body.get("output") or []
    return {"ok": True, "reason": "", "output": output, "indexCodes": codes}


def refreshIfDue(now=None, path=None):
    """없거나 6월·12월 기준보다 오래됐을 때만 조회한다."""
    from trading.surgeTradeInputs import (
        applyKosdaqOutput,
        applyKospiPages,
        loadMembership,
        membershipRefreshDue,
        rebalanceAnchor,
        saveMembership,
    )

    now = now or datetime.now()
    ymd = now.strftime("%Y%m%d")
    filePath = Path(path or MEMBERSHIP_PATH)
    book = loadMembership(filePath)
    if not membershipRefreshDue(book, ymd):
        return {"fetched": False, "reason": "FRESH", "orders": 0}
    anchor = rebalanceAnchor(ymd)
    kospi = fetchKospiPaperPages()
    if kospi.get("ok"):
        book = applyKospiPages(book, kospi.get("pages") or [], ymd, anchor, "99991231")
    else:
        book["indexes"]["KOSPI200"]["reason"] = kospi.get("reason") or "KA20002_FAIL"
        book["indexes"]["KOSPI200"]["complete"] = False
        book["indexes"]["KOSPI200"]["host"] = "paper"
    try:
        krx = fetchKosdaq150Krx(ymd)
    except Exception as err:
        krx = {"ok": False, "reason": str(err), "output": [], "indexCodes": None}
    if krx.get("ok"):
        book = applyKosdaqOutput(
            book, krx.get("output") or [], ymd, anchor, "99991231", krx.get("indexCodes")
        )
    else:
        book["indexes"]["KOSDAQ150"]["reason"] = krx.get("reason") or "KRX_FAIL"
        book["indexes"]["KOSDAQ150"]["complete"] = False
    book["asOf"] = ymd
    book["orders"] = 0
    saveMembership(filePath, book)
    try:
        from trading.surgeTradeInputs import dartKeyConfigured, mergeIssueRecords, pullIssues, srgQuoteCodes

        if dartKeyConfigured() and srgQuoteCodes():
            from research.dartDiscFetch import fetchDartDiscList

            issuePath = filePath.parent / "surgeTrade" / "issueRecords.json"
            prev = []
            if issuePath.is_file():
                import json

                prev = json.loads(issuePath.read_text(encoding="utf-8"))
            merged = mergeIssueRecords(prev if isinstance(prev, list) else [], pullIssues(srgQuoteCodes(), dartFetch=fetchDartDiscList))
            issuePath.parent.mkdir(parents=True, exist_ok=True)
            import json

            issuePath.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {
        "fetched": True,
        "reason": "SAVED",
        "orders": 0,
        "kospi": len(book["indexes"]["KOSPI200"].get("codes") or []),
        "kosdaq": len(book["indexes"]["KOSDAQ150"].get("codes") or []),
        "kospiComplete": book["indexes"]["KOSPI200"].get("complete") is True,
        "kosdaqComplete": book["indexes"]["KOSDAQ150"].get("complete") is True,
    }


def main():
    result = refreshIfDue()
    print(
        "orders={orders} fetched={fetched} kospi={kospi} kosdaq={kosdaq} "
        "kospiComplete={kospiComplete} kosdaqComplete={kosdaqComplete} reason={reason}".format(
            **{
                "orders": result.get("orders", 0),
                "fetched": result.get("fetched"),
                "kospi": result.get("kospi", ""),
                "kosdaq": result.get("kosdaq", ""),
                "kospiComplete": result.get("kospiComplete", ""),
                "kosdaqComplete": result.get("kosdaqComplete", ""),
                "reason": result.get("reason") or "",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
