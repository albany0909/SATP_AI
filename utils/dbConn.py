"""MySQL 연결 헬퍼"""

import pymysql

from config import dbConfig


def openDbConn():
    """
    config.dbConfig 기준으로 새 커넥션을 연다.
    호출측에서 close / with 사용.
    """
    return pymysql.connect(
        host=dbConfig.dbHost,
        port=int(dbConfig.dbPort),
        user=dbConfig.dbUser,
        password=dbConfig.dbPassword,
        database=dbConfig.dbName,
        charset=dbConfig.dbCharset,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
