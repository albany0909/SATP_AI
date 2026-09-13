"""
키움·텔레그램 등 접속 설정.
민감값은 .env (또는 OS 환경변수)에서 로드 — 저장소에 평문 키를 두지 않음.
"""

import os

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()


def _env(name, default=""):
    """환경변수 문자열. 없거나 공백이면 default."""
    val = os.environ.get(name)
    if val is None:
        return default
    text = str(val).strip()
    return text if text != "" else default


# HTTP / 소켓 URL (공개 엔드포인트 — 기본값 코드 유지, .env 로 덮어쓰기 가능)
real_host_url = _env("KIWOOM_REAL_HOST_URL", "https://api.kiwoom.com")
real_socket_url = _env("KIWOOM_REAL_SOCKET_URL", "wss://api.kiwoom.com:10000")
paper_host_url = _env("KIWOOM_PAPER_HOST_URL", "https://mockapi.kiwoom.com")
paper_socket_url = _env("KIWOOM_PAPER_SOCKET_URL", "wss://mockapi.kiwoom.com:10000")

# 앱 키·시크릿 (필수 — .env 에 설정)
real_app_key = _env("KIWOOM_REAL_APP_KEY", "")
real_app_secret = _env("KIWOOM_REAL_APP_SECRET", "")
paper_app_key = _env("KIWOOM_PAPER_APP_KEY", "")
paper_app_secret = _env("KIWOOM_PAPER_APP_SECRET", "")

# 텔레그램
telegram_chat_id = _env("TELEGRAM_CHAT_ID", "")
telegram_token = _env("TELEGRAM_BOT_TOKEN", "")

# Open DART (공시·재무 — 인증키)
dart_api_key = _env("DART_API_KEY", "")
