import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMainWindow

from echoplay.services.updater import PreparedUpdate
from echoplay.ui.update_controller import UpdateController


class UpdateControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QMainWindow()
        self.controller = UpdateController(self.window)

    def tearDown(self):
        self.controller._stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_source_run_does_not_start_network_worker(self):
        with patch("echoplay.ui.update_controller.sys.frozen", False, create=True), \
                patch("echoplay.ui.update_controller.Worker") as worker:
            self.controller.start()
        worker.assert_not_called()

    def test_no_new_version_does_not_prepare_or_restart(self):
        with patch("echoplay.ui.update_controller.check_release", return_value=None), \
                patch("echoplay.ui.update_controller.prepare_update") as prepare:
            self.assertIsNone(self.controller._prepare())
        prepare.assert_not_called()

    def test_close_cancels_pending_restart_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            work.mkdir()
            prepared = PreparedUpdate(work, work / "payload", Path(directory) / "app", "EchoPlay.exe", "0.2.4")
            with patch("echoplay.ui.update_controller.QTimer.singleShot") as timer:
                self.controller._ready(prepared)
            self.assertEqual(timer.call_args.args[0], 10000)
            self.assertIn("10 秒", self.window.statusBar().currentMessage())
            self.controller._stop()
            self.assertFalse(work.exists())
            with patch("echoplay.ui.update_controller.launch_installer") as launch:
                self.controller._install()
            launch.assert_not_called()

    def test_installer_start_failure_keeps_current_app_running(self):
        with patch("echoplay.ui.update_controller.launch_installer", side_effect=OSError("blocked")), \
                patch.object(self.window, "close") as close:
            self.controller._install()
        close.assert_not_called()
        self.assertFalse(self.controller.installing)
        self.assertIn("下次启动", self.window.statusBar().currentMessage())
        self.assertIn("blocked", self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
