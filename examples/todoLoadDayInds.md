# 이어할 일: 업종일봉(코스피/코스닥) 적재

## 상태
- **완료 (2026-08-02)**: 실전 API로 적재 완료 — KOSPI 6310건 + KOSDAQ 6310건 = **12620건**
- 적재기간: `20010102` ~ `20260731` (요청 시작일 20010101은 휴장, 종료일은 직전 거래일)

## 할 일 (최초 요청)
키움 REST API가 정상(토큰 발급·TR 호출 가능)일 때
**코스피(001), 코스닥(101)** 일별 지수를 **20010101 ~ 오늘**까지 `AI_DAY_INDS_PRC`에 적재한다.

## 이미 준비된 것
| 항목 | 경로 |
|------|------|
| DDL | `sql/dayIndsPrc.sql` (테이블 `AI_DAY_INDS_PRC` — thor_db에 생성됨) |
| API | `api/getDayIndsPrc.py` (ka20006, OHLC ÷100, 연속조회+재시작) |
| UPSERT | `utils/dayIndsPrcStore.py` |
| 실행 스크립트 | `examples/loadDayIndsPrc.py` |

## 실행
```powershell
cd c:\dev-workspace\workspace\SATP_AI
$env:PYTHONPATH = "c:\dev-workspace\workspace\SATP_AI"
python examples/loadDayIndsPrc.py
```

## 참고
- 이전에 API가 `302 → start.html`로 떨어져 적재 중단됨 (실전/모의 동일).
- 적재 전 토큰 발급(`au10001`)이 성공하는지 먼저 확인.
- PK: `(MKT_TP, INDS_CD, TRADE_DT)` — `MKT_TP=KR`, `SRC_TR=ka20006`
- UPSERT이므로 중간에 끊겨도 재실행 가능.
