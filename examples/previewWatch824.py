"""8/24(월) 관심종목·매매대상 예측 — 8/21(금) 순위 스냅샷 기준 DB only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()

from trading.maTouchUnivLoad import screenUnivFromDb
from utils.dbConn import openDbConn
from utils.rankToWatchLoad import (
    WATCH_GRP_RANK,
    _getDayOhlc,
    _isBullCandle,
    _isKospiKosdaq,
    _loadRankRows,
)
from api.getInqRank import QRY_TP_DAY

RANK_DT = "20260821"
TRADE_DT = "20260824"


def previewRankWatch():
    """rank_close 규칙: 코스피/코스닥 + 양봉 (DB 일봉만)."""
    rows = _loadRankRows(RANK_DT, qryTp=QRY_TP_DAY)
    picks = []
    for r in rows:
        stkCd = str(r.get("STK_CD") or "").strip()
        if not stkCd:
            continue
        if not _isKospiKosdaq(r.get("MRKT_TP"), r.get("MARKET_NAME")):
            continue
        ohlc = _getDayOhlc(stkCd, RANK_DT)
        if not ohlc or not _isBullCandle(ohlc.get("openPrc"), ohlc.get("closePrc")):
            continue
        picks.append(
            {
                "rankNo": r.get("RANK_NO"),
                "stkCd": stkCd,
                "stkNm": r.get("STK_NM") or r.get("INFO_NM") or stkCd,
                "marketName": r.get("MARKET_NAME") or "",
            }
        )
    return picks


def main():
    print(f"[기준] 순위 스냅샷 {RANK_DT}(금) → 다음 거래일 {TRADE_DT}(월) 관심·매매대상")
    print()

    watch = previewRankWatch()
    print(f"=== rank_close 관심 후보 (양봉·코스피/코스닥) {len(watch)}종 ===")
    for p in watch:
        print(
            f"  {int(p['rankNo'] or 0):>2} {p['stkCd']} {p['stkNm']} "
            f"({p['marketName'] or '-'})"
        )
    print()

    univ = screenUnivFromDb(RANK_DT)
    print(f"=== MTS01 매매대상 (순위20∪거래대금30) ===")
    if not univ.get("ok"):
        print("  실패:", univ.get("errMsg"))
    else:
        picks = univ.get("picks") or []
        print(f"  선정 {len(picks)}종 (스킵 {len(univ.get('skips') or [])})")
        for p in picks:
            print(
                f"  {p.get('rankNo', 0):>2} {p['stkCd']} {p.get('stkNm')} "
                f"{p.get('marketName') or '-'} "
                f"종가 {p.get('closePrc') or 0:,.0f} "
                f"SMA20 {p.get('sma20') or 0:,.0f} "
                f"매차 {p.get('stepQty')}주"
            )
    print()

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT STK_CD, STK_NM, USE_TRADE, REF_TRADE_DT, LOAD_DT
                FROM AI_WATCH_STK
                WHERE STATUS='Y'
                ORDER BY PRIORITY, STK_CD
                """
            )
            cur = cur.fetchall() or []
        print(f"=== 현재 DB 활성 관심 ({len(cur)}종, 8/21 16시 반영 전일 수) ===")
        for r in cur:
            print(
                f"  {r['STK_CD']} {r['STK_NM']} "
                f"매매={r['USE_TRADE']} ref={r['REF_TRADE_DT']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
