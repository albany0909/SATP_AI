# 모의 데스크 워치독

모의 전용입니다. 데스크가 꺼져 있으면 다시 띄우고, 떠 있으면 아무것도 하지 않습니다. `settings.json`은 읽기만 합니다. 실전 모드로 기동하는 경로는 없습니다.

기동 조건은 모두 맞아야 합니다.

- `deskTradeMode`가 `paper`
- `maTouchLive.paperOnly`와 `stratLive.*`의 `paperOnly`가 있는 항목마다 `true`
- `config/data/desk_watchdog.pause` 파일이 없음
- 창 제목이 `천둥이 MAIN`으로 시작하는 데스크, 또는 `python main.py` 프로세스가 없음

예약 작업은 로그온 때 한 번, 그리고 3분마다 숨은 작업으로 `pythonw.exe tools\deskWatchdog.py`를 실행합니다. 작업 디렉터리는 리포 루트입니다. 이미 실행 중이면 새 인스턴스는 무시하고, 한 번 실행은 3분 안에 끝냅니다. 로그인된 현재 사용자(InteractiveToken)로만 등록합니다.

## 등록과 제거

STP에서, 리포 루트가 현재 작업 폴더가 아니어도 됩니다. 스크립트가 자기 위치로 리포를 찾습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\installDeskWatchdog.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\installDeskWatchdog.ps1 -Uninstall
```

`pythonw.exe`는 PATH에서 찾고, 없으면 `SATP_DESK_PYTHONW`에 전체 경로를 넣습니다. 데스크를 띄울 때 쓰는 `python.exe`는 워치독을 실행한 인터프리터와 같은 폴더입니다. `pythonw.exe`로 돌고 있으면 같은 폴더의 `python.exe`를 씁니다. `SATP_DESK_PYTHON`이 있으면 그 경로가 우선입니다.

## 일부러 끌 때

데스크를 끄기 **전에** 일시정지를 켭니다. 켜지 않으면 3분 안에 워치독이 다시 띄웁니다.

```text
python tools\deskWatchdogPause.py on
```

다시 띄운 뒤 3분 이상 안정인지 확인합니다. PID가 하나이고, 부팅 텔레그램이 왔고, 보유 복원이 끝났으면 일시정지를 끕니다.

```text
python tools\deskWatchdogPause.py off
python tools\deskWatchdogPause.py status
```

`status`가 `PAUSE_OFF`여야 합니다. 작업을 끝낼 때 플래그를 켜 둔 채로 두지 않습니다. 켜 둔 채 두면 데스크가 죽어도 워치독이 살리지 않습니다.

`desk_watchdog.pause`와 `desk_watchdog.lock`은 런타임 파일이라 커밋하지 않습니다.

## 배포

이 도구는 알고리즘 버전과 앱 버전을 올리지 않습니다. 앱 버전 범프는 장전 배포 절차에서 따로 합니다. 재기동 텔레그램의 버전 문구는 `config/data/appVersion.json`을 읽어 `v{major}.{minor}`로 붙입니다. 파일을 못 읽으면 버전 없이 보냅니다.

월요일 장전 배포에 코드와 함께 올리고, STP에서 등록 스크립트를 한 번 실행합니다. 등록 전에 `deskWatchdogPause.py status`로 플래그가 꺼져 있는지 확인합니다. 배포 확인이 끝나면 플래그가 꺼진 상태인지 다시 봅니다.
