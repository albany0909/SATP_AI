-- 종목별 투자자·기관 매매동향 일별 적재 (ka10059)
-- HTS 종목별투자자(순매수)와 동일 컬럼
-- PK: 종목 + 일자 + 금액수량구분 + 매매구분 + 단위

CREATE TABLE IF NOT EXISTS AI_DAY_INV_SR (
  STK_CD          VARCHAR(20)   NOT NULL COMMENT '종목코드',
  TRADE_DT        CHAR(8)       NOT NULL COMMENT '일자 YYYYMMDD',
  AMT_QTY_TP      CHAR(1)       NOT NULL COMMENT '1:금액(백만원) 2:수량',
  TRDE_TP         CHAR(1)       NOT NULL COMMENT '0:순매수 1:매수 2:매도',
  UNIT_TP         VARCHAR(4)    NOT NULL COMMENT '1:단주 1000:천주',

  CUR_PRC         BIGINT        NULL COMMENT '종가/현재가(원, 절대값)',
  PRE_SIG         CHAR(1)       NULL COMMENT '대비기호 1상한2상승3보합4하한5하락',
  PRED_PRE        BIGINT        NULL COMMENT '전일대비(원, 부호유지)',
  FLU_RT          DECIMAL(10,2) NULL COMMENT '등락률(%)',
  ACC_TRDE_QTY    BIGINT        NULL COMMENT '누적거래량(주)',
  ACC_TRDE_PRICA  BIGINT        NULL COMMENT '누적거래대금(백만원)',

  IND_INVSR       BIGINT        NULL COMMENT '개인투자자(부호유지)',
  FRGNR_INVSR     BIGINT        NULL COMMENT '외국인투자자',
  ORGN            BIGINT        NULL COMMENT '기관계',
  FNNC_INVT       BIGINT        NULL COMMENT '금융투자',
  INSRNC          BIGINT        NULL COMMENT '보험',
  INVTRT          BIGINT        NULL COMMENT '투신',
  ETC_FNNC        BIGINT        NULL COMMENT '기타금융',
  BANK            BIGINT        NULL COMMENT '은행',
  PENFND_ETC      BIGINT        NULL COMMENT '연기금등',
  SAMO_FUND       BIGINT        NULL COMMENT '사모펀드',
  NATN            BIGINT        NULL COMMENT '국가',
  ETC_CORP        BIGINT        NULL COMMENT '기타법인',
  NATFOR          BIGINT        NULL COMMENT '내외국인',

  SRC_TR          VARCHAR(20)   NULL COMMENT '원천 TR (ka10059)',

  CREATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (STK_CD, TRADE_DT, AMT_QTY_TP, TRDE_TP, UNIT_TP),
  KEY IDX_AI_DAY_INV_SR_DT (TRADE_DT, STK_CD),
  KEY IDX_AI_DAY_INV_SR_STK_DT (STK_CD, TRADE_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ka10059 종목별투자자기관별 일별';
