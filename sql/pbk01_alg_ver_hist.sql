-- PBK01 v2. 배포 때 AI_ALG_VER_HIST 에 넣는다. 이 파일만 준비하고 여기서 실행하지 않는다.
-- GIT_HASH 는 v2 커밋의 짧은 해시다.

INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('PBK01', 'pullbackTwoStep_v1', 2, '2026-11-20',
   'v2 모의 매수는 매도 1호가 지정가. 호가가 이평+추격한도를 넘거나 호가가 없으면 미주문. 백테스트 체결은 min(시가, 이평).',
   'PENDING_COMMIT', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
