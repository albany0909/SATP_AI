"""
SATP_AI 로그인 런처 (배포용 진입점).

흐름 (구 SATP_UPDATE 유사):
  1) 로그인 창 상시 유지 · X 닫기 시 트레이로 숨김
  2) 로그인 → 실전/모의 선택 · 해당 모드 앱키 필수
  3) [매매창 실행] → 설치/업데이트 진행창 → 매매창 기동
  4) 매매창 종료해도 로그인(트레이) 유지 · 다시 실행 가능
  5) 트레이 우클릭「종료」시 매매창도 함께 종료
"""

from __future__ import annotations

import sys

from utils.termLog import termLog


def main():
    try:
        from utils.appIcon import setAppUserModelId

        setAppUserModelId("SATP_AI.Login")
    except Exception:
        pass

    from PySide6.QtWidgets import QApplication

    from ui.loginWin import runLoginLauncher

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    try:
        from utils.appIcon import applyAppIcon

        applyAppIcon(app, appId="SATP_AI.Login", kind="login")
    except Exception:
        pass

    termLog("[loginMain] 로그인 런처 시작 (창 유지 모드)")
    code = runLoginLauncher(app)
    termLog(f"[loginMain] 종료 code={code}")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        termLog("[loginMain] Ctrl+C")
        raise SystemExit(130)
