-- 매매창 거래로그 일별 (00 체결 등 UI 로그)
-- 알고리즘 BUY/SELL·스킵은 AI_ALG_EVT 에 이미 일자별 적재한다.
-- 전날 화면은 거래일 08:00 에 비우고, DB 이력은 남긴다.

CREATE TABLE IF NOT EXISTS AI_DESK_LOG (
  LOG_ID      BIGINT        NOT NULL AUTO_INCREMENT COMMENT '로그ID',
  TRADE_DT    CHAR(8)       NOT NULL COMMENT '거래일 YYYYMMDD',
  LOG_TM      CHAR(6)       NOT NULL COMMENT '시각 HHmmss',
  KIND        VARCHAR(16)   NOT NULL COMMENT 'BUY|SELL|FILL|BUY_SKIP 등',
  SRC         VARCHAR(8)    NOT NULL DEFAULT 'FILL' COMMENT 'FILL=00체결',
  LINE        VARCHAR(500)  NOT NULL COMMENT '화면 표시 한 줄',
  MODE        VARCHAR(8)    NOT NULL DEFAULT 'paper' COMMENT 'paper|real',
  STK_CD      CHAR(6)       NULL COMMENT '종목코드',
  STK_NM      VARCHAR(80)   NULL COMMENT '종목명',
  QTY         INT           NULL COMMENT '수량',
  PX          DECIMAL(18,4) NULL COMMENT '가격',
  ORD_NO      VARCHAR(20)   NULL COMMENT '주문번호',
  CMSN        INT           NULL COMMENT '수수료(원) 00 FID 938',
  TAX         INT           NULL COMMENT '세금(원) 00 FID 939',
  CREATED_AT  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (LOG_ID),
  KEY IDX_AI_DESK_LOG_DT (TRADE_DT, LOG_TM, LOG_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='매매창 거래로그 일별';
