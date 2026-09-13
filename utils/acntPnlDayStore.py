"""
계좌 당일·월간·년간 손익 (AI_ACNT_PNL_DAY).

키움 kt00004 의 tdy_lspft / lspft2 는 모의에서 0으로 오는 경우가 많다.
- 당일 수익금·수익률: 오늘 매도된 거래만 (평단대비, 이월은 원장). 분모=매도분 매입원가
- 월·년: AI_STRAT_LIVE_TRADE 전량청산 라운드 PNL_WON 합 (분할익절 포함 1라운드=1건)
"""

from datetime import datetime

from api.getAcntEvlt import parsePadNum
from utils.acntPnlDayTables import ensureAcntPnlDayTable
from utils.algTradeHist import sumDeskSellPnlDetail
from utils.dbConn import openDbConn
from utils.stratLiveTradeTables import ensureStratLiveTradeTable
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_ACNT_PNL_DAY (
  MODE, ACNT_NM, TRADE_DT,
  ASET_EVLT, OPEN_ASET, PREV_ASET, TOT_PUR,
  TDY_PNL, TDY_PNL_RT, MON_PNL, MON_PNL_RT, YEAR_PNL, YEAR_PNL_RT,
  API_TDY_PNL, API_MON_PNL
) VALUES (
  %s, %s, %s,
  %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s,
  %s, %s
)
ON DUPLICATE KEY UPDATE
  ASET_EVLT = VALUES(ASET_EVLT),
  OPEN_ASET = CASE
    WHEN OPEN_ASET IS NULL OR OPEN_ASET <= 0 THEN VALUES(OPEN_ASET)
    ELSE OPEN_ASET
  END,
  PREV_ASET = VALUES(PREV_ASET),
  TOT_PUR = VALUES(TOT_PUR),
  TDY_PNL = VALUES(TDY_PNL),
  TDY_PNL_RT = VALUES(TDY_PNL_RT),
  MON_PNL = VALUES(MON_PNL),
  MON_PNL_RT = VALUES(MON_PNL_RT),
  YEAR_PNL = VALUES(YEAR_PNL),
  YEAR_PNL_RT = VALUES(YEAR_PNL_RT),
  API_TDY_PNL = VALUES(API_TDY_PNL),
  API_MON_PNL = VALUES(API_MON_PNL)
"""


def _todayYmd():
    return datetime.now().strftime("%Y%m%d")


def _acntNm(body):
    nm = str((body or {}).get("acnt_nm") or "").strip()
    return (nm[:40] if nm else "_")


def _won(raw):
    num = parsePadNum(raw)
    return int(num) if num is not None else None


def _rt(pnl, base):
    try:
        b = float(base or 0)
        if b <= 0:
            return None
        return float(pnl or 0) / b * 100.0
    except (TypeError, ValueError):
        return None


def _fetchOne(cur, sql, args):
    cur.execute(sql, args)
    return cur.fetchone()


def _positiveBase(*cands):
    """수익률 분모 — 양수만."""
    for c in cands:
        if c is None:
            continue
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def _sumLiveRoundPnl(cur, modeKey, prefix, throughYmd):
    """
    기간(YYYYMM / YYYY) 내 전량청산 라운드 실현손익 합.
    분할익절 후 잔량 청산도 라운드 1건으로 집계된다.
    """
    if not ensureStratLiveTradeTable():
        return 0.0
    like = str(prefix) + "%"
    end = str(throughYmd)[:8]
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(PNL_WON), 0) AS SUM_PNL
              FROM AI_STRAT_LIVE_TRADE
             WHERE MODE=%s
               AND EXIT_DT LIKE %s
               AND EXIT_DT <= %s
            """,
            (modeKey, like, end),
        )
        row = cur.fetchone() or {}
        return float(row.get("SUM_PNL") or 0)
    except Exception as err:
        termLog(f"[acntPnlDay] 라운드손익 집계 실패: {err}")
        return 0.0


def upsertAcntPnlDay(body, mode="paper", tradeDt=None):
    """
    kt00004 본문으로 오늘 행을 갱신하고 당일/월/년 손익을 반환한다.
    실패해도 예외를 올리지 않는다.
    """
    if not ensureAcntPnlDayTable():
        return {}
    ymd = str(tradeDt or _todayYmd())[:8]
    if len(ymd) != 8:
        return {}
    modeKey = "paper" if str(mode or "") != "real" else "real"
    acntNm = _acntNm(body)
    aset = _won((body or {}).get("aset_evlt_amt"))
    if aset is None:
        aset = _won((body or {}).get("prsm_dpst_aset_amt")) or 0
    # 예탁자산이 비정상(<=0)이면 예수금+유가평가로 보강
    if aset is None or float(aset) <= 0:
        entr = _won((body or {}).get("entr")) or 0
        est = _won((body or {}).get("tot_est_amt")) or 0
        if entr + est > 0:
            aset = entr + est
    totPur = _won((body or {}).get("tot_pur_amt"))
    apiTdy = _won((body or {}).get("tdy_lspft"))
    apiMon = _won((body or {}).get("lspft2"))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            prevRow = _fetchOne(
                cur,
                """
                SELECT ASET_EVLT FROM AI_ACNT_PNL_DAY
                 WHERE MODE=%s AND ACNT_NM=%s AND TRADE_DT<%s
                   AND ASET_EVLT > 0
                 ORDER BY TRADE_DT DESC LIMIT 1
                """,
                (modeKey, acntNm, ymd),
            )
            todayRow = _fetchOne(
                cur,
                """
                SELECT OPEN_ASET, ASET_EVLT FROM AI_ACNT_PNL_DAY
                 WHERE MODE=%s AND ACNT_NM=%s AND TRADE_DT=%s
                """,
                (modeKey, acntNm, ymd),
            )
            prevAset = None
            if prevRow and prevRow.get("ASET_EVLT") is not None:
                prevAset = float(prevRow.get("ASET_EVLT"))
            openAset = None
            if todayRow and todayRow.get("OPEN_ASET") is not None:
                try:
                    openAset = float(todayRow.get("OPEN_ASET"))
                except (TypeError, ValueError):
                    openAset = None
            # 시초 스냅이 없거나 음수면 현재 예탁으로 고친다
            if openAset is None or openAset <= 0:
                openAset = float(aset) if float(aset or 0) > 0 else None

            # 당일 = 오늘 매도된 거래만 (수익금·수익률 분모=매도분 매입원가)
            # 알고리즘 당일·거래탭과 동일 기준
            sellPnl = 0.0
            tdyRt = 0.0
            try:
                sellDet = sumDeskSellPnlDetail(tradeDt=ymd, mode=modeKey) or {}
                sellPnl = float(sellDet.get("pnl") or 0)
                tdyRt = float(sellDet.get("pnlRt") or 0)
            except Exception as err:
                termLog(f"[acntPnlDay] 매도실현 집계 실패: {err}")
            tdyPnl = sellPnl
            baseAset = _positiveBase(prevAset, openAset, aset)

            # 월·년 = 전량청산 라운드 합 (알고리즘 승패와 동일 기준)
            monPnl = _sumLiveRoundPnl(cur, modeKey, ymd[:6], ymd)
            yearPnl = _sumLiveRoundPnl(cur, modeKey, ymd[:4], ymd)
            monRt = _rt(monPnl, _periodBase(cur, modeKey, acntNm, ymd[:6], monPnl, aset))
            yearRt = _rt(
                yearPnl, _periodBase(cur, modeKey, acntNm, ymd[:4], yearPnl, aset)
            )
            # 라운드 이력 없고 키움 당월손익만 있으면 API 값 사용
            if abs(monPnl) < 0.5 and apiMon:
                monPnl = float(apiMon)
                monRt = _rt(monPnl, baseAset)

            cur.execute(
                _UPSERT_SQL,
                (
                    modeKey,
                    acntNm,
                    ymd,
                    float(aset),
                    float(openAset) if openAset is not None else None,
                    float(prevAset) if prevAset is not None else None,
                    float(totPur) if totPur is not None else None,
                    float(tdyPnl),
                    tdyRt,
                    float(monPnl),
                    monRt,
                    float(yearPnl),
                    yearRt,
                    float(apiTdy) if apiTdy is not None else None,
                    float(apiMon) if apiMon is not None else None,
                ),
            )
        conn.commit()
        return {
            "ok": True,
            "mode": modeKey,
            "acntNm": acntNm,
            "tradeDt": ymd,
            "tdyPnl": tdyPnl,
            "tdyRt": tdyRt,
            "monPnl": monPnl,
            "monRt": monRt,
            "yearPnl": yearPnl,
            "yearRt": yearRt,
            "asetEvlt": float(aset),
        }
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[acntPnlDay] 적재 실패: {err}")
        return {"ok": False, "errMsg": str(err)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _periodBase(cur, mode, acntNm, prefix, periodPnl, todayAset):
    """기간 첫 양수 PREV/OPEN — 라운드 손익률 분모."""
    like = str(prefix) + "%"
    cur.execute(
        """
        SELECT PREV_ASET, OPEN_ASET, ASET_EVLT, TRADE_DT
          FROM AI_ACNT_PNL_DAY
         WHERE MODE=%s AND ACNT_NM=%s AND TRADE_DT LIKE %s
         ORDER BY TRADE_DT ASC
        """,
        (mode, acntNm, like),
    )
    rows = cur.fetchall() or []
    for first in rows:
        base = _positiveBase(
            first.get("PREV_ASET"),
            first.get("OPEN_ASET"),
            first.get("ASET_EVLT"),
        )
        if base is not None:
            return base
    return _positiveBase(float(todayAset or 0) - float(periodPnl or 0), todayAset)


def formatPnlLine(label, pnl, rt):
    """계좌창·리포트 한 줄."""
    try:
        pnlTxt = f"{int(round(float(pnl))):+,}원"
    except (TypeError, ValueError):
        pnlTxt = "-원"
    if rt is None:
        rtTxt = "-"
    else:
        try:
            rtTxt = f"{float(rt):+.2f}"
        except (TypeError, ValueError):
            rtTxt = "-"
    return f"{label} {pnlTxt} ({rtTxt}%)"


def readTodayAcntPnl(mode="paper", acntNm="", tradeDt=None):
    """오늘 저장된 손익. 없으면 {}."""
    if not ensureAcntPnlDayTable():
        return {}
    ymd = str(tradeDt or _todayYmd())[:8]
    modeKey = "paper" if str(mode or "") != "real" else "real"
    nm = (str(acntNm or "").strip() or "_")[:40]
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TDY_PNL, TDY_PNL_RT, MON_PNL, MON_PNL_RT,
                       YEAR_PNL, YEAR_PNL_RT, ASET_EVLT, ACNT_NM
                  FROM AI_ACNT_PNL_DAY
                 WHERE MODE=%s AND ACNT_NM=%s AND TRADE_DT=%s
                """,
                (modeKey, nm, ymd),
            )
            row = cur.fetchone() or {}
        if not row:
            return {}
        return {
            "tdyPnl": float(row.get("TDY_PNL") or 0),
            "tdyRt": float(row["TDY_PNL_RT"]) if row.get("TDY_PNL_RT") is not None else None,
            "monPnl": float(row.get("MON_PNL") or 0),
            "monRt": float(row["MON_PNL_RT"]) if row.get("MON_PNL_RT") is not None else None,
            "yearPnl": float(row.get("YEAR_PNL") or 0),
            "yearRt": float(row["YEAR_PNL_RT"]) if row.get("YEAR_PNL_RT") is not None else None,
            "asetEvlt": float(row.get("ASET_EVLT") or 0),
            "acntNm": row.get("ACNT_NM") or nm,
        }
    finally:
        conn.close()
