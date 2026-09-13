"""종목 테마/업종 그룹 해석 (순위 적재용) — 가급적 빈값 없이 채움"""

import json
import time
from pathlib import Path

from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_MAP_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "data" / "stkIndGrp.json"
)
_cachedMap = None
_DEFAULT_GRP = "기타"


def _loadMap():
    global _cachedMap
    if _cachedMap is not None:
        return _cachedMap
    try:
        with open(_MAP_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        _cachedMap = data if isinstance(data, dict) else {}
    except Exception as err:
        termLog(f"[stkIndGrp] 맵 로드 실패: {err}")
        _cachedMap = {}
    return _cachedMap


def reloadStkIndGrpMap():
    """테스트/갱신용 — 캐시 초기화."""
    global _cachedMap
    _cachedMap = None
    return _loadMap()


def fetchStkMetaByCds(stkCds):
    """
    AI_STK_INFO에서 종목코드별 UP_NAME / MARKET_NAME / STK_NM.
    반환: {stkCd: {upName, marketName, stkNm}}
    """
    codes = [normStkCd(c) for c in (stkCds or []) if normStkCd(c)]
    if not codes:
        return {}

    out = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            batch = 500
            for i in range(0, len(codes), batch):
                chunk = codes[i : i + batch]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"""
                    SELECT STK_CD, STK_NM, UP_NAME, MARKET_NAME
                    FROM AI_STK_INFO
                    WHERE STK_CD IN ({placeholders})
                    """,
                    chunk,
                )
                for row in cur.fetchall() or []:
                    cd = row.get("STK_CD")
                    if not cd:
                        continue
                    out[cd] = {
                        "upName": str(row.get("UP_NAME") or "").strip() or None,
                        "marketName": str(row.get("MARKET_NAME") or "").strip()
                        or None,
                        "stkNm": str(row.get("STK_NM") or "").strip() or None,
                    }
    except Exception as err:
        termLog(f"[stkIndGrp] 메타 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _updateUpNameInDb(stkCd, upName, stkNm=None):
    """캐시에 업종명이 비어 있을 때 ka10100 결과로 UP_NAME만 보강."""
    code = normStkCd(stkCd)
    nm = str(upName or "").strip()
    if not code or not nm:
        return
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if stkNm:
                cur.execute(
                    """
                    UPDATE AI_STK_INFO
                    SET UP_NAME=%s,
                        STK_NM=COALESCE(NULLIF(STK_NM,''), %s)
                    WHERE STK_CD=%s
                    """,
                    (nm, str(stkNm).strip(), code),
                )
            else:
                cur.execute(
                    "UPDATE AI_STK_INFO SET UP_NAME=%s WHERE STK_CD=%s",
                    (nm, code),
                )
        conn.commit()
    except Exception as err:
        termLog(f"[stkIndGrp] UP_NAME 보강 실패 {code}: {err}")
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def fillMissingUpNamesViaApi(
    missingCds,
    hostUrl,
    token,
    tokenType="bearer",
    isPaper=True,
    nameByCd=None,
):
    """
    UP_NAME 없는 종목을 ka10100으로 보강.
    반환: {stkCd: upName}
    """
    from api.getStkInfoByCd import fetchStkInfoByCd

    if not missingCds or not hostUrl or not token:
        return {}

    waitSec = 1.05 if isPaper else 0.25
    out = {}
    nameByCd = nameByCd or {}
    # 순위 상위만 보통 수십~100건 — 전체 보강
    for i, cd in enumerate(missingCds):
        if i > 0:
            time.sleep(waitSec)
        got = fetchStkInfoByCd(hostUrl, token, cd, tokenType=tokenType)
        if not got.get("ok"):
            continue
        body = got.get("body") or {}
        upName = str(body.get("upName") or "").strip() or None
        apiNm = str(body.get("name") or "").strip() or None
        if upName:
            out[cd] = upName
            _updateUpNameInDb(cd, upName, stkNm=apiNm or nameByCd.get(cd))
    if out:
        termLog(f"[stkIndGrp] ka10100 업종 보강 {len(out)}/{len(missingCds)}건")
    return out


def resolveIndGrp(stkCd, stkNm="", upName=""):
    """
    테마/업종 그룹.
    byStkCd → byKeyword → byUpName → UP_NAME 원문 → 기타
    """
    code = normStkCd(stkCd)
    name = str(stkNm or "").strip()
    data = _loadMap()

    byCd = data.get("byStkCd") or {}
    if code and code in byCd:
        return str(byCd[code]).strip() or _DEFAULT_GRP

    for rule in data.get("byKeyword") or []:
        grp = str(rule.get("grp") or "").strip()
        if not grp:
            continue
        for kw in rule.get("kw") or []:
            k = str(kw).strip()
            if not k:
                continue
            # 짧은 키워드 오탐 방지 (예: '테스' ⊂ '테스트')
            if len(k) < 3:
                if name == k:
                    return grp
                continue
            if k in name:
                return grp

    up = str(upName or "").strip()
    if up:
        byUp = data.get("byUpName") or {}
        if up in byUp:
            mapped = str(byUp[up]).strip()
            if mapped:
                return mapped
        return up

    return _DEFAULT_GRP


def enrichRankRecordsWithInd(
    records,
    hostUrl=None,
    token=None,
    tokenType="bearer",
    isPaper=True,
):
    """
    IND_GRP / UP_NAME 채움. 가능하면 빈칸 없이.
    UP_NAME 없으면(로그인 세션 있을 때) ka10100 보강.
    """
    if not records:
        return records

    cds = [r.get("STK_CD") for r in records if r.get("STK_CD")]
    metaMap = fetchStkMetaByCds(cds)

    missing = []
    nameByCd = {}
    for rec in records:
        cd = rec.get("STK_CD") or ""
        if not cd:
            continue
        meta = metaMap.get(cd) or {}
        nm = rec.get("STK_NM") or meta.get("stkNm") or ""
        nameByCd[cd] = nm
        if not meta.get("upName"):
            missing.append(cd)

    # 중복 제거·순서 유지
    seen = set()
    missingUniq = []
    for cd in missing:
        if cd not in seen:
            seen.add(cd)
            missingUniq.append(cd)

    apiUp = {}
    if missingUniq and hostUrl and token:
        apiUp = fillMissingUpNamesViaApi(
            missingUniq,
            hostUrl,
            token,
            tokenType=tokenType,
            isPaper=isPaper,
            nameByCd=nameByCd,
        )

    for rec in records:
        cd = rec.get("STK_CD") or ""
        meta = metaMap.get(cd) or {}
        upName = meta.get("upName") or apiUp.get(cd) or None
        stkNm = rec.get("STK_NM") or meta.get("stkNm") or ""
        rec["UP_NAME"] = upName
        rec["IND_GRP"] = resolveIndGrp(cd, stkNm, upName or "")

    return records


# 하위 호환
def fetchUpNamesByCds(stkCds):
    meta = fetchStkMetaByCds(stkCds)
    return {k: v.get("upName") for k, v in meta.items() if v.get("upName")}
