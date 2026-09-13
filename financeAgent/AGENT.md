# Finance Agent (재무 관리 AI)

SATP_AI에서 **매매 봇·차트 서버와 분리**된 재무·리서치 전담 역할 정의.

## 역할

종목·기업 단위의 **재무·공시·리포트·가이던스** 수집·조회·적재·요약.
주문·실전 매매 판단·백테스트 게이트는 **하지 않는다**.

## 담당

| 영역 | 구현 위치 | 비고 |
|------|-----------|------|
| DART 재무 요약 | `research/dartFinFetch.py`, `dartCorpCode.py` | `DART_API_KEY` |
| 관심 공시·뉴스 주기 | `utils/watchNewsDiscScheduler.py` + `research/watchNewsDiscLoad.py` | 08~22시 / 30분 |
| 종목 재무 번들 | `research/stkFinanceBundle.py` | 웹 패널 데이터 |
| 증권사 리포트 | `research/naverCorpReport.py`, `reportAnalyze.py`, `rptLoadService.py` | creport |
| 가이던스/목표가 DB | `utils/rptAnalStore.py`, `sql/rptAnal.sql` | AI_RPT_ANAL / AI_CNS_EST |
| 연구 소스 목록 | `config/researchSources.py` | FnGuide 유료는 스킵 정책 |
| 공개 API (호출부) | `financeAgent/serviceApi.py` | 파사드 경유 필수 |

## 하지 않는 것

- 매수/매도/STLS/체결 알림
- 전략 명세·백테스트 `val` / `bt`
- 키움 TR 주문·일봉 적재 본체 (차트 `stkLoadDay`는 차트 서버 소유)
- 실계좌 API 키 출력, 시크릿 커밋

## 호출 경로

```text
웹  /api/stkFinance, /api/corpReport
  → financeAgent.serviceApi
텔레그램 creport / cns
  → financeAgent.serviceApi
CLI  python -m financeAgent.agentMain …
  → financeAgent.serviceApi
```

`research/*` 를 웹·텔레그램 명령에서 **직접 import 하지 말 것** (이 경계를 유지).

## 작업 시 원칙

1. 코드·DB 변경 전 사용자 컨펌
2. 시크릿은 `.env`만, 로그에 키 출력 금지
3. FnSpace 등 유료 소스는 스킵 정책 준수
4. 재무 수치는 **투자 권유 아님** 문구 유지
5. 매매 전략 코드(`trading/`, `strategies/`) 수정 범위에 끼워 넣지 않음

## Cursor에서 이 역할로 일할 때

- 스킬: `.cursor/skills/financeAgent/SKILL.md` 준수
- 재무 관련 사용자 요청이 오면 **이 에이전트 관점**으로만 설계·구현
- 매매 쪽 변경이 필요하면 **경계를 명시**하고 매매 에이전트/사용자에 이관
