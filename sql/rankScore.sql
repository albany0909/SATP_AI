-- 관심종목 순위 (조회순위 누적 종목 대상 점수화)
-- 조건코드 확장을 위해 조건셋 / 조건항목 / 일별점수 3개로 분리한다.
-- 조건항목을 켜고 끄거나 추가하면 만점(TOT_SCORE)을 활성 항목 수로 나눠 재배분한다.

CREATE TABLE IF NOT EXISTS AI_RANK_COND (
  COND_CD           VARCHAR(10)   NOT NULL COMMENT '조건코드 (001 등)',
  COND_NM           VARCHAR(100)  NOT NULL COMMENT '조건명',
  TOT_SCORE         DECIMAL(6,2)  NOT NULL DEFAULT 10.00 COMMENT '만점(기본 10점)',
  UNIV_DAYS         INT           NOT NULL DEFAULT 30 COMMENT '조회순위 누적 대상기간(달력일)',
  TOP_N             INT           NOT NULL DEFAULT 10 COMMENT '화면 표시 순위 수',
  SAVE_MAX          INT           NOT NULL DEFAULT 300 COMMENT '일별 저장 최대 종목수',
  STATUS            CHAR(1)       NOT NULL DEFAULT 'Y' COMMENT 'Y사용 N미사용',
  NOTE              VARCHAR(200)  NULL COMMENT '메모',

  CREATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (COND_CD)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='관심종목 순위 조건셋';

CREATE TABLE IF NOT EXISTS AI_RANK_COND_ITEM (
  COND_CD           VARCHAR(10)   NOT NULL COMMENT '조건코드',
  ITEM_CD           VARCHAR(20)   NOT NULL COMMENT '항목코드 FRGN_NET/ORGN_NET/MA_ALIGN/THEME_HOT/RSI_UNDER',
  ITEM_NM           VARCHAR(100)  NOT NULL COMMENT '항목명',
  SORT_NO           INT           NOT NULL DEFAULT 1 COMMENT '표시·채점 순서',
  USE_YN            CHAR(1)       NOT NULL DEFAULT 'Y' COMMENT '채점 사용 Y/N',
  WEIGHT            DECIMAL(6,2)  NULL COMMENT '배점 고정(NULL이면 활성항목 균등배분)',
  PARAM_JSON        VARCHAR(500)  NULL COMMENT '항목 파라미터 JSON',
  NOTE              VARCHAR(200)  NULL COMMENT '메모',

  CREATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (COND_CD, ITEM_CD),
  KEY IDX_AI_RANK_COND_ITEM_SORT (COND_CD, SORT_NO)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='관심종목 순위 조건항목';

CREATE TABLE IF NOT EXISTS AI_RANK_SCORE (
  TRADE_DT          CHAR(8)       NOT NULL COMMENT '기준일자 YYYYMMDD (순위 적재일)',
  COND_CD           VARCHAR(10)   NOT NULL COMMENT '조건코드',
  STK_CD            VARCHAR(20)   NOT NULL COMMENT '종목코드',
  STK_NM            VARCHAR(80)   NULL COMMENT '종목명',
  SCORE             DECIMAL(6,2)  NOT NULL DEFAULT 0 COMMENT '점수(만점 기준)',
  RANK_NO           INT           NULL COMMENT '점수 순위 (1위부터)',
  HIT_CNT           INT           NOT NULL DEFAULT 0 COMMENT '충족 항목수',
  HIT_ITEMS         VARCHAR(200)  NULL COMMENT '충족 항목코드 CSV',
  DETAIL_JSON       VARCHAR(1000) NULL COMMENT '항목별 값·배점 상세',
  IND_GRP           VARCHAR(40)   NULL COMMENT '테마/업종그룹',
  APPEAR_CNT        INT           NOT NULL DEFAULT 0 COMMENT '대상기간 조회순위 등장 횟수',

  CREATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UPDATED_AT        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (TRADE_DT, COND_CD, STK_CD),
  KEY IDX_AI_RANK_SCORE_RANK (TRADE_DT, COND_CD, RANK_NO),
  KEY IDX_AI_RANK_SCORE_STK (STK_CD, TRADE_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='관심종목 순위 점수(일별)';

INSERT IGNORE INTO AI_RANK_COND
  (COND_CD, COND_NM, TOT_SCORE, UNIV_DAYS, TOP_N, SAVE_MAX, STATUS, NOTE)
VALUES
  ('001', '수급_이평_테마_RSI', 10.00, 30, 10, 300, 'Y',
   '외국인·기관 순매수 증가 + 20>50>150 이평 + 테마 빈출 + RSI 70 미만');

INSERT IGNORE INTO AI_RANK_COND_ITEM
  (COND_CD, ITEM_CD, ITEM_NM, SORT_NO, USE_YN, WEIGHT, PARAM_JSON, NOTE)
VALUES
  ('001', 'FRGN_NET', '최근 일주일 외국인 순매수 증가', 1, 'Y', NULL,
   '{"days": 7, "minNet": 0}',
   'AI_DAY_INV_SR FRGNR_INVSR 합 > minNet 이면 증가로 판단'),
  ('001', 'ORGN_NET', '최근 일주일 기관 순매수 증가', 2, 'Y', NULL,
   '{"days": 7, "minNet": 0}',
   'AI_DAY_INV_SR ORGN 합 > minNet 이면 증가로 판단'),
  ('001', 'MA_ALIGN', '이평 정배열 (20 > 50 > 150)', 3, 'Y', NULL,
   '{"fast": 20, "mid": 50, "slow": 150}',
   'AI_DAY_STK_PRC 종가 SMA · 봉수 부족 시 미충족'),
  ('001', 'THEME_HOT', '테마·업종 조회순위 빈출', 4, 'Y', NULL,
   '{"topPct": 30}',
   '대상기간 IND_GRP 등장 건수 상위 topPct%'),
  ('001', 'RSI_UNDER', 'RSI 과열 아님 (70 미만)', 5, 'Y', NULL,
   '{"period": 14, "maxRsi": 70}',
   'AI_DAY_STK_PRC 종가 RSI(Wilder)');
