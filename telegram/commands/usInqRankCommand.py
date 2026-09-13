"""usrank [구분] — usa01980 미국주식 실시간종목조회순위"""

from api.getUsInqRank import (
    QRY_TP_1M,
    QRY_TP_DAY,
    QRY_TP_LABEL,
    fetchUsInqRank,
    normUsQryTp,
    normUsStkCd,
)
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.rankEmaUtil import formatIndThemeReport, parseChgrPct
from utils.termLog import termLog
from utils.usIndGrp import resolveUsIndGrp
from utils.usRankEmaUtil import getUsEmaAlignMap

DEFAULT_TOP_N = 20


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


def _val(row, key, default="-"):
    v = row.get(key) if isinstance(row, dict) else None
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip()


def _fmtPrc(raw):
    num = parseChgrPct(raw)
    if num is None:
        text = str(raw or "").strip()
        return text if text else "-"
    return f"{abs(float(num)):,.4f}"


def _fmtRankChg(row):
    sign = _val(row, "sign", "")
    chg = _val(row, "chg_val", "")
    chgNum = chg.lstrip("0") or "0"
    if sign in ("", "N", "'") or chgNum in ("", "0", "-"):
        return "→"
    if sign == "+":
        return f"↑{chgNum}"
    if sign == "-":
        return f"↓{chgNum}"
    return "→"


def formatUsInqRankMsg(rows, modeLabel, qryLabel, baseDate="", baseTime="", topN=DEFAULT_TOP_N):
    """실시간 미국순위 메시지."""
    show = rows[:topN] if topN and topN > 0 else rows
    lines = [
        f"[미국실시간조회순위] ({modeLabel}) {qryLabel}",
        f"표시: 상위 {len(show)} / 전체 {len(rows)}건",
    ]
    if baseDate or baseTime:
        tmFmt = baseTime
        if len(baseTime) == 6 and baseTime.isdigit():
            tmFmt = f"{baseTime[0:2]}:{baseTime[2:4]}:{baseTime[4:6]}"
        ymd = baseDate
        if len(ymd) == 8 and ymd.isdigit():
            ymd = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        lines.append(f"기준: {ymd} {tmFmt}".strip())

    stkCds = []
    for row in show:
        cd = normUsStkCd(_val(row, "stk_cd", ""))
        if cd:
            stkCds.append(cd)
    emaMap = getUsEmaAlignMap(stkCds)

    lines.append("")
    indItems = []
    for row in show:
        rank = _val(row, "rank", "?").lstrip("0") or "0"
        stkNm = _val(row, "stk_nm")
        stkCd = normUsStkCd(_val(row, "stk_cd", "")) or _val(row, "stk_cd")
        stex = _val(row, "stex_tp", "")
        stexTxt = f"/{stex}" if stex not in ("-", "") else ""
        prc = _fmtPrc(_val(row, "curr_pric", ""))
        chgrRaw = _val(row, "diff_rate_for_gjga", "-")
        chgrF = parseChgrPct(chgrRaw if chgrRaw != "-" else None)
        signGj = _val(row, "sign_for_gjga", "")
        if chgrF is not None:
            # 부호가 - 인데 값이 양수면 부호 반영
            if signGj == "-" and chgrF > 0:
                chgrF = -chgrF
            chgr = f"{chgrF:+.2f}%"
        else:
            chgr = chgrRaw if chgrRaw == "-" else f"{chgrRaw}%"

        mid = ""
        if signGj == "+":
            mid = " 상승"
        elif signGj == "-":
            mid = " 하락"

        rankChg = _fmtRankChg(row)
        indGrp = resolveUsIndGrp(stkCd, stkNm if stkNm != "-" else "", stex) or "기타"
        emaTxt = emaMap.get(normUsStkCd(stkCd) or stkCd, "이평부족")

        lines.append(
            f"{rank}. {stkNm}({stkCd}{stexTxt}) [{indGrp}] "
            f"{prc} {chgr}{mid} {rankChg} | {emaTxt}".rstrip()
        )
        indItems.append({"ind": indGrp, "chgr": chgrF})

    if len(rows) > len(show):
        lines.append(f"... 외 {len(rows) - len(show)}건 생략")
    lines.extend(formatIndThemeReport(indItems))
    lines.append("")
    lines.append("※ [업종/테마] · 10/20/50 이평 대소(값 큰 순, 예: 50>10>20)")
    return "\n".join(lines)


def _usage():
    opts = ", ".join(f"{k}:{v}" for k, v in QRY_TP_LABEL.items())
    return (
        "사용법: usrank [구분] — 미국 실시간 조회\n"
        "        usrank last [구분] — 최근 적재분(DB)\n"
        "        usrank load [구분] — DB 적재+일봉(기본: 당일누적)\n"
        f"구분 — {opts} (또는 B281/B284…)\n"
        "  예) usrank / usrank 4 / usrank last / usrank load"
    )


def _handleUsRankLast(parts):
    from utils.usRankEmaUtil import formatLatestUsRankFromDb

    qryRaw = parts[2] if len(parts) >= 3 else ""
    qryTp = None
    if qryRaw:
        qryTp = normUsQryTp(qryRaw, default=None)
        if qryTp is None:
            return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"
    msg = formatLatestUsRankFromDb(qryTp=qryTp, topN=DEFAULT_TOP_N)
    termLog(msg)
    return msg


def _handleUsRankLoad(parts):
    import threading

    from utils.usDayInqRankLoad import loadUsDayInqRank
    from utils.usRankDayPrcLoad import DEFAULT_MONTHS, loadUsDayPrcForRankStks
    from utils.usRankEmaUtil import formatUsRankSummaryFromDb

    qryRaw = parts[2] if len(parts) >= 3 else ""
    qryTp = normUsQryTp(qryRaw, default=QRY_TP_DAY)
    if qryTp is None:
        return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"

    result = loadUsDayInqRank(qryTp=qryTp)
    if not result.get("ok"):
        failMsg = f"[미국순위적재실패] {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    okMsg = (
        f"[미국순위적재] ({result.get('modeLabel')}) 완료\n"
        f"구분: {result.get('qryLabel')}\n"
        f"일자: {result.get('tradeDt')}\n"
        f"건수: {result.get('upsertCnt')}건"
        f" (테마 {result.get('indFilled') or 0}건)"
    )

    if str(qryTp) == str(QRY_TP_DAY):
        stkItems = result.get("stkItems") or []
        if stkItems:
            okMsg += (
                f"\n→ 선정 {len(stkItems)}종목 {DEFAULT_MONTHS}개월 일봉 "
                f"적재 시작(백그라운드·완료 시 알림)"
            )

            def _bg():
                try:
                    dayRes = loadUsDayPrcForRankStks(
                        stkItems,
                        months=DEFAULT_MONTHS,
                        endDt=result.get("tradeDt"),
                        skipIfEnough=True,
                    )
                    msg = dayRes.get("summary") or str(dayRes)
                    termLog(msg)
                    try:
                        from telegram.telSend import sendTelegramMsg

                        sendTelegramMsg(msg, pushKind="batch")
                        emaSum = formatUsRankSummaryFromDb(
                            result.get("tradeDt"), qryTp=QRY_TP_DAY, topN=20
                        )
                        if emaSum:
                            sendTelegramMsg(emaSum, pushKind="batch")
                    except Exception as err:
                        termLog(f"[usRankDayPrc] 텔레그램 실패: {err}")
                except Exception as err:
                    termLog(f"[usRankDayPrc] 예외: {err}")

            threading.Thread(
                target=_bg, name="usRankDayPrcManual", daemon=True
            ).start()

    termLog(okMsg)
    return okMsg


def handleUsInqRank(rawParts=None):
    """
    usrank / usrank 4 / usrank last / usrank load
    """
    parts = list(rawParts or [])
    sub = parts[1].lower() if len(parts) >= 2 else ""

    if sub == "load":
        return _handleUsRankLoad(parts)
    if sub in ("last", "db", "적재", "saved"):
        return _handleUsRankLast(parts)

    qryRaw = parts[1] if len(parts) >= 2 else ""
    qryTp = normUsQryTp(qryRaw, default=QRY_TP_1M)
    if qryTp is None:
        return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"

    session = _pickQuerySession()
    if session is None:
        failMsg = "미국순위 조회 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    qryLabel = QRY_TP_LABEL.get(qryTp, qryTp)

    termLog(f"[usrank] ({modeLabel}) {qryLabel} 조회 시작")
    result = fetchUsInqRank(hostUrl, token, qryTp=qryTp, tokenType=tokenType)
    if not result.get("ok"):
        failMsg = f"[미국순위실패] ({modeLabel}) {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    rows = result.get("rows") or []
    if not rows:
        msg = f"[미국실시간조회순위] ({modeLabel}) {qryLabel}\n조회 결과 없음"
        termLog(msg)
        return msg

    msg = formatUsInqRankMsg(
        rows,
        modeLabel,
        qryLabel,
        baseDate=result.get("baseDate") or "",
        baseTime=result.get("baseTime") or "",
        topN=DEFAULT_TOP_N,
    )
    termLog(msg)
    return msg
