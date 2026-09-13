"""usa20930 급등·급락 → AI_US_DAY_FLU_ISSUE 적재"""

from __future__ import annotations

import time
from datetime import datetime

from api.getUsFluStk import (
    FLU_DN,
    FLU_UP,
    fetchUsFluStkList,
    rowToFluRecord,
)
from config.usHoliday import resolveUsSessionDtForKstMorning
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.termLog import termLog
from utils.usDayFluIssueStore import (
    deleteUsFluIssueByDt,
    upsertUsFluIssueRows,
)
from utils.usIndGrp import resolveUsIndGrp


def _pickQuerySession(preferMode=None):
    """
    키움 세션.
    메인(runtime) 로그인 → 캐시 → .env 앱키 발급 (웹 차트 서버 포함)
    """
    return acquireKiwoomSession(preferMode=preferMode)


def _sortKeyFlu(row, fluTp):
    """등락률 절댓값 큰 순 (급등은 +우선, 급락은 -우선)."""
    try:
        rt = float(str(row.get("flu_rt") or "0").replace(",", ""))
    except (TypeError, ValueError):
        rt = 0.0
    if str(fluTp) == FLU_UP:
        return -rt
    return rt


def _recordsFromItems(items, tradeDt, fluTp, maxRows=80):
    sortedItems = sorted(
        [x for x in (items or []) if isinstance(x, dict)],
        key=lambda r: _sortKeyFlu(r, fluTp),
    )
    out = []
    for i, row in enumerate(sortedItems[: max(1, int(maxRows))], start=1):
        rec = rowToFluRecord(row, tradeDt, fluTp, i)
        if not rec:
            continue
        rec["IND_GRP"] = resolveUsIndGrp(
            rec.get("STK_CD"),
            rec.get("STK_NM") or "",
            rec.get("STEX_TP") or "",
        )
        out.append(rec)
    return out


def loadUsDayFluIssue(
    tradeDt=None,
    tmTp="2",
    tm="1",
    maxRows=60,
    sleepSec=1.1,
    preferMode=None,
):
    """
    급등+급락 조회·DB 적재.
    tradeDt 미지정 시: KST 아침 기준 직전 미장세션일, 없으면 오늘(로컬).
    """
    sess = _pickQuerySession(preferMode=preferMode)
    if not sess:
        return {
            "ok": False,
            "errMsg": (
                "키움 토큰 없음 — .env 앱키(KIWOOM_PAPER_*/REAL_*) 확인 "
                "또는 메인에서 login paper|real"
            ),
        }

    mode, hostUrl, token, tokenType = sess
    if not token:
        return {"ok": False, "errMsg": "토큰 없음 — 앱키·로그인 확인"}

    if not tradeDt:
        usDt = resolveUsSessionDtForKstMorning(datetime.now())
        if usDt is not None:
            tradeDt = usDt.strftime("%Y%m%d")
        else:
            tradeDt = datetime.now().strftime("%Y%m%d")
    tradeDt = str(tradeDt).replace("-", "")[:8]

    upRes = fetchUsFluStkList(
        hostUrl,
        token,
        fluTp=FLU_UP,
        tmTp=tmTp,
        tm=tm,
        tokenType=tokenType,
        sleepSec=sleepSec,
    )
    time.sleep(max(0.5, float(sleepSec)))
    dnRes = fetchUsFluStkList(
        hostUrl,
        token,
        fluTp=FLU_DN,
        tmTp=tmTp,
        tm=tm,
        tokenType=tokenType,
        sleepSec=sleepSec,
    )

    errors = []
    if not upRes.get("ok"):
        errors.append(f"급등: {upRes.get('errMsg') or '실패'}")
    if not dnRes.get("ok"):
        errors.append(f"급락: {dnRes.get('errMsg') or '실패'}")

    upRecs = (
        _recordsFromItems(upRes.get("items"), tradeDt, FLU_UP, maxRows)
        if upRes.get("ok")
        else []
    )
    dnRecs = (
        _recordsFromItems(dnRes.get("items"), tradeDt, FLU_DN, maxRows)
        if dnRes.get("ok")
        else []
    )

    if not upRecs and not dnRecs:
        return {
            "ok": False,
            "errMsg": " / ".join(errors) if errors else "급등락 데이터 없음",
            "tradeDt": tradeDt,
            "mode": mode,
            "upCnt": 0,
            "dnCnt": 0,
        }

    # 해당일 재적재
    deleteUsFluIssueByDt(tradeDt)
    allRecs = upRecs + dnRecs
    save = upsertUsFluIssueRows(allRecs)
    if not save.get("ok"):
        return {
            "ok": False,
            "errMsg": save.get("errMsg") or "DB 저장 실패",
            "tradeDt": tradeDt,
            "mode": mode,
        }

    termLog(
        f"[usFluIssue] {tradeDt} 급등 {len(upRecs)} · 급락 {len(dnRecs)}"
        + (f" · 경고 {errors}" if errors else "")
    )
    return {
        "ok": True,
        "tradeDt": tradeDt,
        "mode": mode,
        "upCnt": len(upRecs),
        "dnCnt": len(dnRecs),
        "upsertCnt": save.get("upsertCnt") or 0,
        "warnings": errors,
        "summary": (
            f"[미국급등락적재] {tradeDt} ({mode})\n"
            f"급등 {len(upRecs)}건 · 급락 {len(dnRecs)}건"
            + (f"\n※ {'; '.join(errors)}" if errors else "")
        ),
    }
