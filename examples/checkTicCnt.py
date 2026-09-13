from utils.dbConn import openDbConn

conn = openDbConn()
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS cnt, MIN(CNTR_TM) AS mn, MAX(CNTR_TM) AS mx
            FROM AI_TIC_STK_PRC
            WHERE STK_CD=%s AND TIC_SCOPE=%s AND TRADE_DT=%s
            """,
            ("005930", "1", "20260731"),
        )
        print("tic", dict(cur.fetchone()))
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM AI_MIN_STK_PRC
            WHERE STK_CD=%s AND TIC_SCOPE=%s AND TRADE_DT=%s
            """,
            ("005930", "1", "20260731"),
        )
        print("min", dict(cur.fetchone()))
finally:
    conn.close()
