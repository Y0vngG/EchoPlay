import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from PySide6.QtCore import Qt
from echoplay.services.single_instance import activate_window

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from echoplay.services.single_instance import SingleInstance
app = QCoreApplication([])
directory = Path(sys.argv[1])
guard = SingleInstance(directory)
if not guard.start():
    print("secondary", flush=True)
    sys.exit(0)
guard.activation_requested.connect(lambda: print("activated", flush=True))
print("primary", flush=True)
QTimer.singleShot(15000, app.quit)
app.aboutToQuit.connect(guard.close)
app.exec()
'''


class SingleInstanceTests(unittest.TestCase):
    def launch(self, directory):
        return subprocess.Popen([sys.executable, '-u', '-c', CHILD, directory],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def test_repeated_launches_and_recovery_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = self.launch(directory)
            try:
                self.assertEqual(primary.stdout.readline().strip(), 'primary')
                children = [self.launch(directory) for _ in range(5)]
                for child in children:
                    out, err = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, err)
                    self.assertEqual(out.strip(), 'secondary')
                for _ in children:
                    self.assertEqual(primary.stdout.readline().strip(), 'activated')
            finally:
                primary.kill()
                primary.communicate(timeout=5)
            restarted = self.launch(directory)
            try:
                self.assertEqual(restarted.stdout.readline().strip(), 'primary')
            finally:
                restarted.kill()
                restarted.communicate(timeout=5)

    def test_activation_restores_minimized_window_and_preserves_maximized_state(self):
        window = Mock()
        window.isMinimized.return_value = True
        window.windowState.return_value = (Qt.WindowState.WindowMinimized |
                                           Qt.WindowState.WindowMaximized)
        activate_window(window)
        window.setWindowState.assert_called_once_with(Qt.WindowState.WindowMaximized)
        window.show.assert_called_once()
        window.raise_.assert_called_once()
        window.activateWindow.assert_called_once()

    def test_activation_does_not_change_normal_window_state(self):
        window = Mock()
        window.isMinimized.return_value = False
        activate_window(window)
        window.setWindowState.assert_not_called()
        window.show.assert_called_once()
