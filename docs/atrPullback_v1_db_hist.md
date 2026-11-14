# ATR01 v1 버전 이력 — 운영 DB 에 넣지 말 것

`AI_ALG_VER_HIST` 에 아래를 넣을 수 있다. 이 작업에서는 실행하지 않았다.
`GIT_HASH` 는 스펙 등록 커밋의 짧은 해시다.

- ALG_CD: `ATR01`
- STRAT_ID: `atrPullback_v1`
- ALG_VER: `1`
- YMD: `2026-11-14`
- SRC: `manual`
- NOTE: `v1 눌림 ATR 스펙. 주문 없음. enabled=false, paperOnly=true. 1년 비용 없는 참고 +2.79%/거래, PF 1.52, 153건.`
- GIT_HASH: `272937cf33fc`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('ATR01', 'atrPullback_v1', 1, '2026-11-14',
   'v1 눌림 ATR 스펙. 주문 없음. enabled=false, paperOnly=true. 1년 비용 없는 참고 +2.79%/거래, PF 1.52, 153건.',
   '272937cf33fc', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```
