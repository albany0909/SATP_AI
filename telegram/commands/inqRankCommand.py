"""rank [구분] — ka00198 실시간종목조회순위"""

from api.getAcntEvlt import parsePadNum
from api.getInqRank import (
    QRY_TP_1M,
    QRY_TP_DAY,
    QRY_TP_LABEL,
    fetchInqRank,
    normQryTp,
)
from config.config import paper_host_url, real_host_url
from config import runtimeState
from utils.dayInqRankLoad import loadDayInqRank
from utils.rankEmaUtil import formatIndThemeReport, getEmaAlignMap, parseChgrPct
from utils.stkCdUtil import normStkCd
from utils.stkIndGrp import fetchStkMetaByCds, resolveIndGrp
from utils.termLog import termLog

# 텔레그램 메시지 길이 고려 — 상위 N건만 표시
DEFAULT_TOP_N = 20

# 기준가 대비 부호
_BASE_SIGN = {
    "1": "상한",
    "2": "상승",
    "3": "보합",
    "4": "하한",
    "5": "하락",
}


def _pickQuerySession():
    """모의 우선, 없으면 실투자."""
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
    """부호 포함 현재가 → 절대값 천단위. 실패 시 원문."""
    num = parsePadNum(raw)
    if num is None:
        text = str(raw or "").strip()
        return text if text else "-"
    return f"{abs(int(num)):,}"


def _fmtRankChg(row):
    """순위 등락 표시. 변동없음은 →."""
    sign = _val(row, "rank_chg_sign", "")
    chg = _val(row, "rank_chg", "")
    if sign in ("N", "'", "") or chg in ("", "0", "-"):
        if chg in ("", "0") or sign in ("N", "'"):
            return "→"
    if sign == "+":
        return f"↑{str(chg).lstrip('+')}"
    if sign == "-":
        return f"↓{str(chg).lstrip('-')}"
    if str(chg).startswith("-"):
        return f"↓{str(chg).lstrip('-')}"
    if chg not in ("-", ""):
        return f"↑{str(chg).lstrip('+')}"
    return "→"


def _pickRow(row, *keys, default="-"):
    """여러 후보 키 중 첫 유효값."""
    if not isinstance(row, dict):
        return default
    for key in keys:
        v = row.get(key)
        if v is None:
            continue
        text = str(v).strip()
        if text != "":
            return text
    return default


def formatInqRankMsg(rows, modeLabel, qryLabel, topN=DEFAULT_TOP_N):
    """순위 목록 정리 — 업종/테마 + 10/20/50이평 대소(값 큰 순)."""
    # 종목코드·명 없는 빈 슬롯 제외 (이평 유무와 무관하게 종목은 항상 표시 대상)
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cd = normStkCd(
            _pickRow(row, "stk_cd", "stkCd", "code", "종목코드", default="")
        )
        nm = _pickRow(row, "stk_nm", "stkNm", "name", "종목명", default="")
        if not cd and (not nm or nm == "-"):
            continue
        cleaned.append(row)

    show = cleaned[:topN] if topN and topN > 0 else cleaned
    lines = [
        f"[실시간조회순위] ({modeLabel}) {qryLabel}",
        f"표시: 상위 {len(show)} / 전체 {len(cleaned)}건"
        + (
            f" (빈행 {len(rows or []) - len(cleaned)}건 제외)"
            if len(cleaned) < len(rows or [])
            else ""
        ),
    ]
    if show:
        first = show[0]
        dt = _pickRow(first, "dt", "date")
        tm = _pickRow(first, "tm", "time")
        if dt != "-" or tm != "-":
            tmFmt = tm
            if len(tm) == 6 and tm.isdigit():
                tmFmt = f"{tm[0:2]}:{tm[2:4]}:{tm[4:6]}"
            lines.append(f"기준: {dt} {tmFmt}".strip())

    stkCds = []
    for row in show:
        cd = normStkCd(
            _pickRow(row, "stk_cd", "stkCd", "code", "종목코드", default="")
        )
        if cd:
            stkCds.append(cd)
    emaMap = getEmaAlignMap(stkCds)
    metaMap = fetchStkMetaByCds(stkCds)

    lines.append("")
    indItems = []
    for row in show:
        rank = _pickRow(row, "bigd_rank", "rank", "순위", default="?")
        stkCd = normStkCd(
            _pickRow(row, "stk_cd", "stkCd", "code", "종목코드", default="")
        ) or ""
        code = stkCd
        meta = metaMap.get(code) or {}
        stkNm = _pickRow(row, "stk_nm", "stkNm", "name", "종목명", default="")
        if not stkNm or stkNm == "-":
            stkNm = meta.get("stkNm") or code or "종목미상"
        if not code:
            # 이름만 있는 경우 — 그래도 이름 표시
            code = "-"
            stkCd = "-"

        prc = _fmtPrc(
            _pickRow(row, "past_curr_prc", "curr_pric", "cur_prc", "현재가", default="")
        )
        chgrRaw = _pickRow(
            row, "base_comp_chgr", "flu_rt", "chgr", "등락률", default="-"
        )
        chgrF = parseChgrPct(chgrRaw if chgrRaw != "-" else None)
        chgr = chgrRaw
        if chgr != "-" and not str(chgr).endswith("%"):
            chgr = f"{chgr}%"
        signCd = _pickRow(row, "base_comp_sign", "pred_pre_sig", default="")
        signKr = _BASE_SIGN.get(signCd, "")
        rankChg = _fmtRankChg(row)

        # 이평 부족이어도 종목명은 위에서 확정 — 이평 텍스트만 별도
        emaTxt = emaMap.get(code, "이평부족") if code not in ("", "-") else "이평부족"

        indGrp = _pickRow(row, "IND_GRP", "ind_grp", default="")
        if indGrp in ("-", ""):
            indGrp = resolveIndGrp(
                code if code != "-" else "",
                stkNm if stkNm not in ("-", "종목미상") else (meta.get("stkNm") or ""),
                meta.get("upName") or "",
            ) or "기타"
        mid = f"{signKr} ".strip()
        if mid:
            mid = f" {mid}"
        # 예) 1. 삼성전자(005930) [반도체] 70,700 +0.57% 상승 ↑2 | 50>10>20
        #     6. 종목명(코드) [테마] ... | 이평부족  ← 이평 없어도 종목 항상 표시
        lines.append(
            f"{rank}. {stkNm}({stkCd}) [{indGrp}] "
            f"{prc} {chgr}{mid} {rankChg} | {emaTxt}".rstrip()
        )
        if chgrF is not None or indGrp:
            indItems.append({"ind": indGrp, "chgr": chgrF})

    if len(cleaned) > len(show):
        lines.append(f"... 외 {len(cleaned) - len(show)}건 생략")
    if not show:
        lines.append("(표시할 종목 데이터 없음 — API 종목코드/명 확인)")
    lines.extend(formatIndThemeReport(indItems))
    lines.append("")
    lines.append("※ [업종/테마] · 10/20/50 이평 대소(값 큰 순, 예: 50>10>20)")
    return "\n".join(lines)


def _usage():
    # 구분 안내 — 숫자/별칭 모두 허용
    opts = ", ".join(f"{k}:{v}" for k, v in QRY_TP_LABEL.items())
    return (
        "사용법: rank [구분] — 실시간 조회\n"
        "        rank last [구분] — 최근 적재분(DB) 조회\n"
        "        rank load [구분] — DB 적재 (기본: 당일누적)\n"
        f"구분 — {opts}\n"
        "  예) rank          — 1분 (기본, 실시간)\n"
        "  예) rank 4        — 당일누적 실시간\n"
        "  예) rank last     — 가장 최근 적재분\n"
        "  예) rank last 4   — 당일누적 최근 적재분\n"
        "  예) rank load     — 당일누적 적재\n"
        "  (자동: 거래일 장마감 후 당일누적 적재)"
    )


def _handleRankLast(parts):
    """
    rank last [구분] — AI_DAY_INQ_RANK 최근 적재 스냅샷 조회.
    구분 생략 시 전체 중 적재시각 최신.
    """
    from utils.rankEmaUtil import formatLatestRankFromDb

    qryRaw = parts[2] if len(parts) >= 3 else ""
    qryTp = None
    if qryRaw:
        qryTp = normQryTp(qryRaw, default=None)
        if qryTp is None:
            return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"

    msg = formatLatestRankFromDb(qryTp=qryTp, topN=DEFAULT_TOP_N)
    termLog(msg)
    return msg


def _handleRankLoad(parts):
    """
    rank load [구분] — AI_DAY_INQ_RANK 수동 적재.
    구분 생략 시 당일누적(4). 수동은 휴일에도 허용.
    당일누적이면 선정 종목 6개월 일봉도 백그라운드 적재.
    """
    import threading

    from utils.rankDayPrcLoad import DEFAULT_MONTHS, loadDayPrcForRankStks

    qryRaw = parts[2] if len(parts) >= 3 else ""
    qryTp = normQryTp(qryRaw, default=QRY_TP_DAY)
    if qryTp is None:
        return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"

    result = loadDayInqRank(qryTp=qryTp)
    if not result.get("ok"):
        failMsg = f"[순위적재실패] {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    okMsg = (
        f"[순위적재] ({result.get('modeLabel')}) 완료\n"
        f"구분: {result.get('qryLabel')}\n"
        f"일자: {result.get('tradeDt')}\n"
        f"건수: {result.get('upsertCnt')}건"
        f" (업종 {result.get('indFilled') or 0}건"
        f" / 테마매핑 {result.get('themeFilled') or 0}건)"
    )

    # 당일누적 수동 적재 시에도 6개월 일봉 연계
    if str(qryTp) == str(QRY_TP_DAY):
        stkCds = result.get("stkCds") or []
        if stkCds:
            okMsg += (
                f"\n→ 선정 {len(stkCds)}종목 {DEFAULT_MONTHS}개월 일봉 "
                f"적재 시작(백그라운드·완료 시 알림)"
                f"\n→ 완료 후 코스피/코스닥 양봉만 관심종목 반영"
            )

            def _bg():
                try:
                    dayRes = loadDayPrcForRankStks(
                        stkCds,
                        months=DEFAULT_MONTHS,
                        endDt=result.get("tradeDt"),
                        skipIfEnough=True,
                    )
                    msg = dayRes.get("summary") or str(dayRes)
                    termLog(msg)
                    try:
                        from telegram.telSend import sendTelegramMsg

                        sendTelegramMsg(msg, pushKind="batch")

                        # 순위→관심종목 (코스피/코스닥 + 양봉마감)
                        from utils.rankToWatchLoad import syncWatchFromRankClose

                        watchRes = syncWatchFromRankClose(
                            result.get("tradeDt"), qryTp=QRY_TP_DAY
                        )
                        if watchRes.get("summary"):
                            sendTelegramMsg(watchRes.get("summary"), pushKind="batch")
                        elif not watchRes.get("ok"):
                            sendTelegramMsg(
                                f"[관심종목실패] {watchRes.get('errMsg')}",
                                pushKind="batch",
                            )

                        from utils.rankEmaUtil import formatRankEmaSummaryFromDb

                        emaSum = formatRankEmaSummaryFromDb(
                            result.get("tradeDt"), qryTp=QRY_TP_DAY, topN=20
                        )
                        if emaSum:
                            sendTelegramMsg(emaSum, pushKind="batch")
                    except Exception as err:
                        termLog(f"[rankDayPrc] 텔레그램 실패: {err}")
                except Exception as err:
                    termLog(f"[rankDayPrc] 예외: {err}")

            threading.Thread(
                target=_bg, name="rankDayPrcManual", daemon=True
            ).start()

    termLog(okMsg)
    return okMsg


def handleInqRank(rawParts=None):
    """
    ka00198 실시간종목조회순위 조회 또는 DB 적재/최근적재 조회.
    rawParts: ['rank'] / ['rank', '4'] / ['rank', 'load'] /
              ['rank', 'last'] / ['rank', 'last', '4']
    """
    parts = list(rawParts or [])
    sub = parts[1].lower() if len(parts) >= 2 else ""

    # rank load [구분]
    if sub == "load":
        return _handleRankLoad(parts)

    # rank last|db|적재 [구분] — 로그인 없이 DB만 조회
    if sub in ("last", "db", "적재", "saved"):
        return _handleRankLast(parts)

    qryRaw = parts[1] if len(parts) >= 2 else ""
    qryTp = normQryTp(qryRaw, default=QRY_TP_1M)
    if qryTp is None:
        return f"지원하지 않는 구분입니다: {qryRaw}\n{_usage()}"

    session = _pickQuerySession()
    if session is None:
        failMsg = "순위 조회 전 login paper 또는 login real이 필요합니다."
        termLog(failMsg)
        return failMsg

    mode, hostUrl, token, tokenType = session
    modeLabel = "모의투자" if mode == "paper" else "실투자"
    qryLabel = QRY_TP_LABEL.get(qryTp, qryTp)

    termLog(f"[rank] ({modeLabel}) {qryLabel} 조회 시작")
    result = fetchInqRank(hostUrl, token, qryTp=qryTp, tokenType=tokenType)
    if not result.get("ok"):
        failMsg = f"[순위실패] ({modeLabel}) {result.get('errMsg')}"
        termLog(failMsg)
        return failMsg

    rows = result.get("rows") or []
    if not rows:
        msg = f"[실시간조회순위] ({modeLabel}) {qryLabel}\n조회 결과 없음"
        termLog(msg)
        return msg

    msg = formatInqRankMsg(rows, modeLabel, qryLabel, topN=DEFAULT_TOP_N)
    termLog(msg)
    return msg
