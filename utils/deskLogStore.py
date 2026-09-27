"""
매매창 거래로그 일별 적재.

알고리즘 이벤트는 AI_ALG_EVT, 00 체결 한 줄은 AI_DESK_LOG.
화면은 거래일 08:00 에 전날을 지우고 그날 분만 보여 준다. DB는 일자별로 남긴다.
"""

from datetime import datetime

from utils.deskLogTables import ensureDeskLogTable
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 장 시작 전, 거래일 화면을 당일로 넘기는 시각
RESET_HM = "08:00"


def deskLogYmd(now=None):
    """
    로그·거래 탭에 보여줄 일자.
    거래일 08:00 전·휴장·주말은 직전 거래일, 거래일 08:00 이후는 당일.
    """
    from config.krHoliday import isTradingDay, prevTradingDay

    dt = now or datetime.now()
    ymd = dt.strftime("%Y%m%d")
    hm = dt.strftime("%H:%M")
    if isTradingDay(dt) and hm >= RESET_HM:
        return ymd
    return prevTradingDay(dt) or ymd


def fmtFillLine(sideLabel, stkCd, stkNm, qty, px, ordNo="", tm=None):
    """00 체결 화면 한 줄. (line, kind, HHmmss)"""
    dt = tm if hasattr(tm, "strftime") else datetime.now()
    hm = dt.strftime("%H:%M:%S")
    hms = dt.strftime("%H%M%S")
    name = f"{stkNm}({stkCd})" if stkNm else (stkCd or "-")
    qtyTxt = ""
    try:
        if qty:
            qtyTxt = f"{int(qty):,}주"
    except (TypeError, ValueError):
        qtyTxt = ""
    pxTxt = ""
    if px is not None and str(px).strip() != "":
        try:
            pxTxt = f"{abs(int(float(px))):,}원"
        except (TypeError, ValueError):
            pxTxt = str(px)
    line = f"{hm} [00] {sideLabel}체결 {name} {qtyTxt} {pxTxt}".strip()
    if ordNo:
        line += f" #{ordNo}"
    kind = "BUY" if "매수" in str(sideLabel) else (
        "SELL" if "매도" in str(sideLabel) else "FILL"
    )
    return line, kind, hms


# 단위체결량 저장 이후 행 표식 (분할체결 2건째부터 LINE 에 붙는다)
FILL_CUM_MARK = "누계"


def insertDeskFill(
    sideLabel,
    stkCd,
    stkNm,
    qty,
    px,
    ordNo="",
    mode="paper",
    tradeDt=None,
    cmsn=0,
    tax=0,
    cumQty=0,
):
    """
    00 체결을 당일 로그로 남긴다. 실패해도 예외를 올리지 않는다.
    QTY 는 단위 체결량. 분할 체결이면 LINE 끝에 FILL_CUM_MARK(누계) 를 붙여
    예전(누계수량 저장) 행과 구분한다 (utils.stratRoundFill.normDeskFillRows).
    """
    if not ensureDeskLogTable():
        return {"ok": False, "errMsg": "AI_DESK_LOG 없음"}
    now = datetime.now()
    ymd = str(tradeDt or now.strftime("%Y%m%d"))[:8]
    line, kind, hms = fmtFillLine(sideLabel, stkCd, stkNm, qty, px, ordNo, tm=now)
    try:
        cumNum = int(cumQty or 0)
        if qty and cumNum > int(qty):
            line = f"{line or ''} ({FILL_CUM_MARK} {cumNum}주)"
    except (TypeError, ValueError):
        pass
    cd = normStkCd(stkCd) or None
    pxNum = None
    try:
        if px is not None and str(px).strip() != "":
            pxNum = abs(float(str(px).replace(",", "")))
    except (TypeError, ValueError):
        pxNum = None
    qtyNum = None
    try:
        if qty:
            qtyNum = int(qty)
    except (TypeError, ValueError):
        qtyNum = None
    try:
        cmsnNum = abs(int(cmsn or 0))
    except (TypeError, ValueError):
        cmsnNum = 0
    try:
        taxNum = abs(int(tax or 0))
    except (TypeError, ValueError):
        taxNum = 0
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO AI_DESK_LOG (
                  TRADE_DT, LOG_TM, KIND, SRC, LINE, MODE,
                  STK_CD, STK_NM, QTY, PX, ORD_NO, CMSN, TAX
                ) VALUES (
                  %s, %s, %s, 'FILL', %s, %s,
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    ymd,
                    hms,
                    kind[:16],
                    (line or "")[:500],
                    str(mode or "paper")[:8],
                    cd,
                    (stkNm or None),
                    qtyNum,
                    pxNum,
                    (str(ordNo)[:20] if ordNo else None),
                    cmsnNum if cmsnNum > 0 else None,
                    taxNum if taxNum > 0 else None,
                ),
            )
        conn.commit()
        return {
            "ok": True,
            "line": line,
            "kind": kind,
            "tradeDt": ymd,
            "cmsn": cmsnNum,
            "tax": taxNum,
        }
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[deskLog] 적재 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listDeskLog(tradeDt, src=None, limit=400, mode=None):
    """하루 로그 (시간순). mode 지정 시 paper|real 만."""
    if not ensureDeskLogTable():
        return []
    ymd = str(tradeDt or "").replace("-", "")[:8]
    if len(ymd) != 8:
        return []
    lim = max(1, min(int(limit or 400), 2000))
    md = None
    if mode is not None:
        raw = str(mode or "").strip().lower()
        md = "real" if raw in ("real", "live", "실전") else "paper"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            where = ["TRADE_DT=%s"]
            args = [ymd]
            if src:
                where.append("SRC=%s")
                args.append(str(src)[:8])
            if md:
                # 구데이터 MODE 빈값은 모의로 취급
                if md == "paper":
                    where.append("(MODE=%s OR MODE IS NULL OR MODE='')")
                else:
                    where.append("MODE=%s")
                args.append(md)
            args.append(lim)
            cur.execute(
                f"""
                    SELECT LOG_ID, TRADE_DT, LOG_TM, KIND, SRC, LINE, MODE,
                           STK_CD, STK_NM, QTY, PX, ORD_NO, CMSN, TAX
                      FROM AI_DESK_LOG
                 WHERE {' AND '.join(where)}
                 ORDER BY LOG_TM ASC, LOG_ID ASC
                 LIMIT %s
                """,
                tuple(args),
            )
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[deskLog] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
