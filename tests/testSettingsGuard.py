"""settings.json 을 디폴트로 조용히 덮어쓰지 않는지."""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SettingsGuardTest(unittest.TestCase):
    def _paths(self, folder):
        import config.settingsStore as store

        root = Path(folder)
        default = root / "settings-default.json"
        live = root / "settings.json"
        default.write_text(
            json.dumps({"deskTradeMode": "real", "mktOpenHm": "09:00", "marker": "default"}),
            encoding="utf-8",
        )
        store._recoverOnce.clear()
        return store, default, live

    def test_broken_file_keeps_trade_mode_and_backs_up(self):
        with tempfile.TemporaryDirectory() as folder:
            store, default, live = self._paths(folder)
            live.write_text('{"deskTradeMode": "paper", "keep": ', encoding="utf-8")
            with patch.object(store, "_SETTINGS_PATH", live), patch.object(store, "_DEFAULT_PATH", default):
                got = store.loadSettings()
            self.assertEqual(got["deskTradeMode"], "paper")
            saved = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual(saved["deskTradeMode"], "paper")
            baks = list(Path(folder).glob("settings.json.bak-*"))
            self.assertEqual(len(baks), 1)
            self.assertIn("paper", baks[0].read_text(encoding="utf-8"))

    def test_unreadable_mode_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            store, default, live = self._paths(folder)
            live.write_text("this is not json", encoding="utf-8")
            with patch.object(store, "_SETTINGS_PATH", live), patch.object(store, "_DEFAULT_PATH", default):
                store.loadSettings()
            self.assertEqual(live.read_text(encoding="utf-8"), "this is not json")
            self.assertTrue(list(Path(folder).glob("settings.json.bak-*")))

    def test_reset_keeps_trade_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            store, default, live = self._paths(folder)
            live.write_text(
                json.dumps({"deskTradeMode": "paper", "keep": 1}),
                encoding="utf-8",
            )
            with patch.object(store, "_SETTINGS_PATH", live), patch.object(store, "_DEFAULT_PATH", default):
                got = store.resetSettingsToDefault()
            self.assertEqual(got["deskTradeMode"], "paper")
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["deskTradeMode"], "paper")
            self.assertTrue(list(Path(folder).glob("settings.json.bak-*")))

    def test_invalid_mode_patches_one_key(self):
        with tempfile.TemporaryDirectory() as folder:
            store, default, live = self._paths(folder)
            live.write_text(
                json.dumps({"deskTradeMode": "???", "keep": 7}),
                encoding="utf-8",
            )
            with patch.object(store, "_SETTINGS_PATH", live), patch.object(store, "_DEFAULT_PATH", default):
                mode = store.getDeskTradeMode()
            saved = json.loads(live.read_text(encoding="utf-8"))
            self.assertEqual(mode, "paper")
            self.assertEqual(saved["deskTradeMode"], "paper")
            self.assertEqual(saved["keep"], 7)

    def test_zip_update_does_not_replace_settings(self):
        from utils.deskAppUpdate import applyZipUpdate

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "config" / "data"
            data.mkdir(parents=True)
            settings = data / "settings.json"
            settings.write_text('{"deskTradeMode": "paper", "keep": 3}\n', encoding="utf-8")
            zpath = root / "upd.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("config/data/settings.json", '{"deskTradeMode": "real"}\n')
                zf.writestr("hello.txt", "ok")
            applyZipUpdate(zpath, root, skipRuntime=True)
            self.assertIn("paper", settings.read_text(encoding="utf-8"))
            self.assertEqual((root / "hello.txt").read_text(encoding="utf-8"), "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
