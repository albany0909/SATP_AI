"""관심종목 순위 점수 계산 (조회순위 누적 종목 → 조건 채점 → AI_RANK_SCORE)

대상: 기준일 기준 최근 UNIV_DAYS 일간 AI_DAY_INQ_RANK(당일누적)에 등장한 전 종목.
채점: 조건셋(AI_RANK_COND) 만점을 활성 항목끼리 배분하고, 충족 항목 배점을 합산한다.
데이터가 없는 항목(일봉 부족·수급 미적재)은 가점 없이 미충족 처리하므로 점수만 낮아진다.
"""

import json
import math
from datetime import datetime, timedelta

from api.getInqRank import QRY_TP_DAY
from strategies.indicators import calcRsiSeries, calcSmaSeries
from utils.dbConn import openDbConn
from utils.rankCondStore import DEFAULT_COND_CD, getRankCond
from utils.rankScoreStore import replaceRankScoreRows
from utils.stkCdUtil import normStkCd
from utils.termLog import termLog

# 일봉 IN 절을 나누는 단위 — 유니버스가 커도 쿼리 하나가 과도해지지 않게
_CHUNK = 200

# AI_DAY_INV_SR 기본 구분: 순매수·수량·단주 (HTS 종목별투자자와 동일)
_INV_AMT_QTY_TP = "2"
_INV_TRDE_TP = "0"
_INV_UNIT_TP = "1"


def _ymdMinusDays(ymd, days):
    """YYYYMMDD 에서 달력일 차감."""
    dt = datetime.strptime(str(ymd)[:8], "%Y%m%d") - timedelta(days=max(0, int(days)))
    return dt.strftime("%Y%m%d")


def _resolveTradeDt(tradeDt=None):
    """기준일 확정 — 미지정이면 조회순위(당일누적) 최근 적재일."""
    ymd = str(tradeDt or "").replace("-", "")[:8]
    if ymd.isdigit() and len(ymd) == 8:
        from utils.dayInqRankStore import resolveInqRankTradeDt

        resolved = resolveInqRankTradeDt(ymd, QRY_TP_DAY)
        if resolved.get("ok") and resolved.get("tradeDt"):
            return resolved.get("tradeDt")
        return ymd
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(TRADE_DT) AS MAX_DT FROM AI_DAY_INQ_RANK WHERE QRY_TP=%s",
                (QRY_TP_DAY,),
            )
            row = cur.fetchone() or {}
            return str(row.get("MAX_DT") or "")
    except Exception as err:
        termLog(f"[rankScore] 기준일 조회 실패: {err}")
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _loadUniverse(startDt, endDt):
    """
    기간 내 조회순위 등장 종목 + 테마 등장 건수.
    반환: (univ{cd: {stkNm, indGrp, appearCnt, bestRank}}, themeCnt{theme: cnt})
    """
    univ = {}
    themeCnt = {}
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TRADE_DT, STK_CD, STK_NM, IND_GRP, UP_NAME, RANK_NO
                FROM AI_DAY_INQ_RANK
                WHERE QRY_TP=%s AND TRADE_DT BETWEEN %s AND %s
                ORDER BY TRADE_DT ASC, RANK_NO ASC
                """,
                (QRY_TP_DAY, str(startDt), str(endDt)),
            )
            rows = cur.fetchall() or []
    except Exception as err:
        termLog(f"[rankScore] 유니버스 조회 실패: {err}")
        return {}, {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for r in rows:
        cd = normStkCd(r.get("STK_CD"))
        if not cd:
            continue
        # 테마 없으면 공식 업종명으로 대체 (테마 집계 누락 방지)
        theme = (str(r.get("IND_GRP") or "").strip()
                 or str(r.get("UP_NAME") or "").strip())
        try:
            rankNo = int(r.get("RANK_NO") or 0)
        except (TypeError, ValueError):
            rankNo = 0

        entry = univ.get(cd)
        if entry is None:
            univ[cd] = {
                "stkNm": str(r.get("STK_NM") or "").strip(),
                "indGrp": theme,
                "appearCnt": 1,
                "bestRank": rankNo or 9999,
            }
        else:
            entry["appearCnt"] += 1
            # 최신 행이 뒤에 오므로 이름·테마는 최신값으로 갱신
            if str(r.get("STK_NM") or "").strip():
                entry["stkNm"] = str(r.get("STK_NM")).strip()
            if theme:
                entry["indGrp"] = theme
            if rankNo and rankNo < entry["bestRank"]:
                entry["bestRank"] = rankNo

        if theme:
            themeCnt[theme] = themeCnt.get(theme, 0) + 1

    return univ, themeCnt


def _loadNetSums(stkCds, startDt, endDt):
    """기간 외국인·기관 순매수 합. 반환: {cd: {"frgn": int, "orgn": int}}"""
    out = {}
    codes = list(stkCds)
    if not codes:
        return out
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for i in range(0, len(codes), _CHUNK):
                part = codes[i:i + _CHUNK]
                marks = ",".join(["%s"] * len(part))
                cur.execute(
                    f"""
                    SELECT STK_CD,
                           SUM(COALESCE(FRGNR_INVSR, 0)) AS FRGN_SUM,
                           SUM(COALESCE(ORGN, 0)) AS ORGN_SUM
                    FROM AI_DAY_INV_SR
                    WHERE AMT_QTY_TP=%s AND TRDE_TP=%s AND UNIT_TP=%s
                      AND TRADE_DT BETWEEN %s AND %s
                      AND STK_CD IN ({marks})
                    GROUP BY STK_CD
                    """,
                    [
                        _INV_AMT_QTY_TP,
                        _INV_TRDE_TP,
                        _INV_UNIT_TP,
                        str(startDt),
                        str(endDt),
                    ]
                    + part,
                )
                for r in cur.fetchall() or []:
                    cd = normStkCd(r.get("STK_CD"))
                    if not cd:
                        continue
                    out[cd] = {
                        "frgn": int(r.get("FRGN_SUM") or 0),
                        "orgn": int(r.get("ORGN_SUM") or 0),
                    }
    except Exception as err:
        termLog(f"[rankScore] 수급 집계 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _loadCloses(stkCds, startDt, endDt):
    """기간 종가 시계열(오름차순). 반환: {cd: [close, ...]}"""
    out = {}
    codes = list(stkCds)
    if not codes:
        return out
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            for i in range(0, len(codes), _CHUNK):
                part = codes[i:i + _CHUNK]
                marks = ",".join(["%s"] * len(part))
                cur.execute(
                    f"""
                    SELECT STK_CD, TRADE_DT, CLOSE_PRC
                    FROM AI_DAY_STK_PRC
                    WHERE INDC_TP='0'
                      AND TRADE_DT BETWEEN %s AND %s
                      AND STK_CD IN ({marks})
                    ORDER BY STK_CD ASC, TRADE_DT ASC
                    """,
                    [str(startDt), str(endDt)] + part,
                )
                for r in cur.fetchall() or []:
                    cd = normStkCd(r.get("STK_CD"))
                    close = r.get("CLOSE_PRC")
                    if not cd or close is None:
                        continue
                    out.setdefault(cd, []).append(float(close))
    except Exception as err:
        termLog(f"[rankScore] 일봉 조회 실패: {err}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _topCutoff(totalCnt, topPct):
    """상위 topPct% 컷 인원수 (최소 1)."""
    try:
        pct = float(topPct)
    except (TypeError, ValueError):
        pct = 30.0
    pct = min(100.0, max(1.0, pct))
    return max(1, int(math.ceil(totalCnt * pct / 100.0)))


def _hitsByNetIncrease(univCds, netMap, key, param):
    """
    기간 순매수 합이 늘어난(=보유가 증가한) 종목코드 set.
    상위 컷이 아니라 절대 기준이라, 대상 전체가 순매도면 아무도 득점하지 않는다.
    minNet: 이 값을 초과해야 증가로 본다 (기본 0주).
    """
    try:
        minNet = float(param.get("minNet", 0))
    except (TypeError, ValueError):
        minNet = 0.0
    hits = set()
    for cd in univCds:
        val = (netMap.get(cd) or {}).get(key)
        if val is None:
            continue
        if val > minNet:
            hits.add(cd)
    return hits


def _hitsByTheme(univ, themeCnt, param):
    """테마 등장 건수 상위 컷에 속한 종목코드 set."""
    if not themeCnt:
        return set(), set()
    themes = sorted(themeCnt.items(), key=lambda x: (-x[1], x[0]))
    cutoff = _topCutoff(len(themes), param.get("topPct", 30))
    hotThemes = {t for t, _ in themes[:cutoff]}
    hits = {
        cd for cd, info in univ.items()
        if info.get("indGrp") and info["indGrp"] in hotThemes
    }
    return hits, hotThemes


def listRankUnivCds(tradeDt=None, condCd=DEFAULT_COND_CD):
    """
    순위 채점 대상 종목코드 목록 (등장 횟수 많은 순).
    일봉 보강 배치가 점수 계산과 같은 모집단을 쓰도록 여기서 한 번만 정의한다.
    반환: {ok, tradeDt, univFrom, stkCds, errMsg?}
    """
    condRes = getRankCond(condCd)
    univDays = 30
    if condRes.get("ok"):
        univDays = max(1, int(condRes["cond"].get("univDays") or 30))

    ymd = _resolveTradeDt(tradeDt)
    if not ymd:
        return {
            "ok": False,
            "errMsg": "조회순위(AI_DAY_INQ_RANK) 적재 데이터가 없습니다.",
            "stkCds": [],
        }

    univStart = _ymdMinusDays(ymd, univDays - 1)
    univ, _themeCnt = _loadUniverse(univStart, ymd)
    if not univ:
        return {
            "ok": False,
            "errMsg": f"{univStart}~{ymd} 조회순위 누적 종목이 없습니다.",
            "stkCds": [],
        }

    ordered = sorted(
        univ.items(),
        key=lambda kv: (-int(kv[1].get("appearCnt") or 0), int(kv[1].get("bestRank") or 9999)),
    )
    return {
        "ok": True,
        "tradeDt": ymd,
        "univFrom": univStart,
        "univDays": univDays,
        "stkCds": [cd for cd, _ in ordered],
    }


def _maSlowPeriod(param):
    try:
        return max(2, int(param.get("slow", 100)))
    except (TypeError, ValueError):
        return 100


def calcRankScore(tradeDt=None, condCd=DEFAULT_COND_CD, skipStkCds=None):
    """
    기준일 순위 점수를 계산해 AI_RANK_SCORE 에 저장한다.
    skipStkCds: 수급 적재 실패 종목 등 — 유니버스에서 빼고 순위를 매긴다.
    반환: {ok, tradeDt, condCd, univCnt, savedCnt, summary, errMsg?}
    """
    condRes = getRankCond(condCd)
    if not condRes.get("ok"):
        return {"ok": False, "errMsg": condRes.get("errMsg")}
    cond = condRes["cond"]
    items = condRes["items"]
    if str(cond.get("status") or "Y").upper() != "Y":
        return {"ok": False, "errMsg": f"조건셋 미사용 상태: {cond.get('condCd')}"}

    activeItems = [
        it for it in items
        if str(it.get("useYn") or "").upper() == "Y" and it.get("supported")
    ]
    if not activeItems:
        return {"ok": False, "errMsg": "활성 조건항목이 없습니다."}

    ymd = _resolveTradeDt(tradeDt)
    if not ymd:
        return {"ok": False, "errMsg": "조회순위(AI_DAY_INQ_RANK) 적재 데이터가 없습니다."}

    univStart = _ymdMinusDays(ymd, max(1, int(cond.get("univDays") or 30)) - 1)
    univ, themeCnt = _loadUniverse(univStart, ymd)
    if not univ:
        return {
            "ok": False,
            "errMsg": f"{univStart}~{ymd} 조회순위 누적 종목이 없습니다.",
        }
    # 수급 적재 실패 종목은 순위에서 제외 (1~2건 스킵용)
    skipSet = set()
    for raw in skipStkCds or []:
        cd = normStkCd(raw)
        if cd:
            skipSet.add(cd)
    skippedCds = []
    if skipSet:
        for cd in list(univ.keys()):
            if cd in skipSet:
                skippedCds.append(cd)
                univ.pop(cd, None)
    if not univ:
        return {
            "ok": False,
            "errMsg": "수급 실패 종목을 제외하면 순위 대상이 없습니다.",
        }
    univCds = list(univ.keys())

    itemByCd = {it["itemCd"]: it for it in activeItems}
    hitSets = {}
    extra = {}

    # 1·2) 수급 — 최근 N일 외국인·기관 순매수 합이 증가(플러스)했는지
    netItems = [cd for cd in ("FRGN_NET", "ORGN_NET") if cd in itemByCd]
    if netItems:
        maxDays = 7
        for icd in netItems:
            try:
                maxDays = max(maxDays, int(itemByCd[icd]["param"].get("days", 7)))
            except (TypeError, ValueError):
                pass
        netStart = _ymdMinusDays(ymd, max(1, maxDays) - 1)
        netMap = _loadNetSums(univCds, netStart, ymd)
        extra["netFrom"] = netStart
        extra["netCnt"] = len(netMap)
        for icd in netItems:
            param = itemByCd[icd]["param"]
            days = param.get("days", 7)
            try:
                itemStart = _ymdMinusDays(ymd, max(1, int(days)) - 1)
            except (TypeError, ValueError):
                itemStart = netStart
            useMap = (
                netMap if itemStart == netStart
                else _loadNetSums(univCds, itemStart, ymd)
            )
            key = "frgn" if icd == "FRGN_NET" else "orgn"
            hitSets[icd] = _hitsByNetIncrease(univCds, useMap, key, param)
            itemByCd[icd]["_netMap"] = useMap
            itemByCd[icd]["_netKey"] = key

    # 3·5) 이평 정배열 / RSI — 종가 시계열 한 번만 읽어 공용
    closesMap = {}
    if "MA_ALIGN" in itemByCd or "RSI_UNDER" in itemByCd:
        needBars = 60
        if "MA_ALIGN" in itemByCd:
            needBars = max(needBars, _maSlowPeriod(itemByCd["MA_ALIGN"]["param"]))
        if "RSI_UNDER" in itemByCd:
            try:
                needBars = max(
                    needBars, int(itemByCd["RSI_UNDER"]["param"].get("period", 14)) + 2
                )
            except (TypeError, ValueError):
                needBars = max(needBars, 16)
        # 거래일 ≈ 달력일 * 0.68 → 여유 있게 환산
        lookbackDays = int(needBars * 1.7) + 40
        prcStart = _ymdMinusDays(ymd, lookbackDays)
        closesMap = _loadCloses(univCds, prcStart, ymd)
        extra["prcFrom"] = prcStart
        extra["prcCnt"] = len(closesMap)

    if "MA_ALIGN" in itemByCd:
        param = itemByCd["MA_ALIGN"]["param"]
        try:
            fast = max(2, int(param.get("fast", 20)))
            mid = max(2, int(param.get("mid", 50)))
            slow = max(2, int(param.get("slow", 100)))
        except (TypeError, ValueError):
            fast, mid, slow = 20, 50, 100
        hits = set()
        lackCnt = 0
        for cd in univCds:
            closes = closesMap.get(cd) or []
            if len(closes) < slow:
                lackCnt += 1
                continue
            maF = calcSmaSeries(closes, fast)[-1]
            maM = calcSmaSeries(closes, mid)[-1]
            maS = calcSmaSeries(closes, slow)[-1]
            if maF is None or maM is None or maS is None:
                lackCnt += 1
                continue
            if maF > maM > maS:
                hits.add(cd)
        hitSets["MA_ALIGN"] = hits
        extra["maLack"] = lackCnt

    if "RSI_UNDER" in itemByCd:
        param = itemByCd["RSI_UNDER"]["param"]
        try:
            period = max(2, int(param.get("period", 14)))
            maxRsi = float(param.get("maxRsi", 70))
        except (TypeError, ValueError):
            period, maxRsi = 14, 70.0
        hits = set()
        rsiMap = {}
        for cd in univCds:
            closes = closesMap.get(cd) or []
            if len(closes) <= period:
                continue
            rsi = calcRsiSeries(closes, period)[-1]
            if rsi is None:
                continue
            rsiMap[cd] = rsi
            if rsi < maxRsi:
                hits.add(cd)
        hitSets["RSI_UNDER"] = hits
        itemByCd["RSI_UNDER"]["_rsiMap"] = rsiMap

    # 4) 테마·업종 빈출
    hotThemes = set()
    if "THEME_HOT" in itemByCd:
        hits, hotThemes = _hitsByTheme(univ, themeCnt, itemByCd["THEME_HOT"]["param"])
        hitSets["THEME_HOT"] = hits

    # 채점 — 충족 항목 배점 합
    scored = []
    for cd, info in univ.items():
        score = 0.0
        hitItems = []
        detail = {}
        for it in activeItems:
            icd = it["itemCd"]
            each = float(it.get("scoreEach") or 0)
            isHit = cd in (hitSets.get(icd) or set())
            if isHit:
                score += each
                hitItems.append(icd)
            val = None
            if icd in ("FRGN_NET", "ORGN_NET"):
                netMap = it.get("_netMap") or {}
                raw = (netMap.get(cd) or {}).get(it.get("_netKey"))
                val = int(raw) if raw is not None else None
            elif icd == "RSI_UNDER":
                raw = (it.get("_rsiMap") or {}).get(cd)
                val = round(float(raw), 1) if raw is not None else None
            detail[icd] = {
                "hit": 1 if isHit else 0,
                "sc": round(each, 2) if isHit else 0,
                "val": val,
            }
        scored.append(
            {
                "stkCd": cd,
                "stkNm": info.get("stkNm") or "",
                "indGrp": info.get("indGrp") or "",
                "appearCnt": int(info.get("appearCnt") or 0),
                "bestRank": int(info.get("bestRank") or 9999),
                "score": round(score, 2),
                "hitItems": hitItems,
                "detail": detail,
            }
        )

    # 동점이면 순위 등장 횟수 많은 종목, 그다음 최고 순위가 높은 종목 우선
    scored.sort(
        key=lambda x: (-x["score"], -x["appearCnt"], x["bestRank"], x["stkCd"])
    )

    saveMax = max(10, int(cond.get("saveMax") or 300))
    rows = []
    for idx, s in enumerate(scored[:saveMax], start=1):
        if s["score"] <= 0:
            break
        detailJson = json.dumps(s["detail"], ensure_ascii=False)
        rows.append(
            {
                "TRADE_DT": ymd,
                "COND_CD": cond["condCd"],
                "STK_CD": s["stkCd"],
                "STK_NM": s["stkNm"][:80] or None,
                "SCORE": s["score"],
                "RANK_NO": idx,
                "HIT_CNT": len(s["hitItems"]),
                "HIT_ITEMS": ",".join(s["hitItems"])[:200] or None,
                "DETAIL_JSON": detailJson[:1000],
                "IND_GRP": (s["indGrp"] or None),
                "APPEAR_CNT": s["appearCnt"],
            }
        )

    saved = replaceRankScoreRows(ymd, cond["condCd"], rows)
    if not saved.get("ok"):
        return {"ok": False, "errMsg": saved.get("errMsg")}

    topLines = []
    for r in rows[:5]:
        topLines.append(f"  {r['RANK_NO']}. {r['STK_NM'] or r['STK_CD']} {r['SCORE']}점")
    summary = (
        f"[관심종목순위] {ymd} 조건 {cond['condCd']}({cond['condNm']})\n"
        f"대상 {len(univ)}종목 ({univStart}~{ymd} 조회순위 누적)"
        + (
            f" · 수급실패 제외 {len(skippedCds)}종목({','.join(skippedCds[:5])})"
            if skippedCds
            else ""
        )
        + "\n"
        f"활성조건 {len(activeItems)}개 · 항목배점 "
        + " / ".join(
            f"{it['itemCd']} {it.get('scoreEach')}" for it in activeItems
        )
        + f"\n저장 {saved.get('savedCnt')}종목"
        + ("\n" + "\n".join(topLines) if topLines else "")
    )
    termLog(summary)

    return {
        "ok": True,
        "tradeDt": ymd,
        "condCd": cond["condCd"],
        "condNm": cond["condNm"],
        "univCnt": len(univ),
        "univFrom": univStart,
        "savedCnt": saved.get("savedCnt") or 0,
        "activeCnt": len(activeItems),
        "hotThemes": sorted(hotThemes)[:10],
        "summary": summary,
        **extra,
    }
