"""
키움 신고저가요청 API (ka10016)

- URL: /api/dostk/stkinfo
- dt=250 → 52주(약 1년) 신고/신저
- ntl_tp 1:신고가 2:신저가
"""

import time

import requests

from api.getAcntEvlt import parsePadNum
from utils.stkCdUtil import normStkCd

SRC_TR = "ka10016"
MAX_CONT_PAGES = 30

# 시장
MRKT_ALL = "000"
MRKT_KOSPI = "001"
MRKT_KOSDAQ = "101"
MRKT_LABEL = {
    MRKT_ALL: "전체",
    MRKT_KOSPI: "코스피",
    MRKT_KOSDAQ: "코스닥",
}
MRKT_ALIAS = {
    "000": MRKT_ALL,
    "전체": MRKT_ALL,
    "all": MRKT_ALL,
    "0": MRKT_ALL,
    "001": MRKT_KOSPI,
    "코스피": MRKT_KOSPI,
    "kospi": MRKT_KOSPI,
    "1": MRKT_KOSPI,
    "101": MRKT_KOSDAQ,
    "코스닥": MRKT_KOSDAQ,
    "kosdaq": MRKT_KOSDAQ,
    "10": MRKT_KOSDAQ,
}

# 신고저 구분
NTL_HIGH = "1"
NTL_LOW = "2"
NTL_LABEL = {NTL_HIGH: "신고가", NTL_LOW: "신저가"}
NTL_ALIAS = {
    "1": NTL_HIGH,
    "고": NTL_HIGH,
    "신고": NTL_HIGH,
    "high": NTL_HIGH,
    "2": NTL_LOW,
    "저": NTL_LOW,
    "신저": NTL_LOW,
    "low": NTL_LOW,
}

# 고저종
HL_HIGH_LOW = "1"  # 고저기준
HL_CLOSE = "2"  # 종가기준

# 기간 (52주=250)
DT_5 = "5"
DT_10 = "10"
DT_20 = "20"
DT_60 = "60"
DT_250 = "250"
DT_LABEL = {
    DT_5: "5일",
    DT_10: "10일",
    DT_20: "20일",
    DT_60: "60일",
    DT_250: "250일(52주)",
}


def normMrktTp(raw, default=MRKT_ALL):
    if raw is None or str(raw).strip() == "":
        return default
    key = str(raw).strip().lower()
    if key in MRKT_ALIAS:
        return MRKT_ALIAS[key]
    rawKey = str(raw).strip()
    if rawKey in MRKT_ALIAS:
        return MRKT_ALIAS[rawKey]
    return None


def normNtlTp(raw, default=NTL_HIGH):
    if raw is None or str(raw).strip() == "":
        return default
    key = str(raw).strip().lower()
    if key in NTL_ALIAS:
        return NTL_ALIAS[key]
    rawKey = str(raw).strip()
    if rawKey in NTL_ALIAS:
        return NTL_ALIAS[rawKey]
    return None


def normDt(raw, default=DT_250):
    """기간 코드 또는 별칭. 52/1년/year → 250."""
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip().lower()
    if text in ("52", "52주", "52w", "1년", "year", "y"):
        return DT_250
    if text in DT_LABEL or text in (DT_5, DT_10, DT_20, DT_60, DT_250):
        return text if text in DT_LABEL else text
    # 숫자만
    if text.isdigit() and text in DT_LABEL:
        return text
    return None


def getNtlPric(
    hostUrl,
    token,
    mrktTp=MRKT_ALL,
    ntlTp=NTL_HIGH,
    highLowCloseTp=HL_HIGH_LOW,
    stkCnd="0",
    trdeQtyTp="00000",
    crdCnd="0",
    updownIncls="0",
    dt=DT_250,
    stexTp="3",
    contYn="N",
    nextKey="",
    tokenType="bearer",
):
    """
    ka10016 신고저가 1회 호출.
    stex_tp: 1 KRX / 2 NXT / 3 통합
    """
    url = hostUrl + "/api/dostk/stkinfo"
    authType = (tokenType or "bearer").strip()
    if authType.lower() == "bearer":
        authType = "Bearer"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{authType} {token}",
        "cont-yn": contYn or "N",
        "next-key": nextKey or "",
        "api-id": SRC_TR,
    }
    data = {
        "mrkt_tp": str(mrktTp).strip(),
        "ntl_tp": str(ntlTp).strip(),
        "high_low_close_tp": str(highLowCloseTp).strip(),
        "stk_cnd": str(stkCnd).strip(),
        "trde_qty_tp": str(trdeQtyTp).strip(),
        "crd_cnd": str(crdCnd).strip(),
        "updown_incls": str(updownIncls).strip(),
        "dt": str(dt).strip(),
        "stex_tp": str(stexTp).strip(),
    }
    return requests.post(url, headers=headers, json=data, timeout=30)


def _hdr(res, name, default=""):
    if res is None:
        return default
    return str(res.headers.get(name, default) or default).strip()


def parseFluRt(raw):
    """등락률 문자열 → float (%). 실패 시 None."""
    if raw is None:
        return None
    text = str(raw).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def rowToRecord(row):
    """API row → 정규화 dict. 실패 시 None."""
    if not isinstance(row, dict):
        return None
    rawCd = str(row.get("stk_cd") or "").strip()
    # NXT/통합 접미사(_AL 등) 제거
    if "_" in rawCd:
        rawCd = rawCd.split("_")[0]
    stkCd = normStkCd(rawCd)
    stkNm = str(row.get("stk_nm") or "").strip()
    if not stkCd:
        return None
    cur = parsePadNum(row.get("cur_prc"))
    flu = parseFluRt(row.get("flu_rt"))
    predPre = parsePadNum(row.get("pred_pre"))
    trdeQty = parsePadNum(row.get("trde_qty"))
    highP = parsePadNum(row.get("high_pric"))
    lowP = parsePadNum(row.get("low_pric"))
    return {
        "stkCd": stkCd,
        "stkNm": stkNm or stkCd,
        "curPrc": abs(int(cur)) if cur is not None else None,
        "fluRt": flu,
        "predPre": predPre,
        "predPreSig": str(row.get("pred_pre_sig") or "").strip(),
        "trdeQty": abs(int(trdeQty)) if trdeQty is not None else None,
        "predTrdeQtyPreRt": parseFluRt(row.get("pred_trde_qty_pre_rt")),
        "highPric": abs(int(highP)) if highP is not None else None,
        "lowPric": abs(int(lowP)) if lowP is not None else None,
        "raw": row,
    }


def fetchNtlPric(
    hostUrl,
    token,
    mrktTp=MRKT_ALL,
    ntlTp=NTL_HIGH,
    highLowCloseTp=HL_HIGH_LOW,
    stkCnd="0",
    trdeQtyTp="00000",
    crdCnd="0",
    updownIncls="0",
    dt=DT_250,
    stexTp="3",
    tokenType="bearer",
    isPaper=True,
    minFluRt=None,
    bullOnly=False,
    maxRows=None,
):
    """
    ka10016 연속조회 후 정규화 rows 반환.
    minFluRt: 등락률 하한 (예 10 → +10% 이상만, 신고가용)
    bullOnly: fluRt > 0 만
    """
    waitSec = 1.05 if isPaper else 0.25
    contYn = "N"
    nextKey = ""
    pageUsed = 0
    rawRows = []

    while pageUsed < MAX_CONT_PAGES:
        if pageUsed > 0:
            time.sleep(waitSec)
        try:
            res = getNtlPric(
                hostUrl,
                token,
                mrktTp=mrktTp,
                ntlTp=ntlTp,
                highLowCloseTp=highLowCloseTp,
                stkCnd=stkCnd,
                trdeQtyTp=trdeQtyTp,
                crdCnd=crdCnd,
                updownIncls=updownIncls,
                dt=dt,
                stexTp=stexTp,
                contYn=contYn,
                nextKey=nextKey,
                tokenType=tokenType,
            )
        except Exception as err:
            return {
                "ok": False,
                "errMsg": f"요청 실패: {err}",
                "rows": [],
                "pageUsed": pageUsed,
            }

        try:
            body = res.json()
        except Exception as err:
            return {
                "ok": False,
                "errMsg": f"응답 파싱 실패: {err}",
                "rows": [],
                "pageUsed": pageUsed,
            }

        if res.status_code != 200:
            return {
                "ok": False,
                "errMsg": (
                    f"HTTP {res.status_code} "
                    f"[{body.get('return_code')}] {body.get('return_msg') or res.text}"
                ),
                "body": body,
                "rows": [],
                "pageUsed": pageUsed,
            }

        returnCode = body.get("return_code")
        returnMsg = body.get("return_msg", "")
        try:
            codeOk = int(returnCode) == 0
        except (TypeError, ValueError):
            codeOk = "ntl_pric" in body

        if not codeOk:
            # 첫 페이지 실패만 치명
            if pageUsed == 0:
                return {
                    "ok": False,
                    "errMsg": f"[{returnCode}] {returnMsg or '신고저가 조회 실패'}",
                    "body": body,
                    "rows": [],
                    "pageUsed": pageUsed,
                }
            break

        pageRows = body.get("ntl_pric") or []
        if isinstance(pageRows, list):
            rawRows.extend(pageRows)

        pageUsed += 1
        contYnHdr = (_hdr(res, "cont-yn") or _hdr(res, "Cont-Yn") or "N").upper()
        nextKeyHdr = _hdr(res, "next-key") or _hdr(res, "Next-Key")
        if contYnHdr != "Y" or not nextKeyHdr:
            break
        contYn = "Y"
        nextKey = nextKeyHdr

    # 정규화 + 필터
    out = []
    seen = set()
    for row in rawRows:
        rec = rowToRecord(row)
        if not rec:
            continue
        cd = rec["stkCd"]
        if cd in seen:
            continue
        seen.add(cd)
        flu = rec.get("fluRt")
        if bullOnly and (flu is None or flu <= 0):
            continue
        if minFluRt is not None:
            try:
                thr = float(minFluRt)
            except (TypeError, ValueError):
                thr = None
            if thr is not None:
                if flu is None or flu < thr:
                    continue
        out.append(rec)
        if maxRows is not None and len(out) >= int(maxRows):
            break

    # 등락률 큰 순
    out.sort(key=lambda r: (r.get("fluRt") is None, -(r.get("fluRt") or 0)))

    return {
        "ok": True,
        "rows": out,
        "rowCnt": len(out),
        "rawCnt": len(rawRows),
        "pageUsed": pageUsed,
        "mrktTp": str(mrktTp),
        "mrktLabel": MRKT_LABEL.get(str(mrktTp), str(mrktTp)),
        "ntlTp": str(ntlTp),
        "ntlLabel": NTL_LABEL.get(str(ntlTp), str(ntlTp)),
        "dt": str(dt),
        "dtLabel": DT_LABEL.get(str(dt), str(dt)),
        "minFluRt": minFluRt,
        "returnMsg": "",
    }
