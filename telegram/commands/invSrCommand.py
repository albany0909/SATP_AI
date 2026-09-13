"""invsr — ka10059 종목별투자자 DB 적재·조회"""

from utils.dayInvSrLoad import loadInvSrForStk, loadInvSrUniverse
from utils.dayInvSrStore import latestInvSrMeta, listInvSrByStk
from utils.stkResolve import formatStkLabel, resolveStkRef
from utils.termLog import termLog


def _fmtYmdKr(ymd):
    text = str(ymd or "")
    if len(text) == 8:
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _fmtSigned(num):
    if num is None:
        return "-"
    n = int(num)
    if n > 0:
        return f"+{n:,}"
    return f"{n:,}"


def handleInvSr(rawParts):
    """
    invsr / invsr {종목}
    invsr load / invsr load {종목} [YYYYMMDD]
    invsr last
    """
    usage = (
        "사용법:\n"
        "  invsr {종목코드|종목명} — DB 조회 (순매수·단주)\n"
        "  invsr load — 관심∪순위 전량 적재(ka10059)\n"
        "  invsr load {종목} [YYYYMMDD] — 단건 적재\n"
        "  invsr last — 최근 적재 메타\n"
        "※ 자동: 거래일 18:00 관심·순위 종목 적재"
    )
    parts = [p for p in (rawParts or [])[1:] if str(p).strip()]
    if not parts:
        return usage

    sub = str(parts[0]).lower()

    if sub in ("last", "db", "적재"):
        meta = latestInvSrMeta()
        if not meta:
            return "[종목별투자자] 적재분 없음. invsr load"
        return (
            f"[종목별투자자] 최근 적재\n"
            f"일자: {_fmtYmdKr(meta.get('tradeDt'))}\n"
            f"종목수: {meta.get('stkCnt')} · 행: {meta.get('rowCnt')}\n"
            f"갱신: {meta.get('updatedAt')}"
        )

    if sub in ("load", "적재"):
        rest = parts[1:]
        # invsr load YYYYMMDD
        if len(rest) == 1 and len(str(rest[0]).replace("-", "")) == 8:
            ymd = str(rest[0]).replace("-", "")[:8]
            termLog(f"[invsr] 유니버스 적재 {ymd}")
            result = loadInvSrUniverse(tradeDt=ymd)
            return result.get("summary") or result.get("errMsg") or "적재 완료"
        # invsr load {종목} [일자]
        if rest:
            stkRef = rest[0]
            ymd = None
            if len(rest) >= 2:
                ymd = str(rest[1]).replace("-", "")[:8]
            result = loadInvSrForStk(stkRef, tradeDt=ymd)
            if not result.get("ok"):
                return f"[종목별투자자실패] {result.get('errMsg')}"
            label = formatStkLabel(result.get("stkCd"), result.get("stkNm"))
            return (
                f"[종목별투자자] 단건 적재\n"
                f"{label} · {result.get('rowCnt')}일 · "
                f"UPSERT {result.get('upsertCnt')}"
            )
        termLog("[invsr] 유니버스 적재")
        result = loadInvSrUniverse()
        return result.get("summary") or result.get("errMsg") or "적재 완료"

    # invsr {종목}
    stkRef = " ".join(parts).strip()
    resolved = resolveStkRef(stkRef)
    if not resolved.get("ok"):
        return resolved.get("errMsg") or usage
    stkCd = resolved["stkCd"]
    rows = listInvSrByStk(stkCd, limit=15)
    if not rows:
        return (
            f"[종목별투자자] {formatStkLabel(stkCd, resolved.get('stkNm'))} "
            f"적재 없음.\ninvsr load {stkCd}"
        )
    lines = [
        f"[종목별투자자] {formatStkLabel(stkCd, resolved.get('stkNm'))} (순매수·단주)",
        "일자 | 종가 | 등락% | 개인 | 외인 | 기관",
    ]
    for r in rows[:12]:
        flu = r.get("FLU_RT")
        fluS = f"{float(flu):+.2f}" if flu is not None else "-"
        prc = r.get("CUR_PRC")
        prcS = f"{int(prc):,}" if prc is not None else "-"
        lines.append(
            f"{_fmtYmdKr(r.get('TRADE_DT'))} | {prcS} | {fluS} | "
            f"{_fmtSigned(r.get('IND_INVSR'))} | "
            f"{_fmtSigned(r.get('FRGNR_INVSR'))} | "
            f"{_fmtSigned(r.get('ORGN'))}"
        )
    return "\n".join(lines)
