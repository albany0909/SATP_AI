"""전략별 일일 모의 손익. AI_ALG_EVT 를 읽기만 한다.

쓰는 열 (sql/algEvt.sql):
  EVT_ID, ALG_CD, TRADE_DT, EVT_TM, EVT_TP, STK_CD, PX, QTY, MODE
EVT_TP 는 BUY, SELL, SELL_HALF 만 라운드로 묶는다.
MODE 기본값은 paper. --mode all 이면 실전 행도 포함한다.
--costs 는 백테스트 v40 과 같다. 매수·매도 수수료 0.015%, 매도세 0.20%, 슬리피지 5bp.
자격 증명은 출력하지 않는다. INSERT/UPDATE 는 없다.

예:
  python scripts/algPnlReport.py --csv events.csv --out reports/pnl
  python scripts/algPnlReport.py --out reports/pnl --costs
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

EVENT_COLUMNS = (
    "EVT_ID",
    "ALG_CD",
    "TRADE_DT",
    "EVT_TM",
    "EVT_TP",
    "STK_CD",
    "PX",
    "QTY",
    "MODE",
)
SELECT_SQL = """
SELECT EVT_ID, ALG_CD, TRADE_DT, EVT_TM, EVT_TP, STK_CD, PX, QTY, MODE
FROM AI_ALG_EVT
WHERE EVT_TP IN ('BUY', 'SELL', 'SELL_HALF')
ORDER BY ALG_CD, STK_CD, TRADE_DT, EVT_TM, EVT_ID
"""
FILL_TYPES = {"BUY", "SELL", "SELL_HALF"}


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def loadCsv(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row = {str(key or "").strip(): value for key, value in (raw or {}).items()}
            rows.append(row)
    return rows


def loadDb():
    """SELECT 만 한다. 연결 정보와 예외 본문은 찍지 않는다."""
    from utils.dbConn import openDbConn

    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(SELECT_SQL)
            fetched = cur.fetchall() or []
    finally:
        conn.close()
    rows = []
    for item in fetched:
        if isinstance(item, dict):
            rows.append({key: item.get(key) for key in EVENT_COLUMNS})
        else:
            rows.append(dict(zip(EVENT_COLUMNS, item)))
    return rows


def roundsFromEvents(rows, useCosts=False, mode="paper"):
    """종목별로 매수를 쌓아 매도 수량만큼 실현한다. 실현일은 매도일이다."""
    from backtest.engine import netExecutionPrice

    wanted = str(mode or "paper")
    grouped = {}
    for row in rows or []:
        kind = str((row or {}).get("EVT_TP") or "").upper()
        if kind not in FILL_TYPES:
            continue
        rowMode = str((row or {}).get("MODE") or "paper")
        if wanted != "all" and rowMode != wanted:
            continue
        key = (str(row.get("ALG_CD") or ""), str(row.get("STK_CD") or ""))
        grouped.setdefault(key, []).append(row)
    rounds = []
    for (alg, stk), events in grouped.items():
        events.sort(key=lambda item: (
            str(item.get("TRADE_DT") or ""),
            str(item.get("EVT_TM") or ""),
            int(_num(item.get("EVT_ID"), 0)),
        ))
        lots = []
        for event in events:
            qty = int(_num(event.get("QTY"), 0))
            px = _num(event.get("PX"), 0)
            if qty <= 0 or px <= 0:
                continue
            kind = str(event.get("EVT_TP") or "").upper()
            if kind == "BUY":
                lots.append([qty, px])
                continue
            left = qty
            cost = 0.0
            matched = 0
            while left > 0 and lots:
                take = min(left, lots[0][0])
                cost += take * lots[0][1]
                matched += take
                lots[0][0] -= take
                left -= take
                if lots[0][0] <= 0:
                    lots.pop(0)
            if matched <= 0:
                continue
            if useCosts:
                buyAmt = netExecutionPrice(cost / matched, "buy") * matched
                sellAmt = netExecutionPrice(px, "sell") * matched
            else:
                buyAmt = cost
                sellAmt = px * matched
            pnl = sellAmt - buyAmt
            ret = (sellAmt / buyAmt - 1.0) * 100.0 if buyAmt else 0.0
            rounds.append({
                "algCd": alg or "-",
                "stkCd": stk,
                "day": str(event.get("TRADE_DT") or ""),
                "pnlWon": pnl,
                "retPct": ret,
            })
    return rounds


def _stats(items):
    wins = losses = flats = 0
    gain = loss = pnl = 0.0
    rets = []
    equity = peak = mdd = 0.0
    for item in items:
        money = float(item["pnlWon"])
        rets.append(float(item["retPct"]))
        pnl += money
        equity += money
        if equity > peak:
            peak = equity
        drop = peak - equity
        if drop > mdd:
            mdd = drop
        if money > 0:
            wins += 1
            gain += money
        elif money < 0:
            losses += 1
            loss += money
        else:
            flats += 1
    decided = wins + losses
    pf = (gain / abs(loss)) if loss < 0 else None
    return {
        "trades": wins + losses + flats,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "winRate": (wins / decided * 100.0) if decided else 0.0,
        "pnlWon": pnl,
        "pnlPct": sum(rets) if rets else 0.0,
        "pf": pf,
        "mddWon": mdd,
    }


def summarize(rounds):
    """전략·일, 전략 합계, 전체."""
    buckets = {}
    for item in rounds or []:
        buckets.setdefault((item["algCd"], item["day"]), []).append(item)
        buckets.setdefault((item["algCd"], "ALL"), []).append(item)
        buckets.setdefault(("ALL", item["day"]), []).append(item)
        buckets.setdefault(("ALL", "ALL"), []).append(item)
    rows = []
    for key in sorted(buckets):
        stat = _stats(buckets[key])
        stat["algCd"] = key[0]
        stat["day"] = key[1]
        rows.append(stat)
    return rows


def _pfText(value):
    if value is None:
        return ""
    return f"{value:.3f}"


def writeReports(outDir, rows, useCosts=False, mode="paper"):
    folder = Path(outDir)
    folder.mkdir(parents=True, exist_ok=True)
    csvPath = folder / "algPnl.csv"
    mdPath = folder / "algPnl.md"
    fields = [
        "algCd", "day", "trades", "wins", "losses", "flats", "winRate",
        "pnlWon", "pnlPct", "pf", "mddWon",
    ]
    with csvPath.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "algCd": row["algCd"],
                "day": row["day"],
                "trades": row["trades"],
                "wins": row["wins"],
                "losses": row["losses"],
                "flats": row["flats"],
                "winRate": f"{row['winRate']:.4f}",
                "pnlWon": f"{row['pnlWon']:.4f}",
                "pnlPct": f"{row['pnlPct']:.4f}",
                "pf": _pfText(row["pf"]),
                "mddWon": f"{row['mddWon']:.4f}",
            })
    costLine = "비용 포함 (수수료 0.015%×2, 매도세 0.20%, 슬리피지 5bp)" if useCosts else "비용 없음"
    lines = [
        "# 전략별 일일 손익",
        "",
        f"- 대상: MODE={mode}",
        f"- {costLine}",
        "- 승률은 이익(>0) / (이익+손실) 이다. 0% 는 본전으로 따로 센다.",
        "- 손익% 는 라운드 수익률의 합이다. PF 는 이익금 / 손실금절대값. 손실이 없으면 빈칸.",
        "- MDD 는 누적 실현손익 곡선의 고점 대비 낙폭(원)이다.",
        "- 원천 열: " + ", ".join(EVENT_COLUMNS),
        "",
        "| 전략 | 일 | 거래 | 승 | 패 | 본전 | 승률 | 손익원 | 손익% | PF | MDD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['algCd']} | {row['day']} | {row['trades']} | {row['wins']} | {row['losses']} | "
            f"{row['flats']} | {row['winRate']:.1f}% | {row['pnlWon']:,.0f} | {row['pnlPct']:+.2f} | "
            f"{_pfText(row['pf']) or '-'} | {row['mddWon']:,.0f} |"
        )
    lines.append("")
    mdPath.write_text("\n".join(lines), encoding="utf-8")
    return {"csv": str(csvPath), "md": str(mdPath), "rows": len(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI_ALG_EVT 전략별 일일 손익")
    parser.add_argument("--csv", default="", help="AI_ALG_EVT CSV. 없으면 DB SELECT")
    parser.add_argument("--out", required=True, help="CSV·Markdown 을 쓸 폴더")
    parser.add_argument("--costs", action="store_true", help="v40 수수료·세금·슬리피지")
    parser.add_argument("--mode", default="paper", help="paper | real | all")
    args = parser.parse_args(argv)
    try:
        if args.csv:
            raw = loadCsv(args.csv)
        else:
            raw = loadDb()
    except Exception:
        sys.stderr.write("손익 원천을 읽지 못했습니다. 자격 증명은 출력하지 않습니다.\n")
        return 2
    built = summarize(roundsFromEvents(raw, useCosts=bool(args.costs), mode=args.mode))
    written = writeReports(args.out, built, useCosts=bool(args.costs), mode=args.mode)
    sys.stdout.write(f"행 {written['rows']} · {written['csv']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
