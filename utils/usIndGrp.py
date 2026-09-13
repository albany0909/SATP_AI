"""미국 종목 테마/업종 그룹 해석"""

import json
from pathlib import Path

from api.getUsInqRank import STEX_LABEL, normUsStkCd
from utils.termLog import termLog

_MAP_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "data" / "usIndGrp.json"
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
        termLog(f"[usIndGrp] 맵 로드 실패: {err}")
        _cachedMap = {}
    return _cachedMap


def resolveUsIndGrp(stkCd, stkNm="", stexTp=""):
    """
    테마 그룹.
    byStkCd → byKeyword → 거래소명 → 기타
    """
    code = normUsStkCd(stkCd)
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
            if len(k) < 2:
                continue
            if k.upper() == code or k in name or k.upper() in name.upper():
                return grp

    stex = str(stexTp or "").strip().upper()
    if stex in STEX_LABEL:
        return STEX_LABEL[stex]
    return _DEFAULT_GRP


def enrichUsRankRecordsWithInd(records):
    """순위 record 목록에 IND_GRP 채움."""
    filled = 0
    for rec in records or []:
        if rec.get("IND_GRP"):
            filled += 1
            continue
        grp = resolveUsIndGrp(
            rec.get("STK_CD"),
            rec.get("STK_NM") or "",
            rec.get("STEX_TP") or "",
        )
        rec["IND_GRP"] = grp
        filled += 1
    return filled
