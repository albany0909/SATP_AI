# SRG01 급등주매매 — Windows 로컬 적용·검증

이 문서는 검토용이다. 원격 실행에서는 FTP, 배포, 병합, 앱 재시작, 실계좌 조회, 주문, `.env` 변경, 런타임 설정 활성화를 하지 않는다. 적용 시점을 날짜로 약속하지 않는다. 아래 전제가 로컬에서 확인되기 전에는 전략을 켜지 않는다.

식별자: `SRG01` / `surgeTrade_v1` / Surge Trade / 스펙 version `1`.

## 적용 전에 유지할 것

1. 작업 트리에서 `git status --short` 를 남긴다.
2. 다음을 작업 폴더 밖 백업으로 복사한다. 커밋하지 않는다.
   - `.env`
   - `config/data/settings.json`
   - `config/data/*State.json`
   - `config/data/surgeTrade/` 가 이미 있으면 그 폴더
   - 추적되지 않은 로컬 파일 전부 (이전에 유지하라고 한 5개 포함). 백업 목록에 경로를 적는다.
3. `deskTradeMode` 는 `paper` 로 둔다. `paperOnly` 를 실전으로 바꾸지 않는다.
4. `stratLive` 의 MTS01, MSE01, LDR01, MMG01, RAM01 `enabled` 를 이 작업 때문에 바꾸지 않는다.
5. RCS01 이 로컬에 있더라도 활성화하거나 병합하지 않는다.
6. 운영 DB 에 `sql/surgeTrade.sql` 과 `docs/surgeTrade_v1_db_hist.md` 의 INSERT 를 실행하지 않는다. 검토 후에만 별도로 한다.

## 코드를 받는 방법

검토가 끝난 뒤에만, 로컬 저장소에서 이 브랜치를 받아 적용한다. `scripts/deployTradeApp.py` 와 FTP 는 쓰지 않는다. 서버 재시작은 하지 않는다.

```text
git fetch origin cursor/surge-trade-srg01-3ba4
git checkout cursor/surge-trade-srg01-3ba4
```

백업한 `.env` 와 `settings.json` 이 덮였으면 백업으로 되돌린다.

## 로컬에서 돌릴 테스트

Windows 의 프로젝트 루트에서:

```text
python tests\testSurgeTrade.py
python tests\testMmg01.py
python scripts\verifyClientLaunch.py
```

`verifyClientLaunch.py` 는 `dist\tradeFull\runtime\python.exe` 가 있어야 한다. 그 런타임이 없는 환경의 실패는 Windows 기동 성공이 아니다.

## 전략을 켜기 전에 필요한 확인

순서대로 로컬에서 확인한다. 하나라도 안 되면 `enabled` 를 올리지 않는다.

1. 모의 로그인만 되어 있고, 주문 호스트가 `https://mockapi.kiwoom.com` 이다. 실전 토큰으로 이 전략 주문을 내지 않는다.
2. 모의 호스트에서 `ka00198` `qry_tp=4` 를 직접 호출해 순위 건수, 연속조회, 등락율 단위를 기록한다. 명세의 mock 도메인만으로는 확인이 끝나지 않았다.
3. 날짜 유효기간이 있는 KOSPI200·KOSDAQ150 명부를 준비한다. 없는 종목을 채우지 않는다.
4. 전일 정규 종가(수정주가 기준을 현재가와 같게)를 갖고 일간 등락을 계산한다. 기준가만 있는 행은 매수하지 않는다.
5. 이슈는 공시 URL 과 발표시각이 있는 확인 기록으로만 최초 급등이 된다. 첫 관측이나 테마 이름은 매수 사유가 아니다.
6. 15:18 판단은 15:18 이후 스냅을 쓴다. 16:00 스냅으로 15:18 을 소급하지 않는다.
7. paper 모드에서 소액이 아닌 **주문 미전송** 경로로 게이트가 실전 호스트를 거부하는지 위 테스트로 다시 본다.
8. 그 전에 `python scripts/srgReadiness.py` 로 PASS/FAIL 을 본다. `--fetch` 는 모의 세션이 없으면 `.env` 모의 앱키로 모의 호스트에만 로그인하고 `ka00198` 당일누적을 ADHOC 으로 저장한다. 토큰은 출력하지 않는다. 앱키가 없으면 매매창 모의 로그인 안에서 실행하라고 알리고 끝낸다. 실호스트로는 가지 않는다. 지수 명부는 조회하지 않는다. DART 는 `config.config.dart_api_key` 를 쓰며 값은 출력하지 않는다.
9. 그 다음에만 스펙 `enabled` 와 `settings.stratLive.SRG01.enabled` 를 함께 true 로 바꾼다. 둘 중 하나만으로는 켜지지 않는다. 앱 재시작 여부는 그때 따로 결정한다. 이 커밋은 둘 다 켜지 않는다.

급등주 탭은 전략이 꺼져 있어도 조회용으로 보인다. 탭이 보이는 메인 창은 거래일에 모의 호스트의 ka00198 당일누적만 15분·16:00 에 저장한다. SRG01 을 켜지 않는다. 주문은 만들지 않는다. 수동 갱신은 같은 조회를 백그라운드에서 한 번 더 받아 ADHOC 으로 저장하고, 화면은 관측 시각이 가장 늦은 스냅샷을 보여 준다. 16:00 실측보다 늦은 수동 조회만 그 최종분을 앞선다. 모의 로그인이 없으면 수동 갱신은 `실패` 로 남고 네트워크를 열지 않는다.

## 조회 탭·하단 알고리즘 표시

`deskAlgTabs` 는 실계좌·주도주 조회 탭을 숨기지 못한다. 그 두 탭을 숨기는 기존 설정 키는 없다.

이 코드가 프로세스에 올라간 뒤, `deskMonitorTabs` 키가 없으면 조회 탭은 급등주만 보인다. 실계좌·주도주를 다시 보이려면 그 키를 `["acnt","leader","surge"]` 로 두고 창이 설정을 다시 읽게 해야 한다. 키만 고친 채 이미 떠 있는 프로세스는 바뀌지 않는다.

하단 운영알고리즘과 전략 설명은 유효 가동인 알고리즘만 남긴다. 매매 플래그를 켜서 글을 채우지 않는다.

이미 떠 있는 매매창(2026-11-08 23:32 KST, PID 27356, 모의)은 이 표시를 스스로 바꾸지 않는다. 새 코드를 반영하려면 프로세스를 다시 띄워야 하지만, 기동 때 `telegramPush.boot=false` 인데도 부팅 텔레그램이 나간다. 그 문제는 여기서 고치지 않는다. 사용자가 다시 띄우겠다고 하기 전에는 재시작하지 않는다. FTP·병합·배포는 하지 않는다.
