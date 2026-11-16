"""Verify the app and browser work with omitted Python bindings unavailable."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class MinimalQtBindingTests(unittest.TestCase):
    def test_window_and_browser_without_unused_bindings(self):
        code = textwrap.dedent('''
            import importlib.abc
            import sys
            from build_support.payload import UNUSED_QT_BINDINGS
            class BlockUnusedBindings(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in UNUSED_QT_BINDINGS:
                        raise ImportError("Binding excluded from package: " + fullname)
            sys.meta_path.insert(0, BlockUnusedBindings())
            import tempfile
            from pathlib import Path
            from unittest.mock import patch
            from PySide6.QtCore import QCoreApplication, QSettings, QTimer, Qt
            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
            from PySide6.QtWidgets import QApplication
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from echoplay.storage import LibraryStore
            from echoplay.ui.main_window import MainWindow
            from echoplay.ui.login_dialog import BilibiliLoginDialog
            app = QApplication([])
            app.setOrganizationName("EchoPlayBindingTests")
            app.setApplicationName("IsolatedBindings")
            with tempfile.TemporaryDirectory() as directory:
                QSettings.setDefaultFormat(QSettings.Format.IniFormat)
                QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
                with patch("echoplay.ui.main_window.load_session", return_value={}), patch.object(MainWindow, "_start_worker"):
                    window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                with patch.object(QWebEngineView, "load"):
                    dialog = BilibiliLoginDialog(window)
                passed = []
                def result(value):
                    passed.append(value == 42)
                    app.quit()
                def loaded(ok):
                    if ok:
                        dialog.page.runJavaScript("21*2", result)
                    else:
                        app.quit()
                dialog.browser.loadFinished.connect(loaded)
                dialog.browser.setHtml("<html><body>Browser binding check</body></html>")
                QTimer.singleShot(15000, app.quit)
                app.exec()
                assert passed == [True], passed
                assert not set(UNUSED_QT_BINDINGS).intersection(sys.modules)
                dialog.close()
                dialog.page.deleteLater()
                app.processEvents()
                window.close()
            print("Minimal Qt bindings passed")
        ''')
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Minimal Qt bindings passed", result.stdout)
