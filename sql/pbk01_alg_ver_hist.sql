-- PBK01 v3. 배포 때 AI_ALG_VER_HIST 에 넣는다. 이 파일만 준비하고 여기서 실행하지 않는다.
-- v2(커밋 16c807b, 매도 1호가 지정가·추격 2%)는 STP에 배포된 적 없다. v2 행은 넣지 않는다.
-- GIT_HASH 는 v3 커밋의 짧은 해시다. 커밋 뒤에 맞춘다.

INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('PBK01', 'pullbackTwoStep_v1', 3, '2026-11-20',
   'v3 지정가 min(매도1호가, 이평 호가올림). 이평보다 비싸게 사지 않음. 거래대금은 장대양봉 당일 제외 직전 20일. v2는 배포되지 않음. 백테스트 체결은 min(시가, 이평).',
   'PENDING_COMMIT', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
