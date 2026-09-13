"""코스피/코스닥 일봉 20010101~오늘 적재 (일회 실행용)"""

from datetime import datetime

from api.getDayIndsPrc import (
    INDS_CD_KOSDAQ,
    INDS_CD_KOSPI,
    INDS_NM_MAP,
    fetchDayIndsPrcRange,
)
from api.getToken import issueAccToken
from config.config import (
    paper_app_key,
    paper_app_secret,
    paper_host_url,
    real_app_key,
    real_app_secret,
    real_host_url,
)
from utils.dayIndsPrcStore import upsertDayIndsPrcRows
from utils.termLog import termLog

START_DT = "20010101"
# 적재 대상: (코드, 표시명)
TARGETS = [
    (INDS_CD_KOSPI, INDS_NM_MAP[INDS_CD_KOSPI]),
    (INDS_CD_KOSDAQ, INDS_NM_MAP[INDS_CD_KOSDAQ]),
]


def _issueSession(preferReal=True):
    """실전 우선 토큰 발급. 실패 시 모의."""
    order = []
    if preferReal:
        order.append(("real", real_host_url, real_app_key, real_app_secret))
        order.append(("paper", paper_host_url, paper_app_key, paper_app_secret))
    else:
        order.append(("paper", paper_host_url, paper_app_key, paper_app_secret))
        order.append(("real", real_host_url, real_app_key, real_app_secret))

    lastErr = None
    for mode, hostUrl, appKey, appSecret in order:
        issued = issueAccToken(hostUrl, appKey, appSecret)
        if issued.get("ok"):
            return {
                "ok": True,
                "mode": mode,
                "hostUrl": hostUrl,
                "token": issued.get("token"),
                "tokenType": issued.get("tokenType") or "bearer",
                "isPaper": mode == "paper",
            }
        lastErr = issued.get("errMsg")
        termLog(f"[loadDayInds] {mode} 토큰 실패: {lastErr}")
    return {"ok": False, "errMsg": lastErr or "토큰 발급 실패"}


def main():
    endDt = datetime.now().strftime("%Y%m%d")
    session = _issueSession(preferReal=True)
    if not session.get("ok"):
        termLog(f"[loadDayInds] 중단: {session.get('errMsg')}")
        return 1

    modeLabel = "모의투자" if session["isPaper"] else "실투자"
    termLog(f"[loadDayInds] ({modeLabel}) {START_DT}~{endDt} 적재 시작")

    totalCnt = 0
    for indsCd, indsNm in TARGETS:
        termLog(f"[loadDayInds] {indsNm}({indsCd}) 조회 중...")
        fetched = fetchDayIndsPrcRange(
            session["hostUrl"],
            session["token"],
            indsCd,
            START_DT,
            endDt,
            tokenType=session["tokenType"],
            isPaper=session["isPaper"],
            indsNm=indsNm,
        )
        if not fetched.get("ok"):
            termLog(f"[loadDayInds] {indsNm} 실패: {fetched.get('errMsg')}")
            return 1

        rows = fetched.get("rows") or []
        termLog(
            f"[loadDayInds] {indsNm} 조회 {len(rows)}건 "
            f"(page={fetched.get('pageUsed')}, restart={fetched.get('restartCnt')})"
        )
        if not rows:
            continue

        saved = upsertDayIndsPrcRows(rows)
        if not saved.get("ok"):
            termLog(f"[loadDayInds] {indsNm} DB 실패: {saved.get('errMsg')}")
            return 1

        firstDt = rows[0]["TRADE_DT"]
        lastDt = rows[-1]["TRADE_DT"]
        cnt = saved.get("upsertCnt") or 0
        totalCnt += cnt
        termLog(
            f"[loadDayInds] {indsNm} 적재 완료 {cnt}건 ({firstDt}~{lastDt})"
        )

    termLog(f"[loadDayInds] 전체 완료: {totalCnt}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
