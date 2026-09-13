"""프로젝트 루트 .env 를 1회 로드"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOADED = False


def ensureEnvLoaded():
    """
    루트 .env 를 환경변수에 반영.
    python-dotenv 미설치 시 무시(시스템 환경변수만 사용).
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    envPath = _ROOT / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # 기존 OS 환경변수가 있으면 덮어쓰지 않음
    load_dotenv(dotenv_path=envPath, override=False)
