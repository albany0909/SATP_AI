# SRG01 v1 버전 이력 — 운영 DB 에 넣지 말 것

`AI_ALG_VER_HIST` 에 아래를 넣을 수 있다. 이 작업에서는 실행하지 않았다.
`GIT_HASH` 는 이 기능 커밋의 짧은 해시다. 커밋 뒤에 이 문서의 해시를 맞춘다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `1`
- YMD: `2026-11-08`
- SRC: `manual`
- NOTE: `v1 급등주매매(Surge Trade) 모의 전용 초안. 기본 비활성. 실주문 없음.`
- GIT_HASH: `f5093a17fda8`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 1, '2026-11-08',
   'v1 급등주매매(Surge Trade) 모의 전용 초안. 기본 비활성. 실주문 없음.',
   'f5093a17fda8', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v2 는 별도 행이다. 이 작업에서는 실행하지 않았다. `GIT_HASH` 는 v2 구현 커밋이다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `2`
- YMD: `2026-11-09`
- SRC: `manual`
- NOTE: `v2 급등주 탭 ka00198 당일누적 조회는 전략이 꺼져 있어도 15분·16:00 수집. 수동 갱신은 ADHOC. 주문은 기본 비활성.`
- GIT_HASH: `89c51d20b3d4`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 2, '2026-11-09',
   'v2 급등주 탭 ka00198 당일누적 조회는 전략이 꺼져 있어도 15분·16:00 수집. 수동 갱신은 ADHOC. 주문은 기본 비활성.',
   '89c51d20b3d4', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v3 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `3`
- YMD: `2026-11-09`
- SRC: `manual`
- NOTE: `v3 매수는 구성·확인이슈·신선시세가 있을 때만. 보유 청산은 시세. 주문 기본 비활성.`
- GIT_HASH: `4d089a399fca`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 3, '2026-11-09',
   'v3 매수는 구성·확인이슈·신선시세가 있을 때만. 보유 청산은 시세. 주문 기본 비활성.',
   '4d089a399fca', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v4 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `4`
- YMD: `2026-11-09`
- SRC: `manual`
- NOTE: `v4 KOSPI200은 모의 ka20002 전수. KOSDAQ150은 KRX 지수명 조회. DART 키는 출력하지 않음. 주문 비활성.`
- GIT_HASH: `79f8e44a4bc7`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 4, '2026-11-09',
   'v4 KOSPI200은 모의 ka20002 전수. KOSDAQ150은 KRX 지수명 조회. DART 키는 출력하지 않음. 주문 비활성.',
   '79f8e44a4bc7', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v5 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `5`
- YMD: `2026-11-09`
- SRC: `manual`
- NOTE: `v5 명부 필터 제거. ka00198 base_comp_chgr 10% 이상만. 주문 비활성.`
- GIT_HASH: `PENDING_V5`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 5, '2026-11-09',
   'v5 명부 필터 제거. ka00198 base_comp_chgr 10% 이상만. 주문 비활성.',
   'PENDING_V5', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

현재 이벤트 행의 `ALG_VER` 는 5 다. 헬퍼는 `strategies.impl.surgeTrade.buildAlgVerEvent` 이며 DB 를 열지 않는다.

순위 스냅샷과 이슈 확인 표는 `sql/surgeTrade.sql` 이다. 같은 이유로 운영 DB 에 실행하지 않는다.
