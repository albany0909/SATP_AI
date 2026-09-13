"""
신고가 눌림목매매1 유니버스 선별.

운영:
  ka10016 52주 신고 → 일봉으로 최초 52주 진입 확인 →
  등락≥10% · 코스피/코스닥 · (가능 시) 거래량 급증 · 외인/기관 매수

※ ka10016 은 '현재 시점' 스냅샷. 과거 일자별 최초신고 전수 스캔이 아님.
  선별 후 BT 기간(start~end)에서 눌림 진입을 검증한다.
"""

from utils.dbConn import openDbConn
from utils.ntlFirstEntry import fetchFirst52wHighList
from utils.stkCdUtil import normStkCd
from utils.stratValidate import normMktLabel
from utils.termLog import termLog


def _mktMeta(stkCd):
    """STK_NM, mktLabel(코스피/코스닥), ok."""
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_NM, MARKET_NAME, MRKT_TP
                FROM AI_STK_INFO WHERE STK_CD=%s
                """,
                (str(stkCd),),
            )
            row = cur.fetchone()
    except Exception:
        row = None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not row:
        return None, "-", False
    nm = str(row.get("STK_NM") or "").strip() or None
    mkt = normMktLabel(row.get("MARKET_NAME") or row.get("MRKT_TP"))
    ok = mkt in ("코스피", "코스닥")
    return nm, mkt, ok


def _daySupplyVol(stkCd, tradeDt, volMaPeriod=20, volMult=1.5):
    """
    AI_DAY_STK_PRC 기준 거래량·수급.
    반환: {volOk, supOk, hasDay, ...}  # None = 데이터 없음
    """
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, TRDE_QTY, FOR_NETPRPS, ORGN_NETPRPS,
                       FRGN_VAL, ORGN_VAL, OPEN_PRC, CLOSE_PRC, FLU_RT
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND INDC_TP='0' AND TRADE_DT<=%s
                ORDER BY TRADE_DT DESC
                LIMIT %s
                """,
                (str(stkCd), str(tradeDt), int(volMaPeriod) + 5),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[highPullUni] 일봉수급 조회 실패 {stkCd}: {err}")
        return {
            "hasDay": False,
            "volOk": None,
            "supOk": None,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not rows:
        return {"hasDay": False, "volOk": None, "supOk": None}

    d0 = rows[0]
    qty = float(d0.get("TRDE_QTY") or 0)
    prev = [float(r.get("TRDE_QTY") or 0) for r in rows[1 : volMaPeriod + 1]]
    avg = sum(prev) / len(prev) if prev else 0.0
    volOk = bool(avg > 0 and qty >= avg * float(volMult))

    try:
        forNet = int(d0.get("FOR_NETPRPS") or 0)
        orgNet = int(d0.get("ORGN_NETPRPS") or 0)
        frgn = int(d0.get("FRGN_VAL") or 0)
        orgn = int(d0.get("ORGN_VAL") or 0)
    except (TypeError, ValueError):
        forNet = orgNet = frgn = orgn = 0
    supOk = forNet > 0 or orgNet > 0 or frgn > 0 or orgn > 0

    return {
        "hasDay": True,
        "volOk": volOk,
        "supOk": supOk,
        "trdeQty": qty,
        "avgVol": avg,
        "forNet": forNet,
        "orgNet": orgNet,
        "frgn": frgn,
        "orgn": orgn,
    }


def listHighPullUniverse(
    hostUrl,
    token,
    tokenType="bearer",
    isPaper=True,
    minFluRt=10.0,
    volMaPeriod=20,
    volMult=1.5,
    requireVol=True,
    requireSup=True,
    softIfNoDay=True,
    maxCand=100,
    maxOut=40,
    baseDt=None,
    shouldAbort=None,
):
    """
    highPullback 검증용 종목 목록.
    반환 dict 키는 AI_WATCH_STK 와 비슷하게 STK_CD, STK_NM, MARKET_NAME, REF_TRADE_DT
    """
    if shouldAbort and shouldAbort():
        return {"ok": False, "errMsg": "사용자 중단", "rows": []}

    first = fetchFirst52wHighList(
        hostUrl,
        token,
        tokenType=tokenType,
        isPaper=isPaper,
        minFluRt=minFluRt,
        useClose=False,
        maxCand=maxCand,
        maxOut=maxOut * 2,
        baseDt=baseDt,
    )
    if not first.get("ok"):
        return {
            "ok": False,
            "errMsg": first.get("errMsg") or "최초 52주 신고 조회 실패",
            "rows": [],
        }

    out = []
    skipped = []
    for rec in first.get("rows") or []:
        if shouldAbort and shouldAbort():
            break
        stkCd = normStkCd(str(rec.get("stkCd") or "").split("_")[0])
        if not stkCd:
            continue
        nmDb, mktLabel, mktOk = _mktMeta(stkCd)
        nm = nmDb or rec.get("stkNm") or stkCd
        if not mktOk:
            skipped.append(f"{stkCd} 시장제외({mktLabel})")
            continue

        tradeDt = str(rec.get("tradeDt") or baseDt or "").strip()
        flu = rec.get("fluRt")
        if flu is not None and float(flu) < float(minFluRt):
            skipped.append(f"{stkCd} 등락<{minFluRt}%")
            continue

        sv = _daySupplyVol(
            stkCd, tradeDt or "20991231", volMaPeriod=volMaPeriod, volMult=volMult
        )
        # 거래량·수급: 일봉 없으면 soft 통과 가능
        if requireVol:
            if sv.get("volOk") is False:
                skipped.append(f"{stkCd} 거래량미달")
                continue
            if sv.get("volOk") is None and not softIfNoDay:
                skipped.append(f"{stkCd} 거래량데이터없음")
                continue
        if requireSup:
            if sv.get("supOk") is False:
                skipped.append(f"{stkCd} 수급미달")
                continue
            if sv.get("supOk") is None and not softIfNoDay:
                skipped.append(f"{stkCd} 수급데이터없음")
                continue

        out.append(
            {
                "STK_CD": stkCd,
                "STK_NM": nm,
                "MARKET_NAME": mktLabel,
                "MRKT_TP": mktLabel,
                "REF_TRADE_DT": tradeDt,
                "LOAD_DT": tradeDt,
                "GRP_CD": "HIGH_PULL",
                "fluRt": flu,
                "volOk": sv.get("volOk"),
                "supOk": sv.get("supOk"),
                "curPrc": rec.get("curPrc"),
            }
        )
        if len(out) >= maxOut:
            break

    termLog(
        f"[highPullUni] 선정 {len(out)}종 "
        f"(first={first.get('rowCnt')}, skip={len(skipped)})"
    )
    if skipped[:12]:
        termLog("[highPullUni] skip예: " + "; ".join(skipped[:12]))

    return {
        "ok": True,
        "rows": out,
        "rowCnt": len(out),
        "firstCnt": first.get("rowCnt"),
        "candCnt": first.get("candCnt"),
        "baseDt": first.get("baseDt"),
        "skipped": skipped,
        "minFluRt": minFluRt,
        "label": (
            f"ntl first 52주+등락>={minFluRt:g}% "
            f"코스피/닥"
            + (" ·거래량" if requireVol else "")
            + (" ·수급" if requireSup else "")
            + (" (일봉없으면 완화)" if softIfNoDay else "")
        ),
    }
