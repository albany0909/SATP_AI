"""
ntl / 신고 — ka10016 신고저가 조회

ntl                  → 52주 신고가, 등락률≥10% (장대양봉 관심)
ntl first            → 당일 '최초' 52주 신고 진입만 (일봉 검증)
ntl 10               → 등락률 하한 % (기본 10)
ntl all              → 등락 필터 없이 전체 신고가
ntl kospi|kosdaq|all → 시장
ntl low              → 신저가
ntl 60               → 60일 신고 (5/10/20/60/250)
ntl 10 kospi         → 조합 가능
"""

from api.getNtlPric import (
    DT_250,
    MRKT_ALL,
    NTL_HIGH,
    NTL_LOW,
    fetchNtlPric,
    normDt,
    normMrktTp,
    normNtlTp,
)
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.ntlFirstEntry import fetchFirst52wHighList, formatFirst52wReport
from utils.termLog import termLog

DEFAULT_MIN_FLU = 10.0
DEFAULT_TOP_N = 30
_TG_MAX = 3500


def _pickQuerySession():
    if runtimeState.isPaperLoggedIn():
        return (
            "paper",
            paper_host_url,
            runtimeState.getToken("paper"),
            runtimeState.paperTokenType or "bearer",
        )
    if runtimeState.isRealLoggedIn():
        return (
            "real",
            real_host_url,
            runtimeState.getToken("real"),
            runtimeState.realTokenType or "bearer",
        )
    return None


def _usage():
    return (
        "사용법: ntl (또는 신고) [옵션...]\n"
        "  ntl              — 52주 신고 + 등락≥10%\n"
        "  ntl first        — 당일 최초 52주 신고 진입만 (일봉검증)\n"
        "  ntl first all    — 등락필터 없이 최초 진입\n"
        "  ntl first kospi  — 코스피 최초 진입\n"
        "  ntl load [YYYYMMDD] — 최초진입 일 적재(수급필터 없음, DB)\n"
        "  ntl last [YYYYMMDD] — 적재 스냅샷 조회\n"
        "  ntl all          — 등락 필터 없음\n"
        "  ntl 15           — 등락≥15%\n"
        "  ntl kospi|kosdaq|all (전체)\n"
        "  ntl low          — 신저가\n"
        "  ntl 60|250|52    — 기간(250/52=52주)\n"
        "  ntl close        — 종가기준 (기본 고저기준)\n"
        "예) ntl / ntl first / ntl load / ntl last\n"
        "※ ka10016 · 당일 스냅샷 (과거일 전수 재구성 불가)\n"
        "※ ntl load: 거래량·20일평균증가율·외인/기관 순매수(수량·금액) 적재\n"
        "※ 장마감 후 자동 적재 스케줄 있음\n"
        "※ 로그인한 세션 필요"
    )


def _fmtPrc(num):
    if num is None:
        return "-"
    return f"{int(num):,}"


def _fmtFlu(flu):
    if flu is None:
        return "-"
    return f"{flu:+.2f}%"


def _parseArgs(parts):
    """
    옵션 파싱.
    반환: {mrktTp, ntlTp, dt, minFluRt, highLowCloseTp, maxRows, errMsg?}
    """
    mrktTp = MRKT_ALL
    ntlTp = NTL_HIGH
    dt = DT_250
    minFluRt = DEFAULT_MIN_FLU
    highLowCloseTp = "1"  # 고저
    maxRows = DEFAULT_TOP_N
    noMinFlu = False

    for p in parts[1:]:
        raw = str(p).strip()
        low = raw.lower()

        if low in ("help", "?", "h"):
            return {"help": True}

        if low in ("all", "전체", "nofilter", "any"):
            # 시장 all 과 필터 해제가 겹침 — all 단독·앞쪽은 필터 해제, 시장 전체
            noMinFlu = True
            mrktTp = MRKT_ALL
            continue

        if low in ("close", "종가"):
            highLowCloseTp = "2"
            continue
        if low in ("hl", "고저"):
            highLowCloseTp = "1"
            continue

        if low in ("low", "신저", "저"):
            ntlTp = NTL_LOW
            # 신저 시 등락 하한 기본은 해제·음수 아님
            noMinFlu = True
            continue
        if low in ("high", "신고", "고"):
            ntlTp = NTL_HIGH
            continue

        mt = normMrktTp(raw)
        if mt is not None and low in (
            "000",
            "001",
            "101",
            "kospi",
            "kosdaq",
            "코스피",
            "코스닥",
            "전체",
            "0",
            "1",
            "10",
        ):
            mrktTp = mt
            # all 문자열이면 noMinFlu 이미 처리됨
            if low not in ("전체", "all", "000", "0"):
                pass
            continue

        dtCode = normDt(raw)
        if dtCode is not None and not raw.replace(".", "", 1).lstrip("+-").isdigit():
            # 52주 등 별칭
            dt = dtCode
            continue
        if raw.isdigit() and raw in ("5", "10", "20", "60", "250"):
            # 10 은 등락% 와 기간 충돌 — 10이 단독이면 등락, 앞에 flu 없이 dt 로만 쓸 때
            # 규칙: 5,20,60,250 → 기간 / 10 → 우선 등락 하한 (기간 10은 '10일' 별칭 없이)
            if raw in ("5", "20", "60", "250"):
                dt = raw
                continue
            # "10" → minFlu
            minFluRt = float(raw)
            noMinFlu = False
            continue

        # 실수 등락 하한
        try:
            if raw.endswith("%"):
                minFluRt = float(raw[:-1])
                noMinFlu = False
                continue
            val = float(raw)
            # 1~30 범위면 등락%, 250 등 이미 처리
            if 0 < val <= 30:
                minFluRt = val
                noMinFlu = False
                continue
        except ValueError:
            pass

        mt2 = normMrktTp(raw)
        if mt2 is not None:
            mrktTp = mt2
            continue
        nt = normNtlTp(raw)
        if nt is not None:
            ntlTp = nt
            continue
        dt2 = normDt(raw)
        if dt2 is not None:
            dt = dt2
            continue

        return {"errMsg": f"알 수 없는 옵션: {raw}\n{_usage()}"}

    if noMinFlu:
        minFluRt = None

    return {
        "mrktTp": mrktTp,
        "ntlTp": ntlTp,
        "dt": dt,
        "minFluRt": minFluRt,
        "highLowCloseTp": highLowCloseTp,
        "maxRows": maxRows,
    }


def formatNtlReport(got, modeLabel=""):
    """조회 결과 텔레그램 텍스트."""
    if not got.get("ok"):
        return f"[신고저가 실패] {got.get('errMsg') or '-'}"

    rows = got.get("rows") or []
    minFlu = got.get("minFluRt")
    fluTxt = f"등락≥{minFlu:g}%" if minFlu is not None else "등락필터없음"
    lines = [
        f"[신고저가 ka10016] {got.get('ntlLabel')} · {got.get('dtLabel')}",
        f"시장: {got.get('mrktLabel')} · {fluTxt}"
        + (f" · {modeLabel}" if modeLabel else ""),
        f"건수: {got.get('rowCnt', 0)} (원본 {got.get('rawCnt', 0)} · {got.get('pageUsed', 0)}p)",
        "",
    ]
    if not rows:
        lines.append("조건에 맞는 종목 없음")
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r.get('stkNm')}({r.get('stkCd')}) "
            f"{_fmtPrc(r.get('curPrc'))} {_fmtFlu(r.get('fluRt'))} "
            f"고{_fmtPrc(r.get('highPric'))}"
        )
    return "\n".join(lines)


def handleNtl(rawParts=None):
    """
    ntl [옵션...] / ntl first / ntl load / ntl last
    로그인 세션으로 ka10016 조회 (first 는 일봉 최초진입).
    """
    parts = list(rawParts or [])
    if len(parts) >= 2 and str(parts[1]).strip().lower() in ("help", "?", "h"):
        return _usage()

    sub = str(parts[1]).strip().lower() if len(parts) >= 2 else ""

    # ntl last [YYYYMMDD] — DB 조회 (로그인 불필요)
    if sub in ("last", "list", "db"):
        from datetime import datetime

        from utils.ntlFirstLoad import formatNtlFirstDbReport
        from utils.ntlFirstStore import latestNtlFirstDt, listNtlFirstByDt

        ymd = None
        if len(parts) >= 3 and str(parts[2]).isdigit() and len(str(parts[2])) == 8:
            ymd = str(parts[2])
        if not ymd:
            ymd = latestNtlFirstDt() or datetime.now().strftime("%Y%m%d")
        rows = listNtlFirstByDt(ymd, limit=40)
        msg = formatNtlFirstDbReport(rows, ymd)
        if len(msg) > _TG_MAX:
            msg = msg[:_TG_MAX] + "\n…(생략)"
        termLog(msg)
        return msg

    # ntl load [YYYYMMDD] — 적재 (시간 소요)
    if sub in ("load", "적재"):
        from datetime import datetime

        from utils.ntlFirstLoad import loadNtlFirst
        from utils.ntlFirstStore import listNtlFirstByDt
        from utils.ntlFirstLoad import formatNtlFirstDbReport

        ymd = None
        if len(parts) >= 3 and str(parts[2]).isdigit() and len(str(parts[2])) == 8:
            ymd = str(parts[2])
        if not ymd:
            ymd = datetime.now().strftime("%Y%m%d")

        session = _pickQuerySession()
        if not session:
            return "로그인 후 사용하세요. (login paper | login real)"

        termLog(f"[ntl load] 시작 baseDt={ymd}")
        # 긴 작업 — 결과는 완료 메시지 + 요약
        result = loadNtlFirst(tradeDt=ymd)
        if not result.get("ok"):
            return f"[ntl load 실패] {result.get('errMsg')}"
        summary = result.get("summary") or "완료"
        # 적재일 목록 중 대표 TRADE_DT 조회
        byDt = result.get("byDt") or {}
        peekDt = ymd
        if byDt:
            peekDt = sorted(byDt.keys())[-1]
        rows = listNtlFirstByDt(peekDt, limit=15)
        detail = formatNtlFirstDbReport(rows, peekDt, titlePrefix="[ntl load 결과]")
        msg = summary + "\n\n" + detail
        if len(msg) > _TG_MAX:
            msg = msg[:_TG_MAX] + "\n…(생략)"
        termLog(msg)
        return msg

    session = _pickQuerySession()
    if not session:
        return "로그인 후 사용하세요. (login paper | login real)"

    mode, hostUrl, token, tokenType = session
    isPaper = mode == "paper"
    modeLabel = "모의" if isPaper else "실전"

    # ntl first [옵션...]
    if len(parts) >= 2 and str(parts[1]).strip().lower() in (
        "first",
        "최초",
        "entry",
    ):
        rest = ["ntl"] + list(parts[2:])
        opts = _parseArgs(rest)
        if opts.get("help"):
            return _usage()
        if opts.get("errMsg"):
            return opts["errMsg"]
        # first 기본: 등락 필터 없이 후보 확대 (원하면 ntl first 10)
        minFlu = opts.get("minFluRt")
        if len(parts) == 2:
            minFlu = None
        useClose = str(opts.get("highLowCloseTp") or "1") == "2"
        termLog(
            f"[ntl first] {mode} mrkt={opts.get('mrktTp')} minFlu={minFlu}"
        )
        got = fetchFirst52wHighList(
            hostUrl,
            token,
            tokenType=tokenType,
            isPaper=isPaper,
            mrktTp=opts.get("mrktTp") or MRKT_ALL,
            minFluRt=minFlu,
            useClose=useClose,
            maxCand=80,
            maxOut=40,
        )
        msg = formatFirst52wReport(got, modeLabel=modeLabel)
        if len(msg) > _TG_MAX:
            msg = msg[:_TG_MAX] + "\n…(생략)"
        termLog(msg)
        return msg

    opts = _parseArgs(parts)
    if opts.get("help"):
        return _usage()
    if opts.get("errMsg"):
        return opts["errMsg"]

    termLog(
        f"[ntl] {mode} {opts.get('ntlTp')} dt={opts.get('dt')} "
        f"mrkt={opts.get('mrktTp')} minFlu={opts.get('minFluRt')}"
    )

    got = fetchNtlPric(
        hostUrl,
        token,
        mrktTp=opts["mrktTp"],
        ntlTp=opts["ntlTp"],
        highLowCloseTp=opts["highLowCloseTp"],
        dt=opts["dt"],
        tokenType=tokenType,
        isPaper=isPaper,
        minFluRt=opts["minFluRt"],
        bullOnly=opts["ntlTp"] == NTL_HIGH and opts["minFluRt"] is not None,
        maxRows=opts["maxRows"],
    )

    msg = formatNtlReport(got, modeLabel=modeLabel)
    if len(msg) > _TG_MAX:
        msg = msg[:_TG_MAX] + "\n…(생략)"
    termLog(msg)
    return msg
