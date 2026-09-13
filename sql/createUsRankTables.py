"""AI_US_DAY_INQ_RANK / AI_US_DAY_STK_PRC 테이블 생성 (수동 실행용)"""

from utils.usRankTables import ensureUsRankTables

if __name__ == "__main__":
    ok = ensureUsRankTables()
    print("OK" if ok else "FAIL")
