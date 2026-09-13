"""AI_DAY_INV_SR UPSERT · 조회"""

from utils.dayInvSrTables import ensureDayInvSrTable
from utils.dbConn import openDbConn
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

_UPSERT_SQL = """
INSERT INTO AI_DAY_INV_SR (
  STK_CD, TRADE_DT, AMT_QTY_TP, TRDE_TP, UNIT_TP,
  CUR_PRC, PRE_SIG, PRED_PRE, FLU_RT, ACC_TRDE_QTY, ACC_TRDE_PRICA,
  IND_INVSR, FRGNR_INVSR, ORGN, FNNC_INVT, INSRNC, INVTRT, ETC_FNNC,
  BANK, PENFND_ETC, SAMO_FUND, NATN, ETC_CORP, NATFOR, SRC_TR
) VALUES (
  %(STK_CD)s, %(TRADE_DT)s, %(AMT_QTY_TP)s, %(TRDE_TP)s, %(UNIT_TP)s,
  %(CUR_PRC)s, %(PRE_SIG)s, %(PRED_PRE)s, %(FLU_RT)s, %(ACC_TRDE_QTY)s, %(ACC_TRDE_PRICA)s,
  %(IND_INVSR)s, %(FRGNR_INVSR)s, %(ORGN)s, %(FNNC_INVT)s, %(INSRNC)s, %(INVTRT)s, %(ETC_FNNC)s,
  %(BANK)s, %(PENFND_ETC)s, %(SAMO_FUND)s, %(NATN)s, %(ETC_CORP)s, %(NATFOR)s, %(SRC_TR)s
)
ON DUPLICATE KEY UPDATE
  CUR_PRC = VALUES(CUR_PRC),
  PRE_SIG = VALUES(PRE_SIG),
  PRED_PRE = VALUES(PRED_PRE),
  FLU_RT = VALUES(FLU_RT),
  ACC_TRDE_QTY = VALUES(ACC_TRDE_QTY),
  ACC_TRDE_PRICA = VALUES(ACC_TRDE_PRICA),
  IND_INVSR = VALUES(IND_INVSR),
  FRGNR_INVSR = VALUES(FRGNR_INVSR),
  ORGN = VALUES(ORGN),
  FNNC_INVT = VALUES(FNNC_INVT),
  INSRNC = VALUES(INSRNC),
  INVTRT = VALUES(INVTRT),
  ETC_FNNC = VALUES(ETC_FNNC),
  BANK = VALUES(BANK),
  PENFND_ETC = VALUES(PENFND_ETC),
  SAMO_FUND = VALUES(SAMO_FUND),
  NATN = VALUES(NATN),
  ETC_CORP = VALUES(ETC_CORP),
  NATFOR = VALUES(NATFOR),
  SRC_TR = VALUES(SRC_TR)
"""


def upsertDayInvSrRows(rows):
    """종목별투자자 row UPSERT. 반환: {ok, upsertCnt, errMsg?}"""
    if not ensureDayInvSrTable():
        return {"ok": False, "errMsg": "AI_DAY_INV_SR 테이블 생성 실패", "upsertCnt": 0}
    if not rows:
        return {"ok": True, "upsertCnt": 0}

    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount
        conn.commit()
        upsertCnt = len(rows)
        termLog(f"[AI_DAY_INV_SR] UPSERT {upsertCnt}건 (affected={affected})")
        return {"ok": True, "upsertCnt": upsertCnt, "affected": affected}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"DB 적재 실패: {err}", "upsertCnt": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def hasInvSrForStkDt(stkCd, tradeDt, amtQtyTp="2", trdeTp="0", unitTp="1"):
    """해당 종목·일자·구분 1건 이상이면 True."""
    if not ensureDayInvSrTable():
        return False
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM AI_DAY_INV_SR
                WHERE STK_CD=%s AND TRADE_DT=%s
                  AND AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
                LIMIT 1
                """,
                (
                    normStkCd(stkCd),
                    str(tradeDt),
                    str(amtQtyTp),
                    str(trdeTp),
                    str(unitTp),
                ),
            )
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listLoadedStkCdsByDt(tradeDt, amtQtyTp="2", trdeTp="0", unitTp="1"):
    """해당일 이미 적재된 종목코드 set."""
    if not ensureDayInvSrTable():
        return set()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT STK_CD
                FROM AI_DAY_INV_SR
                WHERE TRADE_DT=%s AND AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
                """,
                (str(tradeDt), str(amtQtyTp), str(trdeTp), str(unitTp)),
            )
            return {str(r.get("STK_CD") or "") for r in (cur.fetchall() or []) if r.get("STK_CD")}
    except Exception:
        return set()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def listInvSrByStk(
    stkCd,
    amtQtyTp="2",
    trdeTp="0",
    unitTp="1",
    startDt=None,
    endDt=None,
    limit=400,
):
    """종목 일별 수급 (최신일 우선)."""
    if not ensureDayInvSrTable():
        return []
    code = normStkCd(stkCd)
    if not code:
        return []
    lim = max(1, min(int(limit or 400), 800))
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            sql = """
                SELECT STK_CD, TRADE_DT, AMT_QTY_TP, TRDE_TP, UNIT_TP,
                       CUR_PRC, PRE_SIG, PRED_PRE, FLU_RT,
                       ACC_TRDE_QTY, ACC_TRDE_PRICA,
                       IND_INVSR, FRGNR_INVSR, ORGN, FNNC_INVT, INSRNC,
                       INVTRT, ETC_FNNC, BANK, PENFND_ETC, SAMO_FUND,
                       NATN, ETC_CORP, NATFOR, SRC_TR, UPDATED_AT
                FROM AI_DAY_INV_SR
                WHERE STK_CD=%s AND AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
            """
            args = [code, str(amtQtyTp), str(trdeTp), str(unitTp)]
            if startDt:
                sql += " AND TRADE_DT>=%s"
                args.append(str(startDt))
            if endDt:
                sql += " AND TRADE_DT<=%s"
                args.append(str(endDt))
            sql += " ORDER BY TRADE_DT DESC LIMIT %s"
            args.append(lim)
            cur.execute(sql, args)
            return list(cur.fetchall() or [])
    except Exception as err:
        termLog(f"[AI_DAY_INV_SR] 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def latestInvSrMeta():
    """최근 적재 메타 (일자·종목수)."""
    if not ensureDayInvSrTable():
        return None
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(TRADE_DT) AS MAX_DT,
                       MAX(UPDATED_AT) AS UPDATED_AT,
                       COUNT(DISTINCT STK_CD) AS STK_CNT,
                       COUNT(*) AS ROW_CNT
                FROM AI_DAY_INV_SR
                """
            )
            row = cur.fetchone() or {}
            if not row.get("MAX_DT"):
                return None
            return {
                "tradeDt": row.get("MAX_DT"),
                "updatedAt": row.get("UPDATED_AT"),
                "stkCnt": int(row.get("STK_CNT") or 0),
                "rowCnt": int(row.get("ROW_CNT") or 0),
            }
    except Exception as err:
        termLog(f"[AI_DAY_INV_SR] 메타 조회 실패: {err}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


_CHUNK = 200
# 직전 N일 대비 |증가율| 이 이 값 이하면 보합으로 본다
_NET_CHG_FLAT = 0.10


def _netChgRate(cur, prev):
    """(최근 − 직전) / |직전|. 직전 0이면 최근 부호만 본다."""
    try:
        curN = int(cur or 0)
        prevN = int(prev or 0)
    except (TypeError, ValueError):
        return 0.0
    if prevN == 0:
        if curN == 0:
            return 0.0
        return 1.0 if curN > 0 else -1.0
    return (curN - prevN) / float(abs(prevN))


def _netChgStep(rate, flatPct=_NET_CHG_FLAT):
    """1 증가 / 0 보합 / -1 감소."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return 0
    if r > flatPct:
        return 1
    if r < -flatPct:
        return -1
    return 0


def _qtyAmtOfDays(daysSlice, key):
    """일별 순매수 주수 합 + 순매수×종가(원). 없는 날은 건너뛴다."""
    qty = 0
    amt = 0
    cnt = 0
    for d in daysSlice or []:
        v = d.get(key)
        if v is None:
            continue
        try:
            q = int(v)
        except (TypeError, ValueError):
            continue
        qty += q
        try:
            prc = abs(int(d.get("curPrc") or 0))
        except (TypeError, ValueError):
            prc = 0
        amt += q * prc
        cnt += 1
    return qty, amt, cnt


def _attachHalfWindow(rec, nDays):
    """최근 N일을 반으로 나눠 최근 N/2 vs 직전 N/2 수량·대금 증감."""
    half = max(1, int(nDays) // 2)
    days = list(rec.get("days") or [])
    h1 = days[:half]
    h0 = days[half : half * 2]
    rec["halfN"] = half
    rec["halfCnt1"] = 0
    rec["halfCnt0"] = 0
    # 기관 세분: 연기금·투신·사모 · 기타(기관계 − 세 항목)
    for key, prefix in (
        ("ind", "ind"),
        ("frgn", "frgn"),
        ("orgn", "orgn"),
        ("pen", "pen"),
        ("invt", "invt"),
        ("samo", "samo"),
        ("orgnEtc", "orgnEtc"),
    ):
        q1, a1, c1 = _qtyAmtOfDays(h1, key)
        q0, a0, c0 = _qtyAmtOfDays(h0, key)
        rec[f"{prefix}H1"] = q1
        rec[f"{prefix}H0"] = q0
        rec[f"{prefix}AmtH1"] = a1
        rec[f"{prefix}AmtH0"] = a0
        rec[f"{prefix}AmtDiff"] = a1 - a0
        rec[f"{prefix}AmtChg"] = _netChgRate(a1, a0)
        rec[f"{prefix}HChg"] = _netChgRate(q1, q0)
        rec[f"{prefix}HStep"] = _netChgStep(rec[f"{prefix}AmtChg"])
        rec["halfCnt1"] = max(int(rec["halfCnt1"]), c1)
        rec["halfCnt0"] = max(int(rec["halfCnt0"]), c0)
    return rec


def loadNetSumLastNDays(stkCds, days=20, amtQtyTp="2", trdeTp="0", unitTp="1"):
    """
    최근 N거래일 개인·외인·기관 순매수 합(주) + 일별 시계열(days).
    달력이 아니라 AI_DAY_INV_SR 최근 N개 일자. 반분(N/2 vs N/2) 증감 포함.
    기관은 연기금(PENFND_ETC)·투신(INVTRT)·사모(SAMO_FUND)·기타(ORGN−셋)도 같이 둔다.
    반환: {stkCd: ind/frgn/orgn/pen/invt/samo/orgnEtc, days[최근→과거], *AmtChg/*AmtDiff}
    """
    out = {}
    codes = []
    seen = set()
    for raw in stkCds or []:
        cd = normStkCd(raw)
        if cd and cd not in seen:
            seen.add(cd)
            codes.append(cd)
    if not codes:
        return out
    try:
        nDays = max(1, min(int(days or 20), 60))
    except (TypeError, ValueError):
        nDays = 20
    if not ensureDayInvSrTable():
        return out
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 시장 공통 최근 N거래일 (히트맵·반분 비교 동일 구간)
            cur.execute(
                """
                SELECT DISTINCT TRADE_DT
                FROM AI_DAY_INV_SR
                WHERE AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (str(amtQtyTp), str(trdeTp), str(unitTp), nDays),
            )
            dtRows = cur.fetchall() or []
            recentDts = [str(r.get("TRADE_DT") or "") for r in dtRows if r.get("TRADE_DT")]
            if not recentDts:
                return out
            dtMarks = ",".join(["%s"] * len(recentDts))
            recSet = set(recentDts)
            for i in range(0, len(codes), _CHUNK):
                part = codes[i : i + _CHUNK]
                cdMarks = ",".join(["%s"] * len(part))
                cur.execute(
                    f"""
                    SELECT STK_CD, TRADE_DT,
                           COALESCE(IND_INVSR, 0) AS IND_VAL,
                           COALESCE(FRGNR_INVSR, 0) AS FRGN_VAL,
                           COALESCE(ORGN, 0) AS ORGN_VAL,
                           COALESCE(PENFND_ETC, 0) AS PEN_VAL,
                           COALESCE(INVTRT, 0) AS INVT_VAL,
                           COALESCE(SAMO_FUND, 0) AS SAMO_VAL,
                           COALESCE(CUR_PRC, 0) AS CUR_PRC
                    FROM AI_DAY_INV_SR
                    WHERE AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
                      AND TRADE_DT IN ({dtMarks})
                      AND STK_CD IN ({cdMarks})
                    """,
                    [str(amtQtyTp), str(trdeTp), str(unitTp)] + recentDts + part,
                )
                byCd = {}
                for r in cur.fetchall() or []:
                    cd = normStkCd(r.get("STK_CD"))
                    dt = str(r.get("TRADE_DT") or "")
                    if not cd or not dt or dt not in recSet:
                        continue
                    rec = byCd.setdefault(
                        cd,
                        {
                            "ind": 0,
                            "frgn": 0,
                            "orgn": 0,
                            "pen": 0,
                            "invt": 0,
                            "samo": 0,
                            "orgnEtc": 0,
                            "dayCnt": 0,
                            "byDt": {},
                        },
                    )
                    indV = int(r.get("IND_VAL") or 0)
                    frgnV = int(r.get("FRGN_VAL") or 0)
                    orgnV = int(r.get("ORGN_VAL") or 0)
                    penV = int(r.get("PEN_VAL") or 0)
                    invtV = int(r.get("INVT_VAL") or 0)
                    samoV = int(r.get("SAMO_VAL") or 0)
                    orgnEtcV = orgnV - penV - invtV - samoV
                    try:
                        curPrc = abs(int(r.get("CUR_PRC") or 0))
                    except (TypeError, ValueError):
                        curPrc = 0
                    rec["ind"] += indV
                    rec["frgn"] += frgnV
                    rec["orgn"] += orgnV
                    rec["pen"] += penV
                    rec["invt"] += invtV
                    rec["samo"] += samoV
                    rec["orgnEtc"] += orgnEtcV
                    rec["dayCnt"] += 1
                    rec["byDt"][dt] = {
                        "ind": indV,
                        "frgn": frgnV,
                        "orgn": orgnV,
                        "pen": penV,
                        "invt": invtV,
                        "samo": samoV,
                        "orgnEtc": orgnEtcV,
                        "curPrc": curPrc,
                    }
                for cd, rec in byCd.items():
                    # 최근 N일, 왼쪽(최근)→오른쪽(과거) 히트맵용. 없는 날은 None
                    byDt = rec.pop("byDt", {}) or {}
                    rec["days"] = []
                    for dt in recentDts:
                        cell = byDt.get(dt)
                        if cell is None:
                            rec["days"].append(
                                {
                                    "dt": dt,
                                    "ind": None,
                                    "frgn": None,
                                    "orgn": None,
                                    "pen": None,
                                    "invt": None,
                                    "samo": None,
                                    "orgnEtc": None,
                                    "curPrc": None,
                                }
                            )
                        else:
                            rec["days"].append(
                                {
                                    "dt": dt,
                                    "ind": cell.get("ind"),
                                    "frgn": cell.get("frgn"),
                                    "orgn": cell.get("orgn"),
                                    "pen": cell.get("pen"),
                                    "invt": cell.get("invt"),
                                    "samo": cell.get("samo"),
                                    "orgnEtc": cell.get("orgnEtc"),
                                    "curPrc": cell.get("curPrc"),
                                }
                            )
                    _attachHalfWindow(rec, nDays)
                    out[cd] = rec
    except Exception as err:
        termLog(f"[AI_DAY_INV_SR] {nDays}일 순매수 합 실패: {err}")
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out
