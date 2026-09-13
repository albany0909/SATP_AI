"""
creport 명령 — 증권사 리포트 분석·DB 적재·조회
cns 명령 — 예상실적(FnSpace 유료 스킵 / 리포트 파생 조회)

  creport {종목}              — 즉시 분석 텍스트(비저장)
  creport load {종목}         — 수집·요약·가이던스 DB 적재
  creport last {종목} [N]     — DB 최근 리포트
  creport guide {종목} [N]    — 가이던스 표시분만
  cns load {종목}             — FnSpace 스킵 안내
  cns last {종목}             — 리포트 파생 목표가/의견 조회
"""

# 재무·리포트 비즈니스는 Finance Agent 공개 API만 사용
from financeAgent.serviceApi import (
    analyzeCorpReports,
    formatCnsLastMsg,
    formatRptLastMsg,
    loadAndStoreCorpReports,
    loadCnsFromFnSpace,
)


def handleCorpReport(stkRefOrParts):
    """
    creport 라우팅. 문자열 또는 rawParts[1:] 리스트.
    """
    if isinstance(stkRefOrParts, (list, tuple)):
        parts = [str(p) for p in stkRefOrParts]
    else:
        parts = str(stkRefOrParts or "").strip().split()

    usage = (
        "사용법:\n"
        "  creport {종목} — 실시간 분석(저장 안 함)\n"
        "  creport load {종목} — 리포트 수집·가이던스·요약 DB 적재\n"
        "  creport last {종목} [건수] — DB 조회\n"
        "  creport guide {종목} [건수] — 가이던스 표시분\n"
        "  cns load {종목} — FnSpace 예상실적(유료 스킵)\n"
        "  cns last {종목} — 리포트 파생 목표가/의견\n"
        "예) creport load 삼성전자\n"
        "※ PDF 전문 미저장 · 투자 권유 아님"
    )

    if not parts:
        return usage

    head = parts[0].lower()
    if head == "load":
        stk = " ".join(parts[1:]).strip()
        if not stk:
            return usage
        res = loadAndStoreCorpReports(stk, maxPages=2, maxItems=15, detailTopN=8)
        if not res.get("ok"):
            return _failMsg(res)
        return res.get("msgText") or "적재 완료"

    if head in ("last", "list", "db"):
        stk = parts[1] if len(parts) >= 2 else ""
        lim = 10
        if len(parts) >= 3 and str(parts[2]).isdigit():
            lim = int(parts[2])
        if not stk:
            return usage
        from utils.stkResolve import resolveStkRef

        r = resolveStkRef(stk)
        if not r.get("ok"):
            return r.get("errMsg") or "종목 오류"
        return formatRptLastMsg(r["stkCd"], guideOnly=False, limit=lim)

    if head in ("guide", "g", "가이던스"):
        stk = parts[1] if len(parts) >= 2 else ""
        lim = 10
        if len(parts) >= 3 and str(parts[2]).isdigit():
            lim = int(parts[2])
        if not stk:
            return usage
        from utils.stkResolve import resolveStkRef

        r = resolveStkRef(stk)
        if not r.get("ok"):
            return r.get("errMsg") or "종목 오류"
        return formatRptLastMsg(r["stkCd"], guideOnly=True, limit=lim)

    # 기본: 즉시 분석
    stk = " ".join(parts).strip()
    result = analyzeCorpReports(stk, maxPages=2, maxItems=20, detailTopN=4)
    if not result.get("ok"):
        return _failMsg(result)
    return result.get("msgText") or usage


def handleCns(parts):
    """cns load|last"""
    usage = (
        "사용법:\n"
        "  cns load {종목} — FnSpace 예상실적 (유료면 스킵)\n"
        "  cns last {종목} — 리포트 파생 목표가·의견 DB\n"
        "예) cns last 005930"
    )
    if not parts:
        return usage
    head = str(parts[0]).lower()
    if head == "load":
        stk = " ".join(parts[1:]).strip()
        if not stk:
            return usage
        from utils.stkResolve import resolveStkRef

        r = resolveStkRef(stk)
        code = r.get("stkCd") if r.get("ok") else stk
        res = loadCnsFromFnSpace(code)
        return res.get("msg") or usage
    if head in ("last", "list", "show"):
        stk = " ".join(parts[1:]).strip()
        if not stk:
            return usage
        from utils.stkResolve import resolveStkRef

        r = resolveStkRef(stk)
        if not r.get("ok"):
            return r.get("errMsg") or "종목 오류"
        return formatCnsLastMsg(r["stkCd"])
    return usage


def _failMsg(result):
    err = (result or {}).get("errMsg") or "실패"
    matches = (result or {}).get("matches") or []
    if not matches:
        return err
    lines = [err, "후보:"]
    for m in matches[:12]:
        lines.append(
            f"  {m.get('STK_CD') or m.get('stkCd')} "
            f"{m.get('STK_NM') or m.get('stkNm') or ''}"
        )
    return "\n".join(lines)
