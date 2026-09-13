"""미국 급등·급락 이슈 텔레그램/웹 요약 문구"""

from __future__ import annotations

from api.getUsFluStk import FLU_DN, FLU_LABEL, FLU_UP
from utils.usDayFluIssueStore import listUsFluIssue, listUsFluThemeAgg


def _fmtRt(v):
    if v is None:
        return "—"
    try:
        n = float(v)
        return f"{n:+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmtPrc(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _themeLines(tradeDt, fluTp, topN=8):
    rows = listUsFluThemeAgg(tradeDt, fluTp=fluTp, topN=topN)
    filtered = [r for r in rows if str(r.get("FLU_TP")) == str(fluTp)]
    lines = []
    for r in filtered[:topN]:
        grp = r.get("IND_GRP") or "기타"
        cnt = int(r.get("CNT") or 0)
        avg = r.get("AVG_FLU")
        lines.append(f"  · {grp} · {cnt}종 · 평균 {_fmtRt(avg)}")
    return lines


def _stkLines(tradeDt, fluTp, topN=12):
    rows = listUsFluIssue(tradeDt, fluTp=fluTp, limit=topN)
    lines = []
    for r in rows[:topN]:
        cd = r.get("STK_CD") or ""
        nm = (r.get("STK_NM") or "")[:16]
        grp = r.get("IND_GRP") or ""
        tag = f" [{grp}]" if grp and grp != "기타" else ""
        lines.append(
            f"  {int(r.get('RANK_NO') or 0):2d}. {cd} {nm}{_fmtRt(r.get('FLU_RT'))}"
            f" {_fmtPrc(r.get('CUR_PRC'))}{tag}"
        )
    return lines


def formatUsFluIssueReport(tradeDt, topTheme=8, topStk=12):
    """
    아침 브리핑용 텍스트.
    DB에 해당일 없으면 빈 문자열.
    """
    up = listUsFluIssue(tradeDt, fluTp=FLU_UP, limit=1)
    dn = listUsFluIssue(tradeDt, fluTp=FLU_DN, limit=1)
    if not up and not dn:
        return ""

    dtDisp = f"{str(tradeDt)[:4]}-{str(tradeDt)[4:6]}-{str(tradeDt)[6:8]}"
    lines = [
        f"[미국 급등·급락 이슈] 세션 {dtDisp}",
        "※ 키움 usa20930 · 테마=내부맵 · 투자권유 아님",
        "",
        f"■ {FLU_LABEL[FLU_UP]} 테마",
    ]
    tUp = _themeLines(tradeDt, FLU_UP, topTheme)
    lines.extend(tUp if tUp else ["  (없음)"])
    lines.append(f"■ {FLU_LABEL[FLU_UP]} 종목 TOP{topStk}")
    sUp = _stkLines(tradeDt, FLU_UP, topStk)
    lines.extend(sUp if sUp else ["  (없음)"])
    lines.append("")
    lines.append(f"■ {FLU_LABEL[FLU_DN]} 테마")
    tDn = _themeLines(tradeDt, FLU_DN, topTheme)
    lines.extend(tDn if tDn else ["  (없음)"])
    lines.append(f"■ {FLU_LABEL[FLU_DN]} 종목 TOP{topStk}")
    sDn = _stkLines(tradeDt, FLU_DN, topStk)
    lines.extend(sDn if sDn else ["  (없음)"])
    return "\n".join(lines)


def slimFluRows(rows):
    out = []
    for r in rows or []:
        out.append(
            {
                "tradeDt": r.get("TRADE_DT"),
                "fluTp": r.get("FLU_TP"),
                "fluLabel": FLU_LABEL.get(str(r.get("FLU_TP")), ""),
                "rankNo": r.get("RANK_NO"),
                "stkCd": r.get("STK_CD"),
                "stkNm": r.get("STK_NM"),
                "stkEnm": r.get("STK_ENM"),
                "stexTp": r.get("STEX_TP"),
                "indGrp": r.get("IND_GRP"),
                "fluRt": float(r["FLU_RT"]) if r.get("FLU_RT") is not None else None,
                "curPrc": float(r["CUR_PRC"]) if r.get("CUR_PRC") is not None else None,
                "predPre": float(r["PRED_PRE"]) if r.get("PRED_PRE") is not None else None,
                "accTrdeQty": (
                    float(r["ACC_TRDE_QTY"]) if r.get("ACC_TRDE_QTY") is not None else None
                ),
            }
        )
    return out


def slimThemeRows(rows):
    out = []
    for r in rows or []:
        out.append(
            {
                "fluTp": r.get("FLU_TP"),
                "fluLabel": FLU_LABEL.get(str(r.get("FLU_TP")), ""),
                "indGrp": r.get("IND_GRP"),
                "cnt": int(r.get("CNT") or 0),
                "avgFlu": float(r["AVG_FLU"]) if r.get("AVG_FLU") is not None else None,
                "maxFlu": float(r["MAX_FLU"]) if r.get("MAX_FLU") is not None else None,
                "minFlu": float(r["MIN_FLU"]) if r.get("MIN_FLU") is not None else None,
            }
        )
    return out
