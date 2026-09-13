"""순위 요약용 일봉 EMA10/20/50 대소 비교 + 업종/테마 종합"""

from collections import defaultdict

from strategies.indicators import calcEmaSeries
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.stkIndGrp import resolveIndGrp

EMA_PERIODS = (10, 20, 50)
# EMA50 시드에 여유를 두고 조회
_LOOKBACK_BARS = 120
# 종합 리포트에 표시할 업종/테마 상위 개수
_IND_REPORT_TOP = 5


def parseChgrPct(raw):
    """등락률 문자열/숫자 → float(%). 실패 시 None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace("%", "").replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def formatIndThemeReport(items, topN=_IND_REPORT_TOP):
    """
    업종/테마 종합 리포트 텍스트 줄 목록.
    items: [{"ind": "반도체", "chgr": 1.2}, ...]  (chgr=% 단위, None 허용)

    - 관심多: 순위 진입 종목 수
    - 상승强: 평균 등락률 (표본 있는 테마만)
    """
    if not items:
        return []

    cntMap = defaultdict(int)
    sumMap = defaultdict(float)
    nMap = defaultdict(int)
    for it in items:
        ind = (it.get("ind") or "").strip() or "기타"
        cntMap[ind] += 1
        chgr = it.get("chgr")
        if chgr is None:
            continue
        try:
            chgrF = float(chgr)
        except (TypeError, ValueError):
            continue
        sumMap[ind] += chgrF
        nMap[ind] += 1

    # 관심: 건수 내림차순, 동률이면 이름
    hotList = sorted(cntMap.items(), key=lambda x: (-x[1], x[0]))[:topN]
    hotTxt = " > ".join(f"{name}({cnt})" for name, cnt in hotList) if hotList else "-"

    # 상승: 평균 등락률 내림차순 (표본≥1)
    avgList = []
    for ind, n in nMap.items():
        if n <= 0:
            continue
        avgList.append((ind, sumMap[ind] / n, n))
    avgList.sort(key=lambda x: (-x[1], -x[2], x[0]))
    riseList = avgList[:topN]
    riseTxt = (
        " > ".join(f"{name}({avg:+.2f}%,{n}종)" for name, avg, n in riseList)
        if riseList
        else "-"
    )

    return [
        "",
        "[업종/테마 종합]",
        f"관심多: {hotTxt}",
        f"상승强: {riseTxt}",
    ]


def _cmpSign(a, b, epsRatio=1e-6):
    """a ? b → '>' | '<' | '='."""
    if a is None or b is None:
        return "?"
    scale = max(abs(a), abs(b), 1.0)
    if abs(a - b) / scale <= epsRatio:
        return "="
    return ">" if a > b else "<"


def formatEmaAlign(ema10, ema20, ema50):
    """
    EMA 대소 → 값 큰 이평이 앞에 오는 짧은 문자열.
    예) 50>10>20 / 10=20>50
    일부만 있으면 가능분만 표시. 전부 없으면 '이평부족'
    """
    items = []
    if ema10 is not None:
        items.append((10, float(ema10)))
    if ema20 is not None:
        items.append((20, float(ema20)))
    if ema50 is not None:
        items.append((50, float(ema50)))

    if not items:
        return "이평부족"

    # 값 내림차순(동률이면 기간 긴 순)
    items.sort(key=lambda x: (-x[1], -x[0]))
    parts = [str(period) for period, _ in items]

    if len(parts) == 1:
        missing = [p for p in (10, 20, 50) if p not in {items[0][0]}]
        lack = ",".join(str(p) for p in missing)
        return f"{parts[0]}({lack}부족)" if lack else parts[0]

    out = parts[0]
    for i in range(len(items) - 1):
        out += f"{_cmpSign(items[i][1], items[i + 1][1])}{parts[i + 1]}"

    have = {p for p, _ in items}
    missing = [p for p in (10, 20, 50) if p not in have]
    if missing:
        out += f"({'/'.join(str(p) for p in missing)}부족)"
    return out


def loadDayClosesAsc(stkCd, limit=_LOOKBACK_BARS):
    """
    AI_DAY_STK_PRC 종가 (오래된→최신).
    반환: [close, ...] 또는 []
    """
    code = normStkCd(stkCd)
    if not code:
        return []
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            # 최신 limit건을 가져와 시간 오름차순으로 뒤집음
            cur.execute(
                """
                SELECT CLOSE_PRC
                FROM (
                  SELECT TRADE_DT, CLOSE_PRC
                  FROM AI_DAY_STK_PRC
                  WHERE STK_CD=%s AND CLOSE_PRC IS NOT NULL
                  ORDER BY TRADE_DT DESC
                  LIMIT %s
                ) t
                ORDER BY TRADE_DT ASC
                """,
                (code, int(limit)),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    closes = []
    for r in rows:
        try:
            closes.append(float(r["CLOSE_PRC"]))
        except (TypeError, ValueError):
            continue
    return closes


def calcEma102050(closes):
    """
    종가(오름차순) → 최신 EMA10/20/50.
    반환: (e10, e20, e50) — 부족 시 None 포함
    """
    if not closes:
        return None, None, None
    e10s = calcEmaSeries(closes, 10)
    e20s = calcEmaSeries(closes, 20)
    e50s = calcEmaSeries(closes, 50)
    return e10s[-1], e20s[-1], e50s[-1]


def getEmaAlignText(stkCd):
    """종목 1건 EMA 부등호 텍스트."""
    closes = loadDayClosesAsc(stkCd)
    e10, e20, e50 = calcEma102050(closes)
    return formatEmaAlign(e10, e20, e50)


def getEmaAlignMap(stkCds):
    """
    여러 종목 EMA 부등호.
    반환: {stkCd: '50>20>10' | '이평부족'}
    """
    out = {}
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if not cd or cd in out:
            continue
        out[cd] = getEmaAlignText(cd)
    return out


def _fmtYmdKr(ymd):
    """YYYYMMDD → YYYY-MM-DD."""
    s = str(ymd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s or "-"


def _fmtUpdatedAt(raw):
    """datetime/str → 표시용."""
    if raw is None:
        return "-"
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d %H:%M:%S")
    text = str(raw).strip()
    return text if text else "-"


def formatRankEmaSummaryFromDb(tradeDt, qryTp="4", topN=20, updatedAt=None, qryLabel=None):
    """
    AI_DAY_INQ_RANK 스냅샷 + EMA 대소 요약 텍스트.
    일봉 적재 후 알림 / rank last 조회용.
    """
    from api.getInqRank import QRY_TP_LABEL

    ymd = str(tradeDt or "").strip()
    if len(ymd) != 8:
        return None

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            # 적재시각 — 인자 없으면 해당 스냅샷 MAX(UPDATED_AT)
            snapUpd = updatedAt
            if snapUpd is None:
                cur.execute(
                    """
                    SELECT MAX(UPDATED_AT) AS UPDATED_AT
                    FROM AI_DAY_INQ_RANK
                    WHERE TRADE_DT=%s AND QRY_TP=%s
                    """,
                    (ymd, str(qryTp)),
                )
                meta = cur.fetchone() or {}
                snapUpd = meta.get("UPDATED_AT")

            cur.execute(
                """
                SELECT RANK_NO, STK_CD, STK_NM, IND_GRP, CUR_PRC,
                       BASE_COMP_CHGR, RANK_CHG, RANK_CHG_SIGN
                FROM AI_DAY_INQ_RANK
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
    emaMap = getEmaAlignMap(cds)

    label = qryLabel or QRY_TP_LABEL.get(str(qryTp), str(qryTp))
    lines = [
        f"[순위적재] 기준일 {_fmtYmdKr(ymd)} · {label}",
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
            ind = resolveIndGrp(cd, nm, "") or "기타"
        prc = r.get("CUR_PRC")
        prcTxt = f"{int(prc):,}" if prc is not None else "-"
        chgr = r.get("BASE_COMP_CHGR")
        chgrF = parseChgrPct(chgr)
        chgrTxt = f"{chgrF:+.2f}%" if chgrF is not None else ""
        emaTxt = emaMap.get(normStkCd(cd) or cd, "이평부족")
        # 이평 부족이어도 종목명·코드는 항상 표시
        lines.append(
            f"{rank}. {nm}({cd}) [{ind}] {prcTxt} {chgrTxt} | {emaTxt}".rstrip()
        )
        indItems.append({"ind": ind, "chgr": chgrF})
    lines.extend(formatIndThemeReport(indItems))
    return "\n".join(lines)


def formatLatestRankFromDb(qryTp=None, topN=20):
    """
    가장 최근 적재 스냅샷 요약.
    qryTp 없으면 전체 중 UPDATED_AT 최신.
    """
    from utils.dayInqRankStore import findLatestDayInqRankMeta
    from api.getInqRank import QRY_TP_LABEL

    meta = findLatestDayInqRankMeta(qryTp=qryTp)
    if not meta:
        tip = f" (구분 {qryTp})" if qryTp else ""
        return f"[순위적재] 저장된 데이터가 없습니다{tip}.\n→ rank load 로 적재하세요."

    label = QRY_TP_LABEL.get(str(meta.get("qryTp")), str(meta.get("qryTp")))
    return formatRankEmaSummaryFromDb(
        meta.get("tradeDt"),
        qryTp=meta.get("qryTp"),
        topN=topN,
        updatedAt=meta.get("updatedAt"),
        qryLabel=label,
    )
