-- ka10080 주식분봉차트 적재 테이블
-- PK: 종목 + 틱범위(몇분봉) + 체결시각
-- tic_scope: 1,3,5,10,15,30,45,60 (분)
-- 권장: 원천은 1분봉 적재, 상위 TF는 리샘플 또는 동일 테이블에 TIC_SCOPE 구분 적재

CREATE TABLE AI_MIN_STK_PRC (
  STK_CD          VARCHAR(20)   NOT NULL COMMENT '종목코드(정규화, 예:005930)',
  TIC_SCOPE       VARCHAR(2)    NOT NULL COMMENT '틱범위(분봉) 1/3/5/10/15/30/45/60',
  CNTR_TM         CHAR(14)      NOT NULL COMMENT '봉시각 YYYYMMDDHHmmss (API cntr_tm)',

  TRADE_DT        CHAR(8)       NOT NULL COMMENT '일자 YYYYMMDD (CNTR_TM 앞 8자리)',
  BAR_TM          CHAR(6)       NOT NULL COMMENT '시각 HHmmss (CNTR_TM 뒤 6자리)',

  OPEN_PRC        BIGINT        NULL COMMENT '시가(원, 절대값)',
  HIGH_PRC        BIGINT        NULL COMMENT '고가(원, 절대값)',
  LOW_PRC         BIGINT        NULL COMMENT '저가(원, 절대값)',
  CLOSE_PRC       BIGINT        NULL COMMENT '종가/현재가(원, 절대값)',
  TRDE_QTY        BIGINT        NULL COMMENT '거래량(주)',
  ACC_TRDE_QTY    BIGINT        NULL COMMENT '누적거래량(주, API acc_trde_qty)',
  PRED_PRE        BIGINT        NULL COMMENT '전일대비(원, 부호유지)',
  PRED_PRE_SIG    CHAR(1)       NULL COMMENT '전일대비기호 1상한2상승3보합4하한5하락',

  UPD_STKPC_TP    CHAR(1)       NULL COMMENT '수정주가구분 0/1 (조회 시 사용값)',
  SRC_TR          VARCHAR(20)   NULL COMMENT '원천 TR (ka10080)',

  CREATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (STK_CD, TIC_SCOPE, CNTR_TM),
  KEY IDX_AI_MIN_STK_PRC_DT_SCOPE (TRADE_DT, TIC_SCOPE),
  KEY IDX_AI_MIN_STK_PRC_STK_DT (STK_CD, TRADE_DT, TIC_SCOPE),
  KEY IDX_AI_MIN_STK_PRC_SCOPE_TM (TIC_SCOPE, CNTR_TM)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ka10080 주식분봉차트';
