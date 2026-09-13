"""KRX 외국인 현선물 → AI_DAY_FRGN_SF 적재."""

from datetime import datetime, timedelta

from utils.dayFrgnSfKrx import fetchFrgnSpotFut
from utils.dayFrgnSfStore import upsertFrgnSfRows
from utils.termLog import termLog


def _normYmd(raw):
    text = str(raw or "").replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def loadFrgnSfRange(startDt, endDt):
    """
    기간 네이버 일자별 순매수 조회 후 UPSERT.
    차트 이평(20·60·120)용으로 시작일 앞을 넉넉히 넣는 것은 호출측에서 한다.
    """
    start = _normYmd(startDt)
    end = _normYmd(endDt) or datetime.now().strftime("%Y%m%d")
    if not start:
        start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    if start > end:
        start, end = end, start

    got = fetchFrgnSpotFut(start, end)
    if not got.get("ok"):
        return {
            "ok": False,
            "errMsg": got.get("errMsg") or "KRX 조회 실패",
            "upsertCnt": 0,
        }
    saved = upsertFrgnSfRows(got.get("rows") or [], src="naver_sise")
    if not saved.get("ok"):
        return saved
    termLog(
        f"[frgnSfLoad] {start}~{end} UPSERT {saved.get('upsertCnt')} "
        f"(네이버 코스피 외국인)"
    )
    return {
        "ok": True,
        "startDt": start,
        "endDt": end,
        "upsertCnt": saved.get("upsertCnt") or 0,
        "futOk": bool(got.get("futOk")),
        "spotCnt": got.get("spotCnt") or 0,
        "futCnt": got.get("futCnt") or 0,
        "summary": (
            f"외국인 현선물 적재 {start}~{end} "
            f"{saved.get('upsertCnt')}일"
            + " · 현물(코스피 외국인)"
        ),
    }
