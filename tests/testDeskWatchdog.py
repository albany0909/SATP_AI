"""모의 데스크 워치독. 임시 파일과 모킹만 쓰고 settings.json 은 열지 않는다."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.liveGuard  # noqa: E402

import tools.deskWatchdog as wd
import tools.deskWatchdogPause as pause


def _settings(mode="paper", ma=True, strat=True, omit=False):
    if omit:
        return {"deskTradeMode": mode}
    return {
        "deskTradeMode": mode,
        "maTouchLive": {"paperOnly": ma},
        "stratLive": {"PBK01": {"paperOnly": strat}},
    }


class DeskWatchdogTest(unittest.TestCase):
    def test_import_has_no_windows_requirement(self):
        self.assertTrue(wd.REPO.is_dir())
        self.assertEqual(wd.REPO, Path(wd.__file__).resolve().parents[1])
        self.assertEqual(pause.REPO, wd.REPO)

    def test_read_paper_guard_modes(self):
        cases = [
            (_settings(), True, "deskTradeMode=paper paperOnly=true"),
            (_settings(mode="real"), False, "deskTradeMode=real"),
            (_settings(strat=False), False, "paperOnly=false:stratLive.PBK01"),
            (_settings(omit=True), False, "paperOnly missing"),
        ]
        for payload, ok, reason in cases:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settings.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with patch.object(wd, "SETTINGS", path):
                    got_ok, got_reason = wd.read_paper_guard()
            self.assertEqual(got_ok, ok)
            self.assertEqual(got_reason, reason)

    def test_is_desk_cmd(self):
        self.assertTrue(wd.is_desk_cmd(r"C:\Py\python.exe C:\repo\main.py"))
        self.assertFalse(wd.is_desk_cmd(r"C:\Py\python.exe C:\repo\tools\deskWatchdog.py"))
        self.assertFalse(wd.is_desk_cmd(r"C:\Py\python.exe C:\repo\tests\main.py"))
        self.assertFalse(wd.is_desk_cmd(r"C:\Py\python.exe -m unittest main.py"))
        self.assertFalse(wd.is_desk_cmd(r"C:\Py\python.exe C:\repo\tools\pbkAmtCheck.py"))
        self.assertFalse(wd.is_desk_cmd(""))

    def test_pause_on_off_status_uses_temp_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / "desk_watchdog.pause"
            with patch.object(pause, "PAUSE", flag):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(pause.main(["deskWatchdogPause.py", "status"]), 0)
                self.assertIn("PAUSE_OFF", buf.getvalue())
                self.assertFalse(flag.is_file())
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(pause.main(["deskWatchdogPause.py", "on"]), 0)
                self.assertIn("PAUSE_ON", buf.getvalue())
                self.assertTrue(flag.is_file())
                self.assertNotIn("settings.json", flag.read_text(encoding="utf-8"))
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(pause.main(["deskWatchdogPause.py"]), 0)
                self.assertIn("PAUSE_ON", buf.getvalue())
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(pause.main(["deskWatchdogPause.py", "off"]), 0)
                self.assertIn("PAUSE_OFF", buf.getvalue())
                self.assertFalse(flag.is_file())

    def test_version_from_file_or_omitted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "appVersion.json"
            path.write_text(json.dumps({"major": 1, "minor": 60}), encoding="utf-8")
            with patch.object(wd, "VERSION", path):
                self.assertEqual(wd.read_app_version(), "v1.060")
                text, stamp = wd.watchdog_notice(15, when="2026-11-23 08:00:00")
            self.assertEqual(stamp, "2026-11-23 08:00:00")
            self.assertIn("(PID 15, v1.060)", text)
            missing = Path(folder) / "missing.json"
            with patch.object(wd, "VERSION", missing):
                self.assertEqual(wd.read_app_version(), "")
                bare, _stamp = wd.watchdog_notice(15, when="2026-11-23 08:00:00")
            self.assertIn("(PID 15)", bare)
            self.assertNotIn("v1.", bare)

    def test_python_and_system_paths(self):
        env = os.environ.copy()
        env.pop("SATP_DESK_PYTHON", None)
        env["SystemRoot"] = r"D:\Win"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                str(wd.conhost_exe()).replace("/", "\\"),
                r"D:\Win\System32\conhost.exe",
            )
            self.assertEqual(
                str(wd.powershell_exe()).replace("/", "\\"),
                r"D:\Win\System32\WindowsPowerShell\v1.0\powershell.exe",
            )
            with patch.object(sys, "executable", r"C:\Py\pythonw.exe"):
                self.assertEqual(wd.desk_python(), Path(r"C:\Py\python.exe"))
            with patch.object(sys, "executable", r"C:\Py\python.exe"):
                self.assertEqual(wd.desk_python(), Path(r"C:\Py\python.exe"))
            env["SATP_DESK_PYTHON"] = r"E:\py\python.exe"
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(wd.desk_python(), Path(r"E:\py\python.exe"))

    def test_window_and_process_queries_are_mocked(self):
        class User32(object):
            def IsWindowVisible(self, hwnd):
                return True

            def GetWindowTextLengthW(self, hwnd):
                return len("천둥이 MAIN")

            def GetWindowTextW(self, hwnd, buf, n):
                buf.value = "천둥이 MAIN"

            def GetWindowThreadProcessId(self, hwnd, pref):
                pref._obj.value = 42

            def EnumWindows(self, cb, lparam):
                cb(1, 0)
                return True

        def _wnd_factory(*_types):
            return lambda fn: fn

        fake_windll = SimpleNamespace(user32=User32())
        payload = json.dumps(
            {
                "ProcessId": 42,
                "Name": "python.exe",
                "CommandLine": r"C:\Py\python.exe C:\repo\main.py",
            }
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as folder, patch.object(
            wd.ctypes, "windll", fake_windll, create=True
        ), patch.object(
            wd.ctypes, "WINFUNCTYPE", _wnd_factory, create=True
        ), patch.object(wd, "LOG_DIR", Path(folder)), patch.object(
            wd.subprocess,
            "run",
            return_value=SimpleNamespace(stdout=payload),
        ) as run:
            titles = wd.window_titles()
            cmds = wd.python_cmds()
            desks, titled, _cmds = wd.find_desk()
        self.assertEqual(titles, [(42, "천둥이 MAIN")])
        self.assertEqual(cmds[0]["pid"], 42)
        self.assertTrue(wd.is_desk_cmd(cmds[0]["cmd"]))
        self.assertEqual(desks[0]["pid"], 42)
        self.assertEqual(titled[0]["how"], "title")
        self.assertIn("powershell.exe", run.call_args.args[0][0].lower())
        joined = " ".join(run.call_args.args[0]).lower()
        self.assertNotIn("kiwoom", joined)

    def test_start_desk_launches_paper_main_only(self):
        calls = []

        class Proc(object):
            pid = 9

        def _popen(args, **kwargs):
            calls.append((args, kwargs))
            return Proc()

        with tempfile.TemporaryDirectory() as folder:
            py = Path(folder) / "python.exe"
            con = Path(folder) / "conhost.exe"
            py.write_text("py", encoding="utf-8")
            con.write_text("con", encoding="utf-8")
            with patch.object(wd, "desk_python", return_value=py), patch.object(
                wd, "conhost_exe", return_value=con
            ), patch.object(wd.subprocess, "Popen", side_effect=_popen):
                self.assertEqual(wd.start_desk(), 9)
        args, kwargs = calls[0]
        self.assertEqual(args, [str(con), "--", str(py), "main.py"])
        self.assertEqual(kwargs["cwd"], str(wd.REPO))
        self.assertEqual(kwargs["env"].get("CURSOR_AGENT"), "1")
        self.assertNotIn("SATP_TEST", kwargs["env"])
        blob = " ".join(args).lower() + json.dumps(kwargs["env"]).lower()
        self.assertNotIn("desktrademode", blob)
        self.assertNotIn("real_host", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
