"""테이블 확인 + 틱 API 깊이 프로브 + 20260601 구간 적재 시도"""

from api.getTicStkPrc import fetchTicStkPrc, getTicStkPrc
from api.getToken import issueAccToken
from config import dbConfig
from config.config import real_app_key, real_app_secret, real_host_url
from pathlib import Path
import pymysql

from utils.ticStkPrcStore import upsertTicStkPrcRows


def ensureTable():
    sqlPath = Path(__file__).resolve().parents[1] / "sql" / "ticStkPrc.sql"
    sqlText = sqlPath.read_text(encoding="utf-8")
    stmts, buf = [], []
    for line in sqlText.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        buf.append(line)
        if s.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    conn = pymysql.connect(
        host=dbConfig.dbHost,
        port=int(dbConfig.dbPort),
        user=dbConfig.dbUser,
        password=dbConfig.dbPassword,
        database=dbConfig.dbName,
        charset=dbConfig.dbCharset,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'AI_TIC_STK_PRC'")
            if cur.fetchone():
                print("TABLE_OK")
            else:
                for st in stmts:
                    cur.execute(st)
                print("TABLE_CREATED")
        conn.commit()
    finally:
        conn.close()


def main():
    ensureTable()
    issued = issueAccToken(real_host_url, real_app_key, real_app_secret)
    print("token", issued.get("ok"), issued.get("errMsg"))
    if not issued.get("ok"):
        return 1

    token = issued["token"]
    tokenType = issued.get("tokenType") or "bearer"

    # 1페이지로 최신 구간 확인
    res = getTicStkPrc(real_host_url, token, "005930", ticScope="1", tokenType=tokenType)
    body = res.json()
    rows = body.get("stk_tic_chart_qry") or []
    print("page0_status", res.status_code, "n", len(rows), "cont", res.headers.get("cont-yn"))
    if rows:
        print("page0_first", rows[0].get("cntr_tm"), "last", rows[-1].get("cntr_tm"))

    # 20260601만 목표로 연속조회 (과거로 내려감) — 페이지 제한
    fetched = fetchTicStkPrc(
        real_host_url,
        token,
        "005930",
        ticScope="1",
        startDt="20260601",
        endDt="20260601",
        tokenType=tokenType,
        isPaper=False,
        maxPages=80,
    )
    print(
        "fetch",
        fetched.get("ok"),
        fetched.get("errMsg"),
        "cnt",
        fetched.get("rowCnt"),
        "page",
        fetched.get("pageUsed"),
    )
    rows = fetched.get("rows") or []
    if rows:
        print("range", rows[0]["CNTR_TM"], "~", rows[-1]["CNTR_TM"])
        print(upsertTicStkPrcRows(rows))
    else:
        # 도달 실패 시: 필터 없이 최근 N페이지만 적재해 깊이 확인
        fetched2 = fetchTicStkPrc(
            real_host_url,
            token,
            "005930",
            ticScope="1",
            startDt=None,
            endDt=None,
            tokenType=tokenType,
            isPaper=False,
            maxPages=5,
        )
        rows2 = fetched2.get("rows") or []
        print(
            "recent5p",
            fetched2.get("rowCnt"),
            "first",
            rows2[0]["CNTR_TM"] if rows2 else None,
            "last",
            rows2[-1]["CNTR_TM"] if rows2 else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
