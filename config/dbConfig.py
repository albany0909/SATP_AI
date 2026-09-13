"""
MySQL 접속 설정.
비밀번호 등 민감값은 .env / 환경변수에서 로드.
"""

import os

from config.loadEnv import ensureEnvLoaded

ensureEnvLoaded()


def _env(name, default=""):
    val = os.environ.get(name)
    if val is None:
        return default
    text = str(val).strip()
    return text if text != "" else default


dbHost = _env("DB_HOST", "localhost")
dbPort = int(_env("DB_PORT", "3306") or "3306")
dbName = _env("DB_NAME", "thor_db")
dbUser = _env("DB_USER", "root")
dbPassword = _env("DB_PASSWORD", "")
dbCharset = _env("DB_CHARSET", "utf8mb4")
