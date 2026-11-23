"""로그 마스킹. 가짜 토큰만 쓰고 실제 비밀은 넣지 않는다."""

import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.logRedact import installSecretFilters, redactSecrets

# 형식만 맞춘 가짜 값. 운영 토큰이 아니다.
_FAKE = "123456789:" + ("A" * 32)


class LogRedactTest(unittest.TestCase):
    def test_redact_url_token_bearer_and_appkey(self):
        url = f"https://api.telegram.org/bot{_FAKE}/getUpdates"
        masked = redactSecrets(f"수신 실패 {url} 토큰 {_FAKE}")
        self.assertNotIn(_FAKE, masked)
        self.assertIn("bot***", masked)
        self.assertIn("***", masked)
        bearer = redactSecrets("Authorization: Bearer ABCDEFGHIJKLMNOP")
        self.assertNotIn("ABCDEFGHIJKLMNOP", bearer)
        self.assertIn("Bearer ***", bearer)
        appkey = redactSecrets("appkey=ABCDEFGHIJKLMNOP")
        self.assertNotIn("ABCDEFGHIJKLMNOP", appkey)
        self.assertIn("appkey ***", appkey)

    def test_file_handler_hides_token_in_error_and_traceback(self):
        installSecretFilters()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "satp-test.log"
            logger = logging.getLogger("SATP")
            logger.setLevel(logging.DEBUG)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            try:
                logger.error("getUpdates %s", f"https://api.telegram.org/bot{_FAKE}/getUpdates")
                try:
                    raise RuntimeError(f"https://api.telegram.org/bot{_FAKE}/getUpdates")
                except RuntimeError:
                    logger.exception("수신 루프 오류")
            finally:
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn(_FAKE, text)
        self.assertIn("bot***", text)
        self.assertIn("수신 루프 오류", text)
        self.assertIn("RuntimeError", text)


if __name__ == "__main__":
    unittest.main()
