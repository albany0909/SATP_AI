"""
관심종목 공시(DART) + 뉴스(네이버) 일괄 적재·신규 요약.
"""

from __future__ import annotations

import time

from research.dartDiscFetch import fetchDartDiscList
from research.naverStkNews import fetchNaverStkNews
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog
from utils.watchNewsDiscStore import upsertDiscRows, upsertNewsRows
from utils.watchStkStore import listActiveWatch


def _toDiscDbRow(stkCd, stkNm, corpCode, it):
    return {
        "STK_CD": stkCd,
        "RCEPT_NO": it.get("rceptNo"),
        "STK_NM": stkNm or "",
        "CORP_CODE": corpCode or "",
        "CORP_NAME": it.get("corpName") or "",
        "REPORT_NM": it.get("reportNm") or "",
        "RCEPT_DT": it.get("rceptDt") or "",
        "FLR_NM": it.get("flrNm") or "",
        "RM": it.get("rm") or "",
        "VIEW_URL": it.get("viewUrl") or "",
        "SRC": "DART",
    }


def _toNewsDbRow(stkCd, stkNm, it):
    return {
        "STK_CD": stkCd,
        "NEWS_ID": it.get("newsId"),
        "STK_NM": stkNm or "",
        "TITLE": it.get("title") or "",
        "OFFICE_NM": it.get("officeNm") or "",
        "PUB_DT": it.get("pubDt") or "",
        "PUB_TM": it.get("pubTm") or "",
        "NEWS_URL": it.get("newsUrl") or "",
        "SRC": "NAVER",
        "ISSUE_YN": it.get("issueYn") or "N",
    }


def loadWatchNewsDisc(
    daysBack=3,
    newsPages=1,
    maxStks=80,
    sleepSec=0.9,
    stkList=None,
):
    """
    활성 관심종목(또는 지정 목록) 공시·뉴스 수집·UPSERT.
    반환: 통계 + newDisc / newNews
    """
    if stkList is not None:
        watch = []
        for x in stkList:
            if isinstance(x, dict):
                watch.append(x)
            else:
                watch.append({"STK_CD": normStkCd(x), "STK_NM": ""})
    else:
        watch = listActiveWatch(mktTp="KR") or []

    if maxStks and len(watch) > int(maxStks):
        watch = watch[: int(maxStks)]

    allNewDisc = []
    allNewNews = []
    discCnt = 0
    newsCnt = 0
    errCnt = 0
    okStk = 0

    for i, w in enumerate(watch):
        stkCd = normStkCd(w.get("STK_CD") or w.get("stkCd"))
        stkNm = (w.get("STK_NM") or w.get("stkNm") or "")[:100]
        if not stkCd:
            continue

        # 공시
        dRes = fetchDartDiscList(stkCd, daysBack=daysBack)
        if dRes.get("ok"):
            dRows = [
                _toDiscDbRow(stkCd, stkNm, dRes.get("corpCode"), it)
                for it in (dRes.get("items") or [])
            ]
            u = upsertDiscRows(dRows)
            if u.get("ok"):
                discCnt += u.get("upsertCnt") or 0
                allNewDisc.extend(u.get("newRows") or [])
            else:
                errCnt += 1
                termLog(f"[watchNewsDisc] disc upsert 실패 {stkCd}: {u.get('errMsg')}")
        else:
            # 키 없음 등은 1회만 카운트
            errCnt += 1
            if i == 0 or (dRes.get("errMsg") or "").find("DART_API_KEY") >= 0:
                termLog(f"[watchNewsDisc] disc {stkCd}: {dRes.get('errMsg')}")

        time.sleep(max(0.2, float(sleepSec)))

        # 뉴스
        nRes = fetchNaverStkNews(stkCd, pages=newsPages)
        if nRes.get("ok"):
            nRows = [
                _toNewsDbRow(stkCd, stkNm, it) for it in (nRes.get("items") or [])
            ]
            u = upsertNewsRows(nRows)
            if u.get("ok"):
                newsCnt += u.get("upsertCnt") or 0
                allNewNews.extend(u.get("newRows") or [])
            else:
                errCnt += 1
        else:
            errCnt += 1
            termLog(f"[watchNewsDisc] news {stkCd}: {nRes.get('errMsg')}")

        okStk += 1
        if i + 1 < len(watch):
            time.sleep(max(0.2, float(sleepSec)))

    summary = (
        f"[관심 공시·뉴스] 종목 {okStk}/{len(watch)} · "
        f"공시적재 {discCnt}(신규 {len(allNewDisc)}) · "
        f"뉴스적재 {newsCnt}(신규 {len(allNewNews)}) · "
        f"오류 {errCnt}"
    )
    termLog(summary)
    return {
        "ok": True,
        "stkCnt": okStk,
        "watchCnt": len(watch),
        "discUpsertCnt": discCnt,
        "newsUpsertCnt": newsCnt,
        "newDisc": allNewDisc,
        "newNews": allNewNews,
        "errCnt": errCnt,
        "summary": summary,
    }


def formatNewPushMsgs(newDisc, newNews, maxMsgs=12):
    """텔레그램용 신규 알림 메시지 리스트 (너무 많으면 상한)."""
    msgs = []
    for r in newDisc or []:
        msgs.append(
            "[공시 신규] {nm}({cd})\n"
            "{title}\n"
            "접수 {dt} · {flr}\n"
            "{url}".format(
                nm=r.get("STK_NM") or "",
                cd=r.get("STK_CD") or "",
                title=(r.get("REPORT_NM") or "")[:120],
                dt=r.get("RCEPT_DT") or "",
                flr=r.get("FLR_NM") or "",
                url=r.get("VIEW_URL") or "",
            )
        )
        if len(msgs) >= maxMsgs:
            return msgs

    # 이슈 뉴스 우선
    newsSorted = sorted(
        newNews or [],
        key=lambda x: 0 if (x.get("ISSUE_YN") == "Y") else 1,
    )
    for r in newsSorted:
        tag = "이슈" if r.get("ISSUE_YN") == "Y" else "뉴스"
        msgs.append(
            "[{tag} 신규] {nm}({cd})\n"
            "{title}\n"
            "{office} {dt} {tm}\n"
            "{url}".format(
                tag=tag,
                nm=r.get("STK_NM") or "",
                cd=r.get("STK_CD") or "",
                title=(r.get("TITLE") or "")[:120],
                office=r.get("OFFICE_NM") or "",
                dt=r.get("PUB_DT") or "",
                tm=r.get("PUB_TM") or "",
                url=r.get("NEWS_URL") or "",
            )
        )
        if len(msgs) >= maxMsgs:
            break
    return msgs
