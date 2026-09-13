# 전략 명세·스펙 관리

매매 전략의 **사람용 명세(MD)** 와 **엔진용 파라미터(JSON)** 를 이 폴더에서 관리한다.
실행 코드(백테스트·실주문)는 `trading/` · `backtest/`(추후)에 두고, 여기에는 정의만 둔다.

## 디렉토리

| 경로 | 역할 |
|------|------|
| `docs/` | 전략 주요 명세 (진입·청산·전제·주의·출처) |
| `specs/` | 백테스트·실전 공통 파라미터 JSON |
| `_template.md` | 신규 전략 MD 작성 템플릿 |

## 네이밍

- `stratId` = 파일 stem (확장자 제외)
- 예: `mahaPullback_v1` → `docs/mahaPullback_v1.md` + `specs/mahaPullback_v1.json`
- 버전: `_v1`, `_v2` … 규칙·파라미터 큰 변경 시 버전 올림 (기존 스펙은 보존)

## 상태 (`status` in JSON)

| 값 | 의미 |
|------|------|
| `draft` | 구상·문서만 |
| `test` | 백테스트·모의 검증 중 |
| `live` | 실전 후보/운용 |
| `archived` | 더 이상 쓰지 않음 |

## 새 전략 추가 절차

1. `_template.md` 복사 → `docs/{stratId}.md` 작성
2. `specs/{stratId}.json` 작성 (아래 스키마)
3. 이 README 목록 표에 한 줄 추가
4. (추후) 엔진 registry에 `stratId` 등록

## 스펙 JSON 최소 스키마

```json
{
  "stratId": "example_v1",
  "name": "표시명",
  "status": "draft",
  "version": 1,
  "enabled": false,
  "timeframe": "1",
  "universe": { "type": "list", "stkCds": [] },
  "params": {},
  "rules": {},
  "fill": {},
  "notes": ""
}
```

- `timeframe`: `"1"`=1분 (원천 적재). `"3"`/`"5"`/`"15"`/`"30"`/`"60"` 은 1분 리샘플 검증용
- `params` / `rules` / `fill`: 전략별 확장. MD 명세와 변수명이 일치해야 함

## 등록 목록

| stratId | 이름 | status | 문서 |
|---------|------|--------|------|
| mahaPullback_v1 | 마하세븐 눌림목(정배열+프랙탈) | test | [docs](docs/mahaPullback_v1.md) / [spec](specs/mahaPullback_v1.json) |
| alignMaBreak_v1 | 정배열 이평선 돌파매매1 | test | [docs](docs/alignMaBreak_v1.md) / [spec](specs/alignMaBreak_v1.json) |
| highPullback_v1 | 신고가 눌림목매매1 | test | [docs](docs/highPullback_v1.md) / [spec](specs/highPullback_v1.json) |
| bullBodyMa2050_v1 | 장대양봉 2050이평매매 | test | [docs](docs/bullBodyMa2050_v1.md) / [spec](specs/bullBodyMa2050_v1.json) |
| maTouchScale_v1 | 이평선터치(SMA20·300만·+3%/−2%·절반후0%) | test | [docs](docs/maTouchScale_v1.md) / [spec](specs/maTouchScale_v1.json) |
| maSupportEod_v1 | 이평선지지 (ka10016 52주신고가 관심, SMA5 돌파→약음봉/십자 지지 15:18~15:30) | test | [docs](docs/maSupportEod_v1.md) / [spec](specs/maSupportEod_v1.json) |
