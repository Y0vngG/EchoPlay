import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from echoplay.storage import LibraryStore
from echoplay.ui.icons import _asset_path
from echoplay.ui.main_window import MainWindow


class TaskbarIconPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayIconTests")
        cls.app.setApplicationName("IconPersistence")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        QSettings().clear()
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch("echoplay.ui.main_window.load_session", return_value={}))
        self.create_icon = self.patches.enter_context(patch("echoplay.ui.settings._create_windows_icon", return_value=123))
        self.set_large_icon = self.patches.enter_context(patch("echoplay.ui.settings._set_taskbar_large_icon"))
        self.set_shell_identity = self.patches.enter_context(patch("echoplay.ui.settings.set_taskbar_identity"))
        self.patches.enter_context(patch("echoplay.ui.main_window._set_taskbar_large_icon"))
        self.patches.enter_context(patch.object(MainWindow, "_destroy_windows_icon"))

    def assert_native_image_matches(self, path):
        actual = self.create_icon.call_args.args[0]
        expected = QImage(str(path))
        self.assertFalse(actual.isNull())
        self.assertEqual(actual.size(), expected.size())
        self.assertTrue(all(actual.pixelColor(x, y) == expected.pixelColor(x, y)
                            for y in range(actual.height()) for x in range(actual.width())))

    def test_presets_restore_only_taskbar_icon_after_restart_and_window_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.db")
            for source, _, filename in MainWindow.TASKBAR_ICON_PRESETS:
                with self.subTest(source=source):
                    window = MainWindow(store)
                    default = QPixmap(32, 32)
                    default.fill(QColor("red"))
                    window.setWindowIcon(QIcon(default))
                    qt_icon = window.windowIcon().cacheKey()
                    window._select_taskbar_icon(source)
                    self.assertEqual(QSettings().value("appearance/taskbar_icon_source"), source)
                    self.assertEqual(window.windowIcon().cacheKey(), qt_icon)
                    self.assert_native_image_matches(_asset_path("assets", "taskbar_icons", filename))
                    window.close()
                    restarted = MainWindow(store)
                    try:
                        default = QPixmap(32, 32)
                        default.fill(QColor("red"))
                        restarted.setWindowIcon(QIcon(default))
                        qt_icon = restarted.windowIcon().cacheKey()
                        restarted.show()
                        restarted.apply_saved_taskbar_icon()
                        self.app.processEvents()
                        previous_calls = self.set_large_icon.call_count
                        restarted.hide()
                        restarted.show()
                        self.app.processEvents()
                        self.assertGreater(self.set_large_icon.call_count, previous_calls)
                        self.assertEqual(restarted.windowIcon().cacheKey(), qt_icon)
                        self.set_large_icon.assert_called_with(int(restarted.winId()), 123)
                        self.assert_native_image_matches(_asset_path("assets", "taskbar_icons", filename))
                    finally:
                        restarted.close()

    def test_custom_image_survives_source_removal_and_reselection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "data" / "library.db")
            original = Path(directory) / "original.png"
            image = QImage(32, 32, QImage.Format.Format_ARGB32)
            image.fill(QColor("blue"))
            self.assertTrue(image.save(str(original)))
            window = MainWindow(store)
            window._select_taskbar_icon("custom", str(original))
            cached = Path(window._custom_taskbar_icon_path)
            self.assertNotEqual(cached, original)
            self.assertTrue(cached.is_file())
            original.unlink()
            window._select_taskbar_icon("excel")
            window._select_taskbar_icon("custom")
            self.assertEqual(window._custom_taskbar_icon_path, str(cached))
            window.close()
            restarted = MainWindow(store)
            try:
                restarted.apply_saved_taskbar_icon()
                self.assertEqual(restarted._taskbar_icon_source, "custom")
                self.assert_native_image_matches(cached)
            finally:
                restarted.close()

    def test_startup_error_does_not_replace_saved_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(LibraryStore(Path(directory) / "library.db"))
            try:
                window._select_taskbar_icon("excel")
                with patch.object(window, "_apply_taskbar_icon", return_value="temporary failure"), \
                     patch("echoplay.ui.settings.QMessageBox.warning"):
                    window.apply_saved_taskbar_icon()
                self.assertEqual(window._taskbar_icon_source, "excel")
                self.assertEqual(QSettings().value("appearance/taskbar_icon_source"), "excel")
            finally:
                window.close()
