"""DART 인증키를 로그·예외·URL 문자열에서 지운다."""

from __future__ import annotations

import re

_KEY_PARAM = re.compile(r"(crtfc_key=)[^&\s\"']+", re.I)


def maskDartSecret(text, key=""):
    """crtfc_key 값과 알고 있는 키 문자열을 *** 로 바꾼다."""
    out = str(text or "")
    out = _KEY_PARAM.sub(r"\1***", out)
    secret = str(key or "").strip()
    if secret:
        out = out.replace(secret, "***")
    return out
