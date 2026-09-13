"""usflu — 미국 급등·급락 이슈 (usa20930)"""

from utils.termLog import termLog


def handleUsFlu(rawParts):
    """
    usflu              — 최신 적재분 요약
    usflu YYYYMMDD     — 해당일 요약
    usflu load [일자]  — API 적재 후 요약
    usflu last         — 적재 일자 목록
    """
    parts = [str(p) for p in (rawParts or [])]
    sub = parts[1].lower() if len(parts) >= 2 else ""

    if sub in ("last", "dates", "list"):
        from utils.usDayFluIssueStore import listUsFluTradeDts

        rows = listUsFluTradeDts(20)
        if not rows:
            return "[usflu] 적재분 없음. usflu load"
        lines = ["[미국급등락 적재일]"]
        for r in rows:
            lines.append(
                f"  {r.get('TRADE_DT')} · 전체 {r.get('CNT')} "
                f"(급등 {r.get('UP_CNT')} / 급락 {r.get('DN_CNT')})"
            )
        return "\n".join(lines)

    if sub == "load":
        ymd = parts[2] if len(parts) >= 3 else None
        if ymd:
            ymd = str(ymd).replace("-", "")[:8]
            if not (ymd.isdigit() and len(ymd) == 8):
                return "일자 형식: YYYYMMDD\n예) usflu load 20250811"
        from utils.usDayFluIssueLoad import loadUsDayFluIssue
        from utils.usDayFluIssueReport import formatUsFluIssueReport

        termLog(f"[usflu] load tradeDt={ymd or 'auto'}")
        res = loadUsDayFluIssue(tradeDt=ymd)
        if not res.get("ok"):
            return f"[usflu load 실패] {res.get('errMsg')}"
        msg = res.get("summary") or "적재 완료"
        report = formatUsFluIssueReport(res.get("tradeDt"))
        if report:
            msg = msg + "\n\n" + report
        return msg

    # 조회
    ymd = None
    if sub and sub.isdigit() and len(sub) == 8:
        ymd = sub
    elif sub in ("", "sum", "report"):
        ymd = None
    elif len(parts) >= 2:
        cand = str(parts[1]).replace("-", "")[:8]
        if cand.isdigit() and len(cand) == 8:
            ymd = cand

    from utils.usDayFluIssueReport import formatUsFluIssueReport
    from utils.usDayFluIssueStore import listUsFluTradeDts

    if not ymd:
        dts = listUsFluTradeDts(1)
        if not dts:
            return "[usflu] 적재분 없음. usflu load"
        ymd = dts[0].get("TRADE_DT")

    report = formatUsFluIssueReport(ymd)
    if not report:
        return f"[usflu] {ymd} 데이터 없음 — usflu load {ymd}"
    return report
