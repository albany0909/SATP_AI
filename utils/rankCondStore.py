"""순위 조건셋·조건항목 조회 / 수정 (AI_RANK_COND, AI_RANK_COND_ITEM)

조건코드를 추가해 확장할 수 있게 조건셋과 항목을 분리했다.
배점은 WEIGHT가 있으면 그 값을 쓰고, 비어 있으면 남은 점수를 활성 항목끼리 균등 배분한다.
"""

import json

from utils.dbConn import openDbConn
from utils.rankScoreTables import ensureRankScoreTables
from utils.termLog import termLog

DEFAULT_COND_CD = "001"

# 지원 항목코드 — 채점 엔진(rankScoreCalc)이 구현한 항목만 켤 수 있다.
SUPPORTED_ITEM_CDS = (
    "FRGN_NET",
    "ORGN_NET",
    "MA_ALIGN",
    "THEME_HOT",
    "RSI_UNDER",
)


def _parseParam(raw):
    """PARAM_JSON 문자열 → dict. 깨진 값은 빈 dict."""
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _condRow(row):
    return {
        "condCd": str(row.get("COND_CD") or ""),
        "condNm": row.get("COND_NM") or "",
        "totScore": float(row.get("TOT_SCORE") or 0),
        "univDays": int(row.get("UNIV_DAYS") or 30),
        "topN": int(row.get("TOP_N") or 10),
        "saveMax": int(row.get("SAVE_MAX") or 300),
        "status": row.get("STATUS") or "Y",
        "note": row.get("NOTE") or "",
        "updatedAt": str(row.get("UPDATED_AT") or ""),
    }


def _itemRow(row):
    weight = row.get("WEIGHT")
    return {
        "condCd": str(row.get("COND_CD") or ""),
        "itemCd": str(row.get("ITEM_CD") or ""),
        "itemNm": row.get("ITEM_NM") or "",
        "sortNo": int(row.get("SORT_NO") or 1),
        "useYn": row.get("USE_YN") or "N",
        "weight": None if weight is None else float(weight),
        "param": _parseParam(row.get("PARAM_JSON")),
        "paramJson": row.get("PARAM_JSON") or "",
        "note": row.get("NOTE") or "",
        "supported": str(row.get("ITEM_CD") or "") in SUPPORTED_ITEM_CDS,
    }


def listRankConds():
    """조건셋 목록 (화면 선택용)."""
    if not ensureRankScoreTables():
        return []
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COND_CD, COND_NM, TOT_SCORE, UNIV_DAYS, TOP_N, SAVE_MAX,
                       STATUS, NOTE, UPDATED_AT
                FROM AI_RANK_COND
                ORDER BY COND_CD ASC
                """
            )
            return [_condRow(r) for r in (cur.fetchall() or [])]
    except Exception as err:
        termLog(f"[rankCond] 조건셋 조회 실패: {err}")
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def getRankCond(condCd=DEFAULT_COND_CD):
    """
    조건셋 + 항목 + 계산된 배점.
    반환: {ok, cond, items, errMsg?}  items[].scoreEach = 실제 배점
    """
    if not ensureRankScoreTables():
        return {"ok": False, "errMsg": "순위 조건 테이블 생성 실패"}
    code = str(condCd or DEFAULT_COND_CD).strip()
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COND_CD, COND_NM, TOT_SCORE, UNIV_DAYS, TOP_N, SAVE_MAX,
                       STATUS, NOTE, UPDATED_AT
                FROM AI_RANK_COND
                WHERE COND_CD=%s
                """,
                (code,),
            )
            condRaw = cur.fetchone()
            if not condRaw:
                return {"ok": False, "errMsg": f"조건코드 없음: {code}"}
            cur.execute(
                """
                SELECT COND_CD, ITEM_CD, ITEM_NM, SORT_NO, USE_YN, WEIGHT,
                       PARAM_JSON, NOTE
                FROM AI_RANK_COND_ITEM
                WHERE COND_CD=%s
                ORDER BY SORT_NO ASC, ITEM_CD ASC
                """,
                (code,),
            )
            items = [_itemRow(r) for r in (cur.fetchall() or [])]
    except Exception as err:
        return {"ok": False, "errMsg": f"조건 조회 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    cond = _condRow(condRaw)
    applyItemScores(cond, items)
    return {"ok": True, "cond": cond, "items": items}


def applyItemScores(cond, items):
    """
    활성 항목에 배점(scoreEach)을 채운다.
    WEIGHT 지정 항목은 그 값을 쓰고, 남은 점수를 미지정 항목끼리 균등 배분한다.
    (조건을 추가·제외하면 자동으로 다시 나눠지므로 만점은 항상 유지)
    """
    totScore = float(cond.get("totScore") or 0)
    active = [it for it in items if str(it.get("useYn") or "").upper() == "Y"]
    fixed = [it for it in active if it.get("weight") is not None]
    free = [it for it in active if it.get("weight") is None]

    fixedSum = sum(float(it["weight"]) for it in fixed)
    remain = max(0.0, totScore - fixedSum)
    each = (remain / len(free)) if free else 0.0

    for it in items:
        if str(it.get("useYn") or "").upper() != "Y":
            it["scoreEach"] = 0.0
            continue
        raw = float(it["weight"]) if it.get("weight") is not None else each
        it["scoreEach"] = round(raw, 2)
    return items


def updateRankCond(condCd, condNm=None, totScore=None, univDays=None, topN=None,
                   saveMax=None, status=None, note=None):
    """조건셋 헤더 수정. 전달된 값만 반영."""
    if not ensureRankScoreTables():
        return {"ok": False, "errMsg": "순위 조건 테이블 생성 실패"}
    code = str(condCd or "").strip()
    if not code:
        return {"ok": False, "errMsg": "조건코드 필수"}

    sets = []
    args = []
    if condNm is not None and str(condNm).strip():
        sets.append("COND_NM=%s")
        args.append(str(condNm).strip()[:100])
    if totScore is not None:
        try:
            val = float(totScore)
        except (TypeError, ValueError):
            return {"ok": False, "errMsg": "만점 값이 숫자가 아닙니다."}
        if val <= 0 or val > 1000:
            return {"ok": False, "errMsg": "만점은 0보다 크고 1000 이하여야 합니다."}
        sets.append("TOT_SCORE=%s")
        args.append(round(val, 2))
    if univDays is not None:
        try:
            val = int(univDays)
        except (TypeError, ValueError):
            return {"ok": False, "errMsg": "대상기간이 숫자가 아닙니다."}
        if val < 1 or val > 365:
            return {"ok": False, "errMsg": "대상기간은 1~365일이어야 합니다."}
        sets.append("UNIV_DAYS=%s")
        args.append(val)
    if topN is not None:
        try:
            val = int(topN)
        except (TypeError, ValueError):
            return {"ok": False, "errMsg": "표시 순위 수가 숫자가 아닙니다."}
        sets.append("TOP_N=%s")
        args.append(max(1, min(val, 100)))
    if saveMax is not None:
        try:
            val = int(saveMax)
        except (TypeError, ValueError):
            return {"ok": False, "errMsg": "저장 최대수가 숫자가 아닙니다."}
        sets.append("SAVE_MAX=%s")
        args.append(max(10, min(val, 3000)))
    if status is not None:
        sets.append("STATUS=%s")
        args.append("Y" if str(status).upper().startswith("Y") else "N")
    if note is not None:
        sets.append("NOTE=%s")
        args.append(str(note)[:200])

    if not sets:
        return {"ok": False, "errMsg": "변경할 값이 없습니다."}

    args.append(code)
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 값이 같으면 UPDATE rowcount가 0이라 존재 여부는 따로 확인한다
            cur.execute("SELECT 1 FROM AI_RANK_COND WHERE COND_CD=%s", (code,))
            if cur.fetchone() is None:
                return {"ok": False, "errMsg": f"조건코드 없음: {code}"}
            cur.execute(
                f"UPDATE AI_RANK_COND SET {', '.join(sets)} WHERE COND_CD=%s",
                args,
            )
        conn.commit()
        termLog(f"[rankCond] 조건셋 수정 {code}")
        return {"ok": True, "condCd": code}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"조건셋 수정 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def updateRankCondItem(condCd, itemCd, useYn=None, weight=None, paramJson=None,
                       itemNm=None, sortNo=None, note=None):
    """
    조건항목 수정. 전달된 값만 반영.
    weight="" 이면 NULL(균등배분)로 되돌린다.
    """
    if not ensureRankScoreTables():
        return {"ok": False, "errMsg": "순위 조건 테이블 생성 실패"}
    code = str(condCd or "").strip()
    item = str(itemCd or "").strip().upper()
    if not code or not item:
        return {"ok": False, "errMsg": "조건코드·항목코드 필수"}

    sets = []
    args = []
    if useYn is not None:
        sets.append("USE_YN=%s")
        args.append("Y" if str(useYn).upper().startswith("Y") else "N")
    if weight is not None:
        raw = str(weight).strip()
        if raw == "":
            sets.append("WEIGHT=NULL")
        else:
            try:
                val = float(raw)
            except ValueError:
                return {"ok": False, "errMsg": "배점이 숫자가 아닙니다."}
            if val < 0 or val > 1000:
                return {"ok": False, "errMsg": "배점은 0~1000 범위여야 합니다."}
            sets.append("WEIGHT=%s")
            args.append(round(val, 2))
    if paramJson is not None:
        raw = str(paramJson).strip()
        if raw == "":
            sets.append("PARAM_JSON=NULL")
        else:
            try:
                parsed = json.loads(raw)
            except Exception as err:
                return {"ok": False, "errMsg": f"파라미터 JSON 오류: {err}"}
            if not isinstance(parsed, dict):
                return {"ok": False, "errMsg": "파라미터는 JSON 객체여야 합니다."}
            compact = json.dumps(parsed, ensure_ascii=False)
            if len(compact) > 500:
                return {"ok": False, "errMsg": "파라미터 JSON이 너무 깁니다(500자)."}
            sets.append("PARAM_JSON=%s")
            args.append(compact)
    if itemNm is not None and str(itemNm).strip():
        sets.append("ITEM_NM=%s")
        args.append(str(itemNm).strip()[:100])
    if sortNo is not None:
        try:
            sets.append("SORT_NO=%s")
            args.append(int(sortNo))
        except (TypeError, ValueError):
            return {"ok": False, "errMsg": "표시순서가 숫자가 아닙니다."}
    if note is not None:
        sets.append("NOTE=%s")
        args.append(str(note)[:200])

    if not sets:
        return {"ok": False, "errMsg": "변경할 값이 없습니다."}

    args.extend([code, item])
    conn = None
    try:
        conn = openDbConn()
        with conn.cursor() as cur:
            # 값이 같으면 UPDATE rowcount가 0이라 존재 여부는 따로 확인한다
            cur.execute(
                "SELECT 1 FROM AI_RANK_COND_ITEM WHERE COND_CD=%s AND ITEM_CD=%s",
                (code, item),
            )
            if cur.fetchone() is None:
                return {"ok": False, "errMsg": f"항목 없음: {code}/{item}"}
            cur.execute(
                f"""
                UPDATE AI_RANK_COND_ITEM SET {', '.join(sets)}
                WHERE COND_CD=%s AND ITEM_CD=%s
                """,
                args,
            )
        conn.commit()
        termLog(f"[rankCond] 항목 수정 {code}/{item}")
        return {"ok": True, "condCd": code, "itemCd": item}
    except Exception as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "errMsg": f"항목 수정 실패: {err}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
