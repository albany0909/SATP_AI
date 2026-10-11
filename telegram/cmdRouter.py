"""텔레그램 명령 파싱 및 라우팅"""

from telegram.commands.btCommand import handleBt
from telegram.commands.buyCommand import handleBuy
from telegram.commands.cnsrCommand import handleCnsr
from telegram.commands.dayPrcCommand import handleDayPrc
from telegram.commands.helpCommand import handleHelp
from telegram.commands.usFluCommand import handleUsFlu
from telegram.commands.usInqRankCommand import handleUsInqRank
from telegram.commands.inqRankCommand import handleInqRank
from telegram.commands.loginCommand import handleLogin, handleLoginRenewal
from telegram.commands.minPrcCommand import handleMinPrc
from telegram.commands.mkhrCommand import handleMkhr
from telegram.commands.mmgCommand import handleMmg
from telegram.commands.offCommand import handleOff
from telegram.commands.reportCommand import handleReport
from telegram.commands.rsvCommand import handleRsv
from telegram.commands.sellCommand import handleSell
from telegram.commands.slrCommand import handleSlr
from telegram.commands.smaCommand import handleSma
from telegram.commands.srchCommand import handleSrch
from telegram.commands.stlsCommand import handleStartCmd, handleStopCmd
from telegram.commands.sttsCommand import handleSttsCmd
from telegram.commands.stkLoadCommand import handleStkFind, handleStkLoad
from telegram.commands.ticPrcCommand import handleTicPrc
from telegram.commands.tprCommand import handleTpr
from telegram.commands.valCommand import handleVal
from telegram.commands.ntlCommand import handleNtl
from telegram.commands.corpReportCommand import handleCorpReport, handleCns
from telegram.commands.wnewsCommand import handleWnews
from telegram.commands.invSrCommand import handleInvSr
from utils.stkArgParse import (
    splitBuySellArgs,
    splitMinTicArgs,
    splitStkRefBeforeYmds,
)


def normalizeCmd(text):
    """앞뒤 공백 제거 후 소문자로 통일해 대소문자 구분 없이 비교한다."""
    if text is None:
        return ""
    return text.strip().lower()


def routeCommand(text):
    """
    수신 텍스트를 명령으로 해석하고 처리 결과 메시지를 반환한다.
    알 수 없는 명령이면 사용법 안내를 반환한다.
    """
    if text is None or not str(text).strip():
        return None

    # 원문 기준 분리 — 종목코드 대소문자(NXT/SOR 접미사) 보존
    rawParts = str(text).strip().split()
    cmd = rawParts[0].lower()

    if cmd == "help":
        return handleHelp()

    if cmd == "off":
        return handleOff()

    if cmd == "start":
        # start stls — 스탑로스 감시 시작
        return handleStartCmd(rawParts)

    if cmd == "stop":
        # stop stls — 스탑로스 감시 중지
        return handleStopCmd(rawParts)

    if cmd == "mmg":
        # mmg list|add|del|adopt|scan|status — MMG01 풀·상태
        return handleMmg(rawParts)

    if cmd in ("report", "r"):
        # 자금현황·보유종목 조회
        return handleReport()

    if cmd == "stts":
        # stts / stts default|def — 설정 조회 또는 디폴트 복원
        subRaw = rawParts[1] if len(rawParts) >= 2 else ""
        return handleSttsCmd(subRaw)

    if cmd in ("tpr", "익절"):
        # 익절 기준(%) — 양수 저장
        pctRaw = rawParts[1] if len(rawParts) >= 2 else ""
        return handleTpr(pctRaw)

    if cmd in ("slr", "손절"):
        # 손절 기준(%) — 음수 저장
        pctRaw = rawParts[1] if len(rawParts) >= 2 else ""
        return handleSlr(pctRaw)

    if cmd == "rsv":
        # rsv list / rsv remove N / rsv {시간} {명령...}
        return handleRsv(rawParts)

    if cmd == "mkhr":
        # mkhr {시작} {종료} — 인자 없으면 현재 장 시간 표시
        openRaw = rawParts[1] if len(rawParts) >= 2 else ""
        closeRaw = rawParts[2] if len(rawParts) >= 3 else ""
        return handleMkhr(openRaw, closeRaw)

    if cmd == "srch":
        # srch {종목코드|종목명...}
        stkRef = " ".join(rawParts[1:]).strip() if len(rawParts) >= 2 else ""
        return handleSrch(stkRef)

    if cmd in ("rank", "inqrank"):
        # rank [구분] — ka00198 실시간종목조회순위
        return handleInqRank(rawParts)

    if cmd in ("ntl", "신고", "신고가"):
        # ntl — ka10016 신고저가 (기본 52주 신고 + 등락≥10%)
        return handleNtl(rawParts)

    if cmd in ("usrank", "usinqrank"):
        # usrank [구분] — usa01980 미국주식 실시간종목조회순위
        return handleUsInqRank(rawParts)

    if cmd in ("usflu", "usissue", "급등락"):
        # usflu / usflu load / usflu YYYYMMDD — 미국 급등·급락 이슈
        return handleUsFlu(rawParts)

    if cmd == "sma":
        stkRef = " ".join(rawParts[1:]).strip() if len(rawParts) >= 2 else ""
        return handleSma(stkRef)

    if cmd in ("cnsr", "조건식"):
        # cnsr / cnsr start {seq} / cnsr stop / cnsr status
        return handleCnsr(rawParts)

    if cmd in ("invsr", "수급", "투자자"):
        # invsr / invsr load / invsr {종목} — ka10059 종목별투자자
        return handleInvSr(rawParts)

    if cmd == "dayprc":
        stkRef, ymds = splitStkRefBeforeYmds(rawParts[1:], ymdCnt=2)
        if not stkRef or len(ymds) < 2:
            return handleDayPrc("", "", "")
        return handleDayPrc(stkRef, ymds[0], ymds[1])

    if cmd in ("creport", "기업리포트", "rsch"):
        # creport load|last|guide|{종목}
        return handleCorpReport(rawParts[1:])

    if cmd in ("cns", "예상실적"):
        # cns load|last {종목} — FnSpace 유료 스킵 / 리포트 파생
        return handleCns(rawParts[1:])

    if cmd in ("wnews", "watchnews", "공시뉴스"):
        # 관심종목 공시·뉴스 주기수집 수동
        return handleWnews(rawParts[1:])

    if cmd == "bt":
        # bt list|hist|status|stop|yes|no | bt {전략} {종목} {시작} {종료} {투자금}
        return handleBt(rawParts)

    if cmd in ("val", "validate"):
        # val list|static|status|stop|yes|no | val {전략} watch {시작} {종료} [투자금]
        return handleVal(rawParts)

    if cmd == "minprc":
        stkRef, ticScope, startDt, endDt = splitMinTicArgs(rawParts[1:])
        if not stkRef:
            return handleMinPrc("", "", "", "")
        return handleMinPrc(stkRef, ticScope, startDt, endDt or startDt)

    if cmd == "ticprc":
        # 날짜 없으면 {종목} {틱범위} 만
        parts = rawParts[1:]
        if len(parts) >= 3 and any(
            len(str(p).replace("-", "").replace(".", "").replace("/", "")) == 8
            and str(p).replace("-", "").replace(".", "").replace("/", "").isdigit()
            for p in parts[-2:]
        ):
            stkRef, ticScope, startDt, endDt = splitMinTicArgs(parts)
            if not stkRef:
                return handleTicPrc("", "", "", "")
            return handleTicPrc(stkRef, ticScope, startDt, endDt or "")
        if len(parts) >= 2:
            # 끝이 틱범위(숫자), 앞이 종목
            ticScope = parts[-1]
            stkRef = " ".join(parts[:-1]).strip()
            return handleTicPrc(stkRef, ticScope, "", "")
        return handleTicPrc("", "", "", "")

    if cmd in ("stkload", "종목적재"):
        return handleStkLoad(rawParts)

    if cmd in ("stkfind", "종목검색"):
        return handleStkFind(rawParts)

    if cmd == "buy":
        stkRef, ordQty, limitPrice = splitBuySellArgs(rawParts[1:])
        if not stkRef:
            return handleBuy("", "", None)
        return handleBuy(stkRef, ordQty, limitPrice)

    if cmd == "sell":
        stkRef, ordQty, limitPrice = splitBuySellArgs(rawParts[1:])
        if not stkRef:
            return handleSell("", "", None)
        return handleSell(stkRef, ordQty, limitPrice)

    if cmd == "login":
        # login 이하는 대소문자 무시
        parts = [p.lower() for p in rawParts]
        if len(parts) >= 2 and parts[1] == "renewal":
            timeStr = parts[2] if len(parts) >= 3 else ""
            return handleLoginRenewal(timeStr)

        mode = parts[1] if len(parts) >= 2 else ""
        return handleLogin(mode)

    return (
        "알 수 없는 명령입니다.\n"
        "전체 목록은 help 를 입력하세요."
    )
