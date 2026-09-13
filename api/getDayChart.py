"""키움 주식일봉차트조회요청 API (ka10081)"""

import time

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

# SMA 240일 확보용 최소 봉 수
MIN_BARS_FOR_SMA240 = 240
# 연속조회 안전 상한 (무한 루프 방지)
MAX_CONT_PAGES = 30


def getDayChart(
    hostUrl,
    token,
    stkCd,
    baseDt,
    updStkpcTp="1",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10081 일봉차트 1회 호출 후 Response 반환.
    upd_stkpc_tp 기본 1(수정주가) — 이동평균 왜곡 방지.
    """
    url = hostUrl + "/api/dostk/chart"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": "ka10081",
    }
    data = {
        "stk_cd": normStkCd(stkCd),
        "base_dt": str(baseDt).strip(),
        "upd_stkpc_tp": str(updStkpcTp),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    """응답 헤더 조회 (대소문자 무시)."""
    if res is None:
        return default
    # requests headers는 CaseInsensitiveDict
    return str(res.headers.get(name, default) or default).strip()


def _parseBody(res):
    """HTTP 응답 → (ok, body|errMsg)."""
    try:
        body = res.json()
    except Exception as err:
        return False, {"errMsg": f"응답 파싱 실패: {err}"}

    if res.status_code != 200:
        returnCode = body.get("return_code")
        returnMsg = body.get("return_msg") or res.text
        return False, {
            "errMsg": f"HTTP {res.status_code} [{returnCode}] {returnMsg}",
            "body": body,
        }

    returnCode = body.get("return_code")
    try:
        codeOk = int(returnCode) == 0
    except (TypeError, ValueError):
        codeOk = "stk_dt_pole_chart_qry" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '일봉 조회 실패'}",
            "body": body,
        }
    return True, body


def fetchDayBars(
    hostUrl,
    token,
    stkCd,
    baseDt,
    minBars=MIN_BARS_FOR_SMA240,
    updStkpcTp="1",
    tokenType="bearer",
    isPaper=True,
):
    """
    ka10081을 연속조회하여 최소 minBars개 일봉을 수집한다.
    반환:
      성공: ok=True, bars=[{dt, curPrc, ...}, ...] (최신→과거 순 유지), barCnt
      실패: ok=False, errMsg
    호출 간격: 모의 1.05초 / 실전 0.25초 (조회 TR 제한 준수)
    """
    waitSec = 1.05 if isPaper else 0.25
    allBars = []
    seenDt = set()
    contYn = "N"
    nextKey = ""

    for pageIdx in range(MAX_CONT_PAGES):
        if pageIdx > 0:
            # 연속조회 전 대기 — 모의 1초 1회 제한
            time.sleep(waitSec)

        try:
            res = getDayChart(
                hostUrl,
                token,
                stkCd,
                baseDt,
                updStkpcTp=updStkpcTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}"}

        ok, parsed = _parseBody(res)
        if not ok:
            return {"ok": False, "errMsg": parsed.get("errMsg"), "body": parsed.get("body")}

        rows = parsed.get("stk_dt_pole_chart_qry") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                dt = str(row.get("dt") or "").strip()
                # 동일 일자 중복 제거 (페이지 경계)
                if dt and dt in seenDt:
                    continue
                if dt:
                    seenDt.add(dt)
                prc = parsePadNum(row.get("cur_prc"))
                if prc is None:
                    continue
                allBars.append(
                    {
                        "dt": dt,
                        "curPrc": abs(int(prc)),
                        "openPric": abs(parsePadNum(row.get("open_pric")) or 0),
                        "highPric": abs(parsePadNum(row.get("high_pric")) or 0),
                        "lowPric": abs(parsePadNum(row.get("low_pric")) or 0),
                        "trdeQty": abs(parsePadNum(row.get("trde_qty")) or 0),
                    }
                )

        if len(allBars) >= minBars:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKey = _hdr(res, "next-key", "")
        if contYnHdr != "Y" or not nextKey:
            break
        # 다음 페이지 요청 헤더
        contYn = "Y"

    if not allBars:
        return {"ok": False, "errMsg": "일봉 데이터가 비어 있습니다."}

    return {
        "ok": True,
        "bars": allBars,
        "barCnt": len(allBars),
        "stkCd": normStkCd(stkCd),
        "baseDt": str(baseDt).strip(),
    }
