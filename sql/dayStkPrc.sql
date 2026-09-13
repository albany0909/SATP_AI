-- ka10086 일별주가 적재 테이블
-- PK: 종목코드 + 일자 + 표시구분(0:수량, 1:금액)

CREATE TABLE AI_DAY_STK_PRC (
  STK_CD          VARCHAR(20)   NOT NULL COMMENT '종목코드(정규화, 예:005930)',
  TRADE_DT        CHAR(8)       NOT NULL COMMENT '일자 YYYYMMDD (API date)',
  INDC_TP         CHAR(1)       NOT NULL COMMENT '표시구분 0:수량 1:금액(백만원)',

  OPEN_PRC        BIGINT        NULL COMMENT '시가(원, 절대값)',
  HIGH_PRC        BIGINT        NULL COMMENT '고가(원, 절대값)',
  LOW_PRC         BIGINT        NULL COMMENT '저가(원, 절대값)',
  CLOSE_PRC       BIGINT        NULL COMMENT '종가(원, 절대값)',
  PRED_RT         BIGINT        NULL COMMENT '전일비(원, 부호유지)',
  FLU_RT          DECIMAL(10,2) NULL COMMENT '등락률(%)',
  TRDE_QTY        BIGINT        NULL COMMENT '거래량(주)',
  AMT_MN          BIGINT        NULL COMMENT '금액(백만원)',

  CRD_RT          DECIMAL(10,2) NULL COMMENT '신용비(%)',
  IND_VAL         BIGINT        NULL COMMENT '개인(수량또는백만원, 부호유지)',
  ORGN_VAL        BIGINT        NULL COMMENT '기관',
  FOR_QTY         BIGINT        NULL COMMENT '외인수량',
  FRGN_VAL        BIGINT        NULL COMMENT '외국계',
  PRM_VAL         BIGINT        NULL COMMENT '프로그램',
  FOR_RT          DECIMAL(10,2) NULL COMMENT '외인비(%)',
  FOR_POSS        DECIMAL(10,2) NULL COMMENT '외인보유(%)',
  FOR_WGHT        DECIMAL(10,2) NULL COMMENT '외인비중(%)',
  FOR_NETPRPS     BIGINT        NULL COMMENT '외인순매수(수량, 부호유지)',
  ORGN_NETPRPS    BIGINT        NULL COMMENT '기관순매수',
  IND_NETPRPS     BIGINT        NULL COMMENT '개인순매수',
  CRD_REMN_RT     DECIMAL(10,2) NULL COMMENT '신용잔고율(%)',

  CREATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (STK_CD, TRADE_DT, INDC_TP),
  KEY IDX_AI_DAY_STK_PRC_TRADE_DT (TRADE_DT),
  KEY IDX_AI_DAY_STK_PRC_STK_DT (STK_CD, TRADE_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ka10086 일별주가';
