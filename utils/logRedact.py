"""로그에서 텔레그램 봇 토큰과 비슷한 비밀을 가린다.

송수신 URL을 만들거나 토큰을 읽는 경로는 바꾸지 않는다.
"""

from __future__ import annotations

import logging
import re
import traceback

# 콜론, %3A/%3a, 이중 인코딩 %253A/%253a
_COLON = r"(?::|%253[Aa]|%3[Aa])"
_SECRET = r"[A-Za-z0-9_-]{30,}"
# 호스트가 없어도 /bot<숫자>:<비밀> 은 /bot*** . 앞의 공백·따옴표·괄호·url= 은 그대로 둔다.
_TG_PATH = re.compile(
    rf"/bot\d{{6,}}{_COLON}{_SECRET}",
    re.IGNORECASE,
)
# 경로에 붙지 않은 단독 토큰
_TG_TOKEN = re.compile(
    rf"(?<![A-Za-z0-9_/])\d{{6,}}{_COLON}{_SECRET}",
    re.IGNORECASE,
)
_BEARER = re.compile(
    r"(?i)\b(bearer|appkey|app_key)\b(\s*[:=]\s*|\s+)([A-Za-z0-9\-._~+/=]{8,})"
)
# '토큰:' '토큰 :' 'token=' 처럼 라벨 뒤에 붙는 접근 토큰
_LABELED = re.compile(
    r"(?i)(토큰|token|access_token|authorization)\s*[:=]\s*([A-Za-z0-9_\-\.~+/=]{8,})"
)

_INSTALLED = False
_ORIG_ADD_HANDLER = None


def maskToken(token):
    """로그에 남길 토큰 표시. 앞 4자와 길이만 두고 나머지는 뺀다."""
    text = "" if token is None else str(token)
    if len(text) <= 4:
        return "***"
    return f"{text[:4]}…(len={len(text)})"


def redactSecrets(text):
    """비밀이 들어간 문자열을 마스킹한다. 문자열이 아니면 문자열로 바꾼 뒤 처리한다."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _TG_PATH.sub("/bot***", text)
    text = _TG_TOKEN.sub("***", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} ***", text)
    text = _LABELED.sub(lambda m: f"{m.group(1)}: ***", text)
    return text


def _redact_obj(value):
    if isinstance(value, str):
        return redactSecrets(value)
    return value


class SecretRedactFilter(logging.Filter):
    """핸들러로 나가기 전에 메시지·인자·traceback 을 가린다."""

    def filter(self, record):
        record.msg = _redact_obj(record.msg)
        args = record.args
        if isinstance(args, dict):
            record.args = {key: _redact_obj(val) for key, val in args.items()}
        elif isinstance(args, tuple):
            record.args = tuple(_redact_obj(val) for val in args)
        if record.exc_info and record.exc_info[0] is not None:
            try:
                text = "".join(traceback.format_exception(*record.exc_info))
            except Exception:
                text = ""
            record.exc_text = redactSecrets(text)
        elif record.exc_text:
            record.exc_text = redactSecrets(record.exc_text)
        return True


def _attach(target, filt):
    if any(isinstance(item, SecretRedactFilter) for item in target.filters):
        return
    target.addFilter(filt)


def installSecretFilters():
    """루트·SATP 핸들러와 urllib3/requests 로거에 마스킹을 붙인다. 여러 번 호출해도 된다."""
    global _INSTALLED, _ORIG_ADD_HANDLER
    filt = SecretRedactFilter()
    names = (
        "SATP",
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "requests.packages.urllib3",
    )
    loggers = [logging.getLogger()] + [logging.getLogger(name) for name in names]
    for logger in loggers:
        _attach(logger, filt)
        for handler in list(logger.handlers):
            _attach(handler, filt)
    for name in ("urllib3", "urllib3.connectionpool", "requests.packages.urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if not _INSTALLED:
        _ORIG_ADD_HANDLER = logging.Logger.addHandler

        def _add_handler(self, hdlr):
            _ORIG_ADD_HANDLER(self, hdlr)
            watched = (
                self.name in ("", "root", "SATP")
                or self.name.startswith("urllib3")
                or self.name.startswith("requests")
            )
            if watched:
                _attach(hdlr, SecretRedactFilter())

        logging.Logger.addHandler = _add_handler
        _INSTALLED = True
    return True
