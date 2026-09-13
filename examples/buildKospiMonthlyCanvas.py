"""AI_DAY_INDS_PRC 코스피 월별 수익률 → canvas 생성 (일회성)"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

from utils.dbConn import openDbConn

CANVAS_PATH = Path(
    r"C:\Users\TR\.cursor\projects\c-dev-workspace-workspace-SATP-AI"
    r"\canvases\kospi-monthly-returns.canvas.tsx"
)


def fetchMonthEnds():
    conn = openDbConn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.ym, a.endDt, b.CLOSE_PRC AS closePrc
                FROM (
                  SELECT SUBSTR(TRADE_DT,1,6) AS ym, MAX(TRADE_DT) AS endDt
                  FROM AI_DAY_INDS_PRC
                  WHERE MKT_TP=%s AND INDS_CD=%s
                  GROUP BY SUBSTR(TRADE_DT,1,6)
                ) a
                JOIN AI_DAY_INDS_PRC b
                  ON b.MKT_TP=%s AND b.INDS_CD=%s
                 AND b.TRADE_DT = a.endDt
                ORDER BY a.ym
                """,
                ("KR", "001", "KR", "001"),
            )
            return cur.fetchall()
    finally:
        conn.close()


def buildPayload(rows):
    monthly = []
    prev = None
    for r in rows:
        close = float(r["closePrc"])
        ym = r["ym"]
        if prev is not None and prev["close"] > 0:
            ret = (close / prev["close"] - 1.0) * 100.0
            monthly.append(
                {
                    "ym": ym,
                    "endDt": r["endDt"],
                    "close": round(close, 2),
                    "retPct": round(ret, 4),
                    "year": int(ym[:4]),
                    "month": int(ym[4:6]),
                }
            )
        prev = {"ym": ym, "close": close}

    rets = [m["retPct"] for m in monthly]
    byYear = defaultdict(list)
    for m in monthly:
        byYear[m["year"]].append(m["retPct"])

    yearStats = []
    for y in sorted(byYear):
        arr = byYear[y]
        cum = 1.0
        for x in arr:
            cum *= 1 + x / 100
        yearStats.append(
            {
                "year": y,
                "months": len(arr),
                "avgPct": round(statistics.mean(arr), 4),
                "cumPct": round((cum - 1) * 100, 4),
                "bestPct": round(max(arr), 4),
                "worstPct": round(min(arr), 4),
                "posCnt": sum(1 for x in arr if x > 0),
                "negCnt": sum(1 for x in arr if x < 0),
            }
        )

    byMon = defaultdict(list)
    for m in monthly:
        byMon[m["month"]].append(m["retPct"])
    season = []
    for mo in range(1, 13):
        arr = byMon[mo]
        if not arr:
            continue
        season.append(
            {
                "month": mo,
                "n": len(arr),
                "avgPct": round(statistics.mean(arr), 4),
                "medPct": round(statistics.median(arr), 4),
                "posRate": round(sum(1 for x in arr if x > 0) / len(arr) * 100, 1),
                "bestPct": round(max(arr), 4),
                "worstPct": round(min(arr), 4),
            }
        )

    cum = 1.0
    for x in rets:
        cum *= 1 + x / 100
    best = max(monthly, key=lambda m: m["retPct"])
    worst = min(monthly, key=lambda m: m["retPct"])
    summary = {
        "startYm": monthly[0]["ym"],
        "endYm": monthly[-1]["ym"],
        "n": len(monthly),
        "avgPct": round(statistics.mean(rets), 4),
        "medPct": round(statistics.median(rets), 4),
        "stdevPct": round(statistics.pstdev(rets), 4),
        "best": best,
        "worst": worst,
        "posCnt": sum(1 for x in rets if x > 0),
        "negCnt": sum(1 for x in rets if x < 0),
        "cumPct": round((cum - 1) * 100, 4),
        "startClose": round(float(rows[0]["closePrc"]), 2),
        "endClose": round(float(rows[-1]["closePrc"]), 2),
    }

    wealth = 100.0
    wealthCats = []
    wealthVals = []
    for i, m in enumerate(monthly):
        wealth *= 1 + m["retPct"] / 100
        isLast = i == len(monthly) - 1
        if m["month"] == 12 or isLast:
            label = str(m["year"]) if m["month"] == 12 else f"{m['year']}-{m['month']:02d}"
            wealthCats.append(label)
            wealthVals.append(round(wealth, 1))

    recent = monthly[-24:]
    return {
        "summary": summary,
        "season": season,
        "yearStats": yearStats,
        "recent": recent,
        "yearCats": [str(y["year"]) for y in yearStats],
        "yearCum": [round(y["cumPct"], 2) for y in yearStats],
        "seasonCats": [f"{s['month']}월" for s in season],
        "seasonAvg": [round(s["avgPct"], 2) for s in season],
        "recentCats": [f"{m['ym'][:4]}-{m['ym'][4:]}" for m in recent],
        "recentRet": [round(m["retPct"], 2) for m in recent],
        "wealthCats": wealthCats,
        "wealthVals": wealthVals,
    }


def writeCanvas(payload):
    dataLiteral = json.dumps(payload, ensure_ascii=False, indent=2)
    tsx = f'''import {{
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  LineChart,
  Stack,
  Stat,
  Table,
  Text,
}} from "cursor/canvas";

/**
 * KOSPI monthly returns from AI_DAY_INDS_PRC (ka20006).
 * Monthly return = month-end close / prior month-end close - 1.
 */
const DATA = {dataLiteral} as const;

function fmtPct(v: number, digits = 2) {{
  const sign = v > 0 ? "+" : "";
  return `${{sign}}${{v.toFixed(digits)}}%`;
}}

function ymLabel(ym: string) {{
  return `${{ym.slice(0, 4)}}-${{ym.slice(4)}}`;
}}

export default function KospiMonthlyReturns() {{
  const s = DATA.summary;
  const winRate = s.n > 0 ? (s.posCnt / s.n) * 100 : 0;

  const seasonRows = DATA.season.map((r) => [
    `${{r.month}}월`,
    String(r.n),
    fmtPct(r.avgPct),
    fmtPct(r.medPct),
    `${{r.posRate.toFixed(1)}}%`,
    fmtPct(r.bestPct),
    fmtPct(r.worstPct),
  ]);

  const yearRows = [...DATA.yearStats].reverse().map((r) => [
    String(r.year),
    String(r.months),
    fmtPct(r.cumPct),
    fmtPct(r.avgPct),
    fmtPct(r.bestPct),
    fmtPct(r.worstPct),
    `${{r.posCnt}}/${{r.negCnt}}`,
  ]);

  const recentRows = [...DATA.recent].reverse().map((r) => [
    ymLabel(r.ym),
    r.close.toLocaleString("ko-KR", {{ maximumFractionDigits: 2 }}),
    fmtPct(r.retPct),
  ]);

  return (
    <Stack gap={{20}}>
      <Stack gap={{6}}>
        <H1>코스피 월별 수익률</H1>
        <Text tone="secondary" size="small">
          Source: AI_DAY_INDS_PRC · KR / 001 · 월말 종가 기준 · {{ymLabel(s.startYm)}} ~ {{ymLabel(s.endYm)}}
        </Text>
        <Text tone="secondary" size="small">
          월수익률 = (당월 말 종가 ÷ 전월 말 종가 − 1) × 100 · 누적은 월복리
        </Text>
      </Stack>

      <Grid columns={{4}} gap={{12}}>
        <Stat value={{fmtPct(s.cumPct, 1)}} label="기간 누적 수익률" tone="info" />
        <Stat value={{fmtPct(s.avgPct)}} label="월평균 수익률" />
        <Stat value={{`${{winRate.toFixed(1)}}%`}} label="월간 승률" tone="success" />
        <Stat value={{fmtPct(s.stdevPct)}} label="월 수익률 표준편차" />
      </Grid>

      <Grid columns={{4}} gap={{12}}>
        <Stat
          value={{fmtPct(s.best.retPct)}}
          label={{`최고월 ${{ymLabel(s.best.ym)}}`}}
          tone="success"
        />
        <Stat
          value={{fmtPct(s.worst.retPct)}}
          label={{`최저월 ${{ymLabel(s.worst.ym)}}`}}
          tone="danger"
        />
        <Stat value={{String(s.n)}} label="분석 개월 수" />
        <Stat
          value={{`${{s.startClose.toLocaleString()}} → ${{s.endClose.toLocaleString()}}`}}
          label="월말 종가 (기준월~종료)"
        />
      </Grid>

      <Callout tone="neutral" title="표본">
        양(+) {{s.posCnt}}개월 / 음(−) {{s.negCnt}}개월 · 중앙값 {{fmtPct(s.medPct)}} ·
        월말 종가 기준이므로 월중 고저 변동은 반영되지 않습니다.
      </Callout>

      <Divider />

      <Stack gap={{8}}>
        <H2>연말 기준 누적 지수 (시작=100)</H2>
        <Text tone="secondary" size="small">
          월복리로 굴린 가상 지수. 첫 월수익률 이후 복리 적용.
        </Text>
        <LineChart
          categories={{[...DATA.wealthCats]}}
          series={{[{{ name: "누적지수(시작=100)", data: [...DATA.wealthVals], tone: "info" }}]}}
          height={{220}}
          beginAtZero={{true}}
        />
      </Stack>

      <Stack gap={{8}}>
        <H2>연간 수익률 (해당 연도 월복리)</H2>
        <BarChart
          categories={{[...DATA.yearCats]}}
          series={{[{{ name: "연간 수익률(%)", data: [...DATA.yearCum] }}]}}
          height={{220}}
          beginAtZero={{false}}
          referenceLines={{[{{ value: 0, label: "0%" }}]}}
        />
      </Stack>

      <Stack gap={{8}}>
        <H2>월별 계절성 (평균 수익률)</H2>
        <BarChart
          categories={{[...DATA.seasonCats]}}
          series={{[{{ name: "평균 월수익률(%)", data: [...DATA.seasonAvg] }}]}}
          height={{200}}
          beginAtZero={{false}}
          referenceLines={{[{{ value: 0, label: "0%" }}]}}
        />
      </Stack>

      <Stack gap={{8}}>
        <H2>최근 24개월 월수익률</H2>
        <BarChart
          categories={{[...DATA.recentCats]}}
          series={{[{{ name: "월수익률(%)", data: [...DATA.recentRet] }}]}}
          height={{200}}
          beginAtZero={{false}}
          referenceLines={{[{{ value: 0, label: "0%" }}]}}
        />
      </Stack>

      <Divider />

      <Card>
        <CardHeader>월별 계절성 상세</CardHeader>
        <CardBody>
          <Table
            headers={{["월", "표본", "평균", "중앙값", "승률", "최고", "최저"]}}
            rows={{seasonRows}}
            rowTone={{DATA.season.map((r) =>
              r.avgPct > 0 ? ("success" as const) : r.avgPct < 0 ? ("danger" as const) : undefined
            )}}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>연도별 요약 (최신순)</CardHeader>
        <CardBody>
          <Table
            headers={{["연도", "개월", "연간누적", "월평균", "최고월", "최저월", "양/음"]}}
            rows={{yearRows}}
            rowTone={{[...DATA.yearStats]
              .reverse()
              .map((r) =>
                r.cumPct > 0 ? ("success" as const) : r.cumPct < 0 ? ("danger" as const) : undefined
              )}}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>최근 24개월 상세 (최신순)</CardHeader>
        <CardBody>
          <Table
            headers={{["년월", "월말종가", "월수익률"]}}
            rows={{recentRows}}
            rowTone={{[...DATA.recent]
              .reverse()
              .map((r) =>
                r.retPct > 0 ? ("success" as const) : r.retPct < 0 ? ("danger" as const) : undefined
              )}}
          />
        </CardBody>
      </Card>
    </Stack>
  );
}}
'''
    CANVAS_PATH.write_text(tsx, encoding="utf-8")
    print(f"wrote {CANVAS_PATH}")


def main():
    rows = fetchMonthEnds()
    payload = buildPayload(rows)
    writeCanvas(payload)
    s = payload["summary"]
    print(
        f"n={s['n']} cum={s['cumPct']}% avg={s['avgPct']}% "
        f"{s['startYm']}~{s['endYm']}"
    )


if __name__ == "__main__":
    main()
