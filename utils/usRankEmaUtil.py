"""미국 순위 요약 — EMA10/20/50 + 업종/테마 종합"""

from strategies.indicators import calcEmaSeries
from utils.dbConn import openDbConn
from utils.rankEmaUtil import formatEmaAlign, formatIndThemeReport, parseChgrPct
from api.getUsInqRank import QRY_TP_LABEL, normUsStkCd
from utils.usIndGrp import resolveUsIndGrp

EMA_PERIODS = (10, 20, 50)
_LOOKBACK_BARS = 120


def loadUsDayClosesAsc(stkCd, limit=_LOOKBACK_BARS):
    code = normUsStkCd(stkCd)
    if not code:
        return []
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CLOSE_PRC
                FROM AI_US_DAY_STK_PRC
                WHERE STK_CD=%s AND CLOSE_PRC IS NOT NULL
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (code, int(limit)),
            )
            rows = cur.fetchall() or []
    except Exception:
        return []
    finally:
        conn.close()

    closes = []
    for r in reversed(rows):
        try:
            closes.append(float(r.get("CLOSE_PRC")))
        except (TypeError, ValueError):
            continue
    return closes


def getUsEmaAlignText(stkCd):
    closes = loadUsDayClosesAsc(stkCd)
    if len(closes) < max(EMA_PERIODS):
        return "이평부족"
    e10 = calcEmaSeries(closes, 10)
    e20 = calcEmaSeries(closes, 20)
    e50 = calcEmaSeries(closes, 50)
    if not e10 or not e20 or not e50:
        return "이평부족"
    return formatEmaAlign(e10[-1], e20[-1], e50[-1])


def getUsEmaAlignMap(stkCds):
    out = {}
    for raw in stkCds or []:
        cd = normUsStkCd(raw)
        if not cd or cd in out:
            continue
        out[cd] = getUsEmaAlignText(cd)
    return out


def _fmtYmdKr(ymd):
    s = str(ymd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s or "-"


def _fmtUpdatedAt(raw):
    if raw is None:
        return "-"
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d %H:%M:%S")
    text = str(raw).strip()
    return text if text else "-"


def formatUsRankSummaryFromDb(tradeDt, qryTp="4", topN=20, updatedAt=None, qryLabel=None):
    """AI_US_DAY_INQ_RANK 스냅샷 요약."""
    ymd = str(tradeDt or "").strip()
    if len(ymd) != 8:
        return None

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            snapUpd = updatedAt
            if snapUpd is None:
                cur.execute(
                    """
                    SELECT MAX(UPDATED_AT) AS UPDATED_AT
                    FROM AI_US_DAY_INQ_RANK
                    WHERE TRADE_DT=%s AND QRY_TP=%s
                    """,
                    (ymd, str(qryTp)),
                )
                meta = cur.fetchone() or {}
                snapUpd = meta.get("UPDATED_AT")

            cur.execute(
                """
                SELECT RANK_NO, STK_CD, STK_NM, STEX_TP, IND_GRP, CUR_PRC,
                       BASE_COMP_CHGR, RANK_CHG, RANK_CHG_SIGN
                FROM AI_US_DAY_INQ_RANK
                WHERE TRADE_DT=%s AND QRY_TP=%s
                ORDER BY RANK_NO ASC
                LIMIT %s
                """,
                (ymd, str(qryTp), int(topN)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    if not rows:
        return None

    cds = [r.get("STK_CD") for r in rows if r.get("STK_CD")]
    emaMap = getUsEmaAlignMap(cds)
    label = qryLabel or QRY_TP_LABEL.get(str(qryTp), str(qryTp))

    lines = [
        f"[미국순위적재] 기준일 {_fmtYmdKr(ymd)} · {label}",
        f"적재시각: {_fmtUpdatedAt(snapUpd)} · 표시 상위 {len(rows)}건",
        "※ [업종/테마] · 10/20/50 이평 대소(값 큰 순, 예: 50>10>20)",
        "",
    ]
    indItems = []
    for r in rows:
        cd = r.get("STK_CD") or ""
        nm = r.get("STK_NM") or cd
        rank = r.get("RANK_NO")
        ind = (r.get("IND_GRP") or "").strip()
        if not ind:
            ind = resolveUsIndGrp(cd, nm, r.get("STEX_TP") or "") or "기타"
        stex = (r.get("STEX_TP") or "").strip()
        stexTxt = f"/{stex}" if stex else ""
        prc = r.get("CUR_PRC")
        prcTxt = f"{float(prc):,.4f}" if prc is not None else "-"
        chgrF = parseChgrPct(r.get("BASE_COMP_CHGR"))
        chgrTxt = f"{chgrF:+.2f}%" if chgrF is not None else ""
        emaTxt = emaMap.get(normUsStkCd(cd) or cd, "이평부족")
        lines.append(
            f"{rank}. {nm}({cd}{stexTxt}) [{ind}] "
            f"{prcTxt} {chgrTxt} | {emaTxt}".rstrip()
        )
        indItems.append({"ind": ind, "chgr": chgrF})
    lines.extend(formatIndThemeReport(indItems))
    return "\n".join(lines)


def formatLatestUsRankFromDb(qryTp=None, topN=20):
    from utils.usDayInqRankStore import findLatestUsDayInqRankMeta

    meta = findLatestUsDayInqRankMeta(qryTp=qryTp)
    if not meta:
        tip = f" (구분 {qryTp})" if qryTp else ""
        return f"[미국순위적재] 저장된 데이터가 없습니다{tip}.\n→ usrank load 로 적재하세요."

    label = QRY_TP_LABEL.get(str(meta.get("qryTp")), str(meta.get("qryTp")))
    return formatUsRankSummaryFromDb(
        meta.get("tradeDt"),
        qryTp=meta.get("qryTp"),
        topN=topN,
        updatedAt=meta.get("updatedAt"),
        qryLabel=label,
    )
