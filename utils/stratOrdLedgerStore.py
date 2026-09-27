"""
알고리즘 종목 원장.

- 미청산(상태 3): 알고리즘+종목 1행을 갱신 (매수1~5 / 익절 / 손절 슬롯)
- 청산(상태 9) 후 당일 같은 종목을 다시 사면 거래코드를 새로 만들어 적재
- 장 시작 전 상태 3 행을 로드해 실시간 등록·연속매매에 쓴다
"""

from datetime import datetime

from strategies.specLoader import getAlgCd, normAlgCd
from utils.dbConn import openDbConn
from strategies.impl.maTouchScale import normalizeFilledSteps
from utils.stkCdUtil import normStkCd, padStkCd
from utils.stratOrdLedgerTables import ensureStratOrdLedgerTable
from utils.termLog import termLog

_TRADE_CD_LEN = 25

_STS_BUY = "3"
_STS_SELL = "9"
# 체결 없이 취소된 주문. 미청산(3)·청산(9) 어디에도 안 걸려 라운드로 집계되지 않는다.
_STS_VOID = "0"
_WIN_OK = "1"
_WIN_NG = "2"


def _nowYmd():
    return datetime.now().strftime("%Y%m%d")


def resolveAlgCd(stratId):
    """스펙 algCd. 이미 5자리 코드면 그대로."""
    algCd = getAlgCd(stratId)
    if algCd:
        return algCd
    return normAlgCd(stratId)


def newTradeCd(stratId, stkCd, when=None):
    """거래코드 25자리 = 알고리즘코드5 + 종목6 + 일시분초14."""
    dt = when or datetime.now()
    algCd = resolveAlgCd(stratId)
    stk = padStkCd(stkCd)
    if not algCd or len(stk) != 6:
        return ""
    tradeCd = f"{algCd}{stk}{dt.strftime('%Y%m%d%H%M%S')}"
    return tradeCd if len(tradeCd) == _TRADE_CD_LEN else ""


def _nextSlotIdx(row, prefix):
    """BUY_/TP_/SL_ 1~5 중 비어 있는 첫 칸. 없으면 None."""
    for i in range(1, 6):
        if row.get(f"{prefix}{i}") is None:
            return i
    return None


def _flt(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def getOpenLedgerRow(stratId, stkCd):
    """해당 알고리즘·종목의 미청산 1행."""
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    algCd = resolveAlgCd(stratId)
    if not algCd or not cd:
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM AI_STRAT_ORD_LEDGER
                WHERE ALG_CD=%s AND STK_CD=%s AND STK_STS_CD=%s
                ORDER BY CREATED_AT DESC, TRADE_CD DESC
                LIMIT 1
                """,
                (algCd, cd, _STS_BUY),
            )
            return cur.fetchone()
    except Exception as err:
        termLog(f"[stratOrdLedger] 미청산 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def getLatestLedgerRow(stratId, stkCd, sinceYmd=None, mode=None):
    """
    상태 무관(미청산·무효0·청산9) 최근 원장 1행.
    계좌잔량0 무효 처리 뒤 늦게 온 매도 체결의 평단·진입일 복구용.
    """
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    algCd = resolveAlgCd(stratId)
    if not algCd or not cd:
        return None
    sql = """
        SELECT *
        FROM AI_STRAT_ORD_LEDGER
        WHERE ALG_CD=%s AND STK_CD=%s AND BUY_PX > 0
    """
    args = [algCd, cd]
    if sinceYmd:
        sql += " AND FIRST_BUY_DT>=%s"
        args.append(str(sinceYmd)[:8])
    modeKey = str(mode or "").strip().lower()
    if modeKey in ("paper", "real"):
        sql += " AND (MODE=%s OR MODE IS NULL OR MODE='')"
        args.append(modeKey)
    sql += " ORDER BY CREATED_AT DESC, TRADE_CD DESC LIMIT 1"
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()
    except Exception as err:
        termLog(f"[stratOrdLedger] 최근 원장 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listOpenLedgerRows(stratId=None):
    """이어 매매할 미청산 원장. 장 시작 전 로드용."""
    if not ensureStratOrdLedgerTable():
        return []
    conn = None
    try:
        conn = openDbConn()
        sql = """
            SELECT *
            FROM AI_STRAT_ORD_LEDGER
            WHERE STK_STS_CD=%s
        """
        args = [_STS_BUY]
        if stratId:
            algCd = resolveAlgCd(stratId)
            if not algCd:
                return []
            sql += " AND ALG_CD=%s"
            args.append(algCd)
        sql += " ORDER BY ALG_CD, STK_CD, CREATED_AT"
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() or []
    except Exception as err:
        termLog(f"[stratOrdLedger] 미청산 목록 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listOpenLedgerStkCds(stratId=None):
    """0B 준비용 종목코드."""
    out = []
    seen = set()
    for row in listOpenLedgerRows(stratId=stratId):
        cd = normStkCd(row.get("STK_CD"))
        if cd and cd not in seen:
            seen.add(cd)
            out.append(cd)
    return out


def fmtYmdDash(ymd):
    """YYYYMMDD → YYYY-MM-DD. 아니면 빈 문자열."""
    text = str(ymd or "").replace("-", "").replace(".", "")[:8]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def openFirstBuyDtMap(stratId=None):
    """
    미청산 원장 종목별 최초매수일(YYYYMMDD).
    같은 종목이 여러 알고리즘이면 더 이른 날을 쓴다.
    """
    out = {}
    for row in listOpenLedgerRows(stratId=stratId):
        cd = normStkCd(row.get("STK_CD"))
        dt = str(row.get("FIRST_BUY_DT") or "")[:8]
        if not cd or len(dt) != 8 or not dt.isdigit():
            continue
        prev = out.get(cd)
        if not prev or dt < prev:
            out[cd] = dt
    return out


def rowToLivePos(row, todayYmd=None):
    """원장 1행 → 실시간 엔진 _pos. 매차(7/13/20)는 청산 전까지 유지한다."""
    if not row:
        return None
    cd = normStkCd(row.get("STK_CD"))
    if not cd:
        return None
    today = todayYmd or _nowYmd()
    lastBuy = str(row.get("LAST_BUY_DT") or "")
    filled = []
    # 미청산 라운드에서는 날짜와 무관하게 매차를 유지한다. 7/13/20은 청산 전까지 각 1회.
    raw = str(row.get("FILLED_STEPS") or "")
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            filled.append(int(part))
        except ValueError:
            pass
    filled = normalizeFilledSteps(filled, [20])
    return {
        "qty": int(row.get("RMND_QTY") or 0),
        "avgPx": _flt(row.get("BUY_PX")) or 0.0,
        "filled": filled,
        "halfTaken": str(row.get("HALF_YN") or "N") == "Y",
        "buyAmt": _flt(row.get("BUY_AMT")) or 0.0,
        "sellAmt": _flt(row.get("SELL_AMT")) or 0.0,
        "buyQty": int(row.get("BUY_QTY") or 0),
        "sellQty": int(row.get("SELL_QTY") or 0),
        "entryDt": str(row.get("FIRST_BUY_DT") or ""),
        "tradeDt": lastBuy or str(row.get("FIRST_BUY_DT") or ""),
        "lastPx": _flt(row.get("BUY_PX")) or 0.0,
        "tradeCd": row.get("TRADE_CD") or "",
        "algCd": str(row.get("ALG_CD") or "").strip(),
        "stkNm": row.get("STK_NM") or "",
        "maxPnlPct": _flt(row.get("MAX_PNL_PCT")),
        "maxPx": _flt(row.get("MAX_PX")),
        "maxPnlTm": str(row.get("MAX_PNL_TM") or ""),
    }


def _winCd(buyAmt, sellAmt):
    if not buyAmt:
        return None
    pnl = float(sellAmt) - float(buyAmt)
    if pnl > 0:
        return _WIN_OK
    if pnl < 0:
        return _WIN_NG
    return None


def _pnlPct(buyAmt, sellAmt):
    buy = float(buyAmt or 0)
    if buy <= 0:
        return None
    return (float(sellAmt or 0) - buy) / buy * 100.0


def upsertLedgerBuy(stratId, stkCd, stkNm, px, qty, step, rec, mode="paper"):
    """
    매수 적재/갱신.
    미청산 행이 있으면 그 행의 다음 매수 슬롯을 채운다.
    없으면(청산 후 당일 재매수 포함) 거래코드를 새로 만들어 INSERT.
    """
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    algCd = resolveAlgCd(stratId)
    if not algCd or not cd or qty <= 0:
        return None
    px = float(px or 0)
    rec = rec or {}
    filled = rec.get("filled") or []
    filledTxt = ",".join(str(int(s)) for s in filled)
    today = _nowYmd()
    openRow = getOpenLedgerRow(stratId, cd)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            if openRow:
                # 같은 라운드 추가매수 — 거래코드 유지, 매수N차만 채움
                tradeCd = openRow.get("TRADE_CD")
                slot = _nextSlotIdx(openRow, "BUY_")
                sets = [
                    "STK_NM=%s",
                    "STK_STS_CD=%s",
                    "BUY_PX=%s",
                    "RMND_QTY=%s",
                    "BUY_QTY=%s",
                    "BUY_AMT=%s",
                    "FILLED_STEPS=%s",
                    "LAST_BUY_DT=%s",
                ]
                args = [
                    stkNm or openRow.get("STK_NM"),
                    _STS_BUY,
                    rec.get("avgPx") or px,
                    int(rec.get("qty") or 0),
                    int(rec.get("buyQty") or 0),
                    float(rec.get("buyAmt") or 0),
                    filledTxt or None,
                    today,
                ]
                if slot is not None:
                    sets.append(f"BUY_{slot}=%s")
                    args.append(px)
                args.append(tradeCd)
                cur.execute(
                    f"UPDATE AI_STRAT_ORD_LEDGER SET {', '.join(sets)} "
                    "WHERE TRADE_CD=%s",
                    args,
                )
            else:
                # 신규 또는 당일 청산 후 재매수 — 유니키 신규
                tradeCd = newTradeCd(stratId, cd)
                if not tradeCd:
                    termLog(f"[stratOrdLedger] 거래코드 생성 실패 {algCd} {cd}")
                    return None
                cur.execute(
                    """
                    INSERT INTO AI_STRAT_ORD_LEDGER (
                      TRADE_CD, ALG_CD, STK_CD, STK_NM, STK_STS_CD,
                      BUY_PX, STRAT_ID, FIRST_BUY_DT,
                      BUY_1, MODE, RMND_QTY, BUY_QTY, BUY_AMT,
                      FILLED_STEPS, LAST_BUY_DT
                    ) VALUES (
                      %s, %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s
                    )
                    """,
                    (
                        tradeCd,
                        algCd,
                        cd,
                        stkNm or None,
                        _STS_BUY,
                        rec.get("avgPx") or px,
                        "",
                        rec.get("entryDt") or today,
                        px,
                        mode or "paper",
                        int(rec.get("qty") or qty),
                        int(rec.get("buyQty") or qty),
                        float(rec.get("buyAmt") or (px * qty)),
                        filledTxt or (str(int(step)) if step else None),
                        today,
                    ),
                )
        conn.commit()
        return tradeCd
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[stratOrdLedger] 매수 적재 실패 {cd}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def patchOpenLedger(stratId, stkCd, rec):
    """
    미청산 행의 평단·잔량·누적매수·매차만 갱신.
    BUY_N 슬롯은 추가하지 않는다. 키움 계좌평단 동기화·주문접수 매차 표시용.
    """
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    if not resolveAlgCd(stratId) or not cd:
        return None
    rec = rec or {}
    openRow = getOpenLedgerRow(stratId, cd)
    if not openRow:
        return None
    tradeCd = rec.get("tradeCd") or openRow.get("TRADE_CD")
    filled = rec.get("filled") or []
    filledTxt = ",".join(str(int(s)) for s in filled) if filled else None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE AI_STRAT_ORD_LEDGER
                SET BUY_PX=%s, RMND_QTY=%s, BUY_QTY=%s, BUY_AMT=%s,
                    FILLED_STEPS=%s, HALF_YN=%s,
                    MAX_PNL_PCT=%s, MAX_PX=%s
                WHERE TRADE_CD=%s
                """,
                (
                    rec.get("avgPx") if rec.get("avgPx") not in (None, "") else openRow.get("BUY_PX"),
                    int(rec.get("qty") if rec.get("qty") is not None else openRow.get("RMND_QTY") or 0),
                    int(rec.get("buyQty") if rec.get("buyQty") is not None else openRow.get("BUY_QTY") or 0),
                    float(rec.get("buyAmt") if rec.get("buyAmt") is not None else openRow.get("BUY_AMT") or 0),
                    filledTxt if filledTxt is not None else openRow.get("FILLED_STEPS"),
                    "Y" if rec.get("halfTaken") else "N",
                    rec.get("maxPnlPct") if rec.get("maxPnlPct") is not None else openRow.get("MAX_PNL_PCT"),
                    rec.get("maxPx") if rec.get("maxPx") is not None else openRow.get("MAX_PX"),
                    tradeCd,
                ),
            )
        conn.commit()
        return tradeCd
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[stratOrdLedger] 평단동기 실패 {cd}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def voidOpenLedger(stratId, stkCd, reason=""):
    """
    체결이 하나도 없는 미청산 원장을 무효(0)로 닫는다.

    지정가 매수는 주문 접수 시점에 원장이 먼저 만들어지는데, 전량 미체결로
    취소되면 그 행이 미청산으로 남는다. 그대로 두면 계좌잔량0 청산으로 닫히면서
    매수가=매도가인 0원 유령 라운드가 되어 승패·라운드 수를 오염시킨다.
    실제 매도 체결이 있었으면 정상 청산이므로 건드리지 않는다.
    """
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    algCd = resolveAlgCd(stratId)
    if not algCd or not cd:
        return None
    openRow = getOpenLedgerRow(stratId, cd)
    if not openRow:
        return None
    if float(openRow.get("SELL_AMT") or 0) > 0 or int(openRow.get("SELL_QTY") or 0) > 0:
        # 매도가 한 번이라도 있었으면 무효가 아니라 실제 라운드다
        return None
    tradeCd = openRow.get("TRADE_CD")
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE AI_STRAT_ORD_LEDGER
                SET STK_STS_CD=%s, RMND_QTY=0, CLOSE_DT=NULL,
                    CLOSE_PNL_PCT=NULL, WIN_CD=NULL
                WHERE TRADE_CD=%s
                """,
                (_STS_VOID, tradeCd),
            )
        conn.commit()
        termLog(
            f"[stratOrdLedger] 미체결 원장 무효 {algCd} {cd} "
            f"{openRow.get('STK_NM') or ''} ({reason or '체결없음'})"
        )
        return tradeCd
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[stratOrdLedger] 원장 무효 실패 {cd}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def upsertLedgerSell(stratId, stkCd, px, qty, sellKind, rec, closed, mode="paper"):
    """
    매도 갱신. sellKind: TP=익절, SL=손절.
    잔량이 0이면 상태 9·청산일·승패·청산수익률을 채운다.
    """
    if not ensureStratOrdLedgerTable():
        return None
    cd = padStkCd(stkCd)
    if not resolveAlgCd(stratId) or not cd:
        return None
    px = float(px or 0)
    rec = rec or {}
    openRow = getOpenLedgerRow(stratId, cd)
    if not openRow:
        termLog(f"[stratOrdLedger] 매도 대상 미청산 없음 {stratId} {cd}")
        return None
    tradeCd = rec.get("tradeCd") or openRow.get("TRADE_CD")
    prefix = "TP_" if str(sellKind or "").upper() == "TP" else "SL_"
    slot = _nextSlotIdx(openRow, prefix)
    buyAmt = float(rec.get("buyAmt") or openRow.get("BUY_AMT") or 0)
    sellAmt = float(rec.get("sellAmt") or openRow.get("SELL_AMT") or 0)
    pnlPct = _pnlPct(buyAmt, sellAmt) if closed else None
    winCd = _winCd(buyAmt, sellAmt) if closed else None
    today = _nowYmd()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sets = [
                "SELL_PX=%s",
                "RMND_QTY=%s",
                "SELL_QTY=%s",
                "SELL_AMT=%s",
                "HALF_YN=%s",
            ]
            args = [
                px,
                int(rec.get("qty") or 0),
                int(rec.get("sellQty") or 0),
                sellAmt,
                "Y" if rec.get("halfTaken") else "N",
            ]
            if rec.get("maxPnlPct") is not None:
                sets.append("MAX_PNL_PCT=%s")
                args.append(rec.get("maxPnlPct"))
            if rec.get("maxPx") is not None:
                sets.append("MAX_PX=%s")
                args.append(rec.get("maxPx"))
            if slot is not None:
                sets.append(f"{prefix}{slot}=%s")
                args.append(px)
            if closed:
                # 전량 청산 — 검증용 승패·수익률. 같은 날 재매수하면 새 TRADE_CD
                sets.extend(
                    [
                        "STK_STS_CD=%s",
                        "CLOSE_DT=%s",
                        "CLOSE_PNL_PCT=%s",
                        "WIN_CD=%s",
                    ]
                )
                args.extend([_STS_SELL, today, pnlPct, winCd])
            else:
                sets.append("STK_STS_CD=%s")
                args.append(_STS_BUY)
            args.append(tradeCd)
            cur.execute(
                f"UPDATE AI_STRAT_ORD_LEDGER SET {', '.join(sets)} "
                "WHERE TRADE_CD=%s",
                args,
            )
        conn.commit()
        return tradeCd
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        termLog(f"[stratOrdLedger] 매도 갱신 실패 {cd}: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
