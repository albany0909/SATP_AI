-- 실전/모의 전략 전량 청산 라운드 (알고리즘 검증용)
-- 손절·익절로 포지션이 끝나면 1행 적재한다.

CREATE TABLE IF NOT EXISTS AI_STRAT_LIVE_TRADE (
  TRADE_ID         BIGINT        NOT NULL AUTO_INCREMENT COMMENT '라운드ID',
  ALG_CD           CHAR(5)       NOT NULL COMMENT '알고리즘코드 (MTS01)',
  STRAT_ID         VARCHAR(64)   NULL COMMENT '미사용(하위호환, 관리는 ALG_CD)',
  MODE             VARCHAR(8)    NOT NULL DEFAULT 'paper' COMMENT 'paper|real',
  STK_CD           VARCHAR(20)   NOT NULL COMMENT '종목코드',
  STK_NM           VARCHAR(80)   NULL COMMENT '종목명',

  ENTRY_DT         CHAR(8)       NULL COMMENT '첫 매수일 YYYYMMDD',
  EXIT_DT          CHAR(8)       NOT NULL COMMENT '전량 청산일',
  EXIT_TM          CHAR(6)       NULL COMMENT '청산시각 HHmmss',
  EXIT_REASON      VARCHAR(40)   NOT NULL COMMENT 'STOP|TAKE',

  BUY_QTY          INT           NOT NULL DEFAULT 0 COMMENT '매수수량 합',
  SELL_QTY         INT           NOT NULL DEFAULT 0 COMMENT '매도수량 합',
  BUY_AMT          DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '매수금액 합',
  SELL_AMT         DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '매도금액 합(절반익절 포함)',
  AVG_BUY_PX       DECIMAL(18,4) NULL COMMENT '평균매입가',
  EXIT_PX          DECIMAL(18,4) NULL COMMENT '전량 청산 체결가',
  PNL_WON          DECIMAL(18,2) NULL COMMENT '실현손익(원)',
  PNL_PCT          DECIMAL(12,6) NULL COMMENT '수익률% (매수금액 대비)',
  HALF_YN          CHAR(1)       NOT NULL DEFAULT 'N' COMMENT '절반익절 사용 Y/N',
  STEPS            VARCHAR(40)   NULL COMMENT '채운 매차 이평 CSV',
  NOTE             VARCHAR(200)  NULL COMMENT '메모',
  MAX_PNL_PCT      DECIMAL(12,6) NULL COMMENT '매수후 청산전 최대수익률%(고가/평단)',
  MAX_PX           DECIMAL(18,4) NULL COMMENT '보유기간 고가',

  CREATED_AT       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_ID),
  KEY IDX_AI_STRAT_LIVE_ALG_DT (ALG_CD, EXIT_DT),
  KEY IDX_AI_STRAT_LIVE_STRAT_DT (STRAT_ID, EXIT_DT),
  KEY IDX_AI_STRAT_LIVE_STK (STK_CD, EXIT_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='전략 모의/실전 전량청산 라운드';
