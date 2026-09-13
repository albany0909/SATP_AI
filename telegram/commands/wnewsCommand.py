"""
wnews — 관심종목 공시·뉴스 적재 상태/수동 실행/최근 목록

  wnews              — 도움말
  wnews run [force]  — 즉시 수집 (force=시간대 무시)
  wnews status       — 스케줄 상태
  wnews last [종목] [N] — 최근 공시·뉴스 DB
  wnews disc [종목]  — 공시만
  wnews news [종목]  — 뉴스만
"""

from financeAgent.serviceApi import (
    formatWatchNewsStatusMsg,
    listWatchRecentDisc,
    listWatchRecentNews,
    runWatchNewsDiscNow,
)


def handleWnews(parts):
    if not parts:
        return (
            "사용법 (Finance Agent · 관심 공시·뉴스):\n"
            "  wnews run [force] — 즉시 수집\n"
            "  wnews status — 스케줄 상태\n"
            "  wnews last [종목] [건수] — 최근 공시+뉴스\n"
            "  wnews disc [종목] [N]\n"
            "  wnews news [종목] [N]\n"
            "주기: 매일 08:00~22:00 · 30분 · 신규만 텔레그램\n"
            "※ 본문 미저장 · 투자 권유 아님"
        )

    head = str(parts[0]).lower()
    if head in ("status", "st"):
        return formatWatchNewsStatusMsg()

    if head in ("run", "load", "now"):
        force = any(str(p).lower() in ("force", "1", "y", "true") for p in parts[1:])
        res = runWatchNewsDiscNow(force=force)
        if res.get("skipped"):
            return res.get("errMsg") or "이미 수집 중"
        if not res.get("ok") and res.get("errMsg"):
            return f"[wnews] {res.get('errMsg')}"
        return res.get("summary") or str(res)

    lim = 8
    stk = None
    rest = parts[1:] if head in ("last", "disc", "news", "list") else parts
    if rest:
        # 마지막이 숫자면 limit
        if str(rest[-1]).isdigit():
            lim = int(rest[-1])
            rest = rest[:-1]
        if rest:
            stk = " ".join(rest).strip()

    if head in ("last", "list", "all"):
        return _fmtBoth(stk, lim)
    if head in ("disc", "공시"):
        return _fmtDisc(stk, lim)
    if head in ("news", "뉴스", "issue"):
        return _fmtNews(stk, lim)

    return handleWnews([])


def _resolveCd(stkRef):
    if not stkRef:
        return None, None
    from utils.stkResolve import resolveStkRef

    r = resolveStkRef(stkRef)
    if not r.get("ok"):
        return None, r.get("errMsg") or "종목 오류"
    return r["stkCd"], None


def _fmtBoth(stkRef, lim):
    cd, err = _resolveCd(stkRef) if stkRef else (None, None)
    if err:
        return err
    d = listWatchRecentDisc(cd, lim)
    n = listWatchRecentNews(cd, lim)
    lines = [f"[관심 공시·뉴스 최근] {cd or '전체'}"]
    lines.append("— 공시 —")
    if not d:
        lines.append("(없음)")
    for r in d:
        lines.append(
            f"{r.get('RCEPT_DT')} {r.get('STK_CD')} {(r.get('REPORT_NM') or '')[:50]}"
        )
    lines.append("— 뉴스 —")
    if not n:
        lines.append("(없음)")
    for r in n:
        tag = "!" if r.get("ISSUE_YN") == "Y" else " "
        lines.append(
            f"{tag}{r.get('PUB_DT')} {r.get('STK_CD')} {(r.get('TITLE') or '')[:50]}"
        )
    return "\n".join(lines)


def _fmtDisc(stkRef, lim):
    cd, err = _resolveCd(stkRef) if stkRef else (None, None)
    if err:
        return err
    rows = listWatchRecentDisc(cd, lim)
    if not rows:
        return f"[공시] {cd or '전체'} 없음 — wnews run 으로 수집"
    lines = [f"[공시 최근] {cd or '전체'}"]
    for r in rows:
        lines.append(
            f"{r.get('RCEPT_DT')} {r.get('STK_CD')} {(r.get('REPORT_NM') or '')[:60]}\n"
            f"  {r.get('VIEW_URL') or ''}"
        )
    return "\n".join(lines)


def _fmtNews(stkRef, lim):
    cd, err = _resolveCd(stkRef) if stkRef else (None, None)
    if err:
        return err
    rows = listWatchRecentNews(cd, lim)
    if not rows:
        return f"[뉴스] {cd or '전체'} 없음 — wnews run 으로 수집"
    lines = [f"[뉴스 최근] {cd or '전체'}"]
    for r in rows:
        tag = "[이슈]" if r.get("ISSUE_YN") == "Y" else "[뉴스]"
        lines.append(
            f"{tag} {r.get('PUB_DT')} {r.get('STK_CD')} {(r.get('TITLE') or '')[:60]}\n"
            f"  {r.get('NEWS_URL') or ''}"
        )
    return "\n".join(lines)
