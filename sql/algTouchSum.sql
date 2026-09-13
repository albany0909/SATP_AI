-- 이평 터치 일별 요약 (알고리즘 복기용)
-- 종목·매차(이평)별로 하루 1행. 터치마다 카운트·마지막 시각만 갱신한다.
-- 틱마다 행을 남기면 양이 커지므로 요약만 적재한다.

CREATE TABLE IF NOT EXISTS AI_ALG_TOUCH_SUM (
  ALG_CD         CHAR(5)       NOT NULL COMMENT '알고리즘코드 (MTS01)',
  TRADE_DT       CHAR(8)       NOT NULL COMMENT '거래일 YYYYMMDD',
  STK_CD         VARCHAR(20)   NOT NULL COMMENT '종목코드',
  MA_STEP        SMALLINT      NOT NULL COMMENT '매차 이평 (5/10/15)',

  STK_NM         VARCHAR(80)   NULL COMMENT '종목명',
  MODE           VARCHAR(8)    NOT NULL DEFAULT 'paper' COMMENT 'paper|real',

  MA_PX          DECIMAL(18,4) NULL COMMENT '그날 고정 이평값 (전일까지 확정 종가)',
  SMA20_PX       DECIMAL(18,4) NULL COMMENT '20선 게이트 값',

  TOUCH_CNT      INT           NOT NULL DEFAULT 0 COMMENT '터치 횟수',
  FIRST_TM       CHAR(6)       NULL COMMENT '첫 터치 HHmmss',
  LAST_TM        CHAR(6)       NULL COMMENT '마지막 터치 HHmmss',
  FIRST_PX       DECIMAL(18,4) NULL COMMENT '첫 터치 가격',
  LAST_PX        DECIMAL(18,4) NULL COMMENT '마지막 터치 가격',

  BUY_YN         CHAR(1)       NOT NULL DEFAULT 'N' COMMENT '이 매차 매수 성공 Y/N',
  BUY_TM         CHAR(6)       NULL COMMENT '매수 체결 시각',
  BUY_PX         DECIMAL(18,4) NULL COMMENT '매수 체결가',
  BUY_QTY        INT           NULL COMMENT '매수 수량',
  STEP_SEQ       SMALLINT      NULL COMMENT '그 종목 당일 매수 순번 (1,2,3)',

  SKIP_CNT       INT           NOT NULL DEFAULT 0 COMMENT '터치했으나 미매수 횟수',
  LAST_SKIP_RSN  VARCHAR(20)   NULL COMMENT 'BELOW_SMA20|AFTER_1400|QTY0|FILLED',

  CREATED_AT     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (ALG_CD, TRADE_DT, STK_CD, MA_STEP),
  KEY IDX_AI_ALG_TOUCH_ALG_DT (ALG_CD, TRADE_DT),
  KEY IDX_AI_ALG_TOUCH_STK (STK_CD, TRADE_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='이평 터치 일별 요약 (종목·매차별 1행)';
