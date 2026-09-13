"""
최초 52주 신고 진입 종목 → AI_DAY_NTL_FIRST 일 적재.

- 기관/외인 수급 여부로 제외하지 않음 (수량·금액만 기록)
- 거래량 · 20일 평균 · 평균 대비 증가율(%) 기록
- 순매수 수량(ka10086 수량) + 순매수 금액(ka10086 금액)
"""

import time
from datetime import datetime, timedelta

from api.getDayStkPrc import INDC_TP_QTY, fetchDayStkPrcRange
from utils.dayPrcLoadService import acquireKiwoomSession
from utils.dbConn import openDbConn
from utils.ntlFirstEntry import fetchFirst52wHighList
from utils.ntlFirstStore import upsertNtlFirstRows
from utils.ntlFirstTables import ensureNtlFirstTable
from utils.stkCdUtil import normStkCd
from utils.stratValidate import normMktLabel
from utils.termLog import termLog

# VARCHAR(20) 제한 — ka 접두사는 첫 TR만, 나머지는 숫자만 표기
SRC_TR = "ka10016+10081+10086"
VOL_MA = 20
# 적재 시 등락·수급 필터 없음 — 후보 넓게 검사
DEFAULT_MAX_CAND = 200
DEFAULT_MAX_OUT = 120


def _pickQuerySession():
    # 웹 프로세스(수동 배치)에서도 쓰이도록 .env 앱키 발급까지 위임
    return acquireKiwoomSession()


def _mktMeta(stkCd):
    """STK_NM, MARKET_NAME from AI_STK_INFO."""
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
        return None, None
    nm = str(row.get("STK_NM") or "").strip() or None
    mkt = normMktLabel(row.get("MARKET_NAME") or row.get("MRKT_TP"))
    if mkt == "-":
        mkt = None
    return nm, mkt


def _toFluRt(raw):
    """등락률 → float(%). 실패 시 None."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _daySupplyFromDb(stkCd, tradeDt):
    """
    AI_DAY_STK_PRC 에서 수량(0)·금액(1) 수급 + 등락률.
    반환: {forNetQty, orgnNetQty, forNetAmt, orgnNetAmt, fluRt}
    """
    out = {
        "forNetQty": None,
        "orgnNetQty": None,
        "forNetAmt": None,
        "orgnNetAmt": None,
        "fluRt": None,
    }
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT INDC_TP, FOR_NETPRPS, ORGN_NETPRPS, FRGN_VAL, ORGN_VAL, FLU_RT
                FROM AI_DAY_STK_PRC
                WHERE STK_CD=%s AND TRADE_DT=%s AND INDC_TP IN ('0','1')
                """,
                (str(stkCd), str(tradeDt)),
            )
            for row in cur.fetchall() or []:
                tp = str(row.get("INDC_TP") or "")
                if tp == "0":
                    # 수량은 순매수 필드 우선
                    out["forNetQty"] = row.get("FOR_NETPRPS")
                    out["orgnNetQty"] = row.get("ORGN_NETPRPS")
                    if out.get("fluRt") is None:
                        out["fluRt"] = _toFluRt(row.get("FLU_RT"))
                elif tp == "1":
                    # 금액: 순매수가 있으면 그것, 없으면 외국계/기관 값
                    forAmt = row.get("FOR_NETPRPS")
                    if forAmt is None:
                        forAmt = row.get("FRGN_VAL")
                    orgAmt = row.get("ORGN_NETPRPS")
                    if orgAmt is None:
                        orgAmt = row.get("ORGN_VAL")
                    out["forNetAmt"] = forAmt
                    out["orgnNetAmt"] = orgAmt
                    if out.get("fluRt") is None:
                        out["fluRt"] = _toFluRt(row.get("FLU_RT"))
    except Exception as err:
        termLog(f"[ntlFirstLoad] 수급DB조회실패 {stkCd}: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _fetchSupplyApi(
    hostUrl, token, stkCd, tradeDt, tokenType="bearer", isPaper=True
):
    """
    ka10086 으로 당일 수급 수량·금액 조회 (직전 3일 범위 — 휴장 대비).
    """
    try:
        end = datetime.strptime(str(tradeDt), "%Y%m%d")
    except ValueError:
        end = datetime.now()
    start = (end - timedelta(days=5)).strftime("%Y%m%d")
    endYmd = end.strftime("%Y%m%d")

    out = {
        "forNetQty": None,
        "orgnNetQty": None,
        "forNetAmt": None,
        "orgnNetAmt": None,
        "fluRt": None,
    }
    # 수량
    qty = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCd,
        start,
        endYmd,
        indcTp=INDC_TP_QTY,
        tokenType=tokenType,
        isPaper=isPaper,
    )
    if qty.get("ok"):
        for rec in qty.get("rows") or []:
            if str(rec.get("TRADE_DT")) == str(tradeDt):
                out["forNetQty"] = rec.get("FOR_NETPRPS")
                out["orgnNetQty"] = rec.get("ORGN_NETPRPS")
                out["fluRt"] = _toFluRt(rec.get("FLU_RT"))
                break
    # 금액 — 모의 1초 대기
    time.sleep(1.15 if isPaper else 0.3)
    amt = fetchDayStkPrcRange(
        hostUrl,
        token,
        stkCd,
        start,
        endYmd,
        indcTp="1",
        tokenType=tokenType,
        isPaper=isPaper,
    )
    if amt.get("ok"):
        for rec in amt.get("rows") or []:
            if str(rec.get("TRADE_DT")) == str(tradeDt):
                forAmt = rec.get("FOR_NETPRPS")
                if forAmt is None:
                    forAmt = rec.get("FRGN_VAL")
                orgAmt = rec.get("ORGN_NETPRPS")
                if orgAmt is None:
                    orgAmt = rec.get("ORGN_VAL")
                out["forNetAmt"] = forAmt
                out["orgnNetAmt"] = orgAmt
                if out.get("fluRt") is None:
                    out["fluRt"] = _toFluRt(rec.get("FLU_RT"))
                break
    return out


def loadNtlFirst(
    tradeDt=None,
    maxCand=DEFAULT_MAX_CAND,
    maxOut=DEFAULT_MAX_OUT,
    onProgress=None,
):
    """
    당일(또는 tradeDt) 최초 52주 신고 진입 종목 적재.
    등락·수급 필터 없이 최초 진입이면 전부 저장.

    반환: {ok, tradeDt, upsertCnt, firstCnt, candCnt, modeLabel, errMsg?, rows?}
    """
    if not ensureNtlFirstTable():
        return {"ok": False, "errMsg": "AI_DAY_NTL_FIRST 테이블 생성 실패", "upsertCnt": 0}

    session = _pickQuerySession()
    if session is None:
        return {
            "ok": False,
            "errMsg": "적재 전 login paper 또는 login real 필요",
            "upsertCnt": 0,
        }

    mode, hostUrl, token, tokenType = session
    isPaper = mode == "paper"
    modeLabel = "모의투자" if isPaper else "실투자"
    baseDt = str(tradeDt or datetime.now().strftime("%Y%m%d")).strip()

    termLog(
        f"[ntlFirstLoad] ({modeLabel}) 최초52 선별·적재 시작 baseDt={baseDt} "
        f"maxCand={maxCand}"
    )
    if onProgress:
        try:
            onProgress(f"[ntl load] 선별 시작 ({modeLabel}) base={baseDt}")
        except Exception:
            pass

    # 수급·등락 무관 — minFluRt=None
    first = fetchFirst52wHighList(
        hostUrl,
        token,
        tokenType=tokenType,
        isPaper=isPaper,
        minFluRt=None,
        useClose=False,
        maxCand=maxCand,
        maxOut=maxOut,
        baseDt=baseDt,
    )
    if not first.get("ok"):
        return {
            "ok": False,
            "errMsg": first.get("errMsg") or "최초52 조회 실패",
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "tradeDt": baseDt,
        }

    firstRows = first.get("rows") or []
    dbRows = []
    supFromApi = 0
    supFromDb = 0

    for i, rec in enumerate(firstRows):
        stkCd = normStkCd(str(rec.get("stkCd") or "").split("_")[0])
        if not stkCd:
            continue
        entryDt = str(rec.get("tradeDt") or baseDt).strip()
        nmDb, mkt = _mktMeta(stkCd)
        nm = nmDb or rec.get("stkNm") or stkCd

        # 수급: DB 우선, 없으면 API (금액 포함)
        sup = _daySupplyFromDb(stkCd, entryDt)
        needApi = (
            sup.get("forNetQty") is None
            and sup.get("orgnNetQty") is None
            and sup.get("forNetAmt") is None
            and sup.get("orgnNetAmt") is None
        )
        if needApi:
            if i > 0 or supFromApi > 0:
                time.sleep(1.15 if isPaper else 0.3)
            if onProgress and (i % 5 == 0):
                try:
                    onProgress(
                        f"[ntl load] 수급보강 {i + 1}/{len(firstRows)} {stkCd}"
                    )
                except Exception:
                    pass
            sup = _fetchSupplyApi(
                hostUrl,
                token,
                stkCd,
                entryDt,
                tokenType=tokenType,
                isPaper=isPaper,
            )
            supFromApi += 1
        else:
            supFromDb += 1

        # 수급 여부 무시 — 값만 기록 (None 가능)
        highP = rec.get("high")
        try:
            highP = int(highP) if highP is not None else None
        except (TypeError, ValueError):
            highP = None
        prevMax = rec.get("prevMax")
        try:
            prevMax = int(prevMax) if prevMax is not None else None
        except (TypeError, ValueError):
            prevMax = None
        curP = rec.get("curPrc")
        try:
            curP = int(curP) if curP is not None else None
        except (TypeError, ValueError):
            curP = None

        # 등락률: 일봉 계산 → ka10016 API → 일별주가(수급 조회분)
        fluRt = _toFluRt(rec.get("fluRt"))
        if fluRt is None:
            fluRt = _toFluRt(rec.get("apiFluRt"))
        if fluRt is None:
            fluRt = _toFluRt(sup.get("fluRt"))

        dbRows.append(
            {
                "TRADE_DT": entryDt,
                "STK_CD": stkCd,
                "STK_NM": nm,
                "MARKET_NAME": mkt,
                "FLU_RT": fluRt,
                "CUR_PRC": curP,
                "HIGH_PRC": highP,
                "PREV_MAX_HIGH": prevMax,
                "TRDE_QTY": rec.get("trdeQty"),
                "AVG_VOL_20": rec.get("avgVol20"),
                "VOL_INC_RT": rec.get("volIncRt"),
                "FOR_NETPRPS": sup.get("forNetQty"),
                "ORGN_NETPRPS": sup.get("orgnNetQty"),
                "FOR_NET_AMT": sup.get("forNetAmt"),
                "ORGN_NET_AMT": sup.get("orgnNetAmt"),
                "USE_CLOSE": "N",
                "SRC_TR": SRC_TR,
            }
        )

    if not dbRows:
        # 0건도 성공으로 (당일 최초 없음) — 재시도 방지용 메타는 스케줄러 판단
        termLog(
            f"[ntlFirstLoad] 최초진입 0건 "
            f"(cand={first.get('candCnt')} checked={first.get('checked')})"
        )
        return {
            "ok": True,
            "upsertCnt": 0,
            "firstCnt": 0,
            "candCnt": first.get("candCnt") or 0,
            "checked": first.get("checked") or 0,
            "tradeDt": baseDt,
            "modeLabel": modeLabel,
            "empty": True,
            "summary": (
                f"[ntl load] ({modeLabel}) 최초진입 0건\n"
                f"일자 기준: {baseDt}\n"
                f"ka10016 후보 {first.get('candCnt')} · 검사 {first.get('checked')}"
            ),
        }

    saved = upsertNtlFirstRows(dbRows)
    if not saved.get("ok"):
        return {
            "ok": False,
            "errMsg": saved.get("errMsg"),
            "upsertCnt": 0,
            "modeLabel": modeLabel,
            "tradeDt": baseDt,
        }

    # 진입일이 여러 날일 수 있어 group 정보
    byDt = {}
    for r in dbRows:
        d = r["TRADE_DT"]
        byDt[d] = byDt.get(d, 0) + 1
    dtHint = ", ".join(f"{d}:{n}" for d, n in sorted(byDt.items()))

    summary = (
        f"[ntl load] ({modeLabel}) 적재 완료\n"
        f"기준: {baseDt} · 건수 {saved.get('upsertCnt')} "
        f"(일자별 {dtHint})\n"
        f"후보 {first.get('candCnt')} → 최초 {len(dbRows)}\n"
        f"수급 API {supFromApi} · DB {supFromDb}\n"
        f"※ 기관/외인 수급 여부 무관 전량 적재\n"
        f"※ FLU_RT=등락률(%) · VOL_INC_RT=(당일/20일평균-1)*100%"
    )
    termLog(summary)
    return {
        "ok": True,
        "upsertCnt": saved.get("upsertCnt") or 0,
        "firstCnt": len(dbRows),
        "candCnt": first.get("candCnt") or 0,
        "checked": first.get("checked") or 0,
        "tradeDt": baseDt,
        "modeLabel": modeLabel,
        "byDt": byDt,
        "supFromApi": supFromApi,
        "supFromDb": supFromDb,
        "summary": summary,
        "rows": dbRows,
    }


def formatNtlFirstDbReport(rows, tradeDt, titlePrefix="[ntl last]"):
    """DB 조회 결과 리포트."""
    lines = [
        f"{titlePrefix} {tradeDt} · {len(rows)}건",
        "종목 | 등락% | 거래량 / 20평균 | 증가% | 외인순매수(금액) | 기관순매수(금액)",
        "",
    ]
    if not rows:
        lines.append("(없음)")
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        flu = r.get("FLU_RT")
        fluTxt = f"{float(flu):+.1f}%" if flu is not None else "-"
        qty = r.get("TRDE_QTY")
        avg = r.get("AVG_VOL_20")
        inc = r.get("VOL_INC_RT")
        qtyTxt = f"{int(qty):,}" if qty is not None else "-"
        avgTxt = f"{int(avg):,}" if avg is not None else "-"
        incTxt = f"{float(inc):+.0f}%" if inc is not None else "-"
        forQ = r.get("FOR_NETPRPS")
        orgQ = r.get("ORGN_NETPRPS")
        forA = r.get("FOR_NET_AMT")
        orgA = r.get("ORGN_NET_AMT")

        def _n(v):
            if v is None:
                return "-"
            try:
                return f"{int(v):,}"
            except (TypeError, ValueError):
                return str(v)

        mkt = r.get("MARKET_NAME") or ""
        mktP = f"/{mkt}" if mkt else ""
        lines.append(
            f"{i}. {r.get('STK_NM')}({r.get('STK_CD')}){mktP} {fluTxt}\n"
            f"   vol {qtyTxt} / avg20 {avgTxt} ({incTxt})\n"
            f"   외인 {_n(forQ)}주 · 금액 {_n(forA)} | "
            f"기관 {_n(orgQ)}주 · 금액 {_n(orgA)}"
        )
    return "\n".join(lines)
