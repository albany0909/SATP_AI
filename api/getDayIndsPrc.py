"""키움 업종일봉조회요청 API (ka20006) — 기간 조회·파싱"""

import time
from datetime import datetime, timedelta

import requests

from api.getAcntEvlt import parsePadNum

# 연속조회 안전 상한 (25년치 약 60~80페이지 여유)
MAX_CONT_PAGES = 300
# 연속조회가 끊겼을 때 base_dt 재시작 횟수 상한
MAX_RESTARTS = 40

# 국내 대표 업종코드
INDS_CD_KOSPI = "001"
INDS_CD_KOSDAQ = "101"
INDS_CD_KOSPI200 = "201"
INDS_NM_MAP = {
    INDS_CD_KOSPI: "KOSPI",
    INDS_CD_KOSDAQ: "KOSDAQ",
    INDS_CD_KOSPI200: "KOSPI200",
}
MKT_TP_KR = "KR"
SRC_TR_KA20006 = "ka20006"


def getDayIndsPrc(
    hostUrl,
    token,
    indsCd,
    baseDt,
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka20006 업종일봉 1회 호출.
    body: inds_cd, base_dt(YYYYMMDD)
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
        "api-id": "ka20006",
    }
    data = {
        "inds_cd": str(indsCd).strip(),
        "base_dt": str(baseDt).strip(),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def _normYmd(raw):
    """YYYYMMDD 또는 YYYY-MM-DD → YYYYMMDD. 실패 시 None."""
    text = str(raw or "").strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _ymdMinusOne(ymd):
    """YYYYMMDD 하루 전. 실패 시 None."""
    try:
        dt = datetime.strptime(ymd, "%Y%m%d") - timedelta(days=1)
        return dt.strftime("%Y%m%d")
    except ValueError:
        return None


def parseIdxPrc(raw):
    """
    지수 OHLC: API는 소수점 제거된 100배 값 → /100 하여 DECIMAL용 float.
    부호·콤마 정리 후 절대값.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "" or text in ("-", "--"):
        return None
    while text.startswith("--"):
        text = "-" + text[2:]
    num = parsePadNum(text)
    if num is None:
        try:
            num = float(text)
        except (TypeError, ValueError):
            return None
    return abs(float(num)) / 100.0


def parseQty(raw):
    """거래량 정수(절대값). 실패 시 None."""
    num = parsePadNum(raw)
    if num is None:
        return None
    return abs(int(num))


def parseAmt(raw):
    """거래대금(백만원 등). 실패 시 None."""
    num = parsePadNum(raw)
    if num is None:
        return None
    return float(num)


def _parseBody(res):
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
        codeOk = "inds_dt_pole_qry" in body

    if not codeOk:
        returnMsg = body.get("return_msg", "")
        return False, {
            "errMsg": f"[{returnCode}] {returnMsg or '업종일봉 조회 실패'}",
            "body": body,
        }
    return True, body


def rowToRecord(row, indsCd, mktTp=MKT_TP_KR, indsNm=None, srcTr=SRC_TR_KA20006):
    """
    API row → AI_DAY_INDS_PRC 적재용 dict.
    OHLC는 100배 → 실제 지수값, CLOSE는 cur_prc.
    """
    if not isinstance(row, dict):
        return None
    tradeDt = str(row.get("dt") or "").strip()
    if len(tradeDt) != 8 or not tradeDt.isdigit():
        return None

    code = str(indsCd).strip()
    name = indsNm if indsNm is not None else INDS_NM_MAP.get(code)

    return {
        "MKT_TP": mktTp,
        "INDS_CD": code,
        "TRADE_DT": tradeDt,
        "OPEN_PRC": parseIdxPrc(row.get("open_pric")),
        "HIGH_PRC": parseIdxPrc(row.get("high_pric")),
        "LOW_PRC": parseIdxPrc(row.get("low_pric")),
        "CLOSE_PRC": parseIdxPrc(row.get("cur_prc")),
        "TRDE_QTY": parseQty(row.get("trde_qty")),
        "TRDE_PRICA": parseAmt(row.get("trde_prica")),
        "INDS_NM": name,
        "SRC_TR": srcTr,
    }


def fetchLastIndsClose(hostUrl, token, indsCd, tokenType="bearer"):
    """
    ka20006 1페이지에서 최근 종가와 직전일 대비 등락.
    DB에 201이 없거나 일봉이 오래된 경우 매매창 폴백용. DB에는 쓰지 않는다.
    """
    code = str(indsCd or "").strip()
    if not code or not hostUrl or not token:
        return None
    baseDt = datetime.now().strftime("%Y%m%d")
    try:
        res = getDayIndsPrc(
            hostUrl,
            token,
            code,
            baseDt,
            tokenType=tokenType,
        )
    except Exception:
        return None
    ok, parsed = _parseBody(res)
    if not ok:
        return None
    recs = []
    for row in parsed.get("inds_dt_pole_qry") or []:
        rec = rowToRecord(row, code)
        if rec is None or rec.get("CLOSE_PRC") is None:
            continue
        recs.append(rec)
    if not recs:
        return None
    recs.sort(key=lambda r: r["TRADE_DT"], reverse=True)
    last = recs[0]
    fluRt = None
    if len(recs) > 1:
        prev = recs[1].get("CLOSE_PRC")
        try:
            prevN = float(prev) if prev is not None else None
            prcN = float(last["CLOSE_PRC"])
            if prevN and prevN > 0:
                fluRt = (prcN - prevN) / prevN * 100.0
        except (TypeError, ValueError):
            fluRt = None
    return {
        "prc": float(last["CLOSE_PRC"]),
        "fluRt": fluRt,
        "tradeDt": str(last.get("TRADE_DT") or ""),
        "src": "tr",
    }


def fetchDayIndsPrcRange(
    hostUrl,
    token,
    indsCd,
    startDt,
    endDt,
    tokenType="bearer",
    isPaper=True,
    mktTp=MKT_TP_KR,
    indsNm=None,
):
    """
    ka20006을 연속조회하여 startDt~endDt(포함) 업종일봉을 수집한다.
    base_dt=endDt 기준으로 과거 방향. 연속조회가 끊기면 가장 오래된 일-1로 재시작.
    반환: ok, rows, rowCnt, indsCd, startDt, endDt
    """
    startYmd = _normYmd(startDt)
    endYmd = _normYmd(endDt)
    if not startYmd or not endYmd:
        return {"ok": False, "errMsg": "시작일/종료일은 YYYYMMDD 형식이어야 합니다."}
    if startYmd > endYmd:
        return {"ok": False, "errMsg": "시작일이 종료일보다 클 수 없습니다."}

    code = str(indsCd or "").strip()
    if not code:
        return {"ok": False, "errMsg": "업종코드가 비어 있습니다."}

    waitSec = 1.05 if isPaper else 0.25
    byDt = {}
    contYn = "N"
    nextKey = ""
    qryDt = endYmd
    reachedPastStart = False
    pageUsed = 0
    restartCnt = 0

    while pageUsed < MAX_CONT_PAGES and restartCnt <= MAX_RESTARTS:
        if pageUsed > 0:
            time.sleep(waitSec)

        try:
            res = getDayIndsPrc(
                hostUrl,
                token,
                code,
                qryDt,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {"ok": False, "errMsg": f"요청 실패: {err}"}

        pageUsed += 1
        ok, parsed = _parseBody(res)
        if not ok:
            return {
                "ok": False,
                "errMsg": parsed.get("errMsg"),
                "body": parsed.get("body"),
            }

        rows = parsed.get("inds_dt_pole_qry") or []
        pageOldest = None
        if isinstance(rows, list):
            for row in rows:
                rec = rowToRecord(row, code, mktTp=mktTp, indsNm=indsNm)
                if rec is None:
                    continue
                dt = rec["TRADE_DT"]
                if pageOldest is None or dt < pageOldest:
                    pageOldest = dt
                if dt > endYmd:
                    continue
                if dt < startYmd:
                    reachedPastStart = True
                    continue
                byDt[dt] = rec

        if reachedPastStart:
            break

        contYnHdr = _hdr(res, "cont-yn", "N").upper()
        nextKey = _hdr(res, "next-key", "")
        if contYnHdr == "Y" and nextKey:
            contYn = "Y"
            continue

        # 연속조회 종료인데 아직 시작일 미도달 → base_dt를 하루 앞당겨 재시작
        if pageOldest and pageOldest > startYmd:
            prevDt = _ymdMinusOne(pageOldest)
            if not prevDt or prevDt < startYmd:
                break
            if prevDt >= qryDt:
                # 동일 구간 반복 방지
                break
            qryDt = prevDt
            contYn = "N"
            nextKey = ""
            restartCnt += 1
            continue
        break

    records = [byDt[k] for k in sorted(byDt.keys())]
    return {
        "ok": True,
        "rows": records,
        "rowCnt": len(records),
        "indsCd": code,
        "startDt": startYmd,
        "endDt": endYmd,
        "mktTp": mktTp,
        "pageUsed": pageUsed,
        "restartCnt": restartCnt,
    }
