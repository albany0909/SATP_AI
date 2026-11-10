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
- GIT_HASH: `3b7521c080d5`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 5, '2026-11-09',
   'v5 명부 필터 제거. ka00198 base_comp_chgr 10% 이상만. 주문 비활성.',
   '3b7521c080d5', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v6 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `6`
- YMD: `2026-11-09`
- SRC: `manual`
- NOTE: `v6 탭은 5% 이상. 원순위와 1년저점대비를 빼고 첫급등대비를 표시. 매수는 10% 유지. 주문 비활성.`
- GIT_HASH: `2e698b34b3f3`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 6, '2026-11-09',
   'v6 탭은 5% 이상. 원순위와 1년저점대비를 빼고 첫급등대비를 표시. 매수는 10% 유지. 주문 비활성.',
   '2e698b34b3f3', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v7 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `7`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v7 장 시작 전 탭은 전일 최종 목록. 조회일시를 상단에 표시. 매수 조건은 그대로. 주문 비활성.`
- GIT_HASH: `67fe98c2e0ee`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 7, '2026-11-10',
   'v7 장 시작 전 탭은 전일 최종 목록. 조회일시를 상단에 표시. 매수 조건은 그대로. 주문 비활성.',
   '67fe98c2e0ee', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v8 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `8`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v8 탭 칸은 ka10059·ka10081·ka10100 과 확인된 헤드라인으로 채운다. 매수 조건은 그대로. 주문 비활성.`
- GIT_HASH: `61d67230340e`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 8, '2026-11-10',
   'v8 탭 칸은 ka10059·ka10081·ka10100 과 확인된 헤드라인으로 채운다. 매수 조건은 그대로. 주문 비활성.',
   '61d67230340e', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v9 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `9`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v9 같은 이슈는 같은 배경. 헤드라인은 네이버 모바일 종목뉴스. 주문 비활성.`
- GIT_HASH: `f7b2101a2ad6`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 9, '2026-11-10',
   'v9 같은 이슈는 같은 배경. 헤드라인은 네이버 모바일 종목뉴스. 주문 비활성.',
   'f7b2101a2ad6', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v10 은 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `10`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v10 급등주 탭에서 시장 칸을 뺌. 시장 값은 캐시에 남김. 주문 비활성.`
- GIT_HASH: `fd3bf7ac1004`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 10, '2026-11-10',
   'v10 급등주 탭에서 시장 칸을 뺌. 시장 값은 캐시에 남김. 주문 비활성.',
   'fd3bf7ac1004', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v11 은 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `11`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v11 RSI14 칸을 거래대금 바로 앞으로 옮김. 주문 비활성.`
- GIT_HASH: `800954c45cc1`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 11, '2026-11-10',
   'v11 RSI14 칸을 거래대금 바로 앞으로 옮김. 주문 비활성.',
   '800954c45cc1', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v12 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `12`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v12 RSI 30 이하 옅은 파랑, 70 이상 옅은 빨강. 주문 비활성.`
- GIT_HASH: `72854eb4275a`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 12, '2026-11-10',
   'v12 RSI 30 이하 옅은 파랑, 70 이상 옅은 빨강. 주문 비활성.',
   '72854eb4275a', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v13 은 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `13`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v13 52주 신고가 신 배지. 일봉은 RSI와 같은 ka10081. 주문 비활성.`
- GIT_HASH: `42ddb69b8a3d`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 13, '2026-11-10',
   'v13 52주 신고가 신 배지. 일봉은 RSI와 같은 ka10081. 주문 비활성.',
   '42ddb69b8a3d', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v14 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `14`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v14 최근 20거래일 첫 급등 뉴 배지. 추가 TR 없음. 주문 비활성.`
- GIT_HASH: `6e1484adfef6`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 14, '2026-11-10',
   'v14 최근 20거래일 첫 급등 뉴 배지. 추가 TR 없음. 주문 비활성.',
   '6e1484adfef6', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v15 는 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `15`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v15 실패로 남은 이슈 캐시는 다시 조회. 단어는 주도주와 같음. 주문 비활성.`
- GIT_HASH: `abbbc5706ba3`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 15, '2026-11-10',
   'v15 실패로 남은 이슈 캐시는 다시 조회. 단어는 주도주와 같음. 주문 비활성.',
   'abbbc5706ba3', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

v16 은 별도 행이다. 이 작업에서는 실행하지 않았다.

- ALG_CD: `SRG01`
- STRAT_ID: `surgeTrade_v1`
- ALG_VER: `16`
- YMD: `2026-11-10`
- SRC: `manual`
- NOTE: `v16 이슈는 종목명 헤드라인의 이유 태그 우선. 테마는 없을 때만. 주문 비활성.`
- GIT_HASH: `46310e80089f`

```sql
INSERT INTO AI_ALG_VER_HIST
  (ALG_CD, STRAT_ID, ALG_VER, YMD, NOTE, GIT_HASH, SRC)
VALUES
  ('SRG01', 'surgeTrade_v1', 16, '2026-11-10',
   'v16 이슈는 종목명 헤드라인의 이유 태그 우선. 테마는 없을 때만. 주문 비활성.',
   '46310e80089f', 'manual')
ON DUPLICATE KEY UPDATE
  NOTE=VALUES(NOTE),
  GIT_HASH=VALUES(GIT_HASH),
  SRC=VALUES(SRC);
```

현재 이벤트 행의 `ALG_VER` 는 16 이다. 헬퍼는 `strategies.impl.surgeTrade.buildAlgVerEvent` 이며 DB 를 열지 않는다.

순위 스냅샷과 이슈 확인 표는 `sql/surgeTrade.sql` 이다. 같은 이유로 운영 DB 에 실행하지 않는다.
