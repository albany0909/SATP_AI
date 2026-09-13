"""AI_DAY_INQ_RANK 에 IND_GRP / UP_NAME 컬럼 추가"""

import pymysql

from config import dbConfig

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
        cur.execute("SHOW COLUMNS FROM AI_DAY_INQ_RANK LIKE 'IND_GRP'")
        hasInd = cur.fetchone() is not None
        cur.execute("SHOW COLUMNS FROM AI_DAY_INQ_RANK LIKE 'UP_NAME'")
        hasUp = cur.fetchone() is not None

        if not hasInd:
            cur.execute(
                """
                ALTER TABLE AI_DAY_INQ_RANK
                  ADD COLUMN IND_GRP VARCHAR(40) NULL
                    COMMENT '테마/업종그룹 (반도체,2차전지 등)' AFTER STK_NM
                """
            )
            print("ADDED IND_GRP")
        else:
            print("IND_GRP EXISTS")

        if not hasUp:
            # IND_GRP 뒤에 UP_NAME
            afterCol = "IND_GRP" if True else "STK_NM"
            cur.execute(
                f"""
                ALTER TABLE AI_DAY_INQ_RANK
                  ADD COLUMN UP_NAME VARCHAR(40) NULL
                    COMMENT '공식업종명(AI_STK_INFO.UP_NAME)' AFTER {afterCol}
                """
            )
            print("ADDED UP_NAME")
        else:
            print("UP_NAME EXISTS")

        cur.execute("SHOW INDEX FROM AI_DAY_INQ_RANK WHERE Key_name='IDX_AI_DAY_INQ_RANK_IND'")
        if cur.fetchone() is None:
            cur.execute(
                "ALTER TABLE AI_DAY_INQ_RANK ADD KEY IDX_AI_DAY_INQ_RANK_IND (IND_GRP)"
            )
            print("ADDED INDEX IDX_AI_DAY_INQ_RANK_IND")
        else:
            print("INDEX EXISTS")

        cur.execute("SHOW COLUMNS FROM AI_DAY_INQ_RANK")
        cols = [r[0] for r in cur.fetchall()]
        print("cols:", ", ".join(cols))
    conn.commit()
    print("COMMIT_OK")
finally:
    conn.close()
