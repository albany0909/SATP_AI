-- 최초 52주 신고가 진입 일별 적재 (ka10016 스냅샷 + 일봉 최초진입 판별)
-- 수급(기관/외인)으로 필터하지 않음 — 수치만 저장
-- PK: 진입일 + 종목코드

CREATE TABLE IF NOT EXISTS AI_DAY_NTL_FIRST (
  TRADE_DT        CHAR(8)       NOT NULL COMMENT '최초 진입일 YYYYMMDD',
  STK_CD          VARCHAR(20)   NOT NULL COMMENT '종목코드',
  STK_NM          VARCHAR(100)  NULL COMMENT '종목명',
  MARKET_NAME     VARCHAR(40)   NULL COMMENT '시장(코스피/코스닥 등)',

  FLU_RT          DECIMAL(10,2) NULL COMMENT '등락률(%)',
  CUR_PRC         BIGINT        NULL COMMENT '종가/현재가',
  HIGH_PRC        BIGINT        NULL COMMENT '당일고가(고저기준 시)',
  PREV_MAX_HIGH   BIGINT        NULL COMMENT '직전250거래일 고점',

  TRDE_QTY        BIGINT        NULL COMMENT '당일 거래량(주)',
  AVG_VOL_20      BIGINT        NULL COMMENT '직전20일 평균 거래량',
  VOL_INC_RT      DECIMAL(12,2) NULL COMMENT '평균 대비 증가율% (qty/avg-1)*100',

  FOR_NETPRPS     BIGINT        NULL COMMENT '외인 순매수 수량',
  ORGN_NETPRPS    BIGINT        NULL COMMENT '기관 순매수 수량',
  FOR_NET_AMT     BIGINT        NULL COMMENT '외인 순매수금액(API 금액단위)',
  ORGN_NET_AMT    BIGINT        NULL COMMENT '기관 순매수금액(API 금액단위)',

  USE_CLOSE       CHAR(1)       NOT NULL DEFAULT 'N' COMMENT 'Y:종가기준 신고 / N:고저',
  SRC_TR          VARCHAR(20)   NULL COMMENT '소스트레이스 ka10016+10081+10086',

  CREATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_DT, STK_CD),
  KEY IDX_AI_DAY_NTL_FIRST_STK_DT (STK_CD, TRADE_DT),
  KEY IDX_AI_DAY_NTL_FIRST_DT_FLU (TRADE_DT, FLU_RT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='최초 52주 신고 진입 일별 적재';
