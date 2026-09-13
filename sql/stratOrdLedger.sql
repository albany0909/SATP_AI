-- 알고리즘 종목 원장 (검증·연속매매)
-- 알고리즘코드 5자리(영문3+버전2) + 종목 6 + 일시분초 14 = 거래코드 25자리
-- 미청산 1행. 청산 후 당일 재매수면 거래코드를 새로 만든다.

CREATE TABLE IF NOT EXISTS AI_STRAT_ORD_LEDGER (
  TRADE_CD         CHAR(25)      NOT NULL COMMENT '거래코드=ALG_CD5+종목6+일시분초14',
  ALG_CD           CHAR(5)       NOT NULL COMMENT '알고리즘코드 영문3+버전2 (MTS01)',
  STK_CD           CHAR(6)       NOT NULL COMMENT '종목코드 6자리',
  STK_NM           VARCHAR(80)   NULL COMMENT '종목명',
  STK_STS_CD       CHAR(1)       NOT NULL DEFAULT '3' COMMENT '종목상태 3=매수 9=매도(청산) 0=무효(체결없이 취소)',
  BUY_PX           DECIMAL(18,4) NULL COMMENT '매수가(평단)',
  SELL_PX          DECIMAL(18,4) NULL COMMENT '매도가(최근 매도 체결가)',
  CLOSE_PNL_PCT    DECIMAL(12,6) NULL COMMENT '청산수익률%',
  STRAT_ID         VARCHAR(64)   NULL COMMENT '미사용(하위호환, 관리는 ALG_CD)',
  FIRST_BUY_DT     CHAR(8)       NOT NULL COMMENT '최초매수일자 YYYYMMDD',
  CLOSE_DT         CHAR(8)       NULL COMMENT '청산일자 YYYYMMDD',
  BUY_1            DECIMAL(18,4) NULL COMMENT '매수1차 체결가',
  BUY_2            DECIMAL(18,4) NULL COMMENT '매수2차 체결가',
  BUY_3            DECIMAL(18,4) NULL COMMENT '매수3차 체결가',
  BUY_4            DECIMAL(18,4) NULL COMMENT '매수4차 체결가',
  BUY_5            DECIMAL(18,4) NULL COMMENT '매수5차 체결가',
  TP_1             DECIMAL(18,4) NULL COMMENT '익절1차 체결가',
  TP_2             DECIMAL(18,4) NULL COMMENT '익절2차 체결가',
  TP_3             DECIMAL(18,4) NULL COMMENT '익절3차 체결가',
  TP_4             DECIMAL(18,4) NULL COMMENT '익절4차 체결가',
  TP_5             DECIMAL(18,4) NULL COMMENT '익절5차 체결가',
  SL_1             DECIMAL(18,4) NULL COMMENT '손절1차 체결가',
  SL_2             DECIMAL(18,4) NULL COMMENT '손절2차 체결가',
  SL_3             DECIMAL(18,4) NULL COMMENT '손절3차 체결가',
  SL_4             DECIMAL(18,4) NULL COMMENT '손절4차 체결가',
  SL_5             DECIMAL(18,4) NULL COMMENT '손절5차 체결가',
  WIN_CD           CHAR(1)       NULL COMMENT '승패 1=승 2=패 (본전/미청산 NULL)',

  MODE             VARCHAR(8)    NOT NULL DEFAULT 'paper' COMMENT 'paper|real',
  RMND_QTY         INT           NOT NULL DEFAULT 0 COMMENT '잔여수량(연속매매)',
  BUY_QTY          INT           NOT NULL DEFAULT 0 COMMENT '매수수량 합',
  SELL_QTY         INT           NOT NULL DEFAULT 0 COMMENT '매도수량 합',
  BUY_AMT          DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '매수금액 합',
  SELL_AMT         DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '매도금액 합',
  HALF_YN          CHAR(1)       NOT NULL DEFAULT 'N' COMMENT '1차 절반익절 Y/N',
  FILLED_STEPS     VARCHAR(40)   NULL COMMENT '당일 채운 매차 이평 CSV',
  LAST_BUY_DT      CHAR(8)       NULL COMMENT '마지막 매수일(매차 리셋 판단)',
  MAX_PNL_PCT      DECIMAL(12,6) NULL COMMENT '매수후 청산전 최대수익률%(고가/평단)',
  MAX_PX           DECIMAL(18,4) NULL COMMENT '보유기간 고가',

  CREATED_AT       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_CD),
  KEY IDX_AI_STRAT_ORD_OPEN (ALG_CD, STK_CD, STK_STS_CD),
  KEY IDX_AI_STRAT_ORD_STS (STK_STS_CD, ALG_CD)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='알고리즘별 종목 원장(ALG_CD 5자리, 거래코드 25자리)';
