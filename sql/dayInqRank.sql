-- ka00198 실시간종목조회순위 일별 스냅샷
-- PK: 일자 + 조회구분 + 순위
-- 장마감 적재 시 qry_tp=4(당일누적) 권장

CREATE TABLE AI_DAY_INQ_RANK (
  TRADE_DT          CHAR(8)       NOT NULL COMMENT '기준일자 YYYYMMDD',
  QRY_TP            CHAR(1)       NOT NULL COMMENT '구분 1:1분 2:10분 3:1시간 4:당일누적 5:30초',
  RANK_NO           INT           NOT NULL COMMENT '빅데이터 순위 (bigd_rank)',

  STK_CD            VARCHAR(20)   NOT NULL COMMENT '종목코드',
  STK_NM            VARCHAR(80)   NULL COMMENT '종목명',
  IND_GRP           VARCHAR(40)   NULL COMMENT '테마/업종그룹 (반도체,2차전지 등)',
  UP_NAME           VARCHAR(40)   NULL COMMENT '공식업종명(AI_STK_INFO.UP_NAME)',
  RANK_CHG          INT           NULL COMMENT '순위등락(부호유지, 0=변동없음)',
  RANK_CHG_SIGN     VARCHAR(4)    NULL COMMENT '순위등락부호 + / - / N',
  CUR_PRC           BIGINT        NULL COMMENT '기준시점 현재가(원, 절대값)',
  BASE_COMP_SIGN    CHAR(1)       NULL COMMENT '기준가대비부호 1상한2상승3보합4하한5하락',
  BASE_COMP_CHGR    DECIMAL(10,2) NULL COMMENT '기준가대비등락율(%)',
  PREV_BASE_SIGN    CHAR(1)       NULL COMMENT '직전기준대비부호',
  PREV_BASE_CHGR    DECIMAL(10,2) NULL COMMENT '직전기준대비등락율(%)',
  BASE_TM           CHAR(6)       NULL COMMENT '기준시간 HHmmss',
  SRC_TR            VARCHAR(20)   NULL COMMENT '원천 TR (ka00198)',

  CREATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_DT, QRY_TP, RANK_NO),
  KEY IDX_AI_DAY_INQ_RANK_STK_DT (STK_CD, TRADE_DT),
  KEY IDX_AI_DAY_INQ_RANK_DT_QRY (TRADE_DT, QRY_TP),
  KEY IDX_AI_DAY_INQ_RANK_IND (IND_GRP)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ka00198 실시간종목조회순위 일별';
