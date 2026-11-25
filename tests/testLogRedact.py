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
        path_only = f"url: /bot{_FAKE}/getUpdates?offset=0&timeout=30"
        masked_path = redactSecrets(f" ({path_only}) ")
        self.assertNotIn(_FAKE, masked_path)
        self.assertIn("/bot***/getUpdates", masked_path)
        enc = "123456789%3A" + ("B" * 32)
        dbl = "123456789%253A" + ("C" * 32)
        for token in (enc, dbl):
            got = redactSecrets(f"url=/bot{token}/getUpdates token {token}")
            self.assertNotIn(token, got)
            self.assertIn("/bot***/getUpdates", got)
        access = "AbCd" + ("x" * 40)
        for line in (
            f"[모의투자] 토큰: {access}",
            f"[실투자] 토큰 : {access}",
            f"token={access}",
            f"access_token: {access}",
        ):
            masked = redactSecrets(line)
            self.assertNotIn(access, masked)
            self.assertIn("***", masked)

    def test_pool_timeout_and_desk_log_hide_path_token(self):
        """호스트 없이 url: /bot<더미>/getUpdates 로 찍힌 수신 오류."""
        installSecretFilters()
        secret = "987654321:" + ("D" * 32)
        pool = (
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Max retries exceeded with url: "
            f"/bot{secret}/getUpdates?offset=7&timeout=30 "
            "(Caused by ReadTimeoutError(\"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Read timed out. (read timeout=40)\"))"
        )
        conn = (
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Max retries exceeded with url: "
            f"/bot{secret}/getUpdates?offset=7&timeout=30 "
            "(Caused by ConnectionError(\"Connection aborted.\"))"
        )
        loop_line = f"수신 루프 오류: {pool}"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "handler.log"
            logger = logging.getLogger("SATP")
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            try:
                logger.error("%s", pool)
                try:
                    raise TimeoutError(conn)
                except TimeoutError:
                    logger.exception("수신 루프 오류")
            finally:
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
            logged = path.read_text(encoding="utf-8")

            import utils.deskFileLog as logs

            patched = logs.appendDeskFileLog
            import importlib
            importlib.reload(logs)
            day_file = Path(folder) / "SATP_20261123.log"
            try:
                logs.deskLogsDir = lambda: Path(folder)
                logs.deskLogFilePath = lambda ymd=None: day_file
                logs.appendDeskFileLog(loop_line, tag="TERM")
                logs.appendDeskFileLog(f"오프셋 확정 실패(무시): {conn}", tag="TERM")
            finally:
                logs.appendDeskFileLog = patched
            day_text = day_file.read_text(encoding="utf-8")
        self.assertNotIn(secret, logged)
        self.assertNotIn(secret, day_text)
        self.assertIn("/bot***/getUpdates", logged)
        self.assertIn("/bot***/getUpdates", day_text)
        self.assertIn("수신 루프 오류", logged)
        self.assertIn("ReadTimeout", logged)
        self.assertIn("ConnectionError", day_text)

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

    def test_login_token_line_is_masked_and_scan_hides_value(self):
        from io import StringIO
        from contextlib import redirect_stdout

        from tools.scanLogSecrets import main as scanMain
        from tools.scanLogSecrets import suspectLine
        from utils.logRedact import maskToken

        access = "ZzYy" + ("q" * 36)
        shown = maskToken(access)
        self.assertNotIn(access, shown)
        self.assertIn("(len=", shown)
        self.assertNotIn(access, redactSecrets(f"[모의투자] 토큰: {access}"))
        self.assertNotIn(access, redactSecrets(f"[실투자] 토큰 : {access}"))
        self.assertFalse(suspectLine(f"[모의투자] 토큰: {shown}"))
        self.assertTrue(suspectLine(f"[실투자] 토큰: {access}"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SATP_20261125.log"
            path.write_text(
                f"[모의투자] 토큰: {access}\n[모의투자] 토큰: {shown}\n",
                encoding="utf-8",
            )
            buf = StringIO()
            with redirect_stdout(buf):
                code = scanMain([str(path)])
            text = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn(access, text)
        self.assertIn("의심 1", text)
        self.assertIn("의심 줄 1", text)


if __name__ == "__main__":
    unittest.main()
