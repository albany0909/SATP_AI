-- 시장 국면 일자별 판정. 앱이 이 파일을 실행하거나 테이블을 만들지 않는다.
-- 운영 DB에 넣기 전에 사람이 확인한다. 기존 테이블은 수정·삭제하지 않는다.
-- 앱은 이미 있는 경우에만 INSERT 한다. 같은 날짜가 있으면 다시 쓰지 않는다.

CREATE TABLE IF NOT EXISTS AI_MKT_REGIME (
  TRADE_DT    CHAR(8)       NOT NULL COMMENT '판정 기준일 YYYYMMDD',
  STK_CD      CHAR(6)       NOT NULL COMMENT '판정 종목. 기본 069500',
  REGIME      VARCHAR(8)    NOT NULL COMMENT '상승장|하락장|횡보장|판정불가',
  REASON      VARCHAR(200)  NULL COMMENT '판정 근거',
  CLOSE_PRC   DECIMAL(18,4) NULL COMMENT '확정 종가',
  SMA200      DECIMAL(18,4) NULL COMMENT '200일 단순이평',
  ADX         DECIMAL(10,4) NULL COMMENT 'Wilder ADX',
  PDI         DECIMAL(10,4) NULL COMMENT '+DI',
  MDI         DECIMAL(10,4) NULL COMMENT '-DI',
  VOL20       DECIMAL(10,4) NULL COMMENT '20일 연환산 변동성',
  DD_PCT      DECIMAL(10,4) NULL COMMENT '고점 대비 낙폭 %',
  DIST200     DECIMAL(10,4) NULL COMMENT '200일선까지 거리 %',
  REG01       VARCHAR(8)    NULL COMMENT 'ON|OFF. 표시만',
  CREATED_AT  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_DT, STK_CD)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='시장 국면 일자별 판정. INSERT만 사용';
