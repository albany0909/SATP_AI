-- 외국인 현물·선물 일별 순매수 (KRX 투자자별 거래실적)
-- 단위: 원. 화면은 억원으로 환산한다.
-- PK: 일자

CREATE TABLE IF NOT EXISTS AI_DAY_FRGN_SF (
  TRADE_DT        CHAR(8)         NOT NULL COMMENT '일자 YYYYMMDD',
  SPOT_NET        BIGINT          NULL COMMENT '현물(코스피) 외국인 순매수(원)',
  FUT_NET         BIGINT          NULL COMMENT '선물(코스피200) 외국인 순매수(원)',
  SPOT_FUT_NET    BIGINT          NULL COMMENT '현물+선물 합(원)',
  SRC             VARCHAR(40)     NULL COMMENT '원천 (krx_mdc)',
  NOTE            VARCHAR(200)    NULL COMMENT '적재 메모',
  CREATED_AT      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_DT),
  KEY IDX_AI_DAY_FRGN_SF_SRC (SRC, TRADE_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='외국인 현선물 일별 순매수';
