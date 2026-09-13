-- ka10079 주식틱차트 적재 테이블
-- PK: 종목 + 틱범위(몇틱) + 체결시각 + 동일시각 내 순번
-- tic_scope: 1/3/5/10/30 (틱) — 분봉(ka10080)과 의미 다름
-- 참고: 동일 CNTR_TM에 복수 틱이 올 수 있음(명세 예시) → TIC_SEQ 필수

CREATE TABLE AI_TIC_STK_PRC (
  STK_CD          VARCHAR(20)   NOT NULL COMMENT '종목코드(정규화)',
  TIC_SCOPE       VARCHAR(2)    NOT NULL COMMENT '틱범위 1/3/5/10/30 (틱수)',
  CNTR_TM         CHAR(14)      NOT NULL COMMENT '체결시각 YYYYMMDDHHmmss',
  TIC_SEQ         INT           NOT NULL COMMENT '동일 CNTR_TM 내 순번(0..)',

  TRADE_DT        CHAR(8)       NOT NULL COMMENT '일자 YYYYMMDD',
  BAR_TM          CHAR(6)       NOT NULL COMMENT '시각 HHmmss',

  OPEN_PRC        BIGINT        NULL COMMENT '시가(원, 절대값)',
  HIGH_PRC        BIGINT        NULL COMMENT '고가(원, 절대값)',
  LOW_PRC         BIGINT        NULL COMMENT '저가(원, 절대값)',
  CLOSE_PRC       BIGINT        NULL COMMENT '현재가(원, 절대값)',
  TRDE_QTY        BIGINT        NULL COMMENT '거래량(주)',
  PRED_PRE        BIGINT        NULL COMMENT '전일대비(원, 부호유지)',
  PRED_PRE_SIG    CHAR(1)       NULL COMMENT '전일대비기호 1상한2상승3보합4하한5하락',

  UPD_STKPC_TP    CHAR(1)       NULL COMMENT '수정주가구분 0/1',
  LAST_TIC_CNT    VARCHAR(20)   NULL COMMENT '응답 last_tic_cnt(참고)',
  SRC_TR          VARCHAR(20)   NULL COMMENT '원천 TR (ka10079)',

  CREATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (STK_CD, TIC_SCOPE, CNTR_TM, TIC_SEQ),
  KEY IDX_AI_TIC_STK_PRC_DT_SCOPE (TRADE_DT, TIC_SCOPE),
  KEY IDX_AI_TIC_STK_PRC_STK_DT (STK_CD, TRADE_DT, TIC_SCOPE),
  KEY IDX_AI_TIC_STK_PRC_SCOPE_TM (TIC_SCOPE, CNTR_TM)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ka10079 주식틱차트';
