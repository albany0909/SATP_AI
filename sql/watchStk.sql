-- 관심종목 (전략검증·추후 실매매 유니버스)
-- 장마감 실시간조회순위(당일누적) 중 코스피/코스닥·양봉만 자동 반영

CREATE TABLE IF NOT EXISTS AI_WATCH_STK (
  STK_CD            VARCHAR(20)   NOT NULL COMMENT '종목코드',
  MKT_TP            VARCHAR(4)    NOT NULL DEFAULT 'KR' COMMENT '시장 KR (미국 미사용)',
  STK_NM            VARCHAR(80)   NULL COMMENT '종목명',
  MARKET_NAME       VARCHAR(40)   NULL COMMENT '거래소명(코스피/코스닥)',
  IND_GRP           VARCHAR(40)   NULL COMMENT '테마/업종(순위 적재 시)',
  GRP_CD            VARCHAR(40)   NOT NULL DEFAULT 'RANK' COMMENT '그룹 RANK=순위연동',
  STATUS            CHAR(1)       NOT NULL DEFAULT 'Y' COMMENT 'Y활성 N비활성',
  USE_BT            CHAR(1)       NOT NULL DEFAULT 'Y' COMMENT '백테스트 대상 Y/N',
  USE_TRADE         CHAR(1)       NOT NULL DEFAULT 'N' COMMENT '실매매 허용 Y/N (기본N)',
  PRIORITY          INT           NOT NULL DEFAULT 100 COMMENT '우선순위(작을수록 우선)',
  RANK_NO           INT           NULL COMMENT '최근 반영 순위',
  REF_TRADE_DT      CHAR(8)       NULL COMMENT '순위 기준일(장마감 세션일)',
  LOAD_DT           CHAR(8)       NULL COMMENT '관심종목 적재일 YYYYMMDD',
  OPEN_PRC          BIGINT        NULL COMMENT '기준일 시가',
  CLOSE_PRC         BIGINT        NULL COMMENT '기준일 종가',
  SRC               VARCHAR(40)   NULL COMMENT '출처 rank_close 등',
  NOTE              VARCHAR(200)  NULL COMMENT '메모',

  CREATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (STK_CD, MKT_TP),
  KEY IDX_AI_WATCH_STK_STATUS (STATUS, USE_BT),
  KEY IDX_AI_WATCH_STK_GRP (GRP_CD, STATUS),
  KEY IDX_AI_WATCH_STK_REF_DT (REF_TRADE_DT),
  KEY IDX_AI_WATCH_STK_LOAD_DT (LOAD_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='관심종목(순위·전략 유니버스)';
